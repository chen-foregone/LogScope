from test_investigations import summary, assert_listing
import json
import pytest
from datetime import datetime, timedelta, timezone
from uuid import uuid4

from test_investigations import database_url
from test_attempts import configured_client
from test_evidence import started, material


def proposal(**changes):
    return {'format': 'controlled-analysis-v1', 'kind': 'undetermined', 'mechanism': None,
            'evidence_ids': [], 'missing': ['causal_trace'], 'expected_version': 0, 'finish': True, **changes}


def test_finish_without_evidence_is_completed_not_root_cause(database_url, tmp_path):
    credential = uuid4().hex
    with configured_client(database_url, tmp_path, executor_credential=credential) as client:
        path, before, evidence_endpoint, auth = started(client, credential)
        endpoint = evidence_endpoint.removesuffix('/evidence') + '/conclusions'
        headers = {**auth, 'Idempotency-Key': str(uuid4())}
        reply = client.post(endpoint, json=proposal(), headers=headers)
        assert reply.status_code == 201
        after = client.get(path).json()
        attempt = after['attempts'][0]
        assert after['status'] == attempt['status'] == '已完成'
        assert attempt['result'] == '未确定根因'
        assert attempt['end_reason'] == 'normal_completion'
        assert attempt['conclusion']['kind'] == 'undetermined'
        assert attempt['conclusion']['verification'] == 'insufficient'
        assert attempt['conclusions'] == [attempt['conclusion']]
        assert attempt['conclusion']['evidence_ids'] == []
        assert after['updated_at'] > before['updated_at']
        ended = datetime.fromisoformat(attempt['ended_at'].replace('Z', '+00:00'))
        expires = datetime.fromisoformat(after['retention_expires_at'].replace('Z', '+00:00'))
        assert expires == ended + timedelta(days=30)
        assert client.get(path).json() == after
        assert client.get('/api/investigations').json()['items'] == [summary(after)]
        repeated = client.post(endpoint, json=proposal(), headers=headers)
        assert repeated.status_code == 200
        assert repeated.json() == reply.json()
        assert client.get(path).json() == after
        assert client.get(evidence_endpoint.removesuffix('/evidence') + '/context', headers=auth).status_code == 403
    with configured_client(database_url, tmp_path, executor_credential=credential) as restarted:
        assert restarted.get(path).json() == after


def test_hypothesis_versions_reference_only_this_attempt(database_url, tmp_path):
    credential = uuid4().hex
    with configured_client(database_url, tmp_path, executor_credential=credential) as client:
        path, _, evidence_endpoint, auth = started(client, credential)
        saved = client.post(evidence_endpoint, json=material(), headers=auth).json()
        endpoint = evidence_endpoint.removesuffix('/evidence') + '/conclusions'
        payload = proposal(kind='hypothesis', mechanism='dependency_error_propagation',
                           evidence_ids=[saved['id']], finish=False)
        headers = {**auth, 'Idempotency-Key': str(uuid4())}
        reply = client.post(endpoint, json=payload, headers=headers)
        assert reply.status_code == 201
        first = client.get(path).json()
        assert first['status'] == '调查中'
        assert first['attempts'][0]['conclusion']['kind'] == 'hypothesis'
        assert first['attempts'][0]['conclusion']['evidence_ids'] == [saved['id']]
        assert client.post(endpoint, json=payload, headers=headers).status_code == 200
        assert client.get(path).json() == first
        foreign_path, _, foreign_endpoint, _ = started(client, credential)
        foreign = client.post(foreign_endpoint, json=material(), headers=auth).json()
        for invalid in (str(uuid4()), foreign['id']):
            bad = client.post(endpoint, json=proposal(evidence_ids=[invalid], expected_version=1),
                              headers={**auth, 'Idempotency-Key': str(uuid4())})
            assert bad.status_code == 422
            assert client.get(path).json() == first
        stale = client.post(endpoint, json=payload, headers={**auth, 'Idempotency-Key': str(uuid4())})
        assert stale.status_code == 409
        second = client.post(endpoint, json=proposal(expected_version=1),
                             headers={**auth, 'Idempotency-Key': str(uuid4())})
        assert second.status_code == 201
        after = client.get(path).json()
        assert after['status'] == '已完成'
        assert after['attempts'][0]['conclusions'][0] == reply.json()
        assert after['attempts'][0]['conclusion'] == second.json()
        assert [c['version'] for c in after['attempts'][0]['conclusions']] == [1, 2]
        assert after['updated_at'] > first['updated_at']


def test_confirm_requires_observed_propagation_not_correlation(database_url, tmp_path):
    credential = uuid4().hex
    with configured_client(database_url, tmp_path, executor_credential=credential) as client:
        path, _, evidence_endpoint, auth = started(client, credential)
        endpoint = evidence_endpoint.removesuffix('/evidence') + '/conclusions'
        ordinary = client.post(evidence_endpoint, json=material(), headers=auth).json()
        other_ordinary = client.post(evidence_endpoint, json=material(), headers=auth).json()
        correlation_only = proposal(kind='confirmed', mechanism='dependency_error_propagation',
            evidence_ids=[ordinary['id'], other_ordinary['id']], missing=[])
        assert client.post(endpoint, json=correlation_only, headers={**auth, 'Idempotency-Key': str(uuid4())}).status_code == 422
        confirmed = proposal(kind='confirmed', mechanism='dependency_error_propagation',
                             evidence_ids=[ordinary['id']], missing=[])
        assert client.post(endpoint, json=confirmed, headers={**auth, 'Idempotency-Key': str(uuid4())}).status_code == 422
        payload = material()
        payload['record']['causal_trace'] = {
            'format': 'observed-propagation-v1', 'dependency_error': 'ETIMEDOUT',
            'propagated_error': 'ETIMEDOUT', 'request_outcome': 'failed_by_dependency',
            'local_rejection': 'excluded_by_trace', 'independent_failure': 'excluded_by_trace',
            'contradiction': 'none_observed'}
        saved_reply = client.post(evidence_endpoint, json=payload, headers=auth)
        assert saved_reply.status_code == 201
        saved = saved_reply.json()
        confirmed['evidence_ids'] = [saved['id']]
        reply = client.post(endpoint, json=confirmed, headers={**auth, 'Idempotency-Key': str(uuid4())})
        assert reply.status_code == 201
        after = client.get(path).json()
        conclusion = after['attempts'][0]['conclusion']
        assert after['attempts'][0]['result'] == '已确认根因'
        assert conclusion['verification'] == 'supported_controlled_trace'
        assert conclusion['phenomenon'] == '请求失败'
        assert conclusion['causal_chain'] == '依赖调用失败，经同一次调用的错误传播路径导致请求失败。'
        assert conclusion['evidence_ids'] == [saved['id']]
        assert conclusion['alternatives'] and conclusion['contradictions']
        assert '受控' in conclusion['boundary']


def test_safety_late_writes_and_completed_replays(database_url, tmp_path, caplog):
    credential, secret = uuid4().hex, uuid4().hex
    with configured_client(database_url, tmp_path, executor_credential=credential) as client:
        path, before, evidence_endpoint, auth = started(client, credential)
        endpoint = evidence_endpoint.removesuffix('/evidence') + '/conclusions'
        headers = {**auth, 'Idempotency-Key': str(uuid4())}
        for bad in ({**proposal(), secret: secret}, {**proposal(), 'mechanism': secret},
                    {**proposal(), 'missing': [secret]}, {**proposal(), 'kind': secret + '\ud800'},
                    {**proposal(), 'evidence_ids': [secret + 'invalid']},
                    {**proposal(), 'expected_version': True}):
            reply = client.post(endpoint, content=json.dumps(bad), headers=headers)
            assert reply.status_code == 422
            assert secret not in reply.text + caplog.text
            assert client.get(path).json() == before
        dynamic = client.post('/api/investigations', json={'title': '安全校验', secret: secret},
                              headers={'Idempotency-Key': str(uuid4())})
        assert dynamic.status_code == 422
        assert secret not in dynamic.text + caplog.text, '动态字段名不得回显'
        for body in (b'[]', b'{invalid', b'x' * 65537):
            assert client.post(endpoint, content=body, headers=headers).status_code == 422
        assert client.post(endpoint, json=proposal(), headers={'Idempotency-Key': str(uuid4())}).status_code == 401
        wrong = endpoint.replace(before['attempts'][0]['id'], str(uuid4()))
        assert client.post(wrong, json=proposal(), headers=headers).status_code == 403
        assert client.post(endpoint, json=proposal(), headers=headers).status_code == 201
        after = client.get(path).json()
        assert client.post(endpoint, json=proposal(), headers={**auth, 'Idempotency-Key': str(uuid4())}).status_code == 403
        assert client.post(endpoint, json=proposal(finish=False), headers=headers).status_code == 409
        assert client.post(evidence_endpoint, json=material(), headers=auth).status_code == 403
        assert client.get(path).json() == after
        import subprocess
        from urllib.parse import urlsplit, parse_qs
        schema = parse_qs(urlsplit(database_url).query)['options'][0].split('=')[1]
        dumped = subprocess.run(['docker', 'exec', '-e', 'PGOPTIONS=-c max_parallel_workers_per_gather=0', 'logscope-01-test-pg17', 'pg_dump', '-U', 'logscope_test',
            '-d', 'logscope_test', '--data-only', '--schema', schema], capture_output=True)
        assert dumped.returncode == 0
        assert secret.encode() not in dumped.stdout and credential.encode() not in dumped.stdout, '结论持久产物泄漏原值'
        attempt = before['attempts'][0]
        start_body = {'service_id': 'orders', 'starts_at': attempt['starts_at'], 'ends_at': attempt['ends_at']}
        # 09允许新键手动新轮，但旧执行不能复活或修改新轮。
        reopened = client.post(path + '/attempts', json=start_body, headers={'Idempotency-Key': str(uuid4())})
        assert reopened.status_code == 201
        current = reopened.json()
        assert current['attempts'][0] == after['attempts'][0]
        assert current['evidence_count'] == 0
        assert client.post(endpoint, json=proposal(), headers=headers).json() == after['attempts'][0]['conclusion']
        assert client.post(evidence_endpoint, json=material(), headers=auth).status_code == 403
        assert client.get(path).json() == current


def test_unknown_causal_fields_and_unavailable_material_prevent_confirmation(database_url, tmp_path, caplog):
    credential, secret = uuid4().hex, uuid4().hex
    with configured_client(database_url, tmp_path, executor_credential=credential) as client:
        path, _, evidence_endpoint, auth = started(client, credential)
        payload = material()
        trace = {'format': 'observed-propagation-v1', 'dependency_error': 'ETIMEDOUT',
            'propagated_error': 'ETIMEDOUT', 'request_outcome': 'failed_by_dependency',
            'local_rejection': 'excluded_by_trace', 'independent_failure': 'excluded_by_trace',
            'contradiction': 'none_observed'}
        payload['record']['causal_trace'] = trace
        saved = client.post(evidence_endpoint, json=payload, headers=auth).json()
        payload['record']['causal_trace'] = {**trace, secret: secret}
        reply = client.post(evidence_endpoint, json=payload, headers=auth)
        assert reply.status_code == 422 and secret not in reply.text + caplog.text
        before = client.get(path).json()
        endpoint = evidence_endpoint.removesuffix('/evidence') + '/conclusions'
        assert client.post(endpoint, json=proposal(kind='confirmed', mechanism='dependency_error_propagation',
            evidence_ids=[saved['id']], missing=[]), headers={**auth, 'Idempotency-Key': str(uuid4())}).status_code == 422
        assert client.get(path).json() == before
        assert client.post(endpoint, json=proposal(kind='hypothesis', mechanism='dependency_error_propagation',
            evidence_ids=[saved['id']]), headers={**auth, 'Idempotency-Key': str(uuid4())}).status_code == 201
        assert client.get(path).json()['attempts'][0]['result'] == '未确定根因'


@pytest.mark.parametrize('failure_stage', ['INSERT INTO conclusions', 'UPDATE attempts', 'UPDATE controlled_executions', 'UPDATE investigations'])
def test_atomic_finish_rollback_at_each_write(database_url, tmp_path, caplog, failure_stage):
    from sqlalchemy import event
    from sqlalchemy.engine import Engine
    credential = uuid4().hex
    with configured_client(database_url, tmp_path, executor_credential=credential) as client:
        path, before, evidence_endpoint, auth = started(client, credential)
        endpoint = evidence_endpoint.removesuffix('/evidence') + '/conclusions'
        headers = {**auth, 'Idempotency-Key': str(uuid4())}
        def fail(connection, cursor, statement, parameters, context, executemany):
            if statement.startswith(failure_stage):
                cursor.execute('SELECT 1 / 0')
        event.listen(Engine, 'before_cursor_execute', fail)
        try:
            reply = client.post(endpoint, json=proposal(), headers=headers)
        finally:
            event.remove(Engine, 'before_cursor_execute', fail)
        assert reply.status_code == 503
        assert 'SELECT' not in reply.text
        assert client.get(path).json() == before
        assert client.get(evidence_endpoint.removesuffix('/evidence') + '/context', headers=auth).status_code == 200
        assert client.post(endpoint, json=proposal(), headers=headers).status_code == 201
        assert len(client.get(path).json()['attempts'][0]['conclusions']) == 1


def test_concurrent_finish_idempotency_and_version_guard(database_url, tmp_path):
    from concurrent.futures import ThreadPoolExecutor
    credential = uuid4().hex
    with configured_client(database_url, tmp_path, executor_credential=credential) as client:
        path, _, evidence_endpoint, auth = started(client, credential)
        endpoint = evidence_endpoint.removesuffix('/evidence') + '/conclusions'
        headers = {**auth, 'Idempotency-Key': str(uuid4())}
        def submit(_):
            with configured_client(database_url, tmp_path, executor_credential=credential) as other:
                return other.post(endpoint, json=proposal(), headers=headers)
        with ThreadPoolExecutor(max_workers=6) as pool:
            replies = list(pool.map(submit, range(6)))
        assert sorted(r.status_code for r in replies) == [200, 200, 200, 200, 200, 201]
        assert all(r.json() == replies[0].json() for r in replies)
        after = client.get(path).json()
        assert len(after['attempts'][0]['conclusions']) == 1
        assert after['status'] == '已完成'


def test_concurrent_different_requests_use_expected_version(database_url, tmp_path):
    from concurrent.futures import ThreadPoolExecutor
    credential = uuid4().hex
    with configured_client(database_url, tmp_path, executor_credential=credential) as client:
        path, _, evidence_endpoint, auth = started(client, credential)
        endpoint = evidence_endpoint.removesuffix('/evidence') + '/conclusions'
        def submit(_):
            with configured_client(database_url, tmp_path, executor_credential=credential) as other:
                return other.post(endpoint, json=proposal(finish=False), headers={**auth, 'Idempotency-Key': str(uuid4())})
        with ThreadPoolExecutor(max_workers=2) as pool:
            replies = list(pool.map(submit, range(2)))
        assert sorted(r.status_code for r in replies) == [201, 409]
        after = client.get(path).json()
        assert len(after['attempts'][0]['conclusions']) == 1
        assert after['status'] == '调查中'
        assert after['attempts'][0]['ended_at'] is None


def test_all_current_evidence_contradictions_cannot_be_hidden_by_selection(database_url, tmp_path):
    credential = uuid4().hex
    with configured_client(database_url, tmp_path, executor_credential=credential) as client:
        path, _, evidence_endpoint, auth = started(client, credential)
        endpoint = evidence_endpoint.removesuffix('/evidence') + '/conclusions'
        trace = {'format': 'observed-propagation-v1', 'dependency_error': 'ETIMEDOUT',
                 'propagated_error': 'ETIMEDOUT', 'request_outcome': 'failed_by_dependency',
                 'local_rejection': 'excluded_by_trace', 'independent_failure': 'excluded_by_trace',
                 'contradiction': 'none_observed'}
        payload = material()
        payload['record']['causal_trace'] = trace
        saved = client.post(evidence_endpoint, json=payload, headers=auth).json()
        payload['record']['causal_trace'] = {**trace, 'contradiction': 'unresolved'}
        assert client.post(evidence_endpoint, json=payload, headers=auth).status_code == 201
        before = client.get(path).json()
        reply = client.post(endpoint, json=proposal(kind='confirmed', mechanism='dependency_error_propagation',
            evidence_ids=[saved['id']], missing=[]), headers={**auth, 'Idempotency-Key': str(uuid4())})
        assert reply.status_code == 422
        assert client.get(path).json() == before


@pytest.mark.parametrize('finish_first', [False, True])
def test_evidence_finish_race_respects_committed_lock_order(database_url, tmp_path, finish_first):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Event
    from sqlalchemy import event
    from sqlalchemy.engine import Engine
    credential = uuid4().hex
    paused, resume, competing = Event(), Event(), Event()
    with configured_client(database_url, tmp_path, executor_credential=credential) as client:
        path, _, evidence_endpoint, auth = started(client, credential)
        endpoint = evidence_endpoint.removesuffix('/evidence') + '/conclusions'
        def before_query(connection, cursor, statement, parameters, context, executemany):
            if paused.is_set() and statement.startswith('SELECT investigations.') and 'FOR UPDATE' in statement:
                competing.set()
        def after_query(connection, cursor, statement, parameters, context, executemany):
            prefix = 'INSERT INTO conclusions' if finish_first else 'INSERT INTO evidence'
            if not paused.is_set() and statement.startswith(prefix):
                paused.set()
                assert resume.wait(15)
        def finish():
            with configured_client(database_url, tmp_path, executor_credential=credential) as other:
                return other.post(endpoint, json=proposal(), headers={**auth, 'Idempotency-Key': str(uuid4())})
        def evidence_write():
            # 两个独立ASGI事件循环模拟两个HTTP执行连接；同步DB暂停不能堵住同一portal。
            with configured_client(database_url, tmp_path, executor_credential=credential) as other:
                return other.post(evidence_endpoint, json=material(), headers=auth)
        event.listen(Engine, 'before_cursor_execute', before_query)
        event.listen(Engine, 'after_cursor_execute', after_query)
        try:
            with ThreadPoolExecutor(max_workers=2) as pool:
                first = pool.submit(finish if finish_first else evidence_write)
                try:
                    assert paused.wait(10)
                    second = pool.submit(evidence_write if finish_first else finish)
                    assert competing.wait(10)
                finally:
                    resume.set()
                first_reply, second_reply = first.result(timeout=15), second.result(timeout=15)
        finally:
            resume.set()
            event.remove(Engine, 'before_cursor_execute', before_query)
            event.remove(Engine, 'after_cursor_execute', after_query)
        assert first_reply.status_code == 201
        assert second_reply.status_code == (403 if finish_first else 201)
        after = client.get(path).json()
        assert after['status'] == '已完成'
        assert after['evidence_count'] == (0 if finish_first else 1)
        assert len(after['attempts'][0]['conclusions']) == 1


@pytest.mark.parametrize('listing', [False, True])
def test_read_snapshot_during_finish(database_url, tmp_path, listing):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Event
    from sqlalchemy import event
    from sqlalchemy.engine import Engine
    credential = uuid4().hex
    paused, resume = Event(), Event()
    with configured_client(database_url, tmp_path, executor_credential=credential) as client:
        path, before, evidence_endpoint, auth = started(client, credential)
        endpoint = evidence_endpoint.removesuffix('/evidence') + '/conclusions'
        def pause(connection, cursor, statement, parameters, context, executemany):
            if not paused.is_set() and (statement.startswith('SELECT investigations.') or statement.startswith('WITH matched_investigations AS')) and 'FOR UPDATE' not in statement:
                paused.set()
                assert resume.wait(15)
        event.listen(Engine, 'after_cursor_execute', pause)
        try:
            with ThreadPoolExecutor(max_workers=1) as pool:
                reading = pool.submit(client.get, '/api/investigations' if listing else path)
                try:
                    assert paused.wait(10)
                    assert client.post(endpoint, json=proposal(), headers={**auth, 'Idempotency-Key': str(uuid4())}).status_code == 201
                finally:
                    resume.set()
                reply = reading.result(timeout=15)
        finally:
            resume.set()
            event.remove(Engine, 'after_cursor_execute', pause)
        assert (reply.json()['items'][0] if listing else reply.json()) == (summary(before) if listing else before)
        assert client.get(path).json()['status'] == '已完成'


@pytest.mark.parametrize('change', [
    {'propagated_error': 'ECONNREFUSED'}, {'request_outcome': 'independent_failure'},
    {'local_rejection': 'unresolved'}, {'independent_failure': 'unresolved'}, {'contradiction': 'unresolved'}])
def test_causal_semantics_reject_inconsistent_or_unresolved_trace(database_url, tmp_path, change):
    credential = uuid4().hex
    with configured_client(database_url, tmp_path, executor_credential=credential) as client:
        path, _, evidence_endpoint, auth = started(client, credential)
        payload = material()
        payload['record']['causal_trace'] = {'format': 'observed-propagation-v1', 'dependency_error': 'ETIMEDOUT',
            'propagated_error': 'ETIMEDOUT', 'request_outcome': 'failed_by_dependency',
            'local_rejection': 'excluded_by_trace', 'independent_failure': 'excluded_by_trace',
            'contradiction': 'none_observed', **change}
        saved_reply = client.post(evidence_endpoint, json=payload, headers=auth)
        assert saved_reply.status_code == 201
        before = client.get(path).json()
        reply = client.post(evidence_endpoint.removesuffix('/evidence') + '/conclusions',
            json=proposal(kind='confirmed', mechanism='dependency_error_propagation',
                evidence_ids=[saved_reply.json()['id']], missing=[]), headers={**auth, 'Idempotency-Key': str(uuid4())})
        assert reply.status_code == 422
        assert client.get(path).json() == before
        hypothesis = client.post(evidence_endpoint.removesuffix('/evidence') + '/conclusions',
            json=proposal(kind='hypothesis', mechanism='dependency_error_propagation',
                evidence_ids=[saved_reply.json()['id']], missing=['causal_trace', 'alternative_exclusion']),
            headers={**auth, 'Idempotency-Key': str(uuid4())})
        assert hypothesis.status_code == 201
        assert hypothesis.json()['verification'] == 'insufficient'
        assert client.get(path).json()['attempts'][0]['result'] == '未确定根因'


@pytest.mark.parametrize('event', ['request_completed', 'request_failed', 'dependency_failed'])
@pytest.mark.parametrize('error_code', [
    'NONE', 'ETIMEDOUT', 'ECONNREFUSED', 'EACCES', 'HTTP_400', 'HTTP_401',
    'HTTP_403', 'HTTP_404', 'HTTP_429', 'HTTP_500', 'HTTP_502', 'HTTP_503'])
def test_hypothesis_requires_minimum_event_support(database_url, tmp_path, event, error_code):
    credential = uuid4().hex
    with configured_client(database_url, tmp_path, executor_credential=credential) as client:
        path, _, evidence_endpoint, auth = started(client, credential)
        payload = material()
        payload['record'].update(event=event, error_code=error_code)
        saved = client.post(evidence_endpoint, json=payload, headers=auth)
        assert saved.status_code == 201
        before = client.get(path).json()
        endpoint = evidence_endpoint.removesuffix('/evidence') + '/conclusions'
        supported = (event == 'dependency_failed' and error_code != 'NONE') or (
            event == 'request_failed' and error_code in {
                'ETIMEDOUT', 'ECONNREFUSED', 'EACCES', 'HTTP_500', 'HTTP_502', 'HTTP_503'})
        headers = {**auth, 'Idempotency-Key': str(uuid4())}
        reply = client.post(endpoint, json=proposal(kind='hypothesis',
            mechanism='dependency_error_propagation', evidence_ids=[saved.json()['id']]), headers=headers)
        assert reply.status_code == (201 if supported else 422)
        if supported:
            after = client.get(path).json()
            assert after['status'] == '已完成'
            assert after['attempts'][0]['result'] == '未确定根因'
            assert reply.json()['verification'] == 'insufficient'
            assert reply.json()['missing'] == ['causal_trace']
        else:
            assert client.get(path).json() == before
            assert client.get(evidence_endpoint.removesuffix('/evidence') + '/context', headers=auth).status_code == 200
            # 拒绝不消耗版本、幂等键或执行身份，调用方可降为未确定根因。
            assert client.post(endpoint, json=proposal(evidence_ids=[saved.json()['id']]), headers=headers).status_code == 201


@pytest.mark.parametrize('selection', ['normal_only', 'support_only', 'normal_first', 'support_first'])
def test_hypothesis_mixed_evidence_requires_selected_support(database_url, tmp_path, selection):
    credential = uuid4().hex
    with configured_client(database_url, tmp_path, executor_credential=credential) as client:
        path, _, evidence_endpoint, auth = started(client, credential)
        normal_payload = material()
        normal_payload['record'].update(event='request_completed', error_code='NONE')
        normal = client.post(evidence_endpoint, json=normal_payload, headers=auth)
        support = client.post(evidence_endpoint, json=material(), headers=auth)
        assert normal.status_code == support.status_code == 201
        normal_id, support_id = normal.json()['id'], support.json()['id']
        selected = {'normal_only': [normal_id], 'support_only': [support_id],
                    'normal_first': [normal_id, support_id], 'support_first': [support_id, normal_id]}[selection]
        before = client.get(path).json()
        reply = client.post(evidence_endpoint.removesuffix('/evidence') + '/conclusions',
            json=proposal(kind='hypothesis', mechanism='dependency_error_propagation', evidence_ids=selected),
            headers={**auth, 'Idempotency-Key': str(uuid4())})
        assert reply.status_code == (422 if selection == 'normal_only' else 201)
        if selection == 'normal_only':
            assert client.get(path).json() == before
        else:
            assert reply.json()['evidence_ids'] == selected
            assert reply.json()['verification'] == 'insufficient'


def test_unknown_event_is_not_normal_or_hypothesis_support(database_url, tmp_path):
    credential = uuid4().hex
    with configured_client(database_url, tmp_path, executor_credential=credential) as client:
        path, _, evidence_endpoint, auth = started(client, credential)
        payload = material()
        payload['record'].update(event='UNKNOWN', error_code='NONE')
        assert client.post(evidence_endpoint, json=payload, headers=auth).status_code == 422
        before = client.get(path).json()
        assert before['evidence_count'] == 0
        endpoint = evidence_endpoint.removesuffix('/evidence') + '/conclusions'
        assert client.post(endpoint, json=proposal(kind='hypothesis', mechanism='dependency_error_propagation'),
            headers={**auth, 'Idempotency-Key': str(uuid4())}).status_code == 422
        assert client.get(path).json() == before
        assert client.post(endpoint, json=proposal(),
            headers={**auth, 'Idempotency-Key': str(uuid4())}).status_code == 201


def test_clock_and_create_start_replays_return_completed_projection(database_url, tmp_path):
    from test_attempts import create
    now = datetime(2026, 11, 3, 6, 30, tzinfo=timezone.utc)
    credential = uuid4().hex
    with configured_client(database_url, tmp_path, executor_credential=credential, clock=lambda: now) as client:
        pending, create_key = create(client)
        path = '/api/investigations/' + pending['id']
        start_headers = {'Idempotency-Key': str(uuid4())}
        record = client.post(path + '/attempts', json={'service_id': 'orders'}, headers=start_headers).json()
        base = f'/api/executor/investigations/{pending["id"]}/attempts/{record["attempts"][0]["id"]}/executions'
        auth = {'Authorization': 'Bearer ' + credential}
        identity = client.get(base, headers=auth).json()['executions'][0]['id']
        assert client.post(base + '/' + identity + '/conclusions', json=proposal(),
                           headers={**auth, 'Idempotency-Key': str(uuid4())}).status_code == 201
        after = client.get(path).json()
        assert after['updated_at'] == after['attempts'][0]['ended_at'] == '2026-11-03T06:30:00Z'
        assert after['retention_expires_at'] == '2026-12-03T06:30:00Z'
        assert client.post('/api/investigations', json={'title': '启动调查'}, headers={'Idempotency-Key': create_key}).json() == after
        assert client.post(path + '/attempts', json={'service_id': 'orders'}, headers=start_headers).json() == after


@pytest.mark.parametrize('database_url', ['0005'], indirect=True)
def test_upgrade_0005_keeps_existing_evidence_and_accepts_conclusion(database_url, tmp_path):
    import os
    import subprocess
    import sys
    from sqlalchemy import create_engine, MetaData, insert
    from test_investigations import ROOT
    engine = create_engine(database_url)
    old = MetaData()
    with engine.connect() as connection:
        connection.exec_driver_sql('SET max_parallel_workers_per_gather = 0')
        old.reflect(connection)
    investigation, attempt, execution, evidence_id = [uuid4() for _ in range(4)]
    historical_attempt, historical_evidence = uuid4(), uuid4()
    now = datetime(2026, 11, 1, tzinfo=timezone.utc)
    safe = {'source': '来源代号-legacy', 'occurred_at': '2026-11-01T00:15:00Z',
        'query': {'starts_at': '2026-11-01T00:00:00Z', 'ends_at': '2026-11-01T01:00:00Z', 'fields': {}},
        'snippet': {'event': 'request_failed', 'error_code': 'ETIMEDOUT', 'fields': {}},
        'locator': None, 'redaction_note': '旧版脱敏材料'}
    with engine.begin() as connection:
        connection.execute(insert(old.tables['investigations']).values(id=investigation, request_key=uuid4(), title='05升级',
            description='', created_at=now, updated_at=now, retention_expires_at=None))
        connection.execute(insert(old.tables['attempts']).values(id=attempt, investigation_id=investigation,
            request_key=uuid4(), request_body='{}', service_id='orders', source_ids=['controlled-orders'],
            starts_at=now, ends_at=now + timedelta(hours=1), created_at=now, status='调查中'))
        connection.execute(insert(old.tables['attempts']).values(id=historical_attempt, investigation_id=investigation,
            request_key=uuid4(), request_body='{}', service_id='orders', source_ids=['controlled-orders'],
            starts_at=now, ends_at=now + timedelta(hours=1), created_at=now - timedelta(hours=1), status='已完成'))
        connection.execute(insert(old.tables['evidence']).values(id=historical_evidence, attempt_id=historical_attempt,
            created_at=now, material=safe, identity_status='legacy_unknown'))
        connection.execute(insert(old.tables['controlled_executions']).values(id=execution, attempt_id=attempt, valid=True))
        connection.execute(insert(old.tables['evidence']).values(id=evidence_id, attempt_id=attempt, created_at=now,
            material=safe, identity_status='legacy_unknown'))
    engine.dispose()
    migrated = subprocess.run([sys.executable, '-m', 'alembic', '-c', 'backend/alembic.ini', 'upgrade', 'head'],
        cwd=ROOT, env={**os.environ, 'DATABASE_URL': database_url, 'PYTHONPATH': str(ROOT / 'backend')}, capture_output=True)
    assert migrated.returncode == 0
    credential = uuid4().hex
    # 在旧轮期限内验证原有结论/证据契约，不绕过08期限判断。
    with configured_client(database_url, tmp_path, executor_credential=credential, clock=lambda: now) as client:
        path = '/api/investigations/' + str(investigation)
        detail = client.get(path).json()
        assert detail['updated_at'] == '2026-11-01T00:00:00Z'
        assert detail['attempts'][0]['conclusions'] == []
        assert detail['attempts'][-1]['evidence'][0]['id'] == str(evidence_id)
        assert detail['attempts'][0]['evidence'][0]['id'] == str(historical_evidence)
        endpoint = f'/api/executor/investigations/{investigation}/attempts/{attempt}/executions/{execution}/conclusions'
        assert client.post(endpoint, json=proposal(evidence_ids=[str(historical_evidence)]),
            headers={'Authorization': 'Bearer ' + credential, 'Idempotency-Key': str(uuid4())}).status_code == 422
        assert client.get(path).json() == detail
        assert client.post(endpoint, json=proposal(evidence_ids=[str(evidence_id)]),
            headers={'Authorization': 'Bearer ' + credential, 'Idempotency-Key': str(uuid4())}).status_code == 201
        after = client.get(path).json()
        assert after['attempts'][0] == detail['attempts'][0]
        assert after['attempts'][-1]['evidence'] == detail['attempts'][-1]['evidence']
        assert after['status'] == '已完成'
