from datetime import datetime, timezone
from uuid import uuid4

import json
import pytest
from fastapi.testclient import TestClient

from test_investigations import database_url


def configured_client(url, tmp_path, **options):
    from app.main import create_app
    config = tmp_path / f"services-{uuid4().hex}.json"
    config.write_text('{"services":[{"id":"orders","name":"订单服务","source_ids":["controlled-orders"]}]}', encoding="utf-8")
    return TestClient(create_app(database_url=url, services_file=config, **options))


def create(client):
    key = str(uuid4())
    payload = {"title": "启动调查"}
    reply = client.post("/api/investigations", json=payload, headers={"Idempotency-Key": key})
    assert reply.status_code == 201
    return reply.json(), key


def test_default_range_is_fixed_and_current_projection_survives_restart(database_url, tmp_path):
    before = datetime.now(timezone.utc)
    with configured_client(database_url, tmp_path) as client:
        pending, create_key = create(client)
        path = f'/api/investigations/{pending["id"]}'
        reply = client.post(path + "/attempts", json={"service_id": "orders"}, headers={"Idempotency-Key": str(uuid4())})
        assert reply.status_code == 201, reply.text
        record = reply.json()
        assert record["status"] == "调查中"
        assert record["severity"] == "未评估"
        assert record["service"] == "orders"
        assert record["evidence_count"] == 0
        assert record["retention_expires_at"] is None
        assert record["updated_at"] > pending["updated_at"]
        assert len(record["attempts"]) == 1
        attempt = record["attempts"][0]
        start = datetime.fromisoformat(attempt["starts_at"].replace("Z", "+00:00"))
        end = datetime.fromisoformat(attempt["ends_at"].replace("Z", "+00:00"))
        assert (end - start).total_seconds() == 3600
        assert before <= end <= datetime.now(timezone.utc)
        assert attempt["service_id"] == "orders"
        assert attempt["execution_mode"] == "controlled"
        assert "execution_id" not in reply.text
        assert client.get(path).json() == record
        replay = client.post("/api/investigations", json={"title": "启动调查"}, headers={"Idempotency-Key": create_key})
        assert replay.status_code == 200
        assert replay.json() == record
    with configured_client(database_url, tmp_path) as restarted:
        assert restarted.get(path).json() == record
        assert restarted.get("/api/investigations").json() == {"items": [record], "total": 1}


@pytest.mark.parametrize('listing', [False, True], ids=['detail', 'listing'])
def test_read_projection_keeps_one_snapshot_during_concurrent_start(database_url, tmp_path, listing):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Event
    from sqlalchemy import event
    from sqlalchemy.engine import Engine

    with configured_client(database_url, tmp_path) as client:
        pending, _ = create(client)
        path = f'/api/investigations/{pending["id"]}'
        investigation_read = Event()
        resume_read = Event()

        def pause_after_investigation_read(connection, cursor, statement, parameters, context, executemany):
            # 测试DB设施只控制连接时序，不替换SQL结果、不在生产代码插入hook。
            if (not investigation_read.is_set() and statement.lstrip().startswith('SELECT investigations.')
                    and 'FOR UPDATE' not in statement):
                investigation_read.set()
                assert resume_read.wait(15), '并发启动未及时释放读取连接'

        event.listen(Engine, 'after_cursor_execute', pause_after_investigation_read)
        try:
            with ThreadPoolExecutor(max_workers=1) as executor:
                reading = executor.submit(client.get, '/api/investigations' if listing else path)
                try:
                    assert investigation_read.wait(10), 'HTTP读取未到达受控DB边界'
                    started_reply = client.post(path + '/attempts', json={'service_id': 'orders'},
                                                headers={'Idempotency-Key': str(uuid4())})
                    assert started_reply.status_code == 201, started_reply.text
                    started = started_reply.json()
                finally:
                    resume_read.set()
                reply = reading.result(timeout=15)
        finally:
            resume_read.set()
            event.remove(Engine, 'after_cursor_execute', pause_after_investigation_read)
        assert reply.status_code == 200
        observed = reply.json()['items'][0] if listing else reply.json()
        # 读取已在启动提交之前取得快照，不能混合旧保留期/更新时间与新轮次。
        assert observed == pending
        assert client.get(path).json() == started
        assert client.get('/api/investigations').json() == {'items': [started], 'total': 1}


@pytest.mark.parametrize('start_first', [False, True], ids=['replay-first', 'start-first'])
def test_create_replay_keeps_consistent_projection_during_start(database_url, tmp_path, start_first):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Event
    from time import monotonic
    from sqlalchemy import create_engine, event, text
    from sqlalchemy.engine import Engine
    from app.attempts import ControlledExecutionAdapter

    investigation_read = Event()
    resume_replay = Event()
    accepted = Event()
    resume_start = Event()
    start_query = Event()
    replay_query = Event()
    pids = {}

    class PausedAcceptance(ControlledExecutionAdapter):
        def accept(self, connection, attempt_id):
            super().accept(connection, attempt_id)
            if start_first:
                accepted.set()
                assert resume_start.wait(15), '启动事务未及时释放'

    def before_query(connection, cursor, statement, parameters, context, executemany):
        if statement.lstrip().startswith('SELECT investigations.'):
            if 'WHERE investigations.request_key =' in statement:
                pids['replay'] = connection.connection.driver_connection.info.backend_pid
                replay_query.set()
            elif 'FOR UPDATE' in statement:
                pids['start'] = connection.connection.driver_connection.info.backend_pid
                start_query.set()

    def after_query(connection, cursor, statement, parameters, context, executemany):
        if (not start_first and not investigation_read.is_set()
                and statement.lstrip().startswith('SELECT investigations.')
                and 'WHERE investigations.request_key =' in statement):
            investigation_read.set()
            assert resume_replay.wait(15), '创建重放未及时释放'

    observer = create_engine(database_url)
    with configured_client(database_url, tmp_path, execution_adapter=PausedAcceptance()) as client:
        pending, key = create(client)
        path = f'/api/investigations/{pending["id"]}'

        def replay():
            return client.post('/api/investigations', json={'title': '启动调查'},
                               headers={'Idempotency-Key': key})

        def start():
            return client.post(path + '/attempts', json={'service_id': 'orders'},
                               headers={'Idempotency-Key': str(uuid4())})

        event.listen(Engine, 'before_cursor_execute', before_query)
        event.listen(Engine, 'after_cursor_execute', after_query)
        try:
            with ThreadPoolExecutor(max_workers=2) as executor:
                try:
                    if start_first:
                        starting = executor.submit(start)
                        assert accepted.wait(10), '启动未取得调查锁并接纳轮次'
                        replaying = executor.submit(replay)
                        assert replay_query.wait(10), '重放未到达调查读取边界'
                        waiting, pid = replaying, pids['replay']
                    else:
                        replaying = executor.submit(replay)
                        assert investigation_read.wait(10), '重放未读到调查'
                        starting = executor.submit(start)
                        assert start_query.wait(10), '启动未到达调查锁边界'
                        waiting, pid = starting, pids['start']
                    # 只安排真实PG连接时序；业务事实仍全部通过HTTP断言。
                    deadline = monotonic() + 10
                    with observer.connect().execution_options(isolation_level='AUTOCOMMIT') as connection:
                        while not waiting.done():
                            if connection.execute(text('SELECT cardinality(pg_blocking_pids(:pid))'), {'pid': pid}).scalar_one():
                                break
                            assert monotonic() < deadline, '并发HTTP既未完成也未进入PG锁等待'
                    if not start_first and waiting.done():
                        assert waiting.result().status_code == 201
                finally:
                    resume_replay.set()
                    resume_start.set()
                replayed = replaying.result(timeout=15)
                started_reply = starting.result(timeout=15)
        finally:
            resume_replay.set()
            resume_start.set()
            event.remove(Engine, 'before_cursor_execute', before_query)
            event.remove(Engine, 'after_cursor_execute', after_query)
            observer.dispose()
        assert started_reply.status_code == 201, started_reply.text
        started = started_reply.json()
        assert replayed.status_code == 200, replayed.text
        assert replayed.json() == (started if start_first else pending)
        assert len(started['attempts']) == 1
        assert started['retention_expires_at'] is None
        assert started['updated_at'] > pending['updated_at']
        assert client.get(path).json() == started
        assert client.get('/api/investigations').json() == {'items': [started], 'total': 1}
        assert replay().json() == started


def test_ranges_reject_invalid_without_attempt_and_allow_exact_24h(database_url, tmp_path):
    with configured_client(database_url, tmp_path) as client:
        pending, _ = create(client)
        path = f'/api/investigations/{pending["id"]}'
        key = {"Idempotency-Key": str(uuid4())}
        good = {"service_id": "orders", "starts_at": "2026-10-30T08:00:00+08:00", "ends_at": "2026-10-31T08:00:00+08:00"}
        invalid = [
            {}, {"service_id": "missing"}, {"service_id": ""},
            {**good, "ends_at": "2026-10-31T08:00:00.000001+08:00"},
            {**good, "ends_at": good["starts_at"]},
            {**good, "ends_at": "2026-10-29T08:00:00+08:00"},
            {**good, "starts_at": "2026-10-30T08:00:00"},
            {**good, "starts_at": "nonsense"},
            {"service_id": "orders", "starts_at": "1793318400", "ends_at": "1793322000"},
            {"service_id": "orders", "starts_at": good["starts_at"]},
            {"service_id": "orders", "starts_at": None, "ends_at": None},
            {"service_id": "orders", "starts_at": 1793318400, "ends_at": 1793322000},
            {**good, "executionID": str(uuid4())}, {**good, "execution_id": str(uuid4())},
            {**good, "status": "调查中"},
        ]
        for payload in invalid:
            reply = client.post(path + "/attempts", json=payload, headers=key)
            assert reply.status_code == 422, (payload, reply.text)
            assert reply.json()["detail"]
            assert client.get(path).json() == pending
        assert client.post(path + "/attempts", json=good).status_code == 422
        reply = client.post(path + "/attempts", json=good, headers=key)
        assert reply.status_code == 201, reply.text
        record = reply.json()
        attempt = record["attempts"][0]
        assert attempt["starts_at"] == "2026-10-30T00:00:00Z"
        assert attempt["ends_at"] == "2026-10-31T00:00:00Z"
        assert client.post(path + "/attempts", json={"service_id": "orders"}, headers={"Idempotency-Key": str(uuid4())}).status_code == 409
        assert client.patch(path + "/attempts/" + attempt["id"], json={"service_id": "other"}).status_code == 405
        assert client.get(path).json() == record


def test_concurrent_start_has_one_persistent_controlled_acceptance(database_url, tmp_path):
    from concurrent.futures import ThreadPoolExecutor
    credential = uuid4().hex
    with configured_client(database_url, tmp_path, executor_credential=credential) as client:
        pending, _ = create(client)
        path = f'/api/investigations/{pending["id"]}'
        key = str(uuid4())
        def submit(_):
            return client.post(path + "/attempts", json={"service_id": "orders"}, headers={"Idempotency-Key": key})
        with ThreadPoolExecutor(max_workers=6) as executor:
            replies = list(executor.map(submit, range(6)))
        assert sorted(reply.status_code for reply in replies) == [200, 200, 200, 200, 200, 201]
        record = replies[0].json()
        assert all(reply.json() == record for reply in replies)
        attempt = record["attempts"][0]
        context_path = f'/api/executor/investigations/{pending["id"]}/attempts/{attempt["id"]}/executions'
        context = client.get(context_path, headers={"Authorization": f"Bearer {credential}"})
        assert context.status_code == 200, context.text
        assert len(context.json()["executions"]) == 1
        assert context.json()["executions"][0]["attempt_id"] == attempt["id"]
        with ThreadPoolExecutor(max_workers=4) as executor:
            duplicates = list(executor.map(lambda _: client.post(path + "/attempts", json={"service_id": "orders"}, headers={"Idempotency-Key": str(uuid4())}), range(4)))
        assert all(reply.status_code == 409 for reply in duplicates)
        conflict = client.post(path + "/attempts", json={"service_id": "missing"}, headers={"Idempotency-Key": key})
        assert conflict.status_code == 409
        assert client.get(path).json() == record
    with configured_client(database_url, tmp_path, executor_credential=credential) as restarted:
        assert restarted.get(context_path, headers={"Authorization": f"Bearer {credential}"}).json() == context.json()
        assert restarted.get(path).json() == record


def test_acceptance_failure_rolls_back_and_same_request_can_recover(database_url, tmp_path, caplog):
    from app.attempts import ControlledExecutionAdapter
    from sqlalchemy import text

    class FailingAcceptance(ControlledExecutionAdapter):
        fail = True

        def accept(self, connection, attempt_id):
            super().accept(connection, attempt_id)
            if self.fail:
                connection.execute(text("SELECT 1 / 0"))

    adapter = FailingAcceptance()
    with configured_client(database_url, tmp_path, execution_adapter=adapter) as client:
        pending, _ = create(client)
        path = f'/api/investigations/{pending["id"]}'
        headers = {"Idempotency-Key": str(uuid4())}
        failed = client.post(path + "/attempts", json={"service_id": "orders"}, headers=headers)
        assert failed.status_code == 503, failed.text
        assert failed.json() == {"detail": "可控执行接纳失败，尚未启动；可使用同一请求标识重试"}
        assert client.get(path).json() == pending
        adapter.fail = False
        recovered = client.post(path + "/attempts", json={"service_id": "orders"}, headers=headers)
        assert recovered.status_code == 201
        assert len(recovered.json()["attempts"]) == 1
        assert "SELECT" not in failed.text


def test_execution_context_requires_credential_identity_round_and_validity(database_url, tmp_path, caplog):
    credential = uuid4().hex
    auth = {"Authorization": f"Bearer {credential}"}
    with configured_client(database_url, tmp_path, executor_credential=credential) as client:
        pending, _ = create(client)
        path = f'/api/investigations/{pending["id"]}'
        record = client.post(path + "/attempts", json={"service_id": "orders"}, headers={"Idempotency-Key": str(uuid4())}).json()
        attempt = record["attempts"][0]
        executor_path = f'/api/executor/investigations/{pending["id"]}/attempts/{attempt["id"]}'
        accepted = client.get(executor_path + "/executions", headers=auth).json()["executions"][0]
        context_path = executor_path + f'/executions/{accepted["id"]}/context'
        for headers in ({}, {"Authorization": "Bearer wrong"}, {"Authorization": b"Bearer \xff"}):
            reply = client.get(context_path, headers=headers)
            assert reply.status_code == 401
            assert credential not in reply.text
        reply = client.get(context_path, headers=auth)
        assert reply.status_code == 200, reply.text
        context = reply.json()
        assert context["execution_id"] == accepted["id"]
        assert context["attempt_id"] == attempt["id"]
        assert context["investigation_id"] == pending["id"]
        assert context["source_ids"] == ["controlled-orders"]
        assert context["capabilities"] == ["read_execution_context"]
        assert context["environment_tools"] == []
        assert context["execution_mode"] == "controlled"
        for invalid_path in (
            context_path.replace(accepted["id"], str(uuid4())),
            context_path.replace(attempt["id"], str(uuid4())),
            context_path.replace(pending["id"], str(uuid4())),
        ):
            assert client.get(invalid_path, headers=auth).status_code == 403
        assert client.post(context_path, json={"status": "已完成"}, headers=auth).status_code == 405
        for public in (path, "/api/investigations", "/api/services", "/docs", "/openapi.json"):
            content = client.get(public).text
            assert credential not in content
            assert accepted["id"] not in content
        assert client.get(path).json() == record
        assert credential not in caplog.text
    with configured_client(database_url, tmp_path, executor_credential="") as unconfigured:
        assert unconfigured.get(context_path, headers=auth).status_code == 401


def test_invalidated_identity_is_refused_without_public_mutation_api(database_url, tmp_path):
    from app.attempts import ControlledExecutionAdapter, executions
    from sqlalchemy import update

    expired_id = uuid4()

    class ExpiredAcceptance(ControlledExecutionAdapter):
        def accept(self, connection, attempt_id):
            super().accept(connection, attempt_id)
            connection.execute(update(executions).where(executions.c.attempt_id == attempt_id).values(id=expired_id, valid=False))

    credential = uuid4().hex
    auth = {"Authorization": f"Bearer {credential}"}
    with configured_client(database_url, tmp_path, executor_credential=credential, execution_adapter=ExpiredAcceptance()) as client:
        pending, _ = create(client)
        path = f'/api/investigations/{pending["id"]}'
        record = client.post(path + "/attempts", json={"service_id": "orders"}, headers={"Idempotency-Key": str(uuid4())}).json()
        attempt = record["attempts"][0]
        executor_path = f'/api/executor/investigations/{pending["id"]}/attempts/{attempt["id"]}'
        assert client.get(executor_path + "/executions", headers=auth).status_code == 403
        assert client.get(executor_path + f'/executions/{expired_id}/context', headers=auth).status_code == 403
        assert client.get(path).json() == record


def test_adapter_exception_leaves_no_false_running_attempt(database_url, tmp_path, caplog):
    from app.attempts import ControlledExecutionAdapter
    secret = uuid4().hex

    class RejectedAcceptance(ControlledExecutionAdapter):
        def accept(self, connection, attempt_id):
            super().accept(connection, attempt_id)
            raise RuntimeError(secret)

    with configured_client(database_url, tmp_path, execution_adapter=RejectedAcceptance()) as client:
        pending, _ = create(client)
        path = f'/api/investigations/{pending["id"]}'
        reply = client.post(path + "/attempts", json={"service_id": "orders"}, headers={"Idempotency-Key": str(uuid4())})
        assert reply.status_code == 503
        assert client.get(path).json() == pending
        assert secret not in reply.text + caplog.text


def test_invalid_catalog_fails_closed_and_empty_configuration_cannot_start(database_url, tmp_path):
    from app.main import create_app
    config = tmp_path / 'invalid.json'
    for source in ('', ' ', 'bad\u0000source', 'https://user:secret@synthetic.invalid/log?token=synthetic'):
        config.write_text(json.dumps({'services': [{'id': 'orders', 'name': '订单', 'source_ids': [source]}]}), encoding='utf-8')
        with pytest.raises(ValueError, match='服务目录配置无效'):
            create_app(database_url=database_url, services_file=config)
    config.write_text('{"services":[]}', encoding='utf-8')
    with TestClient(create_app(database_url=database_url, services_file=config, executor_credential='')) as client:
        assert client.get('/api/services').json() == {'items': []}
        pending, _ = create(client)
        path = f'/api/investigations/{pending["id"]}'
        assert client.post(path + '/attempts', json={'service_id': 'orders'}, headers={'Idempotency-Key': str(uuid4())}).status_code == 422
        assert client.get(path).json() == pending
        assert client.get(f'/api/executor/investigations/{uuid4()}/attempts/{uuid4()}/executions').status_code == 401


def test_started_attempt_survives_real_process_restart(database_url, tmp_path):
    import os
    import socket
    import subprocess
    import sys
    import time
    from contextlib import contextmanager
    import httpx
    from test_investigations import ROOT

    credential = uuid4().hex
    config = tmp_path / 'restart-services.json'
    config.write_text(json.dumps({'services': [{'id': 'orders', 'name': '订单', 'source_ids': ['controlled-orders']}]}), encoding='utf-8')

    @contextmanager
    def server():
        with socket.socket() as listener:
            listener.bind(('127.0.0.1', 0))
            port = listener.getsockname()[1]
        process = subprocess.Popen(
            [sys.executable, '-m', 'uvicorn', 'app.main:app', '--app-dir', 'backend', '--host', '127.0.0.1', '--port', str(port), '--no-access-log'],
            cwd=ROOT, env={**os.environ, 'DATABASE_URL': database_url, 'SERVICES_FILE': str(config), 'EXECUTOR_CREDENTIAL': credential},
            stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, text=True,
        )
        try:
            with httpx.Client(base_url=f'http://127.0.0.1:{port}', timeout=2) as client:
                deadline = time.monotonic() + 15
                while True:
                    if process.poll() is not None:
                        pytest.fail(process.stderr.read())
                    try:
                        if client.get('/api/health').status_code == 200:
                            break
                    except httpx.TransportError:
                        pass
                    if time.monotonic() >= deadline:
                        pytest.fail('真实API进程未及时启动')
                yield client
        finally:
            process.terminate()
            process.communicate(timeout=10)

    headers = {'Idempotency-Key': str(uuid4())}
    auth = {'Authorization': f'Bearer {credential}'}
    with server() as client:
        pending, _ = create(client)
        path = f'/api/investigations/{pending["id"]}'
        reply = client.post(path + '/attempts', json={'service_id': 'orders'}, headers=headers)
        assert reply.status_code == 201
        record = reply.json()
        attempt = record['attempts'][0]
        controlled = f'/api/executor/investigations/{pending["id"]}/attempts/{attempt["id"]}/executions'
        accepted = client.get(controlled, headers=auth).json()
        from test_evidence import material
        payload = material()
        payload['identity'] = {'kind': 'record', 'record_id': uuid4().hex}
        payload['occurred_at'] = attempt['starts_at']
        payload['query']['starts_at'] = attempt['starts_at']
        payload['query']['ends_at'] = attempt['ends_at']
        endpoint = controlled + '/' + accepted['executions'][0]['id'] + '/evidence'
        saved = client.post(endpoint, json=payload, headers=auth)
        assert saved.status_code == 201
        record = client.get(path).json()
    with server() as client:
        assert client.get(path).json() == record
        replay = client.post(path + '/attempts', json={'service_id': 'orders'}, headers=headers)
        assert replay.status_code == 200
        assert replay.json() == record
        assert client.get(controlled, headers=auth).json() == accepted
        repeated = client.post(endpoint, json=payload, headers=auth)
        assert repeated.status_code == 200
        assert repeated.json() == saved.json()
        assert client.get(path).json() == record
        assert client.get(path).json()['evidence_count'] == 1
        from test_conclusions import proposal
        conclusion_endpoint = endpoint.removesuffix('/evidence') + '/conclusions'
        conclusion_headers = {**auth, 'Idempotency-Key': str(uuid4())}
        completed = client.post(conclusion_endpoint, json=proposal(), headers=conclusion_headers)
        assert completed.status_code == 201
        finished = client.get(path).json()
    with server() as client:
        assert client.get(path).json() == finished
        assert finished['status'] == '已完成'
        assert client.post(path + '/attempts', json={'service_id': 'orders'}, headers=headers).json() == finished
        assert client.post(conclusion_endpoint, json=proposal(), headers=conclusion_headers).json() == completed.json()
        assert client.post(endpoint, json=payload, headers=auth).status_code == 403
        assert client.get(controlled, headers=auth).status_code == 403


def test_configured_services_are_available_without_secrets(database_url, tmp_path):
    from app.main import create_app
    config = tmp_path / "services.json"
    config.write_text('{"services":[{"id":"orders","name":"订单服务","source_ids":["controlled-orders"]}]}', encoding="utf-8")
    with TestClient(create_app(database_url=database_url, services_file=config)) as client:
        reply = client.get("/api/services")
        assert reply.status_code == 200
        assert reply.json() == {"items": [{"id": "orders", "name": "订单服务"}]}
        assert "source" not in reply.text
        assert "credential" not in reply.text
