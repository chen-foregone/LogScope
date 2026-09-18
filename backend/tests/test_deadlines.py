from datetime import datetime, timedelta, timezone
from uuid import uuid4

from test_investigations import database_url
from test_attempts import configured_client
from test_evidence import started, material
from test_conclusions import proposal
import pytest


@pytest.mark.parametrize('finish', [True, False])
@pytest.mark.parametrize('crossing', ['insert', 'last_write'])
def test_finish_crossing_deadline_inside_transaction_rolls_back_material(database_url, tmp_path, finish, crossing):
    from sqlalchemy import event
    from sqlalchemy.engine import Engine
    now = [datetime(2026, 11, 3, 12, tzinfo=timezone.utc)]
    credential = uuid4().hex
    with configured_client(database_url, tmp_path, executor_credential=credential, clock=lambda: now[0],
                           deadline_schedule=ManualTicks()) as client:
        path, _, endpoint, auth = started(client, credential)
        now[0] += timedelta(seconds=599)
        def advance(connection, cursor, statement, parameters, context, executemany):
            if (crossing == 'insert' and statement.startswith('INSERT INTO conclusions')
                    or crossing == 'last_write' and statement.startswith('UPDATE investigations')):
                now[0] = datetime(2026, 11, 3, 12, 10, 1, tzinfo=timezone.utc)
        event.listen(Engine, 'before_cursor_execute', advance)
        try:
            reply = client.post(endpoint.removesuffix('/evidence') + '/conclusions', json=proposal(finish=finish),
                                headers={**auth, 'Idempotency-Key': str(uuid4())})
        finally:
            event.remove(Engine, 'before_cursor_execute', advance)
        assert reply.status_code == 403
        after = client.get(path).json()
        assert after['status'] == '失败'
        assert after['attempts'][0]['conclusions'] == []
        assert after['attempts'][0]['cancel_requested_at'] is None
        assert after['attempts'][0]['timeout_requested_at'] == '2026-11-03T12:10:01Z'


@pytest.mark.parametrize('entry', ['evidence', 'invalid_evidence', 'cancel'])
def test_pending_write_crossing_deadline_is_discarded(database_url, tmp_path, entry):
    from sqlalchemy import event
    from sqlalchemy.engine import Engine
    now = [datetime(2026, 11, 3, 12, tzinfo=timezone.utc)]
    credential = uuid4().hex
    with configured_client(database_url, tmp_path, executor_credential=credential, clock=lambda: now[0],
                           deadline_schedule=ManualTicks()) as client:
        path, before, endpoint, auth = started(client, credential)
        now[0] += timedelta(seconds=599)
        def advance(connection, cursor, statement, parameters, context, executemany):
            if (entry == 'evidence' and statement.startswith('INSERT INTO evidence')
                    or entry == 'invalid_evidence' and statement.startswith('UPDATE attempts') and 'evidence_unavailable' in parameters
                    or entry == 'cancel' and statement.startswith('UPDATE attempts') and 'cancel_requested_at' in parameters):
                now[0] = datetime(2026, 11, 3, 12, 10, 1, tzinfo=timezone.utc)
        event.listen(Engine, 'before_cursor_execute', advance)
        try:
            reply = (client.post(path + '/attempts/' + before['attempts'][0]['id'] + '/cancel')
                     if entry == 'cancel' else client.post(endpoint, json={} if entry == 'invalid_evidence' else material(), headers=auth))
        finally:
            event.remove(Engine, 'before_cursor_execute', advance)
        assert reply.status_code == (200 if entry == 'cancel' else 403)
        after = client.get(path).json()
        attempt = after['attempts'][0]
        assert after['status'] == '失败'
        assert attempt['evidence'] == []
        assert attempt['evidence_notices'] == before['attempts'][0]['evidence_notices']
        assert attempt['cancel_requested_at'] is None
        assert attempt['timeout_requested_at'] == '2026-11-03T12:10:01Z'


@pytest.mark.parametrize('entry', ['evidence', 'conclusions', 'queries'])
def test_replay_crossing_deadline_during_lookup_is_rejected(database_url, tmp_path, entry):
    from sqlalchemy import event
    from sqlalchemy.engine import Engine
    now = [datetime(2026, 11, 3, 12, tzinfo=timezone.utc)]
    credential = uuid4().hex
    class Source:
        async def query(self, **scope):
            return None
    with configured_client(database_url, tmp_path, executor_credential=credential, clock=lambda: now[0],
                           deadline_schedule=ManualTicks(), query_sources={'controlled-orders': Source()}) as client:
        path, _, endpoint, auth = started(client, credential)
        payload = material()
        payload['identity'] = {'kind': 'record', 'record_id': uuid4().hex}
        target = endpoint.removesuffix('/evidence') + '/' + entry
        headers = {**auth, 'Idempotency-Key': str(uuid4())}
        body = {'json': payload} if entry == 'evidence' else {'json': proposal(finish=False)} if entry == 'conclusions' else {}
        assert client.post(target, headers=headers, **body).status_code == 201
        before = client.get(path).json()
        now[0] += timedelta(seconds=599)
        table = 'controlled_queries' if entry == 'queries' else entry
        def advance(connection, cursor, statement, parameters, context, executemany):
            if statement.startswith('SELECT ' + table + '.id'):
                now[0] = datetime(2026, 11, 3, 12, 10, 1, tzinfo=timezone.utc)
        event.listen(Engine, 'before_cursor_execute', advance)
        try:
            reply = client.post(target, headers=headers, **body)
        finally:
            event.remove(Engine, 'before_cursor_execute', advance)
        assert reply.status_code == 403
        after = client.get(path).json()
        assert after['status'] == '失败'
        for key in ('evidence', 'conclusions', 'queries'):
            assert after['attempts'][0][key] == before['attempts'][0][key]


@pytest.mark.parametrize('entry', ['evidence', 'conclusions', 'cancel'])
def test_late_finalization_confirmation_failure_preserves_fence_not_pending_writes(database_url, tmp_path, entry):
    from sqlalchemy import event
    from sqlalchemy.engine import Engine
    now = [datetime(2026, 11, 3, 12, tzinfo=timezone.utc)]
    credential = uuid4().hex
    with configured_client(database_url, tmp_path, executor_credential=credential, clock=lambda: now[0],
                           deadline_schedule=ManualTicks()) as client:
        path, before, endpoint, auth = started(client, credential)
        now[0] += timedelta(seconds=599)
        def advance_and_fail(connection, cursor, statement, parameters, context, executemany):
            if (statement.startswith('INSERT INTO ' + entry)
                    or entry == 'cancel' and statement.startswith('UPDATE attempts') and 'cancel_requested_at' in parameters):
                now[0] = datetime(2026, 11, 3, 12, 10, 1, tzinfo=timezone.utc)
            if statement.startswith('UPDATE attempts') and parameters.get('status') == '失败':
                cursor.execute('SELECT 1 / 0')
        event.listen(Engine, 'before_cursor_execute', advance_and_fail)
        try:
            if entry == 'cancel':
                reply = client.post(path + '/attempts/' + before['attempts'][0]['id'] + '/cancel')
            else:
                reply = client.post(endpoint.removesuffix('/evidence') + '/' + entry,
                    json=material() if entry == 'evidence' else proposal(),
                    headers={**auth, 'Idempotency-Key': str(uuid4())})
        finally:
            event.remove(Engine, 'before_cursor_execute', advance_and_fail)
        assert reply.status_code == 503
        attempt = client.get(path).json()['attempts'][0]
        assert attempt['status'] == '调查中'
        assert attempt['execution_phase'] == 'timeout_stopping'
        assert attempt['cancel_requested_at'] is None
        assert attempt['stop_confirmed_at'] is None
        assert attempt['evidence'] == attempt['conclusions'] == []
        assert client.get(endpoint.removesuffix('/evidence') + '/context', headers=auth).status_code == 403


def test_query_admission_crossing_deadline_does_not_start_source(database_url, tmp_path):
    from sqlalchemy import event
    from sqlalchemy.engine import Engine
    now = [datetime(2026, 11, 3, 12, tzinfo=timezone.utc)]
    calls = []
    class Source:
        async def query(self, **scope):
            calls.append(True)
    credential = uuid4().hex
    with configured_client(database_url, tmp_path, executor_credential=credential, clock=lambda: now[0],
                           deadline_schedule=ManualTicks(), query_sources={'controlled-orders': Source()}) as client:
        path, _, endpoint, auth = started(client, credential)
        now[0] += timedelta(seconds=599)
        def advance(connection, cursor, statement, parameters, context, executemany):
            if statement.startswith('INSERT INTO controlled_queries'):
                now[0] = datetime(2026, 11, 3, 12, 10, 1, tzinfo=timezone.utc)
        event.listen(Engine, 'before_cursor_execute', advance)
        try:
            reply = client.post(endpoint.removesuffix('/evidence') + '/queries',
                                headers={**auth, 'Idempotency-Key': str(uuid4())})
        finally:
            event.remove(Engine, 'before_cursor_execute', advance)
        assert reply.status_code == 403
        assert calls == []
        after = client.get(path).json()
        assert after['status'] == '失败'
        assert after['attempts'][0]['queries'] == []


def test_query_result_crossing_deadline_at_final_write_is_not_accepted(database_url, tmp_path):
    from sqlalchemy import event
    from sqlalchemy.engine import Engine
    now = [datetime(2026, 11, 3, 12, tzinfo=timezone.utc)]
    class Source:
        async def query(self, **scope):
            return None
    credential = uuid4().hex
    with configured_client(database_url, tmp_path, executor_credential=credential, clock=lambda: now[0],
                           deadline_schedule=ManualTicks(), query_sources={'controlled-orders': Source()}) as client:
        path, _, endpoint, auth = started(client, credential)
        now[0] += timedelta(seconds=599)
        def advance(connection, cursor, statement, parameters, context, executemany):
            if statement.startswith('UPDATE controlled_queries') and parameters.get('active') is False:
                now[0] = datetime(2026, 11, 3, 12, 10, 1, tzinfo=timezone.utc)
        event.listen(Engine, 'before_cursor_execute', advance)
        try:
            reply = client.post(endpoint.removesuffix('/evidence') + '/queries',
                                headers={**auth, 'Idempotency-Key': str(uuid4())})
        finally:
            event.remove(Engine, 'before_cursor_execute', advance)
        assert reply.status_code == 403
        after = client.get(path).json()
        assert after['status'] == '失败'
        assert after['attempts'][0]['queries'][0]['status'] == 'timed_out'


def test_lifespan_repeated_cancel_waits_for_database_round_before_dispose(database_url, tmp_path):
    import asyncio
    from threading import Event
    from sqlalchemy import event
    from sqlalchemy.engine import Engine
    from app.main import create_app
    entered, release, exited = Event(), Event(), Event()
    disposed = []
    class Schedule:
        async def wait(self):
            self.coordinator = asyncio.current_task()
            await asyncio.sleep(0)
    schedule = Schedule()
    def block(connection, cursor, statement, parameters, context, executemany):
        if statement.startswith('SELECT attempts.id, attempts.investigation_id'):
            entered.set()
            try:
                assert release.wait(10), '外部调度必须释放真实数据库轮'
            finally:
                exited.set()
    def observe_dispose(engine):
        disposed.append(exited.is_set())
    async def exercise():
        app = create_app(database_url=database_url, deadline_schedule=schedule)
        lifespan = app.router.lifespan_context(app)
        await lifespan.__aenter__()
        assert await asyncio.to_thread(entered.wait, 5)
        closing = asyncio.create_task(lifespan.__aexit__(None, None, None))
        try:
            while not schedule.coordinator.cancelling():
                await asyncio.sleep(0)
            for _ in range(3):
                schedule.coordinator.cancel()
                await asyncio.sleep(0)
            assert not schedule.coordinator.done(), '数据库线程未退出，coordinator不能伪done'
            assert not closing.done()
            assert disposed == []
        finally:
            release.set()
            await closing
        assert schedule.coordinator.cancelled()
        assert exited.is_set()
        assert disposed == [True]
    event.listen(Engine, 'before_cursor_execute', block)
    event.listen(Engine, 'engine_disposed', observe_dispose)
    try:
        asyncio.run(exercise())
    finally:
        release.set()
        event.remove(Engine, 'before_cursor_execute', block)
        event.remove(Engine, 'engine_disposed', observe_dispose)


class ManualTicks:
    async def wait(self):
        import asyncio
        await asyncio.Future()


@pytest.mark.parametrize('entry', ['evidence', 'conclusions', 'queries'])
def test_expired_write_boundaries_fence_replays_without_accepting_new_work(database_url, tmp_path, entry):
    from app.queries import QuerySchedule
    calls = []
    now = [datetime(2026, 11, 3, 12, tzinfo=timezone.utc)]
    class Source:
        async def query(self, **scope):
            calls.append(True)
    credential = uuid4().hex
    with configured_client(database_url, tmp_path, executor_credential=credential, clock=lambda: now[0],
                           deadline_schedule=ManualTicks(), query_sources={'controlled-orders': Source()}) as client:
        path, _, endpoint, auth = started(client, credential)
        payload = material()
        payload['identity'] = {'kind': 'record', 'record_id': uuid4().hex}
        headers = {**auth, 'Idempotency-Key': str(uuid4())}
        target = endpoint.removesuffix('/evidence') + '/' + entry
        body = {'json': payload} if entry == 'evidence' else {'json': proposal(finish=False)} if entry == 'conclusions' else {}
        assert client.post(target, headers=headers, **body).status_code == 201
        before = client.get(path).json()
        now[0] += timedelta(minutes=10)
        assert client.get(path).json() == before
        assert client.post(target, headers=headers, **body).status_code == 403
        after = client.get(path).json()
        assert after['status'] == '失败'
        for key in ('evidence', 'conclusions', 'queries'):
            assert after['attempts'][0][key] == before['attempts'][0][key]
        assert len(calls) == (1 if entry == 'queries' else 0)


@pytest.mark.parametrize('entry', ['evidence', 'cancel'])
def test_timeout_confirmation_failure_cannot_roll_back_persistent_fence(database_url, tmp_path, entry):
    from sqlalchemy import event
    from sqlalchemy.engine import Engine
    now = [datetime(2026, 11, 3, 12, tzinfo=timezone.utc)]
    credential = uuid4().hex
    with configured_client(database_url, tmp_path, executor_credential=credential, clock=lambda: now[0],
                           deadline_schedule=ManualTicks()) as client:
        path, _, endpoint, auth = started(client, credential)
        def fail(connection, cursor, statement, parameters, context, executemany):
            if statement.startswith('UPDATE attempts') and parameters.get('status') == '失败':
                cursor.execute('SELECT 1 / 0')
        now[0] += timedelta(minutes=10)
        event.listen(Engine, 'before_cursor_execute', fail)
        try:
            if entry == 'evidence':
                reply = client.post(endpoint, json=material(), headers=auth)
            else:
                attempt_id = client.get(path).json()['attempts'][0]['id']
                reply = client.post(path + '/attempts/' + attempt_id + '/cancel')
            assert reply.status_code == 503
        finally:
            event.remove(Engine, 'before_cursor_execute', fail)
        after = client.get(path).json()
        assert after['status'] == '调查中'
        assert after['attempts'][0]['execution_phase'] == 'timeout_stopping'
        assert after['attempts'][0]['timeout_requested_at']
        assert after['attempts'][0]['stop_confirmed_at'] is None
        assert after['retention_expires_at'] is None
        assert client.get(endpoint.removesuffix('/evidence') + '/context', headers=auth).status_code == 403
        assert client.post(endpoint, json=material(), headers=auth).status_code == 403


def test_cancel_owned_before_deadline_is_not_reclassified_after_deadline(database_url, tmp_path):
    from app.attempts import ControlledExecutionAdapter
    class Unknown(ControlledExecutionAdapter):
        pass
    now = [datetime(2026, 11, 3, 12, tzinfo=timezone.utc)]
    credential = uuid4().hex
    with configured_client(database_url, tmp_path, executor_credential=credential, clock=lambda: now[0],
                           execution_adapter=Unknown()) as client:
        path, before, _, _ = started(client, credential)
        cancel = path + '/attempts/' + before['attempts'][0]['id'] + '/cancel'
        now[0] += timedelta(seconds=599)
        stopping = client.post(cancel).json()
        now[0] += timedelta(seconds=2)
        assert client.post(cancel).json() == stopping
        assert stopping['attempts'][0]['timeout_requested_at'] is None
        assert stopping['attempts'][0]['execution_phase'] == 'stopping'



def test_expired_evidence_is_rejected_but_timeout_fence_and_confirmation_persist(database_url, tmp_path):
    now = [datetime(2026, 11, 3, 12, tzinfo=timezone.utc)]
    credential = uuid4().hex
    with configured_client(database_url, tmp_path, executor_credential=credential, clock=lambda: now[0]) as client:
        path, before, endpoint, auth = started(client, credential)
        saved = client.post(endpoint, json=material(), headers=auth).json()
        now[0] += timedelta(minutes=10)
        assert client.post(endpoint, json=material(), headers=auth).status_code == 403
        after = client.get(path).json()
        attempt = after['attempts'][0]
        assert after['status'] == '失败'
        assert attempt['timeout_requested_at'] == '2026-11-03T12:10:00Z'
        assert attempt['end_reason'] == 'attempt_deadline_exceeded'
        assert attempt['result'] is None
        assert attempt['evidence'] == [saved]
        assert attempt['stop_confirmation'] == 'controlled_queries_exited_and_writes_fenced'
        assert attempt['ended_at'] == after['updated_at'] == '2026-11-03T12:10:00Z'
        assert after['retention_expires_at'] == '2026-12-03T12:10:00Z'
        assert client.get(path).json() == after


def test_finish_at_deadline_cannot_beat_timeout_but_committed_completion_is_final(database_url, tmp_path):
    now = [datetime(2026, 11, 3, 12, tzinfo=timezone.utc)]
    credential = uuid4().hex
    with configured_client(database_url, tmp_path, executor_credential=credential, clock=lambda: now[0]) as client:
        path, _, endpoint, auth = started(client, credential)
        path2, _, endpoint2, _ = started(client, credential)
        headers = {**auth, 'Idempotency-Key': str(uuid4())}
        assert client.post(endpoint + '/../conclusions', json=proposal(), headers=headers).status_code == 201
        completed = client.get(path).json()
        now[0] += timedelta(minutes=10)
        assert client.post(endpoint2.removesuffix('/evidence') + '/conclusions', json=proposal(),
                           headers={**auth, 'Idempotency-Key': str(uuid4())}).status_code == 403
        expired = client.get(path2).json()
        assert expired['status'] == '失败'
        assert expired['attempts'][0]['conclusions'] == []
        cancel = path2 + '/attempts/' + expired['attempts'][0]['id'] + '/cancel'
        assert client.post(cancel).json() == expired
        assert client.post(endpoint + '/../conclusions', json=proposal(), headers=headers).status_code == 200
        assert client.get(path).json() == completed


def test_cancel_at_deadline_cannot_acquire_user_cancel_ownership(database_url, tmp_path):
    from app.attempts import ControlledExecutionAdapter
    class Unknown(ControlledExecutionAdapter):
        pass
    now = [datetime(2026, 11, 3, 12, tzinfo=timezone.utc)]
    credential = uuid4().hex
    with configured_client(database_url, tmp_path, executor_credential=credential, clock=lambda: now[0],
                           execution_adapter=Unknown()) as client:
        path, before, endpoint, auth = started(client, credential)
        cancel = path + '/attempts/' + before['attempts'][0]['id'] + '/cancel'
        now[0] += timedelta(minutes=10)
        after = client.post(cancel).json()
        attempt = after['attempts'][0]
        assert after['status'] == '调查中'
        assert attempt['execution_phase'] == 'timeout_stopping'
        assert attempt['cancel_requested_at'] is None
        assert attempt['timeout_requested_at'] == '2026-11-03T12:10:00Z'
        assert attempt['stop_confirmed_at'] is None
        assert after['retention_expires_at'] is None
        assert client.post(cancel).json() == after
        assert client.post(endpoint, json=material(), headers=auth).status_code == 403


def test_idle_attempt_times_out_without_http_request_and_get_remains_read_only(database_url, tmp_path):
    import asyncio
    from threading import Event
    class Ticks:
        def __init__(self):
            self.tick, self.ready = Event(), Event()
        async def wait(self):
            self.ready.set()
            while not self.tick.is_set():
                await asyncio.sleep(0.01)
            self.tick.clear()
            self.ready.clear()
    ticks = Ticks()
    now = [datetime(2026, 11, 3, 12, tzinfo=timezone.utc)]
    credential = uuid4().hex
    with configured_client(database_url, tmp_path, executor_credential=credential, clock=lambda: now[0],
                           deadline_schedule=ticks) as client:
        assert ticks.ready.wait(5)
        path, before, _, _ = started(client, credential)
        now[0] += timedelta(minutes=10)
        assert client.get(path).json() == before, 'GET不能协调超时或改变时间'
        ticks.tick.set()
        # 就绪事件只有下一协调轮完成后才能再次置位。
        import time
        limit = time.monotonic() + 5
        while client.get(path).json()['status'] == '调查中':
            assert time.monotonic() < limit
        after = client.get(path).json()
        assert after['status'] == '失败'
        assert after['attempts'][0]['end_reason'] == 'attempt_deadline_exceeded'
        assert client.get(path).json() == after


@pytest.mark.parametrize('cancel_first', [False, True])
def test_inflight_timeout_waits_for_real_exit_and_preserves_cancel_ownership(database_url, tmp_path, cancel_first):
    import asyncio
    from threading import Event
    from concurrent.futures import ThreadPoolExecutor
    entered, cleanup, release = Event(), Event(), Event()
    now = [datetime(2026, 11, 3, 12, tzinfo=timezone.utc)]
    class Source:
        async def query(self, **scope):
            entered.set()
            try:
                await asyncio.Future()
            except asyncio.CancelledError:
                cleanup.set()
                await asyncio.to_thread(release.wait, 10)
                return None
    credential = uuid4().hex
    with configured_client(database_url, tmp_path, executor_credential=credential, clock=lambda: now[0],
                           query_sources={'controlled-orders': Source()}) as client:
        path, before, endpoint, auth = started(client, credential)
        headers = {**auth, 'Idempotency-Key': str(uuid4())}
        with ThreadPoolExecutor(max_workers=1) as pool:
            pending = pool.submit(client.post, endpoint.removesuffix('/evidence') + '/queries', headers=headers)
            try:
                assert entered.wait(5)
                if cancel_first:
                    now[0] += timedelta(seconds=599)
                    client.post(path + '/attempts/' + before['attempts'][0]['id'] + '/cancel')
                    now[0] += timedelta(seconds=1)
                else:
                    now[0] += timedelta(minutes=10)
                assert cleanup.wait(3), '总时限必须打断在途请求，不等完整30秒'
                waiting = client.get(path).json()
                attempt = waiting['attempts'][0]
                assert waiting['status'] == '调查中'
                assert attempt['execution_phase'] == ('stopping' if cancel_first else 'timeout_stopping')
                assert attempt['stop_confirmed_at'] is None
                assert waiting['retention_expires_at'] is None
                assert not pending.done()
                assert client.post(endpoint, json=material(), headers=auth).status_code == 403
                cancel = path + '/attempts/' + attempt['id'] + '/cancel'
                assert bool(client.post(cancel).json()['attempts'][0]['cancel_requested_at']) == cancel_first
            finally:
                release.set()
            assert pending.result(10).status_code == 403
        after = client.get(path).json()
        assert after['status'] == ('已取消' if cancel_first else '失败')
        assert after['attempts'][0]['queries'][0]['status'] == ('canceled' if cancel_first else 'timed_out')
        assert after['attempts'][0]['end_reason'] == ('user_canceled' if cancel_first else 'attempt_deadline_exceeded')


def test_query_near_deadline_uses_remaining_budget_not_full_thirty_seconds(database_url, tmp_path):
    from app.queries import QuerySchedule
    observed = []
    now = [datetime(2026, 11, 3, 12, tzinfo=timezone.utc)]
    class Schedule(QuerySchedule):
        async def deadline(self, operation, seconds):
            observed.append(seconds)
            return await super().deadline(operation, seconds)
    class Source:
        async def query(self, **scope):
            return None
    credential = uuid4().hex
    with configured_client(database_url, tmp_path, executor_credential=credential, clock=lambda: now[0],
                           query_sources={'controlled-orders': Source()}, query_schedule=Schedule()) as client:
        path, before, endpoint, auth = started(client, credential)
        now[0] += timedelta(seconds=597)
        assert client.post(endpoint.removesuffix('/evidence') + '/queries',
                           headers={**auth, 'Idempotency-Key': str(uuid4())}).status_code == 201
        assert len(observed) == 1 and 2.5 < observed[0] <= 3
        assert client.get(path).json()['attempts'][0]['deadline_at'] == before['attempts'][0]['deadline_at']


def test_retry_wait_is_cut_off_by_shared_deadline_without_another_source_call(database_url, tmp_path):
    import asyncio
    from threading import Event
    from concurrent.futures import ThreadPoolExecutor
    from app.queries import SourceFailure, QuerySchedule
    waiting, exited = Event(), Event()
    calls, delays = [], []
    now = [datetime(2026, 11, 3, 12, tzinfo=timezone.utc)]
    class Source:
        async def query(self, **scope):
            calls.append(now[0])
            raise SourceFailure('network')
    class Schedule(QuerySchedule):
        async def wait(self, seconds):
            delays.append(seconds)
            waiting.set()
            try:
                await asyncio.Future()
            finally:
                exited.set()
    credential = uuid4().hex
    with configured_client(database_url, tmp_path, executor_credential=credential, clock=lambda: now[0],
                           query_sources={'controlled-orders': Source()}, query_schedule=Schedule()) as client:
        path, before, endpoint, auth = started(client, credential)
        now[0] += timedelta(seconds=599)
        with ThreadPoolExecutor(max_workers=1) as pool:
            pending = pool.submit(client.post, endpoint.removesuffix('/evidence') + '/queries',
                                  headers={**auth, 'Idempotency-Key': str(uuid4())})
            assert waiting.wait(5)
            try:
                assert len(delays) == 1 and 0 < delays[0] <= 1
            finally:
                now[0] += timedelta(seconds=1)
            assert pending.result(5).status_code == 403
        assert exited.is_set()
        assert len(calls) == 1
        assert client.get(path).json()['status'] == '失败'


def test_expired_execution_context_is_read_only_and_cannot_authorize_work(database_url, tmp_path):
    now = [datetime(2026, 11, 3, 12, tzinfo=timezone.utc)]
    credential = uuid4().hex
    with configured_client(database_url, tmp_path, executor_credential=credential, clock=lambda: now[0],
                           deadline_schedule=ManualTicks()) as client:
        path, before, endpoint, auth = started(client, credential)
        now[0] += timedelta(minutes=10)
        assert client.get(endpoint.removesuffix('/evidence') + '/context', headers=auth).status_code == 403
        assert client.get(endpoint.rsplit('/', 2)[0], headers=auth).status_code == 403
        assert client.get(path).json() == before


@pytest.mark.parametrize('database_url', ['0009'], indirect=True)
def test_upgrade_derives_original_deadline_but_never_invents_old_executor_exit(database_url, tmp_path):
    import os
    import sys
    import subprocess
    from sqlalchemy import create_engine, MetaData, insert
    from test_investigations import ROOT
    from app.evidence import redaction_keys
    import secrets
    engine = create_engine(database_url)
    old = MetaData()
    with engine.connect() as connection:
        connection.exec_driver_sql('SET max_parallel_workers_per_gather = 0')
        old.reflect(connection)
    parent, attempt, execution = uuid4(), uuid4(), uuid4()
    start = datetime(2026, 11, 3, 12, tzinfo=timezone.utc)
    with engine.begin() as connection:
        connection.execute(insert(old.tables['investigations']).values(id=parent, request_key=uuid4(),
            title='旧轮期限', description='', created_at=start, updated_at=start, retention_expires_at=None))
        connection.execute(insert(old.tables['attempts']).values(id=attempt, investigation_id=parent,
            request_key=uuid4(), request_body='{}', service_id='orders', source_ids=['controlled-orders'],
            starts_at=start-timedelta(hours=1), ends_at=start, created_at=start, status='调查中'))
        connection.execute(insert(old.tables['controlled_executions']).values(id=execution, attempt_id=attempt, valid=True))
        connection.execute(insert(redaction_keys).values(investigation_id=parent, secret=secrets.token_bytes(32)))
    engine.dispose()
    migrated = subprocess.run([sys.executable, '-m', 'alembic', '-c', 'backend/alembic.ini', 'upgrade', 'head'],
        cwd=ROOT, env={**os.environ, 'DATABASE_URL': database_url, 'PYTHONPATH': str(ROOT/'backend')}, capture_output=True)
    assert migrated.returncode == 0
    with configured_client(database_url, tmp_path, clock=lambda: start+timedelta(minutes=10),
                           deadline_schedule=ManualTicks()) as client:
        path = '/api/investigations/' + str(parent)
        before = client.get(path).json()
        assert before['attempts'][0]['deadline_at'] == '2026-11-03T12:10:00Z'
        after = client.post(path + '/attempts/' + str(attempt) + '/cancel').json()
        assert after['status'] == '调查中'
        assert after['attempts'][0]['execution_phase'] == 'timeout_stopping'
        assert after['attempts'][0]['stop_confirmed_at'] is None
        assert after['retention_expires_at'] is None


def test_timeout_late_rate_limit_remains_shared_across_api_instances(database_url, tmp_path):
    import asyncio
    from threading import Event
    from concurrent.futures import ThreadPoolExecutor
    from app.queries import SourceFailure
    from test_queries import Source as Healthy
    entered = Event()
    now = [datetime(2026, 11, 3, 12, tzinfo=timezone.utc)]
    class Limited:
        async def query(self, **scope):
            entered.set()
            try:
                await asyncio.Future()
            except asyncio.CancelledError:
                raise SourceFailure('rate_limited', retry_after=30)
    credential = uuid4().hex
    with configured_client(database_url, tmp_path, executor_credential=credential, clock=lambda: now[0],
                           query_sources={'controlled-orders': Limited()}) as client, \
         configured_client(database_url, tmp_path, executor_credential=credential, clock=lambda: now[0],
                           query_sources={'controlled-orders': Healthy()}) as other:
        path, _, endpoint, auth = started(client, credential)
        with ThreadPoolExecutor(max_workers=1) as pool:
            pending = pool.submit(client.post, endpoint.removesuffix('/evidence') + '/queries',
                                  headers={**auth, 'Idempotency-Key': str(uuid4())})
            assert entered.wait(5)
            now[0] += timedelta(minutes=10)
            assert pending.result(5).status_code == 403
        after = other.get(path).json()
        assert after['status'] == '失败'
        assert after['attempts'][0]['queries'][0]['sources'][0]['reason'] == 'rate_limited'
        _, _, second, second_auth = started(other, credential)
        blocked = other.post(second.removesuffix('/evidence') + '/queries',
                             headers={**second_auth, 'Idempotency-Key': str(uuid4())})
        assert blocked.status_code == 409
        assert blocked.json()['detail']['sources'][0]['retry_not_before']


def test_real_remaining_deadline_interrupts_query_without_clock_advance_or_retry(database_url, tmp_path):
    import asyncio
    import time
    calls, observed = [], []
    now = [datetime(2026, 11, 3, 12, tzinfo=timezone.utc)]
    class Source:
        async def query(self, **scope):
            calls.append(True)
            try:
                await asyncio.Future()
            finally:
                observed.append(time.monotonic())
    credential = uuid4().hex
    with configured_client(database_url, tmp_path, executor_credential=credential, clock=lambda: now[0],
                           deadline_schedule=ManualTicks(), query_sources={'controlled-orders': Source()}) as client:
        path, _, endpoint, auth = started(client, credential)
        now[0] += timedelta(seconds=599.8)
        begin = time.monotonic()
        reply = client.post(endpoint.removesuffix('/evidence') + '/queries',
                            headers={**auth, 'Idempotency-Key': str(uuid4())})
        assert reply.status_code == 403
        assert calls == [True]
        assert observed and observed[0] - begin < 2
        after = client.get(path).json()
        assert after['status'] == '失败'
        assert after['attempts'][0]['queries'][0]['status'] == 'timed_out'


def test_independent_coordinators_race_with_late_cancel_and_finish_only_timeout_wins(database_url, tmp_path):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Barrier
    now = [datetime(2026, 11, 3, 12, tzinfo=timezone.utc)]
    credential = uuid4().hex
    with configured_client(database_url, tmp_path, executor_credential=credential, clock=lambda: now[0],
                           deadline_schedule=ManualTicks()) as client:
        path, before, endpoint, auth = started(client, credential)
        now[0] += timedelta(minutes=10)
        barrier = Barrier(3)
        def request(kind):
            with configured_client(database_url, tmp_path, executor_credential=credential, clock=lambda: now[0]) as other:
                barrier.wait()
                if kind == 'cancel':
                    return other.post(path + '/attempts/' + before['attempts'][0]['id'] + '/cancel')
                return other.post(endpoint.removesuffix('/evidence') + '/conclusions', json=proposal(),
                                  headers={**auth, 'Idempotency-Key': str(uuid4())})
        with configured_client(database_url, tmp_path, clock=lambda: now[0]), ThreadPoolExecutor(max_workers=2) as pool:
            cancel, finish = pool.submit(request, 'cancel'), pool.submit(request, 'finish')
            barrier.wait()
            assert cancel.result(10).status_code == 200
            assert finish.result(10).status_code == 403
        after = client.get(path).json()
        assert after['status'] == '失败'
        assert after['attempts'][0]['cancel_requested_at'] is None
        assert after['attempts'][0]['conclusions'] == []
        assert after['attempts'][0]['end_reason'] == 'attempt_deadline_exceeded'
        assert client.get(path).json() == after


def test_retry_recomputes_remaining_budget_from_same_persisted_deadline(database_url, tmp_path):
    from app.queries import QuerySchedule, SourceFailure
    now = [datetime(2026, 11, 3, 12, tzinfo=timezone.utc)]
    budgets, calls = [], []
    class Source:
        async def query(self, **scope):
            calls.append(True)
            if len(calls) == 1:
                now[0] += timedelta(seconds=597)
                raise SourceFailure('network')
    class Schedule(QuerySchedule):
        async def deadline(self, operation, seconds):
            budgets.append(seconds)
            return await super().deadline(operation, seconds)
        async def wait(self, seconds):
            now[0] += timedelta(seconds=seconds)
    credential = uuid4().hex
    with configured_client(database_url, tmp_path, executor_credential=credential, clock=lambda: now[0],
                           deadline_schedule=ManualTicks(), query_sources={'controlled-orders': Source()},
                           query_schedule=Schedule()) as client:
        path, before, endpoint, auth = started(client, credential)
        assert client.post(endpoint.removesuffix('/evidence') + '/queries',
                           headers={**auth, 'Idempotency-Key': str(uuid4())}).status_code == 201
        assert len(budgets) == 2 and 0 < budgets[1] <= 1
        assert client.get(path).json()['attempts'][0]['deadline_at'] == before['attempts'][0]['deadline_at']


def test_start_persists_fixed_ten_minute_deadline_using_server_clock(database_url, tmp_path):
    now = [datetime(2026, 11, 3, 12, tzinfo=timezone.utc)]
    credential = uuid4().hex
    with configured_client(database_url, tmp_path, executor_credential=credential, clock=lambda: now[0]) as client:
        path, before, _, _ = started(client, credential)
        attempt = before['attempts'][0]
        assert attempt['created_at'] == '2026-11-03T12:00:00Z'
        assert attempt['deadline_at'] == '2026-11-03T12:10:00Z'
        now[0] += timedelta(minutes=1)
        assert client.get(path).json() == before
        assert client.post(path + '/attempts', json={'service_id': 'orders'},
                           headers={'Idempotency-Key': str(uuid4())}).status_code == 409
        assert client.get(path).json()['attempts'][0]['deadline_at'] == attempt['deadline_at']
