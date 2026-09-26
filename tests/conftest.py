import os
import secrets
import socket
import subprocess
import sys
import time
from pathlib import Path
from uuid import uuid4

import httpx
import pytest
from sqlalchemy import create_engine, text

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture(autouse=True)
def evidence_executor(tmp_path_factory, request):
    # UI必须先用全新隔离schema真实迁移，再启动API/Vite；凭证仅存在进程内。
    database = os.environ.get('TEST_DATABASE_URL', 'postgresql+psycopg://logscope_test:local_test_only@127.0.0.1:55433/logscope_test')
    from sqlalchemy.engine import make_url
    parsed = make_url(database)
    assert parsed.host == '127.0.0.1' and parsed.port == 55433, 'UI只允许隔离PG17端口55433'
    api_port = int(os.environ.get('UI_API_PORT', '8000'))
    ui_port = int(os.environ.get('UI_PORT', '5173'))
    for port in (api_port, ui_port):
        deadline = time.monotonic() + 10
        while True:
            with socket.socket() as probe:
                probe.settimeout(0.1)
                if probe.connect_ex(('127.0.0.1', port)) != 0:
                    break
            assert time.monotonic() < deadline, 'UI测试端口持续占用，不能复用未知服务'
    schema = 'issue03_ui_' + uuid4().hex
    engine = create_engine(database)
    with engine.begin() as connection:
        connection.execute(text(f'CREATE SCHEMA "{schema}"'))
    engine.dispose()
    credential = secrets.token_urlsafe(32)
    env = {**os.environ, 'DATABASE_URL': database + '?options=-csearch_path%3D' + schema,
           'PYTHONPATH': str(ROOT / 'backend'), 'SERVICES_FILE': str(ROOT / 'backend/services.example.json'),
           'EXECUTOR_CREDENTIAL': credential, 'UI_API_TARGET': f'http://127.0.0.1:{api_port}', 'NO_PROXY': '127.0.0.1,localhost', 'no_proxy': '127.0.0.1,localhost'}
    legacy_mode = getattr(request, 'param', None)
    legacy = legacy_mode in ('legacy_history', 'legacy_narrow')
    migration = subprocess.run([sys.executable, '-m', 'alembic', '-c', 'backend/alembic.ini', 'upgrade', '0013' if legacy else 'head'],
                               cwd=ROOT, env=env, capture_output=True)
    assert migration.returncode == 0, 'UI隔离schema真实迁移失败'
    legacy_path = legacy_ids = None
    if legacy:
        sys.path.insert(0, str(ROOT / 'backend/tests'))
        from test_historical_evidence import upgrade_reliable_history
        legacy_path, legacy_ids = upgrade_reliable_history(env['DATABASE_URL'], 'narrow' if legacy_mode == 'legacy_narrow' else 'same')
    fixture_dir = tmp_path_factory.mktemp('query_ui')
    import json
    config = fixture_dir / 'services.json'
    config.write_text(json.dumps({'services': [
        {'id': 'orders', 'name': '订单服务（仅可控适配）', 'source_ids': ['controlled-orders']},
        {'id': 'query-mixed', 'name': '混合覆盖测试', 'source_ids': ['ui-limited', 'ui-healthy']},
        {'id': 'cancel-ui', 'name': '取消测试', 'source_ids': ['ui-cancel']},
        {'id': 'deadline-ui', 'name': '总时限测试', 'source_ids': ['ui-deadline']}]}), encoding='utf-8')
    env['SERVICES_FILE'] = str(config)
    processes = []
    try:
        startup = '''
import uvicorn
from app.main import create_app
from app.queries import SourceFailure
class Limited:
    async def query(self, **scope):
        raise SourceFailure('rate_limited')
class Healthy:
    async def query(self, **scope):
        return None
class CancelSource:
    async def query(self, **scope):
        import asyncio
        try:
            await asyncio.Future()
        except asyncio.CancelledError:
            await asyncio.sleep(3)
from datetime import datetime, timedelta, timezone
shift = timedelta()
def clock():
    return datetime.now(timezone.utc) + shift
class DeadlineSource(CancelSource):
    async def query(self, **scope):
        global shift
        shift += timedelta(minutes=10)
        await super().query(**scope)
uvicorn.run(create_app(clock=clock, query_sources={'ui-limited': Limited(), 'ui-healthy': Healthy(), 'ui-cancel': CancelSource(), 'ui-deadline': DeadlineSource()}), host='127.0.0.1', port=UI_API_PORT, access_log=False)
'''
        startup = startup.replace('port=UI_API_PORT', f'port={api_port}')
        processes.append(subprocess.Popen([sys.executable, '-c', startup],
                                         cwd=ROOT, env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL))
        processes.append(subprocess.Popen(['node', 'node_modules/vite/bin/vite.js',
                                          *(['preview'] if os.environ.get('UI_PREVIEW') == '1' else []), '--host', '127.0.0.1',
                                          '--port', str(ui_port), '--strictPort'], cwd=ROOT, env=env,
                                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL))
        with httpx.Client(timeout=2, trust_env=False) as client:
            deadline = time.monotonic() + 30
            while True:
                assert all(process.poll() is None for process in processes), 'UI的API/Vite进程启动失败'
                try:
                    services = client.get(f'http://127.0.0.1:{ui_port}/api/services')
                    if services.status_code == 200 and services.json()['items'][0]['id'] == 'orders':
                        break
                except (httpx.TransportError, ValueError, KeyError, IndexError):
                    pass
                assert time.monotonic() < deadline, 'UI真实API/Vite未在期限内就绪'
        print('UI隔离schema: ' + schema)
        class ExecutorHeaders(dict):
            pass
        headers = ExecutorHeaders(Authorization='Bearer ' + credential)
        headers.database_url = env['DATABASE_URL']
        headers.legacy_path, headers.legacy_ids = legacy_path, legacy_ids
        headers.legacy_mode = legacy_mode
        yield headers
    finally:
        for process in reversed(processes):
            process.terminate()
            process.wait(timeout=15)
