from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy

import pytest
from uuid import uuid4

from test_investigations import database_url
from test_attempts import configured_client
from test_evidence import started, material


def reopen(client, path, auth, endpoint, **scope):
    finished = client.post(endpoint.removesuffix('/evidence') + '/conclusions', headers={
        **auth, 'Idempotency-Key': str(uuid4())}, json={
        'format': 'controlled-analysis-v1', 'kind': 'undetermined', 'evidence_ids': [],
        'missing': ['causal_trace'], 'expected_version': 0, 'finish': True})
    assert finished.status_code == 201
    before = client.get(path).json()
    response = client.post(path + '/attempts', headers={'Idempotency-Key': str(uuid4())}, json={
        'service_id': 'orders', 'starts_at': '2026-11-01T00:00:00Z',
        'ends_at': '2026-11-01T01:00:00Z', **scope})
    assert response.status_code == 201
    attempt = response.json()['attempts'][-1]
    executions = '/api/executor' + path.removeprefix('/api') + '/attempts/' + attempt['id'] + '/executions'
    execution = client.get(executions, headers=auth).json()['executions'][0]['id']
    return before, response.json(), executions + '/' + execution + '/evidence'


def reference_path(path, record):
    return path + '/attempts/' + record['attempts'][-1]['id'] + '/evidence-references'


@pytest.mark.parametrize('database_url', ['0013'], indirect=True)
def test_upgrade_preserves_legacy_history_and_checks_material(database_url, tmp_path):
    import os
    import subprocess
    import sys
    from datetime import datetime, timedelta, timezone
    from sqlalchemy import MetaData, create_engine, insert, text
    from test_investigations import ROOT
    now = datetime(2026, 11, 1, tzinfo=timezone.utc)
    engine = create_engine(database_url)
    tables = MetaData()
    with engine.connect() as connection:
        connection.execute(text('SET max_parallel_workers_per_gather = 0'))
        tables.reflect(connection)
    investigation, attempt = uuid4(), uuid4()
    identities = [uuid4(), uuid4()]
    import hashlib
    import hmac
    import json
    source_alias = hmac.new(b'legacy-test-key-not-sensitive-1234',
        json.dumps(['source', 'controlled-orders'], ensure_ascii=True).encode(), hashlib.sha256).hexdigest()
    safe = {'source': '来源代号-' + source_alias, 'occurred_at': '2026-11-01T00:15:00Z',
            'query': {'starts_at': '2026-11-01T00:00:00Z', 'ends_at': '2026-11-01T01:00:00Z', 'fields': {}},
            'snippet': {'event': 'request_failed', 'error_code': 'ETIMEDOUT', 'fields': {}},
            'locator': None, 'redaction_note': '旧版已脱敏材料'}
    with engine.begin() as connection:
        connection.execute(insert(tables.tables['investigations']).values(id=investigation, request_key=uuid4(),
            title='历史升级', description='', created_at=now, updated_at=now, retention_expires_at=now + timedelta(days=30)))
        connection.execute(insert(tables.tables['investigation_redaction_keys']).values(investigation_id=investigation, secret=b'legacy-test-key-not-sensitive-1234'))
        connection.execute(insert(tables.tables['attempts']).values(id=attempt, investigation_id=investigation,
            request_key=uuid4(), request_body='{}', attempt_number=1, service_id='orders', source_ids=['controlled-orders'],
            starts_at=now, ends_at=now + timedelta(hours=1), created_at=now, status='已完成', ended_at=now,
            deadline_at=now + timedelta(minutes=10), result='未确定根因', end_reason='completed'))
        for identity, item in zip(identities, [safe, {**safe, 'snippet': None}]):
            connection.execute(insert(tables.tables['evidence']).values(id=identity, attempt_id=attempt,
                created_at=now, material=item, identity_status='legacy_unknown'))
    engine.dispose()
    migrated = subprocess.run([sys.executable, '-m', 'alembic', '-c', 'backend/alembic.ini', 'upgrade', 'head'],
        cwd=ROOT, env={**os.environ, 'DATABASE_URL': database_url, 'PYTHONPATH': str(ROOT / 'backend')}, capture_output=True)
    assert migrated.returncode == 0
    credential = uuid4().hex
    with configured_client(database_url, tmp_path, executor_credential=credential) as client:
        path = '/api/investigations/' + str(investigation)
        before = client.get(path).json()
        assert before['evidence_count'] == 2
        saved = before['attempts'][0]['evidence']
        assert {item['id'] for item in saved} == {str(identity) for identity in identities}
        assert {item['verification'] for item in saved} == {'retained_structured_material', 'unverifiable'}
        assert all(item['identity_status'] == 'legacy_unknown' for item in saved)
        current = client.post(path + '/attempts', headers={'Idempotency-Key': str(uuid4())}, json={
            'service_id': 'orders', 'starts_at': '2026-11-01T00:00:00Z', 'ends_at': '2026-11-01T01:00:00Z'}).json()
        target = reference_path(path, current)
        rejected = client.post(target, json={'evidence_id': str(identities[1])})
        assert rejected.status_code == 422
        assert '不可核验' in rejected.json()['detail']
        assert client.get(path).json() == current
        assert current['attempts'][0] == before['attempts'][0]
    with configured_client(database_url, tmp_path, executor_credential=credential) as restarted:
        assert restarted.get(path).json() == current


def upgrade_reliable_history(database_url, mode):
    import os
    import subprocess
    import sys
    from datetime import datetime, timedelta, timezone
    from sqlalchemy import MetaData, create_engine, insert, text
    from test_investigations import ROOT
    now = datetime(2026, 11, 1, tzinfo=timezone.utc)
    engine = create_engine(database_url)
    tables = MetaData()
    with engine.connect() as connection:
        connection.execute(text('SET max_parallel_workers_per_gather = 0'))
        tables.reflect(connection)
    investigation = uuid4()
    identities = [uuid4(), uuid4()]
    import hashlib
    import hmac
    import json
    source_alias = hmac.new(b'legacy-test-key-not-sensitive-1234',
        json.dumps(['source', 'controlled-orders'], ensure_ascii=True).encode(), hashlib.sha256).hexdigest()
    safe = {'source': '来源代号-' + source_alias, 'occurred_at': '2026-11-01T00:15:00Z',
            'query': {'starts_at': '2026-11-01T00:00:00Z', 'ends_at': '2026-11-01T01:00:00Z', 'fields': {}},
            'snippet': {'event': 'request_failed', 'error_code': 'ETIMEDOUT', 'fields': {}},
            'locator': None, 'redaction_note': '旧版已脱敏材料'}
    with engine.begin() as connection:
        connection.execute(insert(tables.tables['investigations']).values(id=investigation, request_key=uuid4(),
            title='可靠身份升级', description='', created_at=now, updated_at=now, retention_expires_at=now + timedelta(days=30)))
        connection.execute(insert(tables.tables['investigation_redaction_keys']).values(
            investigation_id=investigation, secret=b'legacy-test-key-not-sensitive-1234'))
        for number, identity in enumerate(identities, 1):
            attempt = uuid4()
            connection.execute(insert(tables.tables['attempts']).values(id=attempt, investigation_id=investigation,
                request_key=uuid4(), request_body='{}', attempt_number=number, service_id='orders', source_ids=['controlled-orders'],
                starts_at=now, ends_at=now + timedelta(hours=1), created_at=now, status='已完成', ended_at=now,
                deadline_at=now + timedelta(minutes=10), result='未确定根因', end_reason='completed'))
            connection.execute(insert(tables.tables['evidence']).values(id=identity, attempt_id=attempt,
                created_at=now, material=({**safe, 'query': {**safe['query'], 'starts_at': '2026-11-01T00:10:00Z'}}
                    if mode == 'narrow' and number == 1 else safe), identity_status='reliable', identity_key='a' * 64,
                content_key=('c' if mode == 'conflict' and number == 2 else 'b') * 64))
    engine.dispose()
    migrated = subprocess.run([sys.executable, '-m', 'alembic', '-c', 'backend/alembic.ini', 'upgrade', 'head'],
        cwd=ROOT, env={**os.environ, 'DATABASE_URL': database_url, 'PYTHONPATH': str(ROOT / 'backend')}, capture_output=True)
    assert migrated.returncode == 0
    return '/api/investigations/' + str(investigation), identities


@pytest.mark.parametrize('database_url', ['0013'], indirect=True)
@pytest.mark.parametrize('mode', ['same', 'conflict', 'concurrent'])
def test_upgrade_reliable_historical_ids_include_once(database_url, tmp_path, mode):
    path, identities = upgrade_reliable_history(database_url, mode)
    with configured_client(database_url, tmp_path, executor_credential=uuid4().hex) as client:
        history = client.get(path).json()
        assert [item['evidence_count'] for item in history['attempts']] == [1, 1]
        current = client.post(path + '/attempts', headers={'Idempotency-Key': str(uuid4())}, json={
            'service_id': 'orders', 'starts_at': '2026-11-01T00:00:00Z', 'ends_at': '2026-11-01T01:00:00Z'}).json()
        target = reference_path(path, current)
        def include(identity):
            return client.post(target, json={'evidence_id': str(identity)})
        if mode == 'concurrent':
            with ThreadPoolExecutor(max_workers=6) as pool:
                replies = list(pool.map(include, identities * 3))
        else:
            replies = [include(identity) for identity in identities]
        if mode == 'conflict':
            assert [reply.status_code for reply in replies] == [409, 409]
            assert client.get(path).json() == current
        else:
            assert sorted(reply.status_code for reply in replies) == [200] * (len(replies) - 1) + [201]
            assert len({reply.json()['id'] for reply in replies}) == 1
            after = client.get(path).json()
            assert after['evidence_count'] == 1
            assert all(include(identity).status_code == 200 for identity in identities)
            assert client.get(path).json() == after
        assert client.get(path).json()['attempts'][:2] == history['attempts']
        states = client.get(path).json()['attempts'][-1]['historical_reference_states']
        if mode == 'conflict':
            assert all(states[str(identity)]['included_evidence_id'] is None for identity in identities)
        else:
            included = replies[0].json()['id']
            assert all(states[str(identity)] == {'included_evidence_id': included, 'reference_allowed': True}
                       for identity in identities)
        public = client.get(path).text
        assert 'identity_key' not in public and 'content_key' not in public
        assert 'legacy-test-key-not-sensitive-1234' not in public
    with configured_client(database_url, tmp_path, executor_credential=uuid4().hex) as restarted:
        assert restarted.get(path).json()['attempts'][-1]['historical_reference_states'] == states


@pytest.mark.parametrize('database_url', ['0013'], indirect=True)
def test_upgrade_equivalent_inclusion_does_not_authorize_out_of_scope_material(database_url, tmp_path):
    path, identities = upgrade_reliable_history(database_url, 'narrow')
    with configured_client(database_url, tmp_path, executor_credential=uuid4().hex) as client:
        history = client.get(path).json()['attempts']
        current = client.post(path + '/attempts', headers={'Idempotency-Key': str(uuid4())}, json={
            'service_id': 'orders', 'starts_at': '2026-11-01T00:10:00Z', 'ends_at': '2026-11-01T01:00:00Z'}).json()
        target = reference_path(path, current)
        assert client.post(target, json={'evidence_id': str(identities[0])}).status_code == 201
        after = client.get(path).json()
        assert after['attempts'][-1]['historical_reference_states'] == {
            str(identities[0]): {'included_evidence_id': str(identities[0]), 'reference_allowed': True},
            str(identities[1]): {'included_evidence_id': str(identities[0]), 'reference_allowed': False}}
        assert client.post(target, json={'evidence_id': str(identities[1])}).status_code == 422
        assert client.get(path).json() == after
        assert after['attempts'][:2] == history
    with configured_client(database_url, tmp_path, executor_credential=uuid4().hex) as restarted:
        assert restarted.get(path).json() == after


@pytest.mark.parametrize('reference_first', [True, False, None])
def test_reliable_new_and_historical_same_source_include_once(database_url, tmp_path, reference_first):
    credential = uuid4().hex
    payload = {**material(), 'identity': {'kind': 'record', 'record_id': uuid4().hex}}
    with configured_client(database_url, tmp_path, executor_credential=credential) as client:
        path, _, endpoint, auth = started(client, credential)
        original = client.post(endpoint, json=payload, headers=auth).json()
        history, current, fresh = reopen(client, path, auth, endpoint)
        target = reference_path(path, current)
        include = lambda: client.post(target, json={'evidence_id': original['id']})
        acquire = lambda: client.post(fresh, json=payload, headers=auth)
        if reference_first is None:
            with ThreadPoolExecutor(max_workers=6) as pool:
                replies = list(pool.map(lambda operation: operation(), [include, acquire] * 3))
        else:
            replies = [operation() for operation in ([include, acquire] if reference_first else [acquire, include])]
        assert sorted(reply.status_code for reply in replies) == [200] * (len(replies) - 1) + [201]
        assert {reply.json()['id'] for reply in replies} == {original['id']}
        after = client.get(path).json()
        assert after['evidence_count'] == 1
        assert after['attempts'][0] == history['attempts'][0]
        assert include().status_code == acquire().status_code == 200
        assert client.get(path).json() == after
        changed = deepcopy(payload)
        changed['record']['fields']['token'] = uuid4().hex
        rejected = client.post(fresh, json=changed, headers=auth)
        assert rejected.status_code == 409
        assert client.get(path).json() == after


@pytest.mark.parametrize('order', ['reference_first', 'acquire_first', 'concurrent'])
def test_reacquire_retains_current_query_context_without_changing_history(database_url, tmp_path, order):
    credential = uuid4().hex
    payload = {**material(), 'identity': {'kind': 'record', 'record_id': uuid4().hex}}
    with configured_client(database_url, tmp_path, executor_credential=credential) as client:
        path, _, endpoint, auth = started(client, credential)
        original = client.post(endpoint, json=payload, headers=auth).json()
        history, current, fresh = reopen(client, path, auth, endpoint, starts_at='2026-11-01T00:10:00Z')
        payload['query']['starts_at'] = '2026-11-01T00:10:00Z'
        target = reference_path(path, current)
        assert client.post(target, json={'evidence_id': original['id']}).status_code == 422
        assert client.get(path).json() == current
        acquire = lambda: client.post(fresh, json=payload, headers=auth)
        include = lambda: client.post(target, json={'evidence_id': original['id']})
        if order == 'concurrent':
            with ThreadPoolExecutor(max_workers=6) as pool:
                replies = list(pool.map(lambda operation: operation(), [include, acquire] * 3))
            acquired = [response for response in replies if response.status_code != 422]
            assert sorted(response.status_code for response in acquired) == [200, 200, 201]
            assert sum(response.status_code == 422 for response in replies) == 3
            reply = next(response for response in acquired if response.status_code == 201)
        elif order == 'reference_first':
            assert include().status_code == 422
            reply = acquire()
        else:
            reply = acquire()
            assert include().status_code == 422
        assert reply.status_code == 201
        saved = reply.json()
        assert saved['id'] != original['id']
        assert saved['query']['starts_at'] == '2026-11-01T00:10:00Z'
        assert saved['source_attempt_id'] == current['attempts'][-1]['id']
        assert saved['source_attempt_number'] == 2
        after = client.get(path).json()
        assert after['attempts'][-1]['historical_reference_states'][original['id']] == {
            'included_evidence_id': saved['id'], 'reference_allowed': False}
        assert include().status_code == 422
        assert client.get(path).json() == after
        assert after['evidence_count'] == 1
        assert after['attempts'][-1]['evidence'] == [saved]
        assert after['attempts'][0] == history['attempts'][0]
        assert client.post(fresh, json=payload, headers=auth).json() == saved
        assert client.get(path).json() == after
        assert client.post(target, json={'evidence_id': original['id']}).status_code == 422
        changed = deepcopy(payload)
        changed['record']['fields']['token'] = uuid4().hex
        assert client.post(fresh, json=changed, headers=auth).status_code == 409
        assert client.get(path).json() == after
        history2, current2, fresh2 = reopen(client, path, auth, fresh, starts_at='2026-11-01T00:10:00Z')
        reused = client.post(fresh2, json=payload, headers=auth)
        assert reused.status_code == 201
        assert reused.json() == saved
        final = client.get(path).json()
        assert final['attempts'][:2] == history2['attempts']
        assert final['evidence_count'] == 1
        assert client.post(reference_path(path, current2), json={'evidence_id': saved['id']}).json() == saved
        assert client.get(path).json() == final


@pytest.mark.parametrize('entry', ['user', 'executor', 'reacquire'])
@pytest.mark.parametrize('moment', ['before', 'final', 'replay'])
def test_reference_deadline_fences_pending_and_replayed_inclusion(database_url, tmp_path, entry, moment):
    from datetime import datetime, timedelta, timezone
    from test_deadlines import ManualTicks
    now = [datetime(2026, 11, 3, 12, tzinfo=timezone.utc)]
    credential = uuid4().hex
    with configured_client(database_url, tmp_path, executor_credential=credential,
                           clock=lambda: now[0], deadline_schedule=ManualTicks()) as client:
        path, _, endpoint, auth = started(client, credential)
        payload = {**material(), 'identity': {'kind': 'record', 'record_id': uuid4().hex}}
        original = client.post(endpoint, json=payload, headers=auth).json()
        scope = {'starts_at': '2026-11-01T00:10:00Z'} if entry == 'reacquire' else {}
        history, current, fresh = reopen(client, path, auth, endpoint, **scope)
        target = reference_path(path, current) if entry == 'user' else fresh if entry == 'reacquire' else fresh.removesuffix('/evidence') + '/evidence-references'
        headers = {} if entry == 'user' else auth
        payload['query']['starts_at'] = '2026-11-01T00:10:00Z'
        body = payload if entry == 'reacquire' else {'evidence_id': original['id']}
        if moment == 'replay':
            assert client.post(target, json=body, headers=headers).status_code == 201
        before = client.get(path).json()
        now[0] += timedelta(seconds=600 if moment == 'before' else 599)
        samples = [0]
        # 在公开写请求的外部时钟采样推进时间，不替换数据库或领域实现。
        def crossing_clock():
            samples[0] += 1
            return now[0] + (timedelta(seconds=2) if samples[0] >= 2 else timedelta())
        if moment != 'before':
            class Crossing:
                def __call__(self):
                    return crossing_clock()
            # 通过同一数据库的应用重启安装可控时钟，避免后台时钟采样。
            with configured_client(database_url, tmp_path, executor_credential=credential,
                                   clock=Crossing(), deadline_schedule=ManualTicks()) as late:
                reply = late.post(target, json=body, headers=headers)
        else:
            reply = client.post(target, json=body, headers=headers)
        assert reply.status_code == 403
        after = client.get(path).json()
        assert after['status'] == '失败'
        assert after['attempts'][0] == history['attempts'][0]
        assert after['attempts'][-1]['evidence'] == before['attempts'][-1]['evidence']
        assert client.post(target, json=body, headers=headers).status_code == 403


def test_executor_reference_binds_execution_identity(database_url, tmp_path):
    credential = uuid4().hex
    with configured_client(database_url, tmp_path, executor_credential=credential) as client:
        path, _, endpoint, auth = started(client, credential)
        original = client.post(endpoint, json=material(), headers=auth).json()
        _, current, fresh = reopen(client, path, auth, endpoint)
        target = fresh.removesuffix('/evidence') + '/evidence-references'
        before = client.get(path).json()
        assert client.post(target, json={'evidence_id': original['id']}).status_code == 401
        stale = endpoint.removesuffix('/evidence') + '/evidence-references'
        assert client.post(stale, json={'evidence_id': original['id']}, headers=auth).status_code == 403
        assert client.get(path).json() == before
        assert client.post(target, json={'evidence_id': original['id']}, headers=auth).status_code == 201
        after = client.get(path).json()
        assert after['evidence_count'] == 1
        assert client.post(target, json={'evidence_id': original['id']}, headers=auth).status_code == 200
        assert client.get(path).json() == after
        assert client.post(path + '/attempts/' + current['attempts'][-1]['id'] + '/cancel').status_code == 200
        stopped = client.get(path).json()
        assert client.post(target, json={'evidence_id': original['id']}, headers=auth).status_code == 403
        assert client.get(path).json() == stopped


@pytest.mark.parametrize('reacquire', [False, True])
def test_conclusion_uses_included_historical_causal_evidence(database_url, tmp_path, reacquire):
    payload = material()
    payload['record']['causal_trace'] = {'format': 'observed-propagation-v1',
        'dependency_error': 'ETIMEDOUT', 'propagated_error': 'ETIMEDOUT',
        'request_outcome': 'failed_by_dependency', 'local_rejection': 'excluded_by_trace',
        'independent_failure': 'excluded_by_trace', 'contradiction': 'none_observed'}
    credential = uuid4().hex
    with configured_client(database_url, tmp_path, executor_credential=credential) as client:
        path, _, endpoint, auth = started(client, credential)
        original = client.post(endpoint, json=payload, headers=auth).json()
        scope = {'starts_at': '2026-11-01T00:10:00Z'} if reacquire else {}
        _, current, fresh = reopen(client, path, auth, endpoint, **scope)
        if reacquire:
            payload['query']['starts_at'] = '2026-11-01T00:10:00Z'
            acquired = client.post(fresh, json=payload, headers=auth)
            assert acquired.status_code == 201
            saved = acquired.json()
            assert saved['query']['starts_at'] == '2026-11-01T00:10:00Z'
        else:
            assert client.post(reference_path(path, current), json={'evidence_id': original['id']}).status_code == 201
            saved = original
        response = client.post(fresh.removesuffix('/evidence') + '/conclusions', headers={
            **auth, 'Idempotency-Key': str(uuid4())}, json={
            'format': 'controlled-analysis-v1', 'kind': 'confirmed',
            'mechanism': 'dependency_error_propagation', 'evidence_ids': [saved['id']],
            'missing': [], 'expected_version': 0, 'finish': True})
        assert response.status_code == 201
        assert client.get(path).json()['attempts'][-1]['result'] == '已确认根因'


@pytest.mark.parametrize('mode', ['service', 'range', 'foreign', 'ended', 'lost', 'cancel'])
def test_reference_rejects_scope_and_inactive_rounds(database_url, tmp_path, mode):
    from fastapi.testclient import TestClient
    from app.main import create_app
    import json
    config = tmp_path / 'scope.json'
    config.write_text(json.dumps({'services': [
        {'id': 'orders', 'name': '订单', 'source_ids': ['controlled-orders']},
        {'id': 'other', 'name': '其他', 'source_ids': ['controlled-orders']}]}), encoding='utf-8')
    credential = uuid4().hex
    with TestClient(create_app(database_url=database_url, services_file=config, executor_credential=credential)) as client:
        path, _, endpoint, auth = started(client, credential)
        original = client.post(endpoint, json=material(), headers=auth).json()
        scope = {'service_id': 'other'} if mode == 'service' else {
            'starts_at': '2026-11-01T00:16:00Z'} if mode == 'range' else {}
        _, current, fresh = reopen(client, path, auth, endpoint, **scope)
        target = reference_path(path, current)
        if mode == 'foreign':
            _, _, foreign, _ = started(client, credential)
            original = client.post(foreign, json=material(), headers=auth).json()
        if mode == 'ended':
            client.post(fresh.removesuffix('/evidence') + '/conclusions', headers={**auth, 'Idempotency-Key': str(uuid4())}, json={
                'format': 'controlled-analysis-v1', 'kind': 'undetermined', 'evidence_ids': [],
                'missing': ['causal_trace'], 'expected_version': 0, 'finish': True})
        if mode == 'lost':
            assert client.post(fresh.removesuffix('/evidence') + '/lost', headers=auth).status_code == 200
        if mode == 'cancel':
            assert client.post(path + '/attempts/' + current['attempts'][-1]['id'] + '/cancel').status_code == 200
        before = client.get(path).json()
        reply = client.post(target, json={'evidence_id': original['id']})
        assert reply.status_code == (422 if mode in {'service', 'range', 'foreign'} else 403)
        assert client.get(path).json() == before
        assert client.post(endpoint, json=material(), headers=auth).status_code == 403


def test_reference_keeps_unreliable_records_separate_and_aliases_scoped(database_url, tmp_path):
    credential = uuid4().hex
    payload = material()
    with configured_client(database_url, tmp_path, executor_credential=credential) as client:
        path, _, endpoint, auth = started(client, credential)
        original = client.post(endpoint, json=payload, headers=auth).json()
        _, current, fresh = reopen(client, path, auth, endpoint)
        target = reference_path(path, current)
        with ThreadPoolExecutor(max_workers=5) as pool:
            replies = list(pool.map(lambda _: client.post(target, json={'evidence_id': original['id']}), range(5)))
        assert sorted(r.status_code for r in replies) == [200, 200, 200, 200, 201]
        acquired = client.post(fresh, json=payload, headers=auth)
        assert acquired.status_code == 201
        assert acquired.json()['id'] != original['id']
        assert acquired.json()['snippet'] == original['snippet']
        assert client.get(path).json()['evidence_count'] == 2
        _, _, foreign, _ = started(client, credential)
        other = client.post(foreign, json=payload, headers=auth).json()
        assert other['snippet']['fields']['email'] != original['snippet']['fields']['email']
        assert other['source'] != original['source']


@pytest.mark.parametrize('reacquire', [False, True])
def test_cross_round_sensitive_material_never_persists_raw_values(database_url, tmp_path, caplog, reacquire):
    import json
    import subprocess
    from urllib.parse import urlsplit, parse_qs
    credential = uuid4().hex
    payload = material()
    payload['identity'] = {'kind': 'file', 'file_id': uuid4().hex, 'offset': 17}
    with configured_client(database_url, tmp_path, executor_credential=credential) as client:
        path, _, endpoint, auth = started(client, credential)
        original = client.post(endpoint, json=payload, headers=auth).json()
        scope = {'starts_at': '2026-11-01T00:10:00Z'} if reacquire else {}
        _, current, fresh = reopen(client, path, auth, endpoint, **scope)
        if reacquire:
            payload['query']['starts_at'] = '2026-11-01T00:10:00Z'
            acquired = client.post(fresh, json=payload, headers=auth)
            assert acquired.status_code == 201
            assert acquired.json()['id'] != original['id']
        else:
            assert client.post(reference_path(path, current), json={'evidence_id': original['id']}).status_code == 201
        assert client.post(fresh, json=payload, headers=auth).status_code == 200
        changed = deepcopy(payload)
        changed['identity']['offset'] = 18
        changed['occurred_at'] = '2026-11-01T00:16:00Z'
        acquired = client.post(fresh, json=changed, headers=auth)
        assert acquired.status_code == 201
        assert acquired.json()['snippet'] == original['snippet']
        assert acquired.json()['query']['fields']['email'] == original['snippet']['fields']['email']
        assert client.get(path).json()['evidence_count'] == 2
        invalid = client.post(reference_path(path, current), json={'evidence_id': uuid4().hex, 'raw': payload['record']['fields']['token']})
        assert invalid.status_code == 422
        conflicting = deepcopy(payload)
        conflicting['record']['fields']['token'] = uuid4().hex
        conflict = client.post(fresh, json=conflicting, headers=auth)
        assert conflict.status_code == 409
        public = json.dumps(client.get(path).json(), ensure_ascii=False) + invalid.text + conflict.text + caplog.text
        schema = parse_qs(urlsplit(database_url).query)['options'][0].split('=')[1].split()[0]
        dumped = subprocess.run(['docker', 'exec', '-e', 'PGOPTIONS=-c max_parallel_workers_per_gather=0',
            'logscope-01-test-pg17', 'pg_dump', '-U', 'logscope_test', '-d', 'logscope_test', '--data-only', '--schema', schema], capture_output=True)
        assert dumped.returncode == 0, '隔离schema隐私审计失败'
        for raw in [*payload['record']['fields'].values(), payload['locator'], payload['identity']['file_id'],
                    conflicting['record']['fields']['token'], credential]:
            assert raw not in public, '跨轮输出包含原敏感值'
            assert raw.encode() not in dumped.stdout, '跨轮持久产物包含原敏感值'


def test_explicit_reference_preserves_history_counts_and_restart(database_url, tmp_path):
    credential = uuid4().hex
    with configured_client(database_url, tmp_path, executor_credential=credential) as client:
        path, _, endpoint, auth = started(client, credential)
        original = client.post(endpoint, json=material(), headers=auth).json()
        history, current, _ = reopen(client, path, auth, endpoint)
        assert current['evidence_count'] == 0
        assert current['attempts'][0] == history['attempts'][0]
        target = reference_path(path, current)
        reply = client.post(target, json={'evidence_id': original['id']})
        assert reply.status_code == 201
        saved = reply.json()
        assert saved['id'] == original['id']
        assert saved['source_attempt_number'] == 1
        assert saved['source_attempt_id'] == history['attempts'][0]['id']
        assert saved['verification'] == 'retained_structured_material'
        after = client.get(path).json()
        assert after['attempts'][0] == history['attempts'][0]
        assert after['evidence_count'] == after['attempts'][-1]['evidence_count'] == 1
        assert after['attempts'][-1]['evidence'] == [saved]
        assert after['updated_at'] > current['updated_at']
        assert after['attempts'][-1]['query_coverage'] == current['attempts'][-1]['query_coverage']
        assert not after['attempts'][-1]['query_coverage']['complete']
        assert client.post(target, json={'evidence_id': original['id']}).status_code == 200
        assert client.get(path).json() == after
    with configured_client(database_url, tmp_path, executor_credential=credential) as restarted:
        assert restarted.get(path).json() == after
        assert restarted.post(target, json={'evidence_id': original['id']}).status_code == 200
        assert restarted.get(path).json() == after
