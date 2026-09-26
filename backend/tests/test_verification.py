from uuid import uuid4

from test_investigations import database_url
from test_attempts import configured_client
from test_evidence import started, material
from test_conclusions import proposal
import pytest


def causal_material():
    payload = material()
    payload['record']['causal_trace'] = {
        'format': 'observed-propagation-v1', 'dependency_error': 'ETIMEDOUT',
        'propagated_error': 'ETIMEDOUT', 'request_outcome': 'failed_by_dependency',
        'local_rejection': 'excluded_by_trace', 'independent_failure': 'excluded_by_trace',
        'contradiction': 'none_observed'}
    return payload


def confirm(client, endpoint, auth, ids, **changes):
    return client.post(endpoint.removesuffix('/evidence') + '/conclusions',
        json=proposal(kind='confirmed', mechanism='dependency_error_propagation',
                      evidence_ids=ids, missing=[], **changes),
        headers={**auth, 'Idempotency-Key': str(uuid4())})


def report(client, path, auth, evidence_id, **changes):
    return client.post('/api/executor' + path.removeprefix('/api') + '/evidence/' + evidence_id + '/verification',
        json={'expected_version': 0, 'source_access': 'unreachable',
              'material_status': 'retained', 'reason': 'source_unreachable', **changes},
        headers={**auth, 'Idempotency-Key': str(uuid4())})


def test_unreachable_source_keeps_retained_evidence_and_completed_conclusion(database_url, tmp_path):
    credential = uuid4().hex
    with configured_client(database_url, tmp_path, executor_credential=credential) as client:
        path, _, endpoint, auth = started(client, credential)
        saved = client.post(endpoint, json=causal_material(), headers=auth).json()
        assert confirm(client, endpoint, auth, [saved['id']]).status_code == 201
        before = client.get(path).json()
        reply = report(client, path, auth, saved['id'])
        assert reply.status_code == 201, reply.text
        after = client.get(path).json()
        attempt = after['attempts'][0]
        item = attempt['evidence'][0]
        assert item['source_access'] == 'unreachable'
        assert item['verification'] == 'retained_structured_material'
        assert item['verification_history'][0]['reason'] == 'source_unreachable'
        assert attempt['conclusion']['verification'] == 'supported_controlled_trace'
        assert attempt['status'] == '已完成'
        assert attempt['ended_at'] == before['attempts'][0]['ended_at']
        assert after['severity'] == before['severity']
        assert len(after['attempts']) == 1
        assert client.get(path).json() == after
    with configured_client(database_url, tmp_path, executor_credential=credential) as restarted:
        assert restarted.get(path).json() == after


@pytest.mark.parametrize('alternative', [False, True])
def test_loss_requires_sufficient_support_and_manual_new_round(database_url, tmp_path, alternative):
    credential = uuid4().hex
    with configured_client(database_url, tmp_path, executor_credential=credential) as client:
        path, _, endpoint, auth = started(client, credential)
        first = client.post(endpoint, json=causal_material(), headers=auth).json()
        other = client.post(endpoint, json=causal_material(), headers=auth).json() if alternative else None
        conclusion = confirm(client, endpoint, auth, [first['id']]).json()
        before = client.get(path).json()
        reply = report(client, path, auth, first['id'], material_status='invalid', reason='material_integrity_failed')
        assert reply.status_code == 201
        after = client.get(path).json()
        old = after['attempts'][0]
        assert old['status'] == '已完成' and old['result'] == '已确认根因'
        assert old['ended_at'] == before['attempts'][0]['ended_at']
        assert old['evidence'][0]['verification'] == 'unverifiable'
        assert old['conclusions'][0]['kind'] == 'confirmed'
        assert after['updated_at'] > before['updated_at']
        if alternative:
            assert old['conclusion']['verification'] == 'supported_controlled_trace'
            assert old['conclusion']['current_evidence_ids'] == [other['id']]
            assert old['conclusion']['verification_history'][-1]['reason'] == 'alternative_sufficient_causal_support'
            return
        assert old['conclusion']['verification'] == 'pending_reverification'
        assert old['conclusion']['original_verification'] == 'supported_controlled_trace'
        assert old['conclusion']['verification_history'][0]['evidence_ids'] == [first['id']]
        scope = {'service_id': 'orders', 'starts_at': old['starts_at'], 'ends_at': old['ends_at'],
                 'reverify_conclusion_id': conclusion['id']}
        new = client.post(path + '/attempts', json=scope, headers={'Idempotency-Key': str(uuid4())})
        assert new.status_code == 201, new.text
        attempt_id = new.json()['attempts'][-1]['id']
        base = '/api/executor' + path.removeprefix('/api') + '/attempts/' + attempt_id + '/executions'
        execution = client.get(base, headers=auth).json()['executions'][0]['id']
        new_endpoint = base + '/' + execution + '/evidence'
        ordinary = client.post(new_endpoint, json=material(), headers=auth).json()
        assert confirm(client, new_endpoint, auth, [ordinary['id']]).status_code == 422
        assert client.get(path).json()['attempts'][0]['conclusion']['verification'] == 'pending_reverification'
        sufficient = client.post(new_endpoint, json=causal_material(), headers=auth).json()
        assert confirm(client, new_endpoint, auth, [sufficient['id']]).status_code == 201
        restored = client.get(path).json()
        history = restored['attempts'][0]['conclusion']['verification_history']
        assert [event['status'] for event in history] == ['pending_reverification', 'supported_controlled_trace']
        assert history[-1]['attempt_id'] == attempt_id
        assert history[-1]['evidence_ids'] == [sufficient['id']]
        assert restored['attempts'][0]['ended_at'] == old['ended_at']
        assert restored['severity'] == before['severity']
        assert client.get(path).json() == restored


def test_link_only_without_retained_material_is_not_causal_support(database_url, tmp_path):
    credential = uuid4().hex
    with configured_client(database_url, tmp_path, executor_credential=credential) as client:
        path, _, endpoint, auth = started(client, credential)
        payload = material()
        payload['format'] = 'controlled-location-v1'
        payload['record'] = None
        reply = client.post(endpoint, json=payload, headers=auth)
        assert reply.status_code == 201, reply.text
        saved = reply.json()
        assert saved['verification'] == 'unverifiable'
        assert report(client, path, auth, saved['id'], material_status='missing', reason='no_retained_material').status_code == 201
        assert confirm(client, endpoint, auth, [saved['id']]).status_code == 422
        after = client.get(path).json()
        assert after['attempts'][0]['evidence'][0]['verification_history'][0]['reason'] == 'no_retained_material'


def test_observation_auth_cas_replay_and_missing_cannot_discard_retained_material(database_url, tmp_path):
    credential = uuid4().hex
    with configured_client(database_url, tmp_path, executor_credential=credential) as client:
        path, _, endpoint, auth = started(client, credential)
        saved = client.post(endpoint, json=causal_material(), headers=auth).json()
        assert report(client, path, {}, saved['id']).status_code == 401
        assert report(client, path, auth, saved['id'], material_status='missing', reason='no_retained_material').status_code == 422
        url = '/api/executor' + path.removeprefix('/api') + '/evidence/' + saved['id'] + '/verification'
        body = {'expected_version': 0, 'source_access': 'unreachable', 'material_status': 'retained', 'reason': 'source_unreachable'}
        headers = {**auth, 'Idempotency-Key': str(uuid4())}
        assert client.post(url, json=body, headers=headers).status_code == 201
        before = client.get(path).json()
        assert client.post(url, json=body, headers=headers).status_code == 200
        assert report(client, path, auth, saved['id']).status_code == 409
        assert client.post(url, json={**body, 'reason': 'secret'}, headers=headers).status_code == 422
        assert client.get(path).json() == before


def test_noncritical_loss_and_restored_material_do_not_bypass_new_round(database_url, tmp_path):
    credential = uuid4().hex
    with configured_client(database_url, tmp_path, executor_credential=credential) as client:
        path, _, endpoint, auth = started(client, credential)
        noncritical = client.post(endpoint, json=material(), headers=auth).json()
        critical = client.post(endpoint, json=causal_material(), headers=auth).json()
        assert confirm(client, endpoint, auth, [critical['id']]).status_code == 201
        assert report(client, path, auth, noncritical['id'], material_status='invalid', reason='material_integrity_failed').status_code == 201
        assert client.get(path).json()['attempts'][0]['conclusion']['verification'] == 'supported_controlled_trace'
        assert report(client, path, auth, critical['id'], material_status='invalid', reason='material_integrity_failed').status_code == 201
        assert report(client, path, auth, critical['id'], expected_version=1,
                      source_access='reachable', reason='source_reachable').status_code == 201
        after = client.get(path).json()
        assert after['attempts'][0]['conclusion']['verification'] == 'pending_reverification'
        assert after['attempts'][0]['evidence'][1]['verification'] == 'retained_structured_material'
        target = after['attempts'][0]['conclusion']['id']
        assert client.post(path + '/attempts', json={'service_id': 'orders', 'reverify_conclusion_id': target},
                           headers={'Idempotency-Key': str(uuid4())}).status_code == 422
        assert client.get(path).json() == after


def test_observation_concurrency_has_one_version_and_cross_investigation_is_rejected(database_url, tmp_path):
    from concurrent.futures import ThreadPoolExecutor
    credential = uuid4().hex
    with configured_client(database_url, tmp_path, executor_credential=credential) as client:
        path, _, endpoint, auth = started(client, credential)
        saved = client.post(endpoint, json=causal_material(), headers=auth).json()
        assert confirm(client, endpoint, auth, [saved['id']]).status_code == 201
        other, _, _, _ = started(client, credential)
        assert report(client, other, auth, saved['id']).status_code == 404
        with ThreadPoolExecutor(max_workers=2) as pool:
            replies = list(pool.map(lambda _: report(client, path, auth, saved['id'],
                material_status='invalid', reason='material_integrity_failed'), range(2)))
        assert sorted(reply.status_code for reply in replies) == [201, 409]
        after = client.get(path).json()
        assert len(after['attempts'][0]['conclusion']['verification_history']) == 1
        assert len(after['attempts'][0]['evidence'][0]['verification_history']) == 1
    with configured_client(database_url, tmp_path, executor_credential=credential) as restarted:
        assert restarted.get(path).json() == after
