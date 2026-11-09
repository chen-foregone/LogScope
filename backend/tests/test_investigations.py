import json
import os
import subprocess
import sys
from datetime import datetime
from pathlib import Path
from uuid import uuid4

import httpx
import pytest
from sqlalchemy import create_engine, text

ROOT = Path(__file__).resolve().parents[2]
DATABASE_URL = os.environ.get(
    "TEST_DATABASE_URL",
    "postgresql+psycopg://logscope_test:local_test_only@127.0.0.1:55433/logscope_test",
)


@pytest.fixture
def database_url(request):
    # 每例独立 schema，仅安排环境；业务事实全部经 HTTP 断言。
    schema = f"test_{uuid4().hex}"
    engine = create_engine(DATABASE_URL)
    with engine.begin() as connection:
        connection.execute(text(f'CREATE SCHEMA "{schema}"'))
    # URL options override PGOPTIONS; retain the small test container's no-parallel limit.
    url = f"{DATABASE_URL}?options=-csearch_path%3D{schema}%20-cmax_parallel_workers_per_gather%3D0"
    result = subprocess.run(
        [sys.executable, "-m", "alembic", "-c", str(ROOT / "backend/alembic.ini"), "upgrade", getattr(request, 'param', 'head')],
        cwd=ROOT, env={**os.environ, "DATABASE_URL": url, "PYTHONPATH": str(ROOT / "backend")},
        capture_output=True, text=True,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    yield url
    # 不删除数据，保留独立测试 schema 供诊断。
    engine.dispose()


def client_for(url):
    from app.main import create_app
    from fastapi.testclient import TestClient
    return TestClient(create_app(database_url=url))


def test_replay_is_atomic_and_conflicting_payload_leaves_no_record(database_url):
    from concurrent.futures import ThreadPoolExecutor
    key = str(uuid4())
    with client_for(database_url) as client:
        def submit(_):
            return client.post("/api/investigations", json={"title": " 重放调查 "}, headers={"Idempotency-Key": key})
        with ThreadPoolExecutor(max_workers=4) as executor:
            replies = list(executor.map(submit, range(4)))
        assert sorted(reply.status_code for reply in replies) == [200, 200, 200, 201]
        record = replies[0].json()
        assert all(reply.json() == record for reply in replies)
        conflict = client.post("/api/investigations", json={"title": "不同调查"}, headers={"Idempotency-Key": key})
        assert conflict.status_code == 409
        assert_listing(client.get("/api/investigations"), [record])
    with client_for(database_url) as restarted:
        replay = restarted.post("/api/investigations", json={"title": "重放调查", "description": ""}, headers={"Idempotency-Key": key})
        assert replay.status_code == 200
        assert replay.json() == record


def test_invalid_inputs_and_missing_identity_leave_no_records(database_url):
    with client_for(database_url) as client:
        key = str(uuid4())
        headers = {"Idempotency-Key": key}
        for payload in ({}, {"title": "   "}, {"title": "a" * 201}, {"title": 42},
                        {"title": "x", "description": "a" * 4001},
                        {"title": "x", "status": "调查中"}, {"title": "x\u0000y"}):
            reply = client.post("/api/investigations", json=payload, headers=headers)
            assert reply.status_code == 422, reply.text
            assert_listing(client.get("/api/investigations"), [])
        for payload in ({"title": "\ud800"}, {"title": "x", "description": "\udfff"}):
            reply = client.post("/api/investigations", content=json.dumps(payload),
                                headers={**headers, "Content-Type": "application/json"})
            assert reply.status_code == 422
            assert_listing(client.get("/api/investigations"), [])
        assert client.post("/api/investigations", json={"title": "x"}).status_code == 422
        assert client.post("/api/investigations", json={"title": "x"}, headers={"Idempotency-Key": "bad"}).status_code == 422
        assert client.get("/api/investigations").json()["total"] == 0
        assert client.post("/api/investigations", json={"title": "有效调查"}, headers=headers).status_code == 201
        assert client.get("/api/investigations/not-a-uuid").status_code == 422
        assert client.get(f"/api/investigations/{uuid4()}").status_code == 404


def test_basic_search_reads_facts_without_touching_times(database_url):
    with client_for(database_url) as client:
        records = [client.post("/api/investigations", json={"title": title}, headers={"Idempotency-Key": str(uuid4())}).json()
                   for title in ("Alpha 超时", "Beta 错误")]
        assert_listing(client.get("/api/investigations", params={"q": " ALPHA "}), [records[0]])
        assert_listing(client.get("/api/investigations", params={"q": "%"}), [])
        assert client.get("/api/investigations", params={"q": records[1]["id"]}).json()["items"] == [summary(records[1])]
        assert client.get("/api/investigations").json()["items"] == [summary(record) for record in reversed(records)]
        for record in records:
            assert client.get(f'/api/investigations/{record["id"]}').json() == record


def test_record_survives_real_server_process_restart(database_url):
    import socket
    import time
    from contextlib import contextmanager

    @contextmanager
    def server():
        with socket.socket() as listener:
            listener.bind(("127.0.0.1", 0))
            port = listener.getsockname()[1]
        process = subprocess.Popen(
            [sys.executable, "-m", "uvicorn", "app.main:app", "--app-dir", "backend", "--host", "127.0.0.1", "--port", str(port)],
            cwd=ROOT, env={**os.environ, "DATABASE_URL": database_url},
            stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, text=True,
        )
        try:
            with httpx.Client(base_url=f"http://127.0.0.1:{port}", timeout=2) as client:
                deadline = time.monotonic() + 15
                while True:
                    if process.poll() is not None:
                        pytest.fail(process.stderr.read())
                    try:
                        if client.get("/api/health").status_code == 200:
                            break
                    except httpx.TransportError:
                        pass
                    if time.monotonic() >= deadline:
                        pytest.fail("真实服务未在期限内启动")
                yield client
        finally:
            process.terminate()
            process.communicate(timeout=10)

    with server() as client:
        response = client.post("/api/investigations", json={"title": "进程重启持久性"}, headers={"Idempotency-Key": str(uuid4())})
        assert response.status_code == 201
        record = response.json()
    with server() as restarted:
        assert restarted.get(f'/api/investigations/{record["id"]}').json() == record
        assert_listing(restarted.get("/api/investigations"), [record])


def test_create_pending_survives_new_application(database_url):
    with client_for(database_url) as client:
        response = client.post("/api/investigations", json={"title": "订单响应超时"}, headers={"Idempotency-Key": str(uuid4())})
        assert response.status_code == 201, response.text
        record = response.json()
        assert record["title"] == "订单响应超时"
        assert record["status"] == "待处理"
        assert record["severity"] == "未评估"
        assert record["attempts"] == []
        assert record["evidence_count"] == 0
        assert record["service"] is None
        assert record["created_at"] == record["updated_at"]
        created = datetime.fromisoformat(record["created_at"].replace("Z", "+00:00"))
        assert record['retention_expires_at'] is None
        assert created.utcoffset().total_seconds() == 0
    with client_for(database_url) as restarted:
        assert restarted.get(f'/api/investigations/{record["id"]}').json() == record
        listing = restarted.get("/api/investigations").json()
        assert listing["items"] == [summary(record)]
        assert listing["total"] == 1
        assert restarted.get(f'/api/investigations/{record["id"]}').json() == record


def summary(record):
    result = {key: record[key] for key in ('id', 'title', 'service', 'status', 'severity',
              'evidence_count', 'created_at', 'updated_at')}
    latest = record['attempts'][-1] if record['attempts'] else None
    result['possible_duplicate_evidence'] = bool(latest and any(
        notice.startswith('可能重复') for notice in latest['evidence_notices']))
    return result


def assert_listing(response, records):
    assert response.status_code == 200
    body = response.json()
    assert body['items'] == [summary(record) for record in records]
    assert body['total'] == len(records)
