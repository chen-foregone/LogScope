import socket
import threading
from urllib.parse import urlsplit
from uuid import uuid4

import pytest
import uvicorn

from test_investigations import database_url
from test_attempts import configured_client
from test_evidence import started


@pytest.fixture
def serve_app():
    servers = []

    def start(application):
        sock = socket.socket()
        sock.bind(('127.0.0.1', 0))
        port = sock.getsockname()[1]
        server = uvicorn.Server(uvicorn.Config(application, log_level='critical', access_log=False, timeout_graceful_shutdown=.2))
        thread = threading.Thread(target=server.run, kwargs={'sockets': [sock]}, daemon=True)
        thread.start()
        import time
        limit = time.monotonic() + 5
        while not server.started:
            assert thread.is_alive() and time.monotonic() < limit
            time.sleep(.01)
        servers.append((server, thread, sock))
        return f'http://127.0.0.1:{port}'

    yield start
    for server, thread, sock in servers:
        server.should_exit = True
        thread.join(5)
        assert not thread.is_alive()
        sock.close()


@pytest.fixture
def external_source(serve_app):
    from app.mock_logs import create_mock_logs

    def start(scenario='normal'):
        application = create_mock_logs(scenario)
        url = serve_app(application)
        start.observed[urlsplit(url).port] = application.state
        return url

    start.observed = {}
    yield start


def test_external_http_collects_redacted_evidence_and_replays(database_url, tmp_path, external_source):
    from app.log_sources import SimulatedLogSource
    credential = uuid4().hex
    source = SimulatedLogSource(external_source())
    with configured_client(database_url, tmp_path, executor_credential=credential,
                           query_sources={'controlled-orders': source}) as client:
        path, _, evidence, auth = started(client, credential)
        endpoint = evidence.removesuffix('/evidence') + '/queries'
        key = {**auth, 'Idempotency-Key': str(uuid4())}
        reply = client.post(endpoint, headers=key)
        assert reply.status_code == 201
        assert reply.json()['sources'][0]['state'] == 'succeeded'
        assert reply.json()['sources'][0]['mode'] == 'simulated'
        detail = client.get(path).json()
        assert detail['evidence_count'] == 2
        assert 'synthetic-secret' not in str(detail)
        assert all(item['identity_status'] == 'reliable' for item in detail['attempts'][0]['evidence'])
        assert all(item['source_mode'] == 'simulated' for item in detail['attempts'][0]['evidence'])
        assert client.post(endpoint, headers=key).status_code == 200
        assert client.post(endpoint, headers={**auth, 'Idempotency-Key': str(uuid4())}).status_code == 201
        assert client.get(path).json()['evidence_count'] == 2
        final = client.get(path).json()
    with configured_client(database_url, tmp_path, executor_credential=credential) as restarted:
        assert restarted.get(path).json() == final


@pytest.mark.parametrize('scenario,count,reason,attempts', [
    ('empty', 0, None, 1), ('unidentified', 3, None, 1),
    ('conflict', 2, 'adapter_error', 1), ('invalid', 2, 'adapter_error', 1),
    ('permission', 0, 'permission', 1), ('credentials', 0, 'credentials', 1),
    ('parameters', 0, 'parameters', 1), ('limited', 0, 'rate_limited', 1),
    ('temporary', 2, None, 3),
])
def test_external_protocol_outcomes(database_url, tmp_path, external_source, scenario, count, reason, attempts):
    from app.log_sources import SimulatedLogSource
    from test_queries import Schedule
    import httpx
    url = external_source(scenario)
    for method in ('post', 'put', 'delete'):
        assert httpx.request(method, url + '/v1/logs', trust_env=False).status_code == 405
    credential, schedule = uuid4().hex, Schedule()
    with configured_client(database_url, tmp_path, executor_credential=credential,
                           query_sources={'controlled-orders': SimulatedLogSource(url)}, query_schedule=schedule) as client:
        path, _, evidence, auth = started(client, credential)
        endpoint = evidence.removesuffix('/evidence') + '/queries'
        reply = client.post(endpoint, headers={**auth, 'Idempotency-Key': str(uuid4())})
        assert reply.status_code == 201
        state = reply.json()['sources'][0]
        assert state['reason'] == reason
        assert state['attempts'] == attempts
        assert schedule.waits == ([2, 5] if scenario == 'temporary' else [])
        after = client.get(path).json()
        assert after['evidence_count'] == count
        assert 'synthetic-secret' not in str(after)
        assert after['status'] == ('失败' if reason else '调查中')
        if scenario == 'unidentified':
            assert any('可能重复' in notice for notice in after['attempts'][0]['evidence_notices'])
        if scenario == 'invalid':
            assert any('不可用' in notice for notice in after['attempts'][0]['evidence_notices'])


def test_partial_http_sources_keep_evidence_and_missing_notice(database_url, tmp_path, external_source):
    import json
    from fastapi.testclient import TestClient
    from app.main import create_app
    from app.log_sources import SimulatedLogSource
    config = tmp_path / 'mixed.json'
    config.write_text(json.dumps({'services': [{'id': 'orders', 'name': 'Synthetic',
                                              'source_ids': ['controlled-orders', 'denied']}]}))
    credential = uuid4().hex
    with TestClient(create_app(database_url=database_url, services_file=config, executor_credential=credential,
            query_sources={'controlled-orders': SimulatedLogSource(external_source()),
                           'denied': SimulatedLogSource(external_source('permission'))})) as client:
        path, _, evidence, auth = started(client, credential)
        reply = client.post(evidence.removesuffix('/evidence') + '/queries',
                            headers={**auth, 'Idempotency-Key': str(uuid4())})
        assert reply.status_code == 201
        after = client.get(path).json()
        assert after['status'] == '调查中'
        assert after['evidence_count'] == 2
        assert after['attempts'][0]['query_coverage']['complete'] is False
        assert len(after['attempts'][0]['query_coverage']['missing_sources']) == 1


def test_collection_sql_fault_rolls_back_current_record_not_previous(database_url, tmp_path, external_source):
    from app.log_sources import SimulatedLogSource
    from sqlalchemy import event
    from sqlalchemy.engine import Engine
    credential = uuid4().hex
    with configured_client(database_url, tmp_path, executor_credential=credential,
                           query_sources={'controlled-orders': SimulatedLogSource(external_source())}) as client:
        path, _, evidence, auth = started(client, credential)
        writes = []

        def break_second(connection, cursor, statement, parameters, context, executemany):
            if statement.startswith('INSERT INTO attempt_evidence'):
                writes.append(True)
                if len(writes) == 2:
                    cursor.execute('SELECT 1 / 0')

        event.listen(Engine, 'before_cursor_execute', break_second)
        try:
            reply = client.post(evidence.removesuffix('/evidence') + '/queries',
                                headers={**auth, 'Idempotency-Key': str(uuid4())})
        finally:
            event.remove(Engine, 'before_cursor_execute', break_second)
        assert reply.status_code == 503
        after = client.get(path).json()
        assert after['evidence_count'] == 1
        assert after['status'] == '调查中'
        assert after['attempts'][0]['queries'][0]['status'] == 'failed'
        retry = client.post(evidence.removesuffix('/evidence') + '/queries',
                            headers={**auth, 'Idempotency-Key': str(uuid4())})
        assert retry.status_code == 201
        assert client.get(path).json()['evidence_count'] == 2


def test_http_query_cancellation_fences_material_and_confirms_exit(database_url, tmp_path, external_source):
    import time
    from concurrent.futures import ThreadPoolExecutor
    from app.log_sources import SimulatedLogSource
    credential = uuid4().hex
    url = external_source('timeout')
    with configured_client(database_url, tmp_path, executor_credential=credential,
                           query_sources={'controlled-orders': SimulatedLogSource(url)}) as client:
        path, record, evidence, auth = started(client, credential)
        with ThreadPoolExecutor() as pool:
            running = pool.submit(client.post, evidence.removesuffix('/evidence') + '/queries',
                                  headers={**auth, 'Idempotency-Key': str(uuid4())})
            until = time.monotonic() + 5
            while not client.get(path).json()['attempts'][0]['queries']:
                assert time.monotonic() < until
                time.sleep(.02)
            time.sleep(.2)
            assert client.post(path + '/attempts/' + record['attempts'][0]['id'] + '/cancel').status_code == 200
            assert running.result(timeout=5).status_code == 403
        after = client.get(path).json()
        assert after['status'] == '已取消'
        assert after['evidence_count'] == 0
        assert after['attempts'][0]['queries'][0]['status'] == 'canceled'
        assert client.post(evidence, json={}, headers=auth).status_code == 403
        from urllib.parse import urlsplit
        observed = external_source.observed[urlsplit(url).port]
        until = time.monotonic() + 3
        while observed.disconnected_requests == 0:
            assert time.monotonic() < until
            time.sleep(.02)


def test_actual_http_timeout_has_thirty_second_budget(database_url, tmp_path, external_source):
    import time
    from app.log_sources import SimulatedLogSource
    from app.queries import QuerySchedule

    class StopAfterFirstTimeout(QuerySchedule):
        async def wait(self, seconds):
            # Keep one real 30-second HTTP boundary; later retries need not wait again.
            raise RuntimeError('controlled schedule failure')

    credential = uuid4().hex
    with configured_client(database_url, tmp_path, executor_credential=credential,
                           query_sources={'controlled-orders': SimulatedLogSource(external_source('timeout'))},
                           query_schedule=StopAfterFirstTimeout()) as client:
        path, _, evidence, auth = started(client, credential)
        start = time.monotonic()
        reply = client.post(evidence.removesuffix('/evidence') + '/queries',
                            headers={**auth, 'Idempotency-Key': str(uuid4())})
        assert reply.status_code == 503
        assert 29 <= time.monotonic() - start < 35
        state = client.get(path).json()['attempts'][0]['queries'][0]['sources'][0]
        assert state['reason'] == 'timeout'
        assert state['attempts'] == 1


def test_simulated_identity_cannot_silently_reuse_unmarked_material(database_url, tmp_path, external_source):
    import httpx
    from app.log_sources import SimulatedLogSource
    url = external_source()
    credential = uuid4().hex
    with configured_client(database_url, tmp_path, executor_credential=credential,
                           query_sources={'controlled-orders': SimulatedLogSource(url)}) as client:
        path, _, evidence, auth = started(client, credential)
        item = httpx.get(url + '/v1/logs', params={'source_id': 'controlled-orders',
            'starts_at': '2026-11-01T00:00:00Z', 'ends_at': '2026-11-01T01:00:00Z'}, trust_env=False).json()['items'][0]
        assert client.post(evidence, json=item, headers=auth).status_code == 201
        reply = client.post(evidence.removesuffix('/evidence') + '/queries',
                            headers={**auth, 'Idempotency-Key': str(uuid4())})
        assert reply.status_code == 201
        assert reply.json()['sources'][0]['reason'] == 'adapter_error'
        assert client.get(path).json()['evidence_count'] == 2


def test_historical_simulated_evidence_keeps_provenance_without_source_config(database_url, tmp_path, external_source):
    from app.log_sources import SimulatedLogSource
    from test_conclusions import proposal
    credential = uuid4().hex
    with configured_client(database_url, tmp_path, executor_credential=credential,
                           query_sources={'controlled-orders': SimulatedLogSource(external_source())}) as client:
        path, _, evidence, auth = started(client, credential)
        assert client.post(evidence.removesuffix('/evidence') + '/queries',
                           headers={**auth, 'Idempotency-Key': str(uuid4())}).status_code == 201
        saved = client.get(path).json()['attempts'][0]['evidence'][0]
        assert client.post(evidence.removesuffix('/evidence') + '/conclusions', json=proposal(),
                           headers={**auth, 'Idempotency-Key': str(uuid4())}).status_code == 201
    with configured_client(database_url, tmp_path, executor_credential=credential) as client:
        reply = client.post(path + '/attempts', json={'service_id': 'orders',
            'starts_at': '2026-11-01T00:00:00Z', 'ends_at': '2026-11-01T01:00:00Z'},
            headers={'Idempotency-Key': str(uuid4())})
        assert reply.status_code == 201
        target = reply.json()['attempts'][-1]['id']
        assert client.post(path + '/attempts/' + target + '/evidence-references',
                           json={'evidence_id': saved['id']}).status_code == 201
        assert client.get(path).json()['attempts'][-1]['evidence'][0]['source_mode'] == 'simulated'


def test_source_endpoint_must_be_explicit_loopback_http():
    from app.log_sources import SimulatedLogSource
    for url in ('http://localhost:8091', 'https://127.0.0.1:8091', 'http://user:pass@127.0.0.1:8091',
                'http://127.0.0.1:8091/v1', 'http://127.0.0.1:8091?x=1', 'http://127.0.0.1', 'http://[::1]:8091'):
        with pytest.raises(ValueError):
            SimulatedLogSource(url)


def test_source_rejects_malformed_oversized_and_excess_responses(serve_app):
    import asyncio
    from datetime import datetime, timezone
    from fastapi import FastAPI, Response
    from app.log_sources import SimulatedLogSource
    from app.queries import SourceFailure
    starts = datetime(2026, 11, 1, tzinfo=timezone.utc)
    ends = datetime(2026, 11, 1, 1, tzinfo=timezone.utc)

    def app_for(payload, *, body=None, status=200):
        application = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)

        @application.get('/v1/logs')
        async def logs():
            return Response(body, status_code=status, media_type='text/plain') if body is not None else payload
        return application

    incidents = [
        ('malformed', app_for(None, body='{')),
        ('oversized', app_for(None, body='x' * 1048577)),
        ('excess', app_for({'protocol': 'simulated-logs-v1', 'items': [{}] * 101})),
        ('protocol', app_for({'protocol': 'other-v1', 'items': []})),
        ('extra', app_for({'protocol': 'simulated-logs-v1', 'items': [], 'more': 1})),
    ]
    for label, application in incidents:
        source = SimulatedLogSource(serve_app(application))

        async def call():
            return await source.query(source_id='synthetic', starts_at=starts, ends_at=ends)

        with pytest.raises(SourceFailure) as error:
            asyncio.run(call())
        assert error.value.reason == 'adapter_error', label


def test_source_does_not_follow_redirects_or_read_error_bodies(serve_app):
    import asyncio
    from datetime import datetime, timezone
    from fastapi import FastAPI, Response
    from app.log_sources import SimulatedLogSource
    from app.queries import SourceFailure
    starts, ends = datetime(2026, 11, 1, tzinfo=timezone.utc), datetime(2026, 11, 1, 1, tzinfo=timezone.utc)
    hits = []
    target = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)

    @target.get('/v1/logs')
    async def target_logs():
        hits.append(True)
        return {'protocol': 'simulated-logs-v1', 'items': []}

    target_url = serve_app(target)
    redirect = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)

    @redirect.get('/v1/logs')
    async def redirect_logs():
        return Response(status_code=307, headers={'Location': target_url + '/v1/logs'})

    failing = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)

    @failing.get('/v1/logs')
    async def failing_logs():
        return Response('synthetic-secret', status_code=500, media_type='text/plain')

    for url in (serve_app(redirect), serve_app(failing)):
        async def call():
            return await SimulatedLogSource(url).query(source_id='synthetic', starts_at=starts, ends_at=ends)

        with pytest.raises(SourceFailure) as error:
            asyncio.run(call())
        assert error.value.reason == 'adapter_error'
        assert 'synthetic-secret' not in str(error.value)
        assert 'synthetic-secret' not in repr(error.value)
    assert hits == []
