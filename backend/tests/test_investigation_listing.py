from datetime import datetime, timezone
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from test_investigations import database_url


@pytest.mark.parametrize('params', [
    {'page': '0'},
    {'page': '1.5'},
    {'page_size': '0'},
    {'page_size': '101'},
    {'q': 'x' * 201},
    {'q': 'x\x00y'},
    {'service_id': 'x' * 81},
    {'service_id': 'orders/*'},
    {'relative_range': 'tomorrow'},
    {'updated_from': '2026-11-04T00:00:00Z'},
    {'updated_before': '2026-11-04T01:00:00Z'},
    {'updated_from': '2026-11-04T00:00:00', 'updated_before': '2026-11-04T01:00:00Z'},
    {'updated_from': '2026-11-04T01:00:00Z', 'updated_before': '2026-11-04T01:00:00Z'},
    {'updated_from': '2026-11-04T02:00:00Z', 'updated_before': '2026-11-04T01:00:00Z'},
    {'relative_range': '1h', 'updated_from': '2026-11-04T00:00:00Z',
     'updated_before': '2026-11-04T01:00:00Z'},
    {'updated_form': '2026-11-04T00:00:00Z'},
])
def test_invalid_listing_filters_are_rejected_without_changing_investigation(database_url, params):
    from app.main import create_app

    with TestClient(create_app(database_url=database_url)) as client:
        created = client.post('/api/investigations', json={'title': 'Filter validation'},
                              headers={'Idempotency-Key': str(uuid4())})
        assert created.status_code == 201
        record = created.json()
        response = client.get('/api/investigations', params=params)
        assert response.status_code == 422, response.text
        assert client.get(f'/api/investigations/{record["id"]}').json() == record


def test_creation_uses_business_clock_and_replay_or_reads_do_not_extend_times(database_url):
    from app.main import create_app

    now = datetime(2026, 11, 4, 0, 0, tzinfo=timezone.utc)
    with TestClient(create_app(database_url=database_url, clock=lambda: now)) as client:
        headers = {'Idempotency-Key': str(uuid4())}
        response = client.post('/api/investigations', json={'title': 'Clock boundary'}, headers=headers)
        assert response.status_code == 201
        record = response.json()
        assert record['created_at'] == '2026-11-04T00:00:00Z'
        assert record['updated_at'] == '2026-11-04T00:00:00Z'
        assert record['retention_expires_at'] is None

        now = datetime(2026, 11, 5, 0, 0, tzinfo=timezone.utc)
        replay = client.post('/api/investigations', json={'title': 'Clock boundary'}, headers=headers)
        assert replay.status_code == 200
        assert replay.json() == record
        assert client.get('/api/investigations', params={'q': 'Clock'}).json()['items'][0]['updated_at'] == record['updated_at']
        assert client.get(f'/api/investigations/{record["id"]}').json() == record


def test_relative_filter_snapshot_pagination_and_full_pending_overview(database_url):
    from app.main import create_app

    now = datetime(2026, 11, 4, 0, 0, tzinfo=timezone.utc)
    with TestClient(create_app(database_url=database_url, clock=lambda: now)) as client:
        for title in ['First', 'Second', 'Third']:
            assert client.post('/api/investigations', json={'title': title},
                               headers={'Idempotency-Key': str(uuid4())}).status_code == 201
        now = datetime(2026, 11, 4, 1, 0, tzinfo=timezone.utc)
        result = client.get('/api/investigations', params={'relative_range': '1h', 'page_size': 2}).json()
        assert result['total'] == 3
        assert len(result['items']) == 2
        assert 'attempts' not in result['items'][0]
        assert result['overview'] == {'investigation_count': 3, 'active_count': 0,
                                      'pending_count': 3, 'evidence_count': 0}
        assert result['filter']['updated_from'] == '2026-11-04T00:00:00Z'
        assert result['filter']['updated_before'] == '2026-11-04T01:00:00Z'
        now = datetime(2026, 11, 5, 1, 0, tzinfo=timezone.utc)
        frozen = {key: result['filter'][key] for key in ('updated_from', 'updated_before')}
        second = client.get('/api/investigations', params={**frozen, 'page_size': 2, 'page': 2}).json()
        assert len(second['items']) == 1
        assert second['overview'] == result['overview']
        assert not ({item['id'] for item in second['items']} & {item['id'] for item in result['items']})
        overview = client.get('/api/investigations/overview', params=frozen)
        assert overview.status_code == 200
        assert overview.json()['overview'] == result['overview']
        assert client.get('/api/investigations', params={'relative_range': '1h'}).json()['total'] == 0
        # The right endpoint is excluded, independent of timezone representation.
        assert client.get('/api/investigations', params={
            'updated_from': '2026-11-03T23:00:00Z', 'updated_before': '2026-11-04T08:00:00+08:00',
        }).json()['total'] == 0


def test_current_service_and_latest_evidence_overview_across_mixed_pages(database_url, tmp_path):
    import json
    from app.main import create_app
    from test_evidence import material
    from test_conclusions import proposal
    from app.queries import SourceFailure

    class Unavailable:
        async def query(self, **scope):
            raise SourceFailure('permission')

    config = tmp_path / 'services.json'
    config.write_text(json.dumps({'services': [
        {'id': name, 'name': name, 'source_ids': ['controlled-orders']}
        for name in ['orders', 'payments']]}), encoding='utf-8')
    credential = uuid4().hex
    auth = {'Authorization': f'Bearer {credential}'}
    with TestClient(create_app(database_url=database_url, services_file=config,
                              executor_credential=credential,
                              query_sources={'controlled-orders': Unavailable()})) as client:
        def create(title):
            response = client.post('/api/investigations', json={'title': title},
                                   headers={'Idempotency-Key': str(uuid4())})
            assert response.status_code == 201
            return '/api/investigations/' + response.json()['id']

        def start(path, service='orders'):
            response = client.post(path + '/attempts', json={'service_id': service,
                'starts_at': '2026-11-01T00:00:00Z', 'ends_at': '2026-11-01T01:00:00Z'},
                headers={'Idempotency-Key': str(uuid4())})
            assert response.status_code == 201, response.text
            attempt = response.json()['attempts'][-1]
            base = path.replace('/api/', '/api/executor/') + '/attempts/' + attempt['id'] + '/executions'
            execution = client.get(base, headers=auth).json()['executions'][0]['id']
            return attempt, base + '/' + execution

        pending = create('Pending')
        multi = create('Multiple rounds')
        old, endpoint = start(multi)
        payload = material()
        payload['identity'] = {'kind': 'record', 'record_id': 'same-record'}
        for _ in range(2):
            assert client.post(endpoint + '/evidence', json=payload, headers=auth).status_code in (200, 201)
        assert client.post(multi + '/attempts/' + old['id'] + '/cancel').status_code == 200
        current, _ = start(multi, 'payments')
        active = create('Active orders')
        active_attempt, endpoint = start(active)
        # Same reliable identity in another investigation counts independently.
        assert client.post(endpoint + '/evidence', json=payload, headers=auth).status_code == 201
        completed = create('Completed')
        _, endpoint = start(completed)
        assert client.post(endpoint + '/evidence', json=payload, headers=auth).status_code == 201
        assert client.post(endpoint + '/conclusions', json=proposal(),
                           headers={**auth, 'Idempotency-Key': str(uuid4())}).status_code == 201
        canceled = create('Canceled')
        attempt, _ = start(canceled)
        assert client.post(canceled + '/attempts/' + attempt['id'] + '/cancel').status_code == 200
        failed = create('Failed')
        _, endpoint = start(failed)
        assert client.post(endpoint + '/queries', headers={**auth, 'Idempotency-Key': str(uuid4())}).status_code == 201
        assert client.get(failed).json()['status'] == '失败'

        first = client.get('/api/investigations', params={'page_size': 2}).json()
        assert first['overview'] == {'investigation_count': 6, 'active_count': 2,
                                     'pending_count': 1, 'evidence_count': 2}
        rows = []
        for page in range(1, 4):
            result = client.get('/api/investigations', params={'page_size': 2, 'page': page}).json()
            assert result['overview'] == first['overview']
            rows.extend(result['items'])
        assert len({row['id'] for row in rows}) == 6
        assert next(row for row in rows if row['id'] == multi.rsplit('/', 1)[1])['evidence_count'] == 0
        assert client.get('/api/investigations', params={'q': ' PAYMENTS '}).json()['total'] == 1
        orders = client.get('/api/investigations', params={'service_id': 'orders', 'q': 'rounds'}).json()
        assert orders['total'] == 0
        assert client.get('/api/investigations', params={'service_id': 'payments'}).json()['items'][0]['id'] == multi.rsplit('/', 1)[1]
        assert client.get('/api/investigations', params={'q': '%'}).json()['total'] == 0
        before = client.get(pending).json()
        assert client.get('/api/investigations/overview', params={'service_id': 'removed-service'}).json()['overview']['investigation_count'] == 0
        assert client.get(pending).json() == before


@pytest.mark.parametrize('phase', ['stopping', 'confirming_execution'])
def test_unconfirmed_execution_phases_remain_active_in_overview(database_url, tmp_path, phase):
    from app.attempts import ControlledExecutionAdapter
    from test_attempts import configured_client
    from test_evidence import started

    class UnknownExecutor(ControlledExecutionAdapter):
        pass

    credential = uuid4().hex
    with configured_client(database_url, tmp_path, executor_credential=credential,
                           execution_adapter=UnknownExecutor()) as client:
        path, before, endpoint, auth = started(client, credential)
        if phase == 'stopping':
            response = client.post(path + '/attempts/' + before['attempts'][0]['id'] + '/cancel')
        else:
            response = client.post(endpoint.removesuffix('/evidence') + '/lost', headers=auth)
        assert response.status_code == 200
        assert response.json()['attempts'][-1]['execution_phase'] == phase
        assert client.get('/api/investigations').json()['overview'] == {
            'investigation_count': 1, 'active_count': 1, 'pending_count': 0, 'evidence_count': 0}


def test_business_mutations_advance_update_time_but_replays_and_reads_do_not(database_url, tmp_path):
    from datetime import timedelta
    from test_attempts import configured_client
    from test_evidence import material
    from test_conclusions import proposal

    now = datetime(2026, 11, 4, tzinfo=timezone.utc)
    credential = uuid4().hex
    auth = {'Authorization': f'Bearer {credential}'}
    with configured_client(database_url, tmp_path, executor_credential=credential, clock=lambda: now) as client:
        response = client.post('/api/investigations', json={'title': 'Timestamp matrix'},
                               headers={'Idempotency-Key': str(uuid4())})
        record = response.json()
        path = '/api/investigations/' + record['id']

        def changed(operation):
            nonlocal now, record
            now += timedelta(seconds=1)
            reply = operation()
            assert reply.status_code in (200, 201), reply.text
            after = client.get(path).json()
            assert after['updated_at'] == now.isoformat().replace('+00:00', 'Z')
            assert after['updated_at'] > record['updated_at']
            record = after
            return reply

        def unchanged(operation):
            nonlocal now
            now += timedelta(seconds=1)
            reply = operation()
            assert reply.status_code in (200, 201), reply.text
            assert client.get(path).json() == record

        key = {'Idempotency-Key': str(uuid4())}
        scope = {'service_id': 'orders', 'starts_at': '2026-11-01T00:00:00Z', 'ends_at': '2026-11-01T01:00:00Z'}
        changed(lambda: client.post(path + '/attempts', json=scope, headers=key))
        unchanged(lambda: client.post(path + '/attempts', json=scope, headers=key))
        attempt = record['attempts'][-1]
        base = path.replace('/api/', '/api/executor/') + '/attempts/' + attempt['id'] + '/executions'
        execution = client.get(base, headers=auth).json()['executions'][0]['id']
        endpoint = base + '/' + execution
        payload = material()
        payload['identity'] = {'kind': 'record', 'record_id': 'timestamp-record'}
        saved = changed(lambda: client.post(endpoint + '/evidence', json=payload, headers=auth)).json()
        unchanged(lambda: client.post(endpoint + '/evidence', json=payload, headers=auth))
        assessment = {'expected_version': 0, 'basis': {'format': 'controlled-impact-v1',
            'business': 'core_unavailable', 'users': 'broad', 'duration': 'ongoing', 'alternative': 'none'}}
        severity_key = {'Idempotency-Key': str(uuid4())}
        changed(lambda: client.post(path + '/severity', json=assessment, headers=severity_key))
        unchanged(lambda: client.post(path + '/severity', json=assessment, headers=severity_key))
        conclusion_key = {**auth, 'Idempotency-Key': str(uuid4())}
        conclusion = proposal(finish=False)
        changed(lambda: client.post(endpoint + '/conclusions', json=conclusion, headers=conclusion_key))
        unchanged(lambda: client.post(endpoint + '/conclusions', json=conclusion, headers=conclusion_key))
        cancel = path + '/attempts/' + attempt['id'] + '/cancel'
        changed(lambda: client.post(cancel))
        unchanged(lambda: client.post(cancel))
        changed(lambda: client.post(path + '/attempts', json=scope, headers={'Idempotency-Key': str(uuid4())}))
        target = record['attempts'][-1]['id']
        reference = path + '/attempts/' + target + '/evidence-references'
        changed(lambda: client.post(reference, json={'evidence_id': saved['id']}))
        unchanged(lambda: client.post(reference, json={'evidence_id': saved['id']}))
        unchanged(lambda: client.get('/api/investigations', params={'service_id': 'orders', 'q': 'Timestamp'}))
        unchanged(lambda: client.get('/api/investigations/overview'))


@pytest.mark.parametrize('phase', ['timeout', 'lost'])
def test_timeout_and_lost_phase_changes_update_time_and_reads_preserve_it(database_url, tmp_path, phase):
    from datetime import timedelta
    from app.attempts import ControlledExecutionAdapter
    from test_attempts import configured_client
    from test_evidence import started

    class UnknownExecutor(ControlledExecutionAdapter):
        pass

    now = datetime(2026, 11, 4, tzinfo=timezone.utc)
    credential = uuid4().hex
    with configured_client(database_url, tmp_path, executor_credential=credential,
                           execution_adapter=UnknownExecutor(), clock=lambda: now) as client:
        path, before, endpoint, auth = started(client, credential)
        now += timedelta(seconds=1)
        if phase == 'lost':
            response = client.post(endpoint.removesuffix('/evidence') + '/lost', headers=auth)
        else:
            now += timedelta(minutes=10)
            attempt = before['attempts'][-1]
            response = client.post(path + '/attempts/' + attempt['id'] + '/cancel')
        assert response.status_code == 200
        after = response.json()
        assert after['updated_at'] == now.isoformat().replace('+00:00', 'Z')
        assert after['updated_at'] > before['updated_at']
        assert after['status'] == '调查中'
        assert after['attempts'][-1]['execution_phase'] == ('timeout_stopping' if phase == 'timeout' else 'confirming_execution')
        now += timedelta(seconds=1)
        assert client.get('/api/investigations/overview').json()['overview']['active_count'] == 1
        assert client.get(path).json() == after


@pytest.mark.parametrize('terminal', ['complete', 'source_failure'])
def test_terminal_writes_update_time_and_do_not_change_on_overview_reads(database_url, tmp_path, terminal):
    from datetime import timedelta
    from test_attempts import configured_client
    from test_evidence import started
    from test_conclusions import proposal
    from app.queries import SourceFailure

    class Missing:
        async def query(self, **scope):
            raise SourceFailure('permission')

    now = datetime(2026, 11, 4, tzinfo=timezone.utc)
    credential = uuid4().hex
    with configured_client(database_url, tmp_path, executor_credential=credential, clock=lambda: now,
                           query_sources={'controlled-orders': Missing()}) as client:
        path, before, evidence, auth = started(client, credential)
        now += timedelta(seconds=1)
        endpoint = evidence.removesuffix('/evidence')
        if terminal == 'complete':
            reply = client.post(endpoint + '/conclusions', json=proposal(),
                                headers={**auth, 'Idempotency-Key': str(uuid4())})
        else:
            reply = client.post(endpoint + '/queries', headers={**auth, 'Idempotency-Key': str(uuid4())})
        assert reply.status_code == 201
        after = client.get(path).json()
        assert after['status'] == ('已完成' if terminal == 'complete' else '失败')
        assert after['updated_at'] == now.isoformat().replace('+00:00', 'Z')
        assert after['updated_at'] > before['updated_at']
        assert after['attempts'][-1]['ended_at'] == after['updated_at']
        now += timedelta(seconds=1)
        assert client.get('/api/investigations/overview').json()['overview']['active_count'] == 0
        assert client.get(path).json() == after


def test_confirming_severity_suggestion_updates_time_only_on_applied_change(database_url, tmp_path):
    from datetime import timedelta
    from test_attempts import configured_client
    from test_evidence import started

    now = datetime(2026, 11, 4, tzinfo=timezone.utc)
    credential = uuid4().hex
    with configured_client(database_url, tmp_path, executor_credential=credential, clock=lambda: now) as client:
        path, _, endpoint, auth = started(client, credential)
        high = {'format': 'controlled-impact-v1', 'business': 'core_unavailable', 'users': 'broad',
                'duration': 'ongoing', 'alternative': 'none'}
        assert client.post(path + '/severity', json={'expected_version': 0, 'basis': high},
                           headers={'Idempotency-Key': str(uuid4())}).status_code == 201
        before = client.get(path).json()
        now += timedelta(seconds=1)
        suggestion = client.post(endpoint.removesuffix('/evidence') + '/severity',
            json={'expected_version': 1, 'basis': {**high, 'business': 'partial_or_slow', 'alternative': 'available'}},
            headers={**auth, 'Idempotency-Key': str(uuid4())})
        assert suggestion.status_code == 201
        assert client.get(path).json()['updated_at'] == before['updated_at']
        now += timedelta(seconds=1)
        confirm = path + '/severity/suggestions/' + suggestion.json()['id'] + '/confirm'
        headers = {'Idempotency-Key': str(uuid4())}
        assert client.post(confirm, json={'expected_version': 1}, headers=headers).status_code == 201
        after = client.get(path).json()
        assert after['severity'] == '中'
        assert after['updated_at'] == now.isoformat().replace('+00:00', 'Z')
        now += timedelta(seconds=1)
        assert client.post(confirm, json={'expected_version': 1}, headers=headers).status_code == 200
        assert client.get(path).json() == after
