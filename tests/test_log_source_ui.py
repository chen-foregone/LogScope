import sys
from pathlib import Path
from uuid import uuid4

import pytest
from playwright.sync_api import sync_playwright, expect

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'backend' / 'tests'))
from test_log_sources import external_source, serve_app
from test_attempts import configured_client
from test_evidence import started
from test_investigations import database_url


@pytest.fixture
def evidence_executor(database_url):
    # This suite owns its API and static server; do not share fixed UI ports.
    return database_url


@pytest.mark.parametrize('scenario,count', [('normal', 2), ('unidentified', 3), ('invalid', 2),
                                         ('database-pool', 3), ('redis-contradiction', 3), ('permission-denied', 2)])
def test_simulated_http_evidence_visible_after_reload(evidence_executor, tmp_path, external_source, scenario, count):
    from app.log_sources import SimulatedLogSource
    credential = uuid4().hex
    import functools
    import threading
    from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer

    class QuietHandler(SimpleHTTPRequestHandler):
        def log_message(self, *args):
            pass

    handler = functools.partial(QuietHandler, directory=str(Path(__file__).resolve().parents[1] / 'dist'))
    server = ThreadingHTTPServer(('127.0.0.1', 0), handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    from test_incident_scenarios import scenario_client
    from test_agent_runtime import agent_config, QueryModel, wait_finished
    agent_options = {'agent_settings': agent_config(), 'agent_model': QueryModel()} if scenario == 'permission-denied' else {}
    source_url = external_source(scenario)
    application = (scenario_client(evidence_executor, tmp_path, source_url, credential, **agent_options)
                   if scenario in ('database-pool', 'redis-contradiction', 'permission-denied') else
                   configured_client(evidence_executor, tmp_path, executor_credential=credential,
                       query_sources={'controlled-orders': SimulatedLogSource(source_url)}))
    with application as api:
        if agent_options:
            from test_attempts import create
            record, _ = create(api)
            path = '/api/investigations/' + record['id']
            assert api.post(path + '/attempts', json={'service_id': 'orders',
                'starts_at': '2026-11-01T00:00:00Z', 'ends_at': '2026-11-01T01:00:00Z'},
                headers={'Idempotency-Key': str(uuid4())}).status_code == 201
            detail = wait_finished(api, path)
        else:
            path, record, evidence, auth = started(api, credential)
            assert api.post(evidence.removesuffix('/evidence') + '/queries',
                            headers={**auth, 'Idempotency-Key': str(uuid4())}).status_code == 201
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            try:
                page = browser.new_page(viewport={'width': 390, 'height': 844})

                def api_route(route):
                    from urllib.parse import urlsplit
                    url = urlsplit(route.request.url)
                    reply = api.request(route.request.method, url.path + ('?' + url.query if url.query else ''),
                                        content=route.request.post_data, headers={'Content-Type': 'application/json'})
                    route.fulfill(status=reply.status_code, body=reply.content,
                                  content_type=reply.headers.get('content-type', 'application/json'))

                page.route('**/api/**', api_route)
                page.goto(f'http://127.0.0.1:{server.server_port}/#/investigations/' + record['id'])
                for refresh in range(2):
                    expect(page.get_by_role('note').filter(has_text='模拟外部日志来源').first).to_be_visible()
                    expect(page.get_by_text(f'本轮证据条数：{count}', exact=False)).to_be_visible()
                    expect(page.get_by_label('脱敏证据', exact=True)).to_have_count(count)
                    if scenario == 'unidentified':
                        expect(page.get_by_role('status').filter(has_text='可能重复')).to_be_visible()
                    if scenario == 'invalid':
                        expect(page.get_by_text('原因：适配器能力或返回不符合受控契约')).to_be_visible()
                        expect(page.get_by_text('缺失来源：', exact=False)).to_be_visible()
                    if scenario in ('database-pool', 'redis-contradiction'):
                        expect(page.get_by_label('结构化观测', exact=True)).to_have_count(3)
                        expect(page.get_by_text('HTTP 状态：504', exact=True)).to_be_visible()
                        expect(page.get_by_text('组件：网关', exact=False)).to_be_visible()
                        if scenario == 'database-pool':
                            expect(page.get_by_text('连接数：100 / 100', exact=True)).to_be_visible()
                        else:
                            expect(page.get_by_text('组件：Redis', exact=False)).to_be_visible()
                    if agent_options:
                        directions = page.get_by_role('region', name='分析分类依据')
                        expect(directions).to_contain_text('模型候选分类（未验证）：依赖异常')
                        expect(directions).to_contain_text('观测支持的方向：权限问题（不代表根因）')
                        directions.get_by_role('button', name='核查分类证据', exact=False).click()
                        expect(page.get_by_text('组件：订单应用 · 观测：权限拒绝', exact=True)).to_be_visible()
                        if refresh:
                            expect(page.get_by_text('原假设引用的材料已不可核验', exact=False)).to_be_visible()
                        else:
                            from test_verification import report
                            assert report(api, path, {'Authorization': 'Bearer ' + credential},
                                detail['attempts'][0]['conclusion']['evidence_ids'][0],
                                source_access='reachable', material_status='invalid',
                                reason='material_integrity_failed').status_code == 201
                    assert 'synthetic-secret' not in page.locator('body').inner_text()
                    assert page.evaluate('document.documentElement.scrollWidth <= innerWidth')
                    page.reload()
            finally:
                browser.close()
                server.shutdown()
                server.server_close()
                thread.join(5)
