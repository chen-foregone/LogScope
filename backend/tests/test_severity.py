from uuid import uuid4
import json
import pytest
from datetime import datetime, timedelta, timezone

from test_investigations import database_url
from test_attempts import configured_client, create
from test_evidence import started


def dump_schema(database_url):
    from sqlalchemy import MetaData, create_engine, select
    engine = create_engine(database_url)
    metadata = MetaData()
    try:
        with engine.connect() as connection:
            metadata.reflect(connection)
            return json.dumps({name: [dict(row) for row in connection.execute(select(table)).mappings()]
                               for name, table in metadata.tables.items()}, default=repr, ensure_ascii=False)
    finally:
        engine.dispose()


def test_agent_cannot_override_human_and_confirmation_is_idempotent(database_url, tmp_path):
    credential = uuid4().hex
    with configured_client(database_url, tmp_path, executor_credential=credential) as client:
        path, _, evidence_endpoint, auth = started(client, credential)
        human = client.post(path + '/severity', json={'expected_version': 0, 'basis': impact()},
                            headers={'Idempotency-Key': str(uuid4())})
        assert human.status_code == 201
        before = client.get(path).json()
        endpoint = evidence_endpoint.removesuffix('/evidence') + '/severity'
        payload = {'expected_version': 1, 'basis': {**impact('partial_or_slow'), 'alternative': 'available'}}
        headers = {**auth, 'Idempotency-Key': str(uuid4())}
        suggestion = client.post(endpoint, json=payload, headers=headers)
        assert suggestion.status_code == 201
        current = client.get(path).json()
        assert current['severity'] == '高'
        assert current['severity_assessed_at'] == before['severity_assessed_at']
        assert current['updated_at'] == before['updated_at']
        assert current['severity_suggestions'][0]['status'] == 'pending'
        assert client.post(endpoint, json=payload, headers=headers).status_code == 200
        confirm = path + '/severity/suggestions/' + suggestion.json()['id'] + '/confirm'
        confirm_headers = {'Idempotency-Key': str(uuid4())}
        reply = client.post(confirm, json={'expected_version': 1}, headers=confirm_headers)
        assert reply.status_code == 201
        after = client.get(path).json()
        assert after['severity'] == '中'
        assert after['severity_source'] == 'human'
        assert after['severity_version'] == 2
        assert after['severity_suggestions'][0]['status'] == 'applied'
        assert len(after['severity_history']) == 3
        assert client.post(confirm, json={'expected_version': 1}, headers=confirm_headers).status_code == 200
        assert client.get(path).json() == after


@pytest.mark.parametrize('basis,level', [
    ({'business': 'core_unavailable'}, '高'),
    ({'business': 'key_operations_blocked', 'users': 'broad'}, '高'),
    ({'business': 'partial_or_slow', 'alternative': 'available'}, '中'),
    ({'business': 'key_operations_blocked', 'users': 'partial', 'alternative': 'available'}, '中'),
    ({'business': 'local_minor', 'users': 'local', 'alternative': 'not_needed'}, '低'),
    ({'business': 'local_minor', 'users': 'local', 'alternative': 'available'}, '低'),
    ({'business': 'insufficient'}, '未评估'),
    ({'business': 'partial_or_slow', 'alternative': 'unknown'}, '未评估'),
    ({'business': 'local_minor', 'users': 'local', 'alternative': 'none'}, '未评估'),
])
def test_qualitative_agent_assessment_before_human(database_url, tmp_path, basis, level):
    credential = uuid4().hex
    with configured_client(database_url, tmp_path, executor_credential=credential) as client:
        path, before, evidence_endpoint, auth = started(client, credential)
        reply = client.post(evidence_endpoint.removesuffix('/evidence') + '/severity',
            json={'expected_version': 0, 'basis': {**impact(), **basis}}, headers={**auth, 'Idempotency-Key': str(uuid4())})
        assert reply.status_code == 201
        after = client.get(path).json()
        assert after['severity'] == level
        assert after['severity_suggestions'] == []
        assert after['severity_history'] == [reply.json()]
        assert after['severity_history'][0]['source'] == 'agent'
        assert after['attempts'] == before['attempts']
        if level == '未评估':
            assert after['severity_source'] is None
            assert after['severity_assessed_at'] is None
            assert after['updated_at'] == before['updated_at']
        else:
            assert after['severity_source'] == 'agent'
            assert after['severity_assessed_at']


def test_sensitive_or_missing_basis_never_persists_or_leaks(database_url, tmp_path, caplog):
    secret = uuid4().hex
    with configured_client(database_url, tmp_path) as client:
        pending, _ = create(client)
        path = '/api/investigations/' + pending['id']
        for payload in ({'expected_version': 0}, {'expected_version': 0, 'basis': secret},
                {'expected_version': 0, 'basis': {**impact(), 'business': secret}},
                {'expected_version': 0, 'basis': {**impact(), secret: secret}},
                {'expected_version': True, 'basis': impact()},
                {'expected_version': 0, 'basis': {**impact(), 'duration': secret + '\ud800'}}):
            reply = client.post(path + '/severity', content=json.dumps(payload), headers={'Idempotency-Key': str(uuid4())})
            assert reply.status_code == 422
            assert secret not in reply.text + caplog.text
            assert client.get(path).json() == pending
        assert secret not in dump_schema(database_url)


def test_concurrent_confirmation_and_human_decision_cannot_overwrite(database_url, tmp_path):
    from concurrent.futures import ThreadPoolExecutor
    credential = uuid4().hex
    with configured_client(database_url, tmp_path, executor_credential=credential) as client:
        path, before, endpoint, auth = started(client, credential)
        def human(version, basis):
            return client.post(path + '/severity', json={'expected_version': version, 'basis': basis},
                               headers={'Idempotency-Key': str(uuid4())})
        assert human(0, impact()).status_code == 201
        endpoint = endpoint.removesuffix('/evidence') + '/severity'
        payload = {'expected_version': 1, 'basis': {**impact('partial_or_slow'), 'alternative': 'available'}}
        suggestion = client.post(endpoint, json=payload, headers={**auth, 'Idempotency-Key': str(uuid4())}).json()
        confirm = path + '/severity/suggestions/' + suggestion['id'] + '/confirm'
        with ThreadPoolExecutor(max_workers=2) as pool:
            a = pool.submit(human, 1, impact('insufficient'))
            b = pool.submit(client.post, confirm, json={'expected_version': 1}, headers={'Idempotency-Key': str(uuid4())})
            replies = [a.result(), b.result()]
        assert sorted(reply.status_code for reply in replies) == [201, 409]
        current = client.get(path).json()
        assert current['severity_version'] == 2
        assert len(current['severity_history']) == 3
        assert human(2, impact()).status_code == 201
        current = client.get(path).json()
        assert client.post(confirm, json={'expected_version': 3}, headers={'Idempotency-Key': str(uuid4())}).status_code == 409
        assert client.get(path).json() == current
        assert client.post(path + '/attempts/' + before['attempts'][0]['id'] + '/cancel').status_code == 200
        after_stop = client.get(path).json()
        reopened = client.post(path + '/attempts', json={'service_id': 'orders'}, headers={'Idempotency-Key': str(uuid4())}).json()
        for key in ('severity', 'severity_source', 'severity_assessed_at', 'severity_history', 'severity_version'):
            assert reopened[key] == after_stop[key]
        assert client.post(endpoint, json=payload, headers={**auth, 'Idempotency-Key': str(uuid4())}).status_code == 403
        assert client.get(path).json() == reopened


def impact(kind='core_unavailable'):
    return {'format': 'controlled-impact-v1', 'business': kind,
            'users': 'broad', 'duration': 'ongoing', 'alternative': 'none'}


def test_pending_suggestion_and_old_execution_rejected_after_round_ends(database_url, tmp_path):
    credential = uuid4().hex
    with configured_client(database_url, tmp_path, executor_credential=credential) as client:
        path, record, evidence_endpoint, auth = started(client, credential)
        assert client.post(path + '/severity', json={'expected_version': 0, 'basis': impact()},
                           headers={'Idempotency-Key': str(uuid4())}).status_code == 201
        endpoint = evidence_endpoint.removesuffix('/evidence') + '/severity'
        payload = {'expected_version': 1, 'basis': {**impact('partial_or_slow'), 'alternative': 'available'}}
        headers = {**auth, 'Idempotency-Key': str(uuid4())}
        suggestion = client.post(endpoint, json=payload, headers=headers)
        assert suggestion.status_code == 201
        assert client.post(path + '/attempts/' + record['attempts'][0]['id'] + '/cancel').status_code == 200
        stopped = client.get(path).json()
        assert stopped['severity_suggestions'][0]['status'] == 'stale'
        confirm = path + '/severity/suggestions/' + suggestion.json()['id'] + '/confirm'
        assert client.post(confirm, json={'expected_version': 1},
                           headers={'Idempotency-Key': str(uuid4())}).status_code == 409
        assert client.get(path).json() == stopped
        assert client.post(endpoint, json=payload, headers=headers).status_code == 403


def test_human_insufficient_decision_still_restricts_agent_to_suggestion(database_url, tmp_path):
    credential = uuid4().hex
    with configured_client(database_url, tmp_path, executor_credential=credential) as client:
        path, _, evidence_endpoint, auth = started(client, credential)
        human = client.post(path + '/severity', json={'expected_version': 0, 'basis': impact('insufficient')},
                            headers={'Idempotency-Key': str(uuid4())})
        assert human.status_code == 201
        current = client.get(path).json()
        assert current['severity'] == '未评估'
        assert current['severity_source'] is None
        assert current['severity_assessed_at'] is None
        endpoint = evidence_endpoint.removesuffix('/evidence') + '/severity'
        suggested = client.post(endpoint, json={'expected_version': 1, 'basis': {
            **impact('partial_or_slow'), 'alternative': 'available'}}, headers={**auth, 'Idempotency-Key': str(uuid4())})
        assert suggested.status_code == 201
        assert suggested.json()['kind'] == 'suggestion'
        current = client.get(path).json()
        assert current['severity'] == '未评估'
        assert current['severity_suggestions'][0]['status'] == 'pending'
        confirm = path + '/severity/suggestions/' + suggested.json()['id'] + '/confirm'
        assert client.post(confirm, json={'expected_version': 1},
                           headers={'Idempotency-Key': str(uuid4())}).status_code == 201
        current = client.get(path).json()
        assert current['severity'] == '中'
        assert current['severity_source'] == 'human'
        assert current['severity_assessed_at']


@pytest.mark.parametrize('database_url', ['0014'], indirect=True)
def test_upgrade_preserves_legacy_severity_without_fabricating_history(database_url, tmp_path):
    import os
    import subprocess
    import sys
    from sqlalchemy import create_engine, text
    from test_investigations import ROOT
    legacy_id = uuid4()
    assessed = datetime(2026, 11, 1, 12, 0, tzinfo=timezone.utc)
    engine = create_engine(database_url)
    with engine.begin() as connection:
        connection.execute(text(
            "INSERT INTO investigations (id, title, description, created_at, updated_at, "
            "retention_expires_at, severity, severity_source, severity_assessed_at) "
            "VALUES (:id, :title, '', :now, :now, :retention, '高', 'agent', :assessed)"),
            {'id': legacy_id, 'title': '升级保留等级', 'now': assessed,
             'retention': assessed + timedelta(days=30), 'assessed': assessed})
    engine.dispose()
    migrated = subprocess.run([sys.executable, '-m', 'alembic', '-c', 'backend/alembic.ini', 'upgrade', 'head'],
        cwd=ROOT, env={**os.environ, 'DATABASE_URL': database_url, 'PYTHONPATH': str(ROOT / 'backend')},
        capture_output=True)
    assert migrated.returncode == 0, migrated.stdout + migrated.stderr
    with configured_client(database_url, tmp_path) as client:
        record = client.get('/api/investigations/' + str(legacy_id)).json()
        assert record['severity'] == '高'
        assert record['severity_source'] == 'agent'
        assert record['severity_assessed_at'].startswith('2026-11-01T12:00:00')
        assert record['severity_version'] == 0
        assert record['severity_history'] == []
        assert record['severity_suggestions'] == []


def test_human_assessment_is_versioned_and_survives_restart(database_url, tmp_path):
    with configured_client(database_url, tmp_path) as client:
        pending, _ = create(client)
        path = '/api/investigations/' + pending['id']
        payload = {'expected_version': 0, 'basis': impact()}
        headers = {'Idempotency-Key': str(uuid4())}
        reply = client.post(path + '/severity', json=payload, headers=headers)
        assert reply.status_code == 201, reply.text
        after = client.get(path).json()
        assert after['severity'] == '高'
        assert after['severity_source'] == 'human'
        assert after['severity_version'] == 1
        assert after['severity_assessed_at']
        assert after['severity_history'] == [reply.json()]
        assert after['severity_history'][0]['basis']['business'] == 'core_unavailable'
        assert after['updated_at'] > pending['updated_at']
        assert client.post(path + '/severity', json=payload, headers=headers).status_code == 200
        conflict = client.post(path + '/severity', json={'expected_version': 1, 'basis': impact('insufficient')},
                               headers=headers)
        assert conflict.status_code == 409
        assert client.get(path).json() == after
    with configured_client(database_url, tmp_path) as restarted:
        assert restarted.get(path).json() == after
