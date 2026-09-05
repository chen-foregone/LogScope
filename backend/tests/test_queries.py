import pytest
from uuid import uuid4

from test_investigations import database_url
from test_attempts import configured_client
from test_evidence import started


class Schedule:
    def __init__(self):
        self.time = 0
        self.waits = []

    async def wait(self, seconds):
        self.waits.append(seconds)
        self.time += seconds

    async def deadline(self, operation, seconds):
        import asyncio
        return await asyncio.wait_for(operation, timeout=seconds)


class Source:
    def __init__(self):
        self.calls = []

    async def query(self, *, source_id, starts_at, ends_at):
        self.calls.append((source_id, starts_at.isoformat(), ends_at.isoformat()))
        return None


def test_query_actually_calls_snapshot_source_and_persists_safe_status(database_url, tmp_path):
    source = Source()
    credential = uuid4().hex
    with configured_client(database_url, tmp_path, executor_credential=credential,
                           query_sources={'controlled-orders': source}) as client:
        path, before, evidence, auth = started(client, credential)
        coverage = before['attempts'][0]['query_coverage']
        assert coverage['complete'] is False
        assert len(coverage['missing_sources']) == 1
        assert coverage['missing_sources'][0].startswith('来源代号-')
        assert 'controlled-orders' not in str(coverage)
        assert client.get(path).json() == before
        endpoint = evidence.removesuffix('/evidence') + '/queries'
        headers = {**auth, 'Idempotency-Key': str(uuid4())}
        reply = client.post(endpoint, headers=headers)
        assert reply.status_code == 201
        assert source.calls == [('controlled-orders', '2026-11-01T00:00:00+00:00', '2026-11-01T01:00:00+00:00')]
        query = reply.json()
        assert query['status'] == 'finished'
        assert query['sources'][0]['state'] == 'succeeded'
        assert query['sources'][0]['attempts'] == 1
        assert query['sources'][0]['source'].startswith('来源代号-')
        assert coverage['missing_sources'] == [query['sources'][0]['source']]
        after = client.get(path).json()
        assert after['status'] == '调查中'
        assert after['attempts'][0]['queries'] == [query]
        assert after['attempts'][0]['query_coverage']['complete'] is True
        assert after['evidence_count'] == 0
        assert '不表示没有异常' in after['attempts'][0]['query_coverage']['notice']
        assert after['updated_at'] > before['updated_at']
        assert client.post(endpoint, headers=headers).status_code == 200
        assert len(source.calls) == 1
        assert client.get(path).json() == after
    with configured_client(database_url, tmp_path, executor_credential=credential) as restarted:
        assert restarted.get(path).json() == after


@pytest.mark.parametrize('database_url', ['0006'], indirect=True)
def test_upgrade_0006_keeps_existing_record_and_adds_query_history(database_url, tmp_path):
    import os
    import subprocess
    import sys
    from test_investigations import ROOT
    # 旧schema安排升级前数据，业务事实仅通过升级后的HTTP API断言。
    from sqlalchemy import create_engine, text
    from datetime import datetime, timezone
    identity = uuid4()
    engine = create_engine(database_url)
    with engine.begin() as connection:
        connection.execute(text('INSERT INTO investigations (id, request_key, title, description, created_at, updated_at, retention_expires_at) '
            'VALUES (:id, :request_key, :title, :description, :now, :now, :expires)'),
            {'id': identity, 'request_key': uuid4(), 'title': '06升级保留', 'description': '',
             'now': datetime(2026, 11, 1, tzinfo=timezone.utc), 'expires': datetime(2026, 12, 1, tzinfo=timezone.utc)})
    engine.dispose()
    migrated = subprocess.run([sys.executable, '-m', 'alembic', '-c', 'backend/alembic.ini', 'upgrade', 'head'],
        cwd=ROOT, env={**os.environ, 'DATABASE_URL': database_url, 'PYTHONPATH': str(ROOT / 'backend')}, capture_output=True)
    assert migrated.returncode == 0
    with configured_client(database_url, tmp_path) as client:
        record = client.get('/api/investigations/' + str(identity)).json()
        assert record['title'] == '06升级保留'
        assert record['status'] == '待处理'
        assert record['attempts'] == []
        started_reply = client.post('/api/investigations/' + str(identity) + '/attempts', json={'service_id': 'orders'},
            headers={'Idempotency-Key': str(uuid4())})
        assert started_reply.status_code == 201
        assert started_reply.json()['attempts'][0]['queries'] == []
        assert started_reply.json()['attempts'][0]['query_coverage']['complete'] is False


def test_sync_adapter_is_rejected_before_it_can_block_deadline(database_url, tmp_path):
    called = []
    class Blocking:
        def query(self, **scope):
            called.append(True)
    credential = uuid4().hex
    with configured_client(database_url, tmp_path, executor_credential=credential,
                           query_sources={'controlled-orders': Blocking()}) as client:
        _, _, evidence, auth = started(client, credential)
        reply = client.post(evidence.removesuffix('/evidence') + '/queries',
            headers={**auth, 'Idempotency-Key': str(uuid4())})
        assert reply.status_code == 201
        assert not called
        assert reply.json()['sources'][0]['reason'] == 'adapter_error'


def test_temporary_network_retries_are_real_calls_after_two_and_five_seconds(database_url, tmp_path):
    from app.queries import SourceFailure
    schedule = Schedule()
    observed = []

    class Flaky:
        async def query(self, **scope):
            observed.append(schedule.time)
            raise SourceFailure('network')

    credential = uuid4().hex
    with configured_client(database_url, tmp_path, executor_credential=credential,
                           query_sources={'controlled-orders': Flaky()}, query_schedule=schedule) as client:
        path, _, evidence, auth = started(client, credential)
        reply = client.post(evidence.removesuffix('/evidence') + '/queries',
                            headers={**auth, 'Idempotency-Key': str(uuid4())})
        assert reply.status_code == 201
        assert observed == [0, 2, 7]
        assert schedule.waits == [2, 5]
        state = reply.json()['sources'][0]
        assert state['attempts'] == 3
        assert state['reason'] == 'network'
        assert state['state'] == 'unavailable'
        assert [f['reason'] for f in state['failures']] == ['network'] * 3
        after = client.get(path).json()
        assert after['status'] == '失败'
        assert after['attempts'][0]['end_reason'] == 'necessary_sources_unavailable'
        assert after['attempts'][0]['result'] is None
        assert after['attempts'][0]['query_coverage']['complete'] is False
        assert client.get(evidence.removesuffix('/evidence') + '/context', headers=auth).status_code == 403


def test_query_inflight_blocks_finish_and_duplicate_without_holding_database_lock(database_url, tmp_path):
    import asyncio
    from threading import Event
    from concurrent.futures import ThreadPoolExecutor
    from test_conclusions import proposal
    entered, release = Event(), Event()

    class Paused:
        async def query(self, **scope):
            entered.set()
            await asyncio.to_thread(release.wait, 10)

    credential = uuid4().hex
    with configured_client(database_url, tmp_path, executor_credential=credential,
                           query_sources={'controlled-orders': Paused()}) as client:
        path, _, evidence, auth = started(client, credential)
        endpoint = evidence.removesuffix('/evidence') + '/queries'
        headers = {**auth, 'Idempotency-Key': str(uuid4())}
        with ThreadPoolExecutor(max_workers=1) as pool:
            running = pool.submit(client.post, endpoint, headers=headers)
            try:
                assert entered.wait(5)
                assert client.get(path).json()['attempts'][0]['queries'][0]['sources'][0]['state'] == 'querying'
                duplicate = client.post(endpoint, headers=headers)
                assert duplicate.status_code == 202
                assert client.post(endpoint, headers={**auth, 'Idempotency-Key': str(uuid4())}).status_code == 409
                finishing = client.post(evidence.removesuffix('/evidence') + '/conclusions', json=proposal(),
                    headers={**auth, 'Idempotency-Key': str(uuid4())})
                assert finishing.status_code == 409
                assert client.get(path).json()['status'] == '调查中'
            finally:
                release.set()
            assert running.result(timeout=10).status_code == 201
        assert client.post(evidence.removesuffix('/evidence') + '/conclusions', json=proposal(),
            headers={**auth, 'Idempotency-Key': str(uuid4())}).status_code == 201
        after = client.get(path).json()
        assert client.post(endpoint, headers=headers).status_code == 403
        assert client.get(path).json() == after


def test_unknown_adapter_output_is_not_fabricated_query_success(database_url, tmp_path):
    class Invalid:
        async def query(self, **scope):
            return {'message': '非受控原文不得持久化', 'records': ['虚构证据']}

    credential = uuid4().hex
    with configured_client(database_url, tmp_path, executor_credential=credential,
                           query_sources={'controlled-orders': Invalid()}) as client:
        path, _, evidence, auth = started(client, credential)
        reply = client.post(evidence.removesuffix('/evidence') + '/queries',
            headers={**auth, 'Idempotency-Key': str(uuid4())})
        assert reply.status_code == 201
        assert reply.json()['sources'][0]['reason'] == 'adapter_error'
        assert reply.json()['sources'][0]['state'] == 'unavailable'
        assert '非受控原文' not in client.get(path).text


def test_default_has_no_real_connection_and_reports_not_configured(database_url, tmp_path):
    credential = uuid4().hex
    with configured_client(database_url, tmp_path, executor_credential=credential) as client:
        _, _, evidence, auth = started(client, credential)
        reply = client.post(evidence.removesuffix('/evidence') + '/queries',
            headers={**auth, 'Idempotency-Key': str(uuid4())})
        assert reply.status_code == 201
        assert reply.json()['sources'][0]['reason'] == 'not_configured'
        assert reply.json()['sources'][0]['attempts'] == 0


def test_query_rejects_client_errors_urls_and_scope_overrides(database_url, tmp_path):
    credential = uuid4().hex
    source = Source()
    with configured_client(database_url, tmp_path, executor_credential=credential,
                           query_sources={'controlled-orders': source}) as client:
        path, before, evidence, auth = started(client, credential)
        endpoint = evidence.removesuffix('/evidence') + '/queries'
        for body in ({'reason': 'network'}, {'url': 'http://127.0.0.1/private'},
                     {'source_id': 'other'}, {'starts_at': '2020-01-01T00:00:00Z'}):
            reply = client.post(endpoint, json=body, headers={**auth, 'Idempotency-Key': str(uuid4())})
            assert reply.status_code == 422
            assert client.get(path).json() == before
        assert not source.calls


@pytest.mark.parametrize('reason,retry_after,expected_waits,attempts', [
    ('permission', None, [], 1), ('credentials', None, [], 1), ('parameters', None, [], 1),
    ('rate_limited', None, [], 1), ('rate_limited', 4, [4, 4], 3),
    ('rate_limited', 50, [], 1), ('rate_limited', 0, [], 1),
    ('rate_limited', float('nan'), [], 1), ('rate_limited', '2', [], 1),
])
def test_source_error_classification_and_rate_limit_are_bounded(database_url, tmp_path, caplog,
                                                               reason, retry_after, expected_waits, attempts):
    from app.queries import SourceFailure
    schedule, observations = Schedule(), []
    secret = uuid4().hex

    class Failing:
        async def query(self, **scope):
            observations.append(schedule.time)
            failure = SourceFailure(reason, retry_after=retry_after)
            failure.args = ('https://synthetic.invalid/?token=' + secret,)
            raise failure

    credential = uuid4().hex
    with configured_client(database_url, tmp_path, executor_credential=credential,
                           query_sources={'controlled-orders': Failing()}, query_schedule=schedule) as client:
        path, _, evidence, auth = started(client, credential)
        reply = client.post(evidence.removesuffix('/evidence') + '/queries',
                            headers={**auth, 'Idempotency-Key': str(uuid4())})
        assert reply.status_code == 201
        assert len(observations) == attempts
        assert schedule.waits == expected_waits
        assert reply.json()['sources'][0]['reason'] == reason
        after = client.get(path)
        assert secret not in after.text + reply.text + caplog.text
        assert 'synthetic.invalid' not in after.text


def test_real_thirty_second_deadline_awaits_cancel_cleanup_before_failure(database_url, tmp_path):
    import asyncio
    import time
    from threading import Event
    from concurrent.futures import ThreadPoolExecutor
    from app.queries import QuerySchedule
    from test_conclusions import proposal
    cancelled, release = Event(), Event()
    observed, exited = [], []

    class DeadlineSource:
        async def query(self, **scope):
            observed.append(time.monotonic())
            if len(observed) == 1:
                try:
                    await asyncio.Event().wait()
                finally:
                    cancelled.set()
                    await asyncio.to_thread(release.wait, 10)
                    exited.append(time.monotonic())
            else:
                return None

    class NoRealRetryWait(Schedule):
        async def deadline(self, operation, seconds):
            assert seconds == 30
            return await QuerySchedule().deadline(operation, seconds)

    credential = uuid4().hex
    schedule = NoRealRetryWait()
    with configured_client(database_url, tmp_path, executor_credential=credential,
            query_sources={'controlled-orders': DeadlineSource()}, query_schedule=schedule) as client:
        path, _, evidence, auth = started(client, credential)
        endpoint = evidence.removesuffix('/evidence') + '/queries'
        with ThreadPoolExecutor(max_workers=1) as pool:
            running = pool.submit(client.post, endpoint, headers={**auth, 'Idempotency-Key': str(uuid4())})
            try:
                assert cancelled.wait(35)
                assert not running.done()
                assert not exited
                assert client.get(path).json()['status'] == '调查中'
                assert client.post(evidence.removesuffix('/evidence') + '/conclusions', json=proposal(),
                    headers={**auth, 'Idempotency-Key': str(uuid4())}).status_code == 409
            finally:
                release.set()
            reply = running.result(timeout=10)
        assert reply.status_code == 201
        assert 29.5 <= exited[0] - observed[0] < 40
        assert observed[1] >= exited[0]
        assert schedule.waits == [2]
        state = reply.json()['sources'][0]
        assert state['state'] == 'succeeded'
        assert state['attempts'] == 2
        assert state['failures'][0]['reason'] == 'timeout'
        assert state['failures'][0]['timeout_seconds'] == 30


def test_partial_sources_continue_and_all_statuses_and_evidence_survive_restart(database_url, tmp_path):
    import json
    from fastapi.testclient import TestClient
    from app.main import create_app
    from app.queries import SourceFailure
    from test_evidence import material
    from test_conclusions import proposal
    from datetime import datetime, timedelta
    credential = uuid4().hex
    config = tmp_path / 'multi.json'
    config.write_text(json.dumps({'services': [{'id': 'orders', 'name': '订单服务',
                                               'source_ids': ['controlled-orders', 'secondary']}]}))
    observed = []

    class Unavailable:
        async def query(self, **scope):
            observed.append(scope['source_id'])
            raise SourceFailure('permission')

    with TestClient(create_app(database_url=database_url, services_file=config, executor_credential=credential,
        query_sources={'controlled-orders': Source(), 'secondary': Unavailable()})) as client:
        path, _, evidence, auth = started(client, credential)
        saved = client.post(evidence, json=material(), headers=auth).json()
        reply = client.post(evidence.removesuffix('/evidence') + '/queries',
                            headers={**auth, 'Idempotency-Key': str(uuid4())})
        assert reply.status_code == 201
        assert observed == ['secondary']
        after = client.get(path).json()
        attempt = after['attempts'][0]
        assert attempt['status'] == '调查中'
        assert [s['state'] for s in attempt['queries'][0]['sources']] == ['succeeded', 'unavailable']
        assert attempt['query_coverage']['complete'] is False
        assert attempt['query_coverage']['missing_sources'] == [reply.json()['sources'][1]['source']]
        assert saved['source'] == reply.json()['sources'][0]['source']
        assert attempt['evidence'] == [saved]
        assert client.post(evidence.removesuffix('/evidence') + '/conclusions', json=proposal(evidence_ids=[saved['id']]),
            headers={**auth, 'Idempotency-Key': str(uuid4())}).status_code == 201
        completed = client.get(path).json()
        assert completed['status'] == '已完成'
        assert completed['attempts'][0]['result'] == '未确定根因'
        assert completed['attempts'][0]['query_coverage']['complete'] is False
    with TestClient(create_app(database_url=database_url, services_file=config)) as restarted:
        assert restarted.get(path).json() == completed


def test_timeout_retry_wait_is_after_call_exit_not_call_start(database_url, tmp_path):
    import asyncio
    observed = []
    class TimedSchedule(Schedule):
        async def deadline(self, operation, seconds):
            assert seconds == 30
            task = asyncio.create_task(operation)
            await entered.wait()
            entered.clear()
            self.time += 30
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass
            raise TimeoutError()
    entered = None
    schedule = TimedSchedule()
    class Slow:
        async def query(self, **scope):
            nonlocal entered
            if entered is None:
                entered = asyncio.Event()
            observed.append(schedule.time)
            entered.set()
            await asyncio.Event().wait()
    # 事件在API所在循环创建，调度通过首次让出控制权等待适配器进入。
    class Controlled(TimedSchedule):
        async def deadline(self, operation, seconds):
            nonlocal entered
            entered = asyncio.Event()
            return await super().deadline(operation, seconds)
    schedule = Controlled()
    credential = uuid4().hex
    with configured_client(database_url, tmp_path, executor_credential=credential,
                           query_sources={'controlled-orders': Slow()}, query_schedule=schedule) as client:
        _, _, evidence, auth = started(client, credential)
        reply = client.post(evidence.removesuffix('/evidence') + '/queries',
            headers={**auth, 'Idempotency-Key': str(uuid4())})
        assert reply.status_code == 201
        assert observed == [0, 32, 67]
        assert schedule.time == 97
        assert schedule.waits == [2, 5]
        assert reply.json()['sources'][0]['attempts'] == 3
        assert [f['reason'] for f in reply.json()['sources'][0]['failures']] == ['timeout'] * 3


@pytest.mark.parametrize('retry_after', [60, None])
def test_rate_limit_cannot_be_bypassed_with_new_idempotency_key(database_url, tmp_path, retry_after):
    import json
    from fastapi.testclient import TestClient
    from app.main import create_app
    from app.queries import SourceFailure
    from datetime import datetime, timedelta, timezone
    now = [datetime(2026, 11, 2, 12, tzinfo=timezone.utc)]
    calls = []

    class Limited:
        async def query(self, **scope):
            calls.append(scope['source_id'])
            raise SourceFailure('rate_limited', retry_after=retry_after)

    config = tmp_path / 'limits.json'
    config.write_text(json.dumps({'services': [{'id': 'orders', 'name': '订单服务',
        'source_ids': ['controlled-orders', 'secondary']}]}))
    credential = uuid4().hex
    with TestClient(create_app(database_url=database_url, services_file=config, executor_credential=credential,
        query_sources={'controlled-orders': Source(), 'secondary': Limited()}, clock=lambda: now[0])) as client:
        path, _, evidence, auth = started(client, credential)
        endpoint = evidence.removesuffix('/evidence') + '/queries'
        assert client.post(endpoint, headers={**auth, 'Idempotency-Key': str(uuid4())}).status_code == 201
        before = client.get(path).json()
        reply = client.post(endpoint, headers={**auth, 'Idempotency-Key': str(uuid4())})
        assert reply.status_code == 201
        assert [s['state'] for s in reply.json()['sources']] == ['succeeded', 'skipped']
        assert reply.json()['sources'][1]['reason'] == 'rate_limited'
        assert reply.json()['sources'][1]['attempts'] == 0
        assert len(calls) == 1
        after = client.get(path).json()
        assert len(after['attempts'][0]['queries']) == 2
        assert after['attempts'][0]['query_coverage']['missing_sources'] == [reply.json()['sources'][1]['source']]
        assert after['status'] == '调查中'
        now[0] += timedelta(seconds=60)
        assert client.post(endpoint, headers={**auth, 'Idempotency-Key': str(uuid4())}).status_code == 201
        assert len(calls) == (2 if retry_after else 1)


def test_rate_limit_is_shared_across_investigations(database_url, tmp_path):
    from app.queries import SourceFailure
    calls = []
    class Limited:
        async def query(self, **scope):
            calls.append(True)
            raise SourceFailure('rate_limited', retry_after=60)
    credential = uuid4().hex
    with configured_client(database_url, tmp_path, executor_credential=credential,
                           query_sources={'controlled-orders': Limited()}) as client:
        _, _, first, auth = started(client, credential)
        assert client.post(first.removesuffix('/evidence') + '/queries',
            headers={**auth, 'Idempotency-Key': str(uuid4())}).status_code == 201
        path, before, second, _ = started(client, credential)
        blocked = client.post(second.removesuffix('/evidence') + '/queries',
            headers={**auth, 'Idempotency-Key': str(uuid4())})
        assert blocked.status_code == 409
        detail = blocked.json()['detail']
        assert detail['code'] == 'sources_blocked'
        assert detail['sources'][0]['reason'] == 'rate_limited'
        assert detail['sources'][0]['attempts'] == 0
        assert detail['sources'][0]['retry_not_before'] is not None
        assert 'controlled-orders' not in blocked.text
        assert client.get(path).json() == before
        assert len(calls) == 1


@pytest.mark.parametrize('retry_after', [None, 60])
def test_all_sources_blocked_has_safe_retry_information_without_query_history(database_url, tmp_path, retry_after):
    from app.queries import SourceFailure
    source = Source()
    class Limited:
        async def query(self, **scope):
            await source.query(**scope)
            raise SourceFailure('rate_limited', retry_after=retry_after)
    credential = uuid4().hex
    with configured_client(database_url, tmp_path, executor_credential=credential,
                           query_sources={'controlled-orders': Limited()}) as client:
        _, _, first, auth = started(client, credential)
        assert client.post(first.removesuffix('/evidence') + '/queries',
            headers={**auth, 'Idempotency-Key': str(uuid4())}).status_code == 201
        path, before, second, _ = started(client, credential)
        for _ in range(2):
            reply = client.post(second.removesuffix('/evidence') + '/queries',
                headers={**auth, 'Idempotency-Key': str(uuid4())})
            assert reply.status_code == 409
            detail = reply.json()['detail']
            assert detail['code'] == 'sources_blocked'
            assert detail['sources'][0]['state'] == 'skipped'
            assert detail['sources'][0]['reason'] == 'rate_limited'
            assert bool(detail['sources'][0]['retry_not_before']) == bool(retry_after)
            assert 'controlled-orders' not in reply.text
            assert client.get(path).json() == before
        assert len(source.calls) == 1
        assert before['status'] == '调查中'
        assert before['attempts'][0]['queries'] == []
        assert len(before['attempts'][0]['query_coverage']['missing_sources']) == 1


def test_concurrent_independent_api_instances_only_call_query_once(database_url, tmp_path):
    import asyncio
    from threading import Event
    from concurrent.futures import ThreadPoolExecutor
    entered, release = Event(), Event()
    calls = []
    class Paused:
        async def query(self, **scope):
            calls.append(True)
            entered.set()
            await asyncio.to_thread(release.wait, 10)
    credential = uuid4().hex
    source = Paused()
    with configured_client(database_url, tmp_path, executor_credential=credential,
                           query_sources={'controlled-orders': source}) as client:
        path, _, evidence, auth = started(client, credential)
        endpoint = evidence.removesuffix('/evidence') + '/queries'
        headers = {**auth, 'Idempotency-Key': str(uuid4())}
        with configured_client(database_url, tmp_path, executor_credential=credential,
                               query_sources={'controlled-orders': source}) as other:
            with ThreadPoolExecutor(max_workers=1) as pool:
                running = pool.submit(client.post, endpoint, headers=headers)
                try:
                    assert entered.wait(5)
                    assert other.post(endpoint, headers=headers).status_code == 202
                    assert other.post(endpoint, headers={**auth, 'Idempotency-Key': str(uuid4())}).status_code == 409
                    assert calls == [True]
                finally:
                    release.set()
                assert running.result(timeout=10).status_code == 201
            assert other.post(endpoint, headers=headers).status_code == 200
            assert calls == [True]
            assert other.get(path).json() == client.get(path).json()


@pytest.mark.parametrize('blocked_reason', ['inflight', 'rate_limited'])
def test_cross_investigation_partial_skips_busy_source_without_double_call(database_url, tmp_path, blocked_reason):
    import asyncio
    import json
    from threading import Event
    from concurrent.futures import ThreadPoolExecutor
    from fastapi.testclient import TestClient
    from app.main import create_app
    from app.queries import SourceFailure
    entered, release = Event(), Event()
    calls = []
    healthy = Source()

    class Busy:
        async def query(self, **scope):
            calls.append(scope['source_id'])
            entered.set()
            if blocked_reason == 'rate_limited':
                raise SourceFailure('rate_limited')
            await asyncio.to_thread(release.wait, 10)

    config = tmp_path / 'cross.json'
    config.write_text(json.dumps({'services': [
        {'id': 'orders', 'name': '单源', 'source_ids': ['controlled-orders']},
        {'id': 'mixed', 'name': '混合源', 'source_ids': ['controlled-orders', 'secondary']}]}))
    credential = uuid4().hex
    def app():
        return create_app(database_url=database_url, services_file=config, executor_credential=credential,
                          query_sources={'controlled-orders': Busy(), 'secondary': healthy})
    with TestClient(app()) as client, TestClient(app()) as other:
        _, _, first, auth = started(client, credential)
        record = other.post('/api/investigations', json={'title': '跨调查partial'},
            headers={'Idempotency-Key': str(uuid4())}).json()
        path = '/api/investigations/' + record['id']
        before = other.post(path + '/attempts', json={'service_id': 'mixed'},
            headers={'Idempotency-Key': str(uuid4())}).json()
        attempt = before['attempts'][0]
        assert len(attempt['query_coverage']['missing_sources']) == 2
        base = f'/api/executor/investigations/{record["id"]}/attempts/{attempt["id"]}/executions'
        identity = other.get(base, headers=auth).json()['executions'][0]['id']
        with ThreadPoolExecutor(max_workers=1) as pool:
            running = pool.submit(client.post, first.removesuffix('/evidence') + '/queries',
                headers={**auth, 'Idempotency-Key': str(uuid4())})
            try:
                assert entered.wait(5)
                if blocked_reason == 'rate_limited':
                    assert running.result(timeout=5).status_code == 201
                single_path, single_before, single, _ = started(other, credential)
                blocked = other.post(single.removesuffix('/evidence') + '/queries',
                    headers={**auth, 'Idempotency-Key': str(uuid4())})
                assert blocked.status_code == 409
                assert blocked.json()['detail']['sources'][0]['reason'] == blocked_reason
                assert other.get(single_path).json() == single_before
                reply = other.post(base + '/' + identity + '/queries',
                    headers={**auth, 'Idempotency-Key': str(uuid4())})
                assert reply.status_code == 201
                assert [s['state'] for s in reply.json()['sources']] == ['skipped', 'succeeded']
                skipped = reply.json()['sources'][0]
                assert skipped['reason'] == blocked_reason
                assert skipped['attempts'] == 0 and skipped['failures'] == []
                assert calls == ['controlled-orders']
                assert len(healthy.calls) == 1
                after = other.get(path).json()
                assert after['status'] == '调查中'
                assert after['attempts'][0]['query_coverage']['missing_sources'] == [skipped['source']]
                assert 'controlled-orders' not in str(after['attempts'][0]['query_coverage'])
            finally:
                release.set()
            assert running.result(timeout=10).status_code == 201


def test_unqueried_normal_finish_keeps_all_snapshot_sources_missing(database_url, tmp_path):
    from test_conclusions import proposal
    credential = uuid4().hex
    with configured_client(database_url, tmp_path, executor_credential=credential) as client:
        path, before, evidence, auth = started(client, credential)
        missing = before['attempts'][0]['query_coverage']['missing_sources']
        assert len(missing) == 1
        assert client.post(evidence.removesuffix('/evidence') + '/conclusions', json=proposal(),
            headers={**auth, 'Idempotency-Key': str(uuid4())}).status_code == 201
        after = client.get(path).json()
        assert after['status'] == '已完成'
        assert after['attempts'][0]['result'] == '未确定根因'
        assert after['attempts'][0]['queries'] == []
        assert after['attempts'][0]['query_coverage']['missing_sources'] == missing
        assert after['attempts'][0]['query_coverage']['complete'] is False


def test_cancellation_suppressing_adapter_cannot_claim_success_after_deadline(database_url, tmp_path):
    import asyncio
    from app.queries import QuerySchedule
    observed = []

    class Suppresses:
        async def query(self, **scope):
            observed.append('called')
            try:
                await asyncio.Event().wait()
            except asyncio.CancelledError:
                return None

    class FastDeadline(Schedule):
        async def deadline(self, operation, seconds):
            assert seconds == 30
            return await QuerySchedule().deadline(operation, 0.01)

    credential = uuid4().hex
    with configured_client(database_url, tmp_path, executor_credential=credential,
        query_sources={'controlled-orders': Suppresses()}, query_schedule=FastDeadline()) as client:
        path, _, evidence, auth = started(client, credential)
        reply = client.post(evidence.removesuffix('/evidence') + '/queries',
            headers={**auth, 'Idempotency-Key': str(uuid4())})
        assert reply.status_code == 201
        assert observed == ['called', 'called', 'called']
        assert reply.json()['sources'][0]['reason'] == 'timeout'
        assert client.get(path).json()['status'] == '失败'


@pytest.mark.parametrize('stage', ['INSERT INTO controlled_queries', 'UPDATE attempts',
                                  'UPDATE controlled_executions', 'UPDATE investigations'])
def test_query_transaction_fault_cannot_publish_false_terminal(database_url, tmp_path, stage):
    from sqlalchemy import event
    from sqlalchemy.engine import Engine
    from app.queries import SourceFailure
    from test_evidence import material
    from datetime import datetime, timedelta
    credential = uuid4().hex

    class Denied:
        async def query(self, **scope):
            raise SourceFailure('permission')

    with configured_client(database_url, tmp_path, executor_credential=credential,
                           query_sources={'controlled-orders': Denied()}) as client:
        path, before, evidence, auth = started(client, credential)
        saved = client.post(evidence, json=material(), headers=auth).json()
        endpoint = evidence.removesuffix('/evidence') + '/queries'
        # 统一在最终查询状态写后触发相应事务语句；PG自身报错使整笔事务回滚。
        terminal = [False]
        def mark(connection, cursor, statement, parameters, context, executemany):
            if statement.startswith('UPDATE controlled_queries') and parameters.get('active') is False:
                terminal[0] = True
            if statement.startswith(stage) and (stage == 'INSERT INTO controlled_queries' or terminal[0]):
                cursor.execute('SELECT 1 / 0')
        event.listen(Engine, 'before_cursor_execute', mark)
        try:
            reply = client.post(endpoint, headers={**auth, 'Idempotency-Key': str(uuid4())})
        finally:
            event.remove(Engine, 'before_cursor_execute', mark)
        assert reply.status_code == 503
        after = client.get(path).json()
        assert after['status'] == '调查中'
        assert after['attempts'][0]['ended_at'] is None
        assert after['retention_expires_at'] is None
        assert after['attempts'][0]['evidence'] == [saved]
        assert client.get(evidence.removesuffix('/evidence') + '/context', headers=auth).status_code == 200
        if stage != 'INSERT INTO controlled_queries':
            assert after['attempts'][0]['queries'][0]['status'] == 'running'
            assert client.post(endpoint, headers={**auth, 'Idempotency-Key': str(uuid4())}).status_code == 409


def test_failure_keeps_evidence_conclusion_history_and_thirty_day_retention(database_url, tmp_path):
    from app.queries import SourceFailure
    from test_evidence import material
    from test_conclusions import proposal
    from datetime import datetime, timedelta
    credential = uuid4().hex

    class Denied:
        async def query(self, **scope):
            raise SourceFailure('credentials')

    with configured_client(database_url, tmp_path, executor_credential=credential,
                           query_sources={'controlled-orders': Denied()}) as client:
        path, _, evidence, auth = started(client, credential)
        saved = client.post(evidence, json=material(), headers=auth).json()
        conclusion = client.post(evidence.removesuffix('/evidence') + '/conclusions',
            json=proposal(evidence_ids=[saved['id']], finish=False),
            headers={**auth, 'Idempotency-Key': str(uuid4())}).json()
        assert client.post(evidence.removesuffix('/evidence') + '/queries',
            headers={**auth, 'Idempotency-Key': str(uuid4())}).status_code == 201
        after = client.get(path).json()
        attempt = after['attempts'][0]
        assert attempt['status'] == '失败'
        assert attempt['evidence'] == [saved]
        assert attempt['conclusions'] == [conclusion]
        assert attempt['result'] is None
        assert datetime.fromisoformat(after['retention_expires_at'].replace('Z', '+00:00')) == (
            datetime.fromisoformat(attempt['ended_at'].replace('Z', '+00:00')) + timedelta(days=30))
        assert client.post(evidence, json=material(), headers=auth).status_code == 403
        assert client.post(evidence.removesuffix('/evidence') + '/conclusions', json=proposal(expected_version=1),
            headers={**auth, 'Idempotency-Key': str(uuid4())}).status_code == 403
        assert client.get(path).json() == after
    with configured_client(database_url, tmp_path) as restarted:
        assert restarted.get(path).json() == after
