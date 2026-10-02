from test_investigations import summary, assert_listing
from datetime import datetime, timedelta, timezone
from uuid import uuid4

import pytest

from test_investigations import database_url
from test_attempts import configured_client
from test_evidence import started, material
from test_conclusions import proposal


@pytest.mark.parametrize('database_url', ['0010'], indirect=True)
@pytest.mark.parametrize('active', [False, True])
def test_upgrade_preserves_terminal_history_but_active_old_query_blocks_reopen(database_url, tmp_path, active):
    import os
    import subprocess
    import sys
    from sqlalchemy import create_engine, text
    from test_investigations import ROOT
    investigation, attempt, execution = uuid4(), uuid4(), uuid4()
    engine = create_engine(database_url)
    with engine.begin() as connection:
        connection.execute(text("INSERT INTO investigations (id,request_key,title,description,created_at,updated_at) VALUES (:i,:k,'旧版安全边界','',now(),now())"), {'i': investigation, 'k': uuid4()})
        connection.execute(text("INSERT INTO attempts (id,investigation_id,request_key,request_body,service_id,source_ids,starts_at,ends_at,created_at,deadline_at,status,ended_at,end_reason) VALUES (:a,:i,:k,'{}','orders','[\"controlled-orders\"]',now()-interval '1 hour',now(),now(),now()+interval '10 minutes','失败',now(),'necessary_sources_unavailable')"), {'a': attempt, 'i': investigation, 'k': uuid4()})
        connection.execute(text("INSERT INTO attempts (id,investigation_id,request_key,request_body,service_id,source_ids,starts_at,ends_at,created_at,deadline_at,status,ended_at,end_reason) VALUES (:a,:i,:k,'{}','orders','[\"controlled-orders\"]',now()-interval '1 hour',now(),now()-interval '1 day',now()-interval '1 day'+interval '10 minutes','已取消',now()-interval '1 day','user_canceled')"), {'a': uuid4(), 'i': investigation, 'k': uuid4()})
        connection.execute(text('INSERT INTO controlled_executions (id,attempt_id,valid) VALUES (:e,:a,false)'), {'e': execution, 'a': attempt})
        connection.execute(text("INSERT INTO investigation_redaction_keys (investigation_id,secret) VALUES (:i,:s)"), {'i': investigation, 's': b'x' * 32})
        connection.execute(text("INSERT INTO controlled_queries (id,attempt_id,execution_id,request_key,active,created_at,material) VALUES (:q,:a,:e,:k,:active,now(),:m)"), {'q': uuid4(), 'a': attempt, 'e': execution, 'k': uuid4(), 'active': active, 'm': '{"id":"legacy-query","status":"running","sources":[]}'})
    engine.dispose()
    migrated = subprocess.run([sys.executable, '-m', 'alembic', '-c', 'backend/alembic.ini', 'upgrade', 'head'],
        cwd=ROOT, env={**os.environ, 'DATABASE_URL': database_url}, capture_output=True)
    assert migrated.returncode == 0, migrated.stderr
    with configured_client(database_url, tmp_path) as client:
        path = '/api/investigations/' + str(investigation)
        before = client.get(path).json()
        assert [a['attempt_number'] for a in before['attempts']] == [1, 2]
        assert before['attempts'][0]['status'] == '已取消'
        assert before['status'] == '失败'
        reply = client.post(path + '/attempts', json={'service_id': 'orders'}, headers={'Idempotency-Key': str(uuid4())})
        assert reply.status_code == (409 if active else 201), reply.text
        if active:
            assert client.get(path).json() == before
        else:
            assert reply.json()['attempts'][:2] == before['attempts']
            assert reply.json()['attempts'][-1]['attempt_number'] == 3


def test_same_clock_rounds_have_persistent_order_and_old_keys_never_reopen(database_url, tmp_path):
    now = datetime(2026, 11, 4, tzinfo=timezone.utc)
    with configured_client(database_url, tmp_path, clock=lambda: now) as client:
        from test_attempts import create
        pending, _ = create(client)
        path = '/api/investigations/' + pending['id']
        requests = []
        for number in range(1, 9):
            headers = {'Idempotency-Key': str(uuid4())}
            reply = client.post(path + '/attempts', json={'service_id': 'orders'}, headers=headers)
            assert reply.status_code == 201, reply.text
            record = reply.json()
            assert [a['attempt_number'] for a in record['attempts']] == list(range(1, number + 1))
            latest = record['attempts'][-1]
            assert latest['status'] == record['status'] == '调查中'
            requests.append(headers)
            canceled = client.post(path + '/attempts/' + latest['id'] + '/cancel')
            assert canceled.status_code == 200
            record = canceled.json()
            assert record['status'] == '已取消'
            for previous in requests:
                replay = client.post(path + '/attempts', json={'service_id': 'orders'}, headers=previous)
                assert replay.status_code == 200
                assert replay.json() == record
            conflict = client.post(path + '/attempts', json={'service_id': 'missing'}, headers=headers)
            assert conflict.status_code == 409
            assert client.get(path).json() == record
    with configured_client(database_url, tmp_path, clock=lambda: now) as restarted:
        assert restarted.get(path).json() == record


def test_query_failure_reopens_with_adjusted_service_and_isolated_identity(database_url, tmp_path):
    import json
    from fastapi.testclient import TestClient
    from app.main import create_app
    from app.queries import SourceFailure
    from test_queries import Schedule, Source

    class Unavailable:
        async def query(self, **scope):
            raise SourceFailure('network')

    config = tmp_path / 'history-services.json'
    config.write_text(json.dumps({'services': [
        {'id': 'orders', 'name': '订单', 'source_ids': ['controlled-orders']},
        {'id': 'billing', 'name': '账单', 'source_ids': ['controlled-billing']}]}), encoding='utf-8')
    credential = uuid4().hex
    now = datetime(2026, 11, 4, tzinfo=timezone.utc)
    with TestClient(create_app(database_url=database_url, services_file=config, executor_credential=credential,
        clock=lambda: now, query_sources={'controlled-orders': Unavailable(), 'controlled-billing': Source()},
        query_schedule=Schedule())) as client:
        path, _, endpoint, auth = started(client, credential)
        assert client.post(endpoint, json=material(), headers=auth).status_code == 201
        query_endpoint = endpoint.removesuffix('/evidence') + '/queries'
        query_headers = {**auth, 'Idempotency-Key': str(uuid4())}
        failed = client.post(query_endpoint, headers=query_headers)
        assert failed.status_code == 201
        assert failed.json()['sources'][0]['attempts'] == 3
        before = client.get(path).json()
        assert before['status'] == '失败'
        assert len(before['attempts']) == 1
        assert before['attempts'][0]['end_reason'] == 'necessary_sources_unavailable'
        now += timedelta(hours=1)
        invalid = {'service_id': 'billing', 'starts_at': '2026-11-02T00:00:00Z', 'ends_at': '2026-11-03T00:00:00.000001Z'}
        assert client.post(path + '/attempts', json=invalid, headers={'Idempotency-Key': str(uuid4())}).status_code == 422
        assert client.get(path).json() == before
        body = {**invalid, 'starts_at': '2026-11-02T08:00:00+08:00', 'ends_at': '2026-11-03T08:00:00+08:00'}
        reopened = client.post(path + '/attempts', json=body, headers={'Idempotency-Key': str(uuid4())})
        assert reopened.status_code == 201, reopened.text
        current = reopened.json()
        assert current['attempts'][0] == before['attempts'][0]
        latest = current['attempts'][-1]
        assert latest['service_id'] == 'billing'
        assert latest['starts_at'] == '2026-11-02T00:00:00Z'
        assert latest['ends_at'] == '2026-11-03T00:00:00Z'
        assert current['evidence_count'] == 0
        assert not latest['query_coverage']['complete']
        assert client.post(query_endpoint, headers=query_headers).status_code == 403
        assert client.post(endpoint, json=material(), headers=auth).status_code == 403
        old_execution = endpoint.split('/')[-2]
        new_base = '/api/executor/investigations/' + current['id'] + '/attempts/' + latest['id'] + '/executions'
        new_execution = client.get(new_base, headers=auth).json()['executions'][0]['id']
        assert new_execution != old_execution
        assert client.post(new_base + '/' + old_execution + '/evidence', json=material(), headers=auth).status_code == 403
        assert client.post(endpoint.removesuffix('/evidence') + '/conclusions', json=proposal(), headers=query_headers).status_code == 403
        new_conclusions = new_base + '/' + new_execution + '/conclusions'
        assert client.post(new_conclusions, json=proposal(evidence_ids=[before['attempts'][0]['evidence'][0]['id']]), headers=query_headers).status_code == 422
        assert client.get(path).json() == current
        now += timedelta(minutes=1)
        assert client.post(new_conclusions, json=proposal(), headers=query_headers).status_code == 201
        ended = client.get(path).json()
        assert ended['retention_expires_at'] is None
        assert ended['updated_at'] == '2026-11-04T01:01:00Z'
        assert ended['attempts'][0] == before['attempts'][0]
        assert client.get('/api/investigations').json()['items'] == [summary(ended)]


def test_concurrent_reopen_and_failed_acceptance_leave_one_new_round(database_url, tmp_path):
    from concurrent.futures import ThreadPoolExecutor
    from sqlalchemy import event
    from sqlalchemy.engine import Engine
    with configured_client(database_url, tmp_path) as client:
        from test_attempts import create
        pending, _ = create(client)
        path = '/api/investigations/' + pending['id']
        initial = client.post(path + '/attempts', json={'service_id': 'orders'}, headers={'Idempotency-Key': str(uuid4())}).json()
        before = client.post(path + '/attempts/' + initial['attempts'][0]['id'] + '/cancel').json()
        headers = {'Idempotency-Key': str(uuid4())}
        def fail(connection, cursor, statement, parameters, context, executemany):
            if statement.startswith('INSERT INTO controlled_executions'):
                cursor.execute('SELECT 1 / 0')
        event.listen(Engine, 'before_cursor_execute', fail)
        try:
            reply = client.post(path + '/attempts', json={'service_id': 'orders'}, headers=headers)
            assert reply.status_code == 503
        finally:
            event.remove(Engine, 'before_cursor_execute', fail)
        assert client.get(path).json() == before
        with ThreadPoolExecutor(max_workers=6) as pool:
            replies = list(pool.map(lambda _: client.post(path + '/attempts', json={'service_id': 'orders'}, headers=headers), range(6)))
        assert sorted(r.status_code for r in replies) == [200] * 5 + [201]
        current = client.get(path).json()
        assert all(r.json() == current for r in replies)
        assert [a['attempt_number'] for a in current['attempts']] == [1, 2]
        assert current['attempts'][0] == before['attempts'][0]
        client.post(path + '/attempts/' + current['attempts'][-1]['id'] + '/cancel')
        with ThreadPoolExecutor(max_workers=6) as pool:
            replies = list(pool.map(lambda _: client.post(path + '/attempts', json={'service_id': 'orders'}, headers={'Idempotency-Key': str(uuid4())}), range(6)))
        assert sorted(r.status_code for r in replies) == [201] + [409] * 5
        current = client.get(path).json()
        assert [a['attempt_number'] for a in current['attempts']] == [1, 2, 3]
        assert sum(a['status'] == '调查中' for a in current['attempts']) == 1


def test_completed_attempt_reopens_without_inheriting_material(database_url, tmp_path):
    credential = uuid4().hex
    now = datetime(2026, 11, 4, tzinfo=timezone.utc)
    with configured_client(database_url, tmp_path, executor_credential=credential, clock=lambda: now) as client:
        path, _, endpoint, auth = started(client, credential)
        saved = client.post(endpoint, json=material(), headers=auth)
        assert saved.status_code == 201
        assert client.post(endpoint.removesuffix('/evidence') + '/conclusions',
            json=proposal(evidence_ids=[saved.json()['id']]),
            headers={**auth, 'Idempotency-Key': str(uuid4())}).status_code == 201
        finished = client.get(path).json()
        now += timedelta(hours=2)
        reply = client.post(path + '/attempts', json={'service_id': 'orders'},
                            headers={'Idempotency-Key': str(uuid4())})
        assert reply.status_code == 201, reply.text
        current = reply.json()
        assert current['status'] == '调查中'
        assert current['severity'] == '未评估'
        assert current['severity_source'] is None
        assert current['severity_assessed_at'] is None
        assert current['evidence_count'] == 0
        assert current['attempts'][0] == finished['attempts'][0]
        latest = current['attempts'][-1]
        assert latest['evidence'] == latest['conclusions'] == latest['queries'] == []
        assert latest['conclusion'] is None
        assert latest['query_coverage']['complete'] is False
        assert latest['starts_at'] == '2026-11-04T01:00:00Z'
        assert latest['ends_at'] == latest['created_at'] == '2026-11-04T02:00:00Z'
        assert latest['deadline_at'] == '2026-11-04T02:10:00Z'
        assert current['updated_at'] == latest['created_at']
        assert current['retention_expires_at'] is None
        assert client.get(path).json() == current
        assert client.get(path + '/attempts/' + finished['attempts'][0]['id']).json() == finished['attempts'][0]
