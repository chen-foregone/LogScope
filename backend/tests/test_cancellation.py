from uuid import uuid4
import pytest

from test_investigations import database_url
from test_attempts import configured_client
from test_evidence import started, material
from test_conclusions import proposal


def test_deadline_repeated_external_cancel_waits_for_adapter_exit():
    import asyncio
    from app.queries import QuerySchedule

    async def scenario():
        cleanup, release, exited = asyncio.Event(), asyncio.Event(), asyncio.Event()

        async def operation():
            try:
                await asyncio.Future()
            except asyncio.CancelledError:
                cleanup.set()
                await release.wait()
            finally:
                exited.set()

        pending = asyncio.create_task(QuerySchedule().deadline(operation(), 0))
        await cleanup.wait()
        try:
            for _ in range(4):
                pending.cancel()
                await asyncio.sleep(0)
            assert not pending.done(), '外部重复取消不能冒充真实适配器退出'
            assert not exited.is_set()
        finally:
            release.set()
            await asyncio.gather(pending, return_exceptions=True)
        assert exited.is_set()

    asyncio.run(scenario())


def test_deadline_keeps_rate_limit_reported_during_timeout_cleanup():
    import asyncio
    from app.queries import QuerySchedule, SourceFailure

    async def operation():
        try:
            await asyncio.Future()
        except asyncio.CancelledError:
            raise SourceFailure('rate_limited', retry_after=30)

    with pytest.raises(SourceFailure) as failure:
        asyncio.run(QuerySchedule().deadline(operation(), 0))
    assert failure.value.reason == 'rate_limited'
    assert failure.value.retry_after == 30


def test_inflight_cancel_waits_for_physical_cleanup_and_isolates_other_api_instance(database_url, tmp_path):
    import asyncio
    from threading import Event
    from concurrent.futures import ThreadPoolExecutor
    entered, cancel_seen, release = Event(), Event(), Event()

    class Source:
        async def query(self, **scope):
            entered.set()
            try:
                await asyncio.Future()
            except asyncio.CancelledError:
                cancel_seen.set()
                await asyncio.to_thread(release.wait, 15)
                return None

    credential = uuid4().hex
    with configured_client(database_url, tmp_path, executor_credential=credential,
                           query_sources={'controlled-orders': Source()}) as client, \
         configured_client(database_url, tmp_path, executor_credential=credential) as other:
        path, before, evidence, auth = started(client, credential)
        endpoint = evidence.removesuffix('/evidence')
        cancel = path + '/attempts/' + before['attempts'][0]['id'] + '/cancel'
        headers = {**auth, 'Idempotency-Key': str(uuid4())}
        with ThreadPoolExecutor(max_workers=1) as pool:
            pending = pool.submit(client.post, endpoint + '/queries', headers=headers)
            try:
                assert entered.wait(5)
                stopping = other.post(cancel)
                assert stopping.status_code == 200
                view = stopping.json()
                assert view['status'] == '调查中'
                attempt = view['attempts'][0]
                assert attempt['execution_phase'] == 'stopping'
                assert attempt['stop_confirmed_at'] is None
                assert view['retention_expires_at'] is None
                assert cancel_seen.wait(5), '另一API实例取消必须通知真实在途协程'
                assert not pending.done(), '协程清理退出前不能确认已停止'
                assert other.post(cancel).json() == view
                assert other.get(path).json() == view
                assert other.post(path + '/attempts', json={'service_id': 'orders'},
                                  headers={'Idempotency-Key': str(uuid4())}).status_code == 409
                assert other.post(evidence, json=material(), headers=auth).status_code == 403
                assert other.post(endpoint + '/conclusions', json=proposal(), headers=headers).status_code == 403
                assert other.post(endpoint + '/queries', headers=headers).status_code == 403
            finally:
                release.set()
            reply = pending.result(10)
            assert reply.status_code == 403
        after = other.get(path).json()
        assert after['status'] == '已取消'
        assert after['attempts'][0]['stop_confirmed_at']
        assert after['attempts'][0]['queries'][0]['status'] == 'canceled'
        assert after['attempts'][0]['queries'][0]['sources'][0]['state'] == 'canceled'
        assert other.post(cancel).json() == after


def test_user_and_repeated_request_cancel_keep_stopping_until_adapter_exits(database_url, tmp_path):
    import asyncio
    from threading import Event
    from concurrent.futures import ThreadPoolExecutor
    entered, cleanup, release, exited = Event(), Event(), Event(), Event()
    request_task = []

    class Source:
        async def query(self, **scope):
            entered.set()
            try:
                await asyncio.Future()
            except asyncio.CancelledError:
                cleanup.set()
                await asyncio.to_thread(release.wait, 10)
            finally:
                exited.set()

    credential = uuid4().hex
    with configured_client(database_url, tmp_path, executor_credential=credential,
                           query_sources={'controlled-orders': Source()}) as client:
        original = client.app

        async def boundary(scope, receive, send):
            if scope.get('path', '').endswith('/queries'):
                request_task.append(asyncio.current_task())
            await original(scope, receive, send)

        client._transport.app = boundary
        path, before, evidence, auth = started(client, credential)
        cancel = path + '/attempts/' + before['attempts'][0]['id'] + '/cancel'
        with ThreadPoolExecutor(max_workers=1) as pool:
            pending = pool.submit(client.post, evidence.removesuffix('/evidence') + '/queries',
                                  headers={**auth, 'Idempotency-Key': str(uuid4())})
            try:
                assert entered.wait(5)
                client.post(cancel)
                assert cleanup.wait(5)

                async def interrupt():
                    for _ in range(4):
                        request_task[0].cancel()
                        await asyncio.sleep(0)

                client.portal.call(interrupt)
                assert not exited.is_set()
                assert not pending.done()
                stopping = client.post(cancel).json()
                assert stopping['status'] == '调查中'
                assert stopping['attempts'][0]['stop_confirmed_at'] is None
                assert stopping['attempts'][0]['queries'][0]['status'] == 'running'
            finally:
                release.set()
            assert pending.result(10).status_code == 403
        assert exited.is_set()
        assert client.get(path).json()['status'] == '已取消'


def test_cancel_during_timeout_cleanup_does_not_cancel_cleanup_twice(database_url, tmp_path):
    import asyncio
    from threading import Event
    from concurrent.futures import ThreadPoolExecutor
    from app.queries import QuerySchedule
    cleanup, release, interrupted = Event(), Event(), Event()

    class ShortDeadline(QuerySchedule):
        async def deadline(self, operation, seconds):
            return await super().deadline(operation, 0.05)

    class Source:
        async def query(self, **scope):
            try:
                await asyncio.Future()
            except asyncio.CancelledError:
                cleanup.set()
                try:
                    await asyncio.to_thread(release.wait, 10)
                except asyncio.CancelledError:
                    interrupted.set()
                    raise

    credential = uuid4().hex
    with configured_client(database_url, tmp_path, executor_credential=credential,
                           query_sources={'controlled-orders': Source()}, query_schedule=ShortDeadline()) as client:
        path, before, evidence, auth = started(client, credential)
        cancel = path + '/attempts/' + before['attempts'][0]['id'] + '/cancel'
        with ThreadPoolExecutor(max_workers=1) as pool:
            pending = pool.submit(client.post, evidence.removesuffix('/evidence') + '/queries',
                                  headers={**auth, 'Idempotency-Key': str(uuid4())})
            try:
                assert cleanup.wait(5)
                assert client.post(cancel).json()['status'] == '调查中'
                assert not interrupted.wait(0.5), '用户取消不能再次打断已进行的超时清理'
                assert not pending.done()
                assert client.get(path).json()['status'] == '调查中'
            finally:
                release.set()
            assert pending.result(10).status_code == 403
        assert client.get(path).json()['status'] == '已取消'


def test_cancel_retry_wait_stops_new_calls_and_releases_only_owned_source_gate(database_url, tmp_path):
    import asyncio
    from threading import Event
    from concurrent.futures import ThreadPoolExecutor
    from app.queries import SourceFailure, QuerySchedule
    waiting = Event()
    calls = []

    class RetryWait(QuerySchedule):
        async def wait(self, seconds):
            waiting.set()
            await asyncio.Future()

    class Source:
        async def query(self, **scope):
            calls.append(scope['source_id'])
            raise SourceFailure('network')

    credential = uuid4().hex
    with configured_client(database_url, tmp_path, executor_credential=credential,
                           query_sources={'controlled-orders': Source()}, query_schedule=RetryWait()) as client:
        path, before, evidence, auth = started(client, credential)
        with ThreadPoolExecutor(max_workers=1) as pool:
            pending = pool.submit(client.post, evidence.removesuffix('/evidence') + '/queries',
                                  headers={**auth, 'Idempotency-Key': str(uuid4())})
            assert waiting.wait(5)
            client.post(path + '/attempts/' + before['attempts'][0]['id'] + '/cancel')
            assert pending.result(10).status_code == 403
        assert calls == ['controlled-orders']
        assert client.get(path).json()['status'] == '已取消'
    from test_queries import Source as Healthy
    with configured_client(database_url, tmp_path, executor_credential=credential,
                           query_sources={'controlled-orders': Healthy()}) as next_client:
        _, _, next_evidence, next_auth = started(next_client, credential)
        assert next_client.post(next_evidence.removesuffix('/evidence') + '/queries',
            headers={**next_auth, 'Idempotency-Key': str(uuid4())}).status_code == 201


def test_cancel_after_completion_returns_truth_and_concurrent_cancels_are_idempotent(database_url, tmp_path):
    from concurrent.futures import ThreadPoolExecutor
    credential = uuid4().hex
    with configured_client(database_url, tmp_path, executor_credential=credential) as client:
        path, before, evidence, auth = started(client, credential)
        cancel = path + '/attempts/' + before['attempts'][0]['id'] + '/cancel'
        assert client.post(evidence.removesuffix('/evidence') + '/conclusions', json=proposal(),
                           headers={**auth, 'Idempotency-Key': str(uuid4())}).status_code == 201
        completed = client.get(path).json()
        assert client.post(cancel).json() == completed
        assert client.post(path + '/attempts/' + str(uuid4()) + '/cancel').status_code == 404
        path2, before2, _, _ = started(client, credential)
        cancel2 = path2 + '/attempts/' + before2['attempts'][0]['id'] + '/cancel'
        with ThreadPoolExecutor(max_workers=3) as pool:
            replies = list(pool.map(lambda _: client.post(cancel2), range(3)))
        assert all(reply.status_code == 200 for reply in replies)
        assert all(reply.json() == replies[0].json() for reply in replies)
        assert client.get(path2).json() == replies[0].json()


def test_unconfirmed_custom_executor_remains_stopping_after_restart(database_url, tmp_path):
    from app.attempts import ControlledExecutionAdapter
    class OtherExecutor(ControlledExecutionAdapter):
        pass
    credential = uuid4().hex
    with configured_client(database_url, tmp_path, executor_credential=credential,
                           execution_adapter=OtherExecutor()) as client:
        path, before, _, _ = started(client, credential)
        cancel = path + '/attempts/' + before['attempts'][0]['id'] + '/cancel'
        stopping = client.post(cancel).json()
        assert stopping['status'] == '调查中'
        assert stopping['attempts'][0]['execution_phase'] == 'stopping'
    with configured_client(database_url, tmp_path, executor_credential=credential) as restarted:
        assert restarted.get(path).json() == stopping
        assert restarted.post(cancel).json() == stopping


@pytest.mark.parametrize('database_url', ['0008'], indirect=True)
def test_upgrade_preserves_old_execution_without_inventing_stop_confirmation(database_url, tmp_path):
    import os
    import sys
    import subprocess
    from sqlalchemy import create_engine, text
    from test_investigations import ROOT
    investigation_id, attempt_id, execution_id = uuid4(), uuid4(), uuid4()
    engine = create_engine(database_url)
    with engine.begin() as connection:
        connection.execute(text("INSERT INTO investigations (id,request_key,title,description,created_at,updated_at) VALUES (:id,:key,'旧轮调查','',now(),now())"),
                           {'id': investigation_id, 'key': uuid4()})
        connection.execute(text("INSERT INTO attempts (id,investigation_id,request_key,request_body,service_id,source_ids,starts_at,ends_at,created_at,status) VALUES (:id,:parent,:key,'{}','orders','[\"controlled-orders\"]',now()-interval '1 hour',now(),now(),'调查中')"),
                           {'id': attempt_id, 'parent': investigation_id, 'key': uuid4()})
        connection.execute(text('INSERT INTO controlled_executions (id,attempt_id,valid) VALUES (:id,:attempt,true)'),
                           {'id': execution_id, 'attempt': attempt_id})
    engine.dispose()
    upgraded = subprocess.run([sys.executable, '-m', 'alembic', '-c', 'backend/alembic.ini', 'upgrade', 'head'],
        cwd=ROOT, env={**os.environ, 'DATABASE_URL': database_url, 'PYTHONPATH': str(ROOT / 'backend')}, capture_output=True)
    assert upgraded.returncode == 0
    # 0008之后新插入的旧记录没有脱敏密钥，安排迁移前基础设施数据。
    from app.evidence import redaction_keys
    import secrets
    engine = create_engine(database_url)
    with engine.begin() as connection:
        connection.execute(redaction_keys.insert().values(investigation_id=investigation_id, secret=secrets.token_bytes(32)))
    engine.dispose()
    with configured_client(database_url, tmp_path) as client:
        path = '/api/investigations/' + str(investigation_id)
        before = client.get(path).json()
        assert before['status'] == '调查中'
        assert before['attempts'][0]['cancel_requested_at'] is None
        after = client.post(path + '/attempts/' + str(attempt_id) + '/cancel').json()
        assert after['status'] == '调查中'
        assert after['attempts'][0]['execution_phase'] == 'stopping'
        assert after['attempts'][0]['stop_confirmed_at'] is None


@pytest.mark.parametrize('stage', ['UPDATE attempts', 'UPDATE controlled_executions', 'UPDATE investigations'])
def test_cancel_transaction_failure_rolls_back_request_fence_and_terminal(database_url, tmp_path, stage):
    from sqlalchemy import event
    from sqlalchemy.engine import Engine
    credential = uuid4().hex
    with configured_client(database_url, tmp_path, executor_credential=credential) as client:
        path, before, evidence, auth = started(client, credential)
        cancel = path + '/attempts/' + before['attempts'][0]['id'] + '/cancel'
        def fail(connection, cursor, statement, parameters, context, executemany):
            if statement.startswith(stage):
                cursor.execute('SELECT 1 / 0')
        event.listen(Engine, 'before_cursor_execute', fail)
        try:
            assert client.post(cancel).status_code == 503
        finally:
            event.remove(Engine, 'before_cursor_execute', fail)
        assert client.get(path).json() == before
        assert client.get(evidence.removesuffix('/evidence') + '/context', headers=auth).status_code == 200
        assert client.post(cancel).json()['status'] == '已取消'


@pytest.mark.parametrize('swallow_cancel', [False, True], ids=['finished-before-poll', 'adapter-swallows-cancel'])
def test_cancel_preserves_late_rate_limit_through_public_api(database_url, tmp_path, swallow_cancel):
    import asyncio
    from threading import Event
    from concurrent.futures import ThreadPoolExecutor
    from app.queries import SourceFailure, QuerySchedule
    ready, release = Event(), Event()

    class Schedule(QuerySchedule):
        async def deadline(self, operation, seconds):
            if swallow_cancel:
                return await super().deadline(operation, seconds)
            try:
                return await operation
            except SourceFailure:
                ready.set()
                # 外部调度阻挡事件循环，确保故障与持久取消在同一检查间隙可见。
                assert release.wait(5)
                raise

    class Limited:
        async def query(self, **scope):
            if swallow_cancel:
                ready.set()
                try:
                    await asyncio.Future()
                except asyncio.CancelledError:
                    pass
            raise SourceFailure('rate_limited', retry_after=30)

    credential = uuid4().hex
    with configured_client(database_url, tmp_path, executor_credential=credential,
                           query_sources={'controlled-orders': Limited()}, query_schedule=Schedule()) as client, \
         configured_client(database_url, tmp_path, executor_credential=credential) as other:
        path, before, evidence, auth = started(client, credential)
        with ThreadPoolExecutor(max_workers=1) as pool:
            pending = pool.submit(client.post, evidence.removesuffix('/evidence') + '/queries',
                                  headers={**auth, 'Idempotency-Key': str(uuid4())})
            try:
                assert ready.wait(5)
                other.post(path + '/attempts/' + before['attempts'][0]['id'] + '/cancel')
            finally:
                release.set()
            assert pending.result(10).status_code == 403
        record = other.get(path).json()
        assert record['status'] == '已取消'
        source = record['attempts'][0]['queries'][0]['sources'][0]
        assert source['reason'] == 'rate_limited'
        assert source['retry_after_seconds'] == 30
        assert not record['attempts'][0]['query_coverage']['complete']
        _, _, next_evidence, next_auth = started(other, credential)
        blocked = other.post(next_evidence.removesuffix('/evidence') + '/queries',
                             headers={**next_auth, 'Idempotency-Key': str(uuid4())})
        assert blocked.status_code == 409
        assert blocked.json()['detail']['sources'][0]['reason'] == 'rate_limited'
        assert blocked.json()['detail']['sources'][0]['retry_not_before']


def test_cancel_rate_limit_wait_does_not_remove_source_retry_after(database_url, tmp_path):
    import asyncio
    from threading import Event
    from concurrent.futures import ThreadPoolExecutor
    from app.queries import SourceFailure, QuerySchedule
    waiting = Event()
    class Schedule(QuerySchedule):
        async def wait(self, seconds):
            waiting.set()
            await asyncio.Future()
    class Limited:
        async def query(self, **scope):
            raise SourceFailure('rate_limited', retry_after=30)
    credential = uuid4().hex
    with configured_client(database_url, tmp_path, executor_credential=credential,
                           query_sources={'controlled-orders': Limited()}, query_schedule=Schedule()) as client:
        path, before, evidence, auth = started(client, credential)
        with ThreadPoolExecutor(max_workers=1) as pool:
            pending = pool.submit(client.post, evidence.removesuffix('/evidence') + '/queries',
                                  headers={**auth, 'Idempotency-Key': str(uuid4())})
            assert waiting.wait(5)
            client.post(path + '/attempts/' + before['attempts'][0]['id'] + '/cancel')
            assert pending.result(10).status_code == 403
    from test_queries import Source
    with configured_client(database_url, tmp_path, executor_credential=credential,
                           query_sources={'controlled-orders': Source()}) as other:
        _, _, next_evidence, _ = started(other, credential)
        reply = other.post(next_evidence.removesuffix('/evidence') + '/queries',
                           headers={**auth, 'Idempotency-Key': str(uuid4())})
        assert reply.status_code == 409
        assert reply.json()['detail']['sources'][0]['reason'] == 'rate_limited'


def test_finish_and_cancel_race_has_one_real_terminal(database_url, tmp_path):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Barrier
    credential = uuid4().hex
    with configured_client(database_url, tmp_path, executor_credential=credential) as client:
        path, before, evidence, auth = started(client, credential)
        cancel = path + '/attempts/' + before['attempts'][0]['id'] + '/cancel'
        barrier = Barrier(2)
        def finish():
            barrier.wait()
            return client.post(evidence.removesuffix('/evidence') + '/conclusions', json=proposal(),
                               headers={**auth, 'Idempotency-Key': str(uuid4())})
        def stop():
            barrier.wait()
            return client.post(cancel)
        with ThreadPoolExecutor(max_workers=2) as pool:
            finished, canceled = pool.submit(finish), pool.submit(stop)
            finish_reply, cancel_reply = finished.result(10), canceled.result(10)
        assert cancel_reply.status_code == 200
        after = client.get(path).json()
        assert after == cancel_reply.json()
        if after['status'] == '已完成':
            assert finish_reply.status_code == 201
            assert after['attempts'][0]['cancel_requested_at'] is None
            assert after['attempts'][0]['end_reason'] == 'normal_completion'
        else:
            assert after['status'] == '已取消'
            assert finish_reply.status_code == 403
            assert after['attempts'][0]['conclusions'] == []
            assert after['attempts'][0]['end_reason'] == 'user_canceled'
        assert client.post(cancel).json() == after


def test_stop_confirmation_database_failure_keeps_query_active_until_recovery(database_url, tmp_path):
    import asyncio
    from threading import Event
    from concurrent.futures import ThreadPoolExecutor
    from sqlalchemy import event
    from sqlalchemy.engine import Engine
    entered, canceled, release = Event(), Event(), Event()
    class Source:
        async def query(self, **scope):
            entered.set()
            try:
                await asyncio.Future()
            except asyncio.CancelledError:
                canceled.set()
                await asyncio.to_thread(release.wait, 10)
    credential = uuid4().hex
    with configured_client(database_url, tmp_path, executor_credential=credential,
                           query_sources={'controlled-orders': Source()}) as client:
        path, before, evidence, auth = started(client, credential)
        cancel = path + '/attempts/' + before['attempts'][0]['id'] + '/cancel'
        def fail(connection, cursor, statement, parameters, context, executemany):
            if statement.startswith('UPDATE attempts') and parameters.get('status') == '已取消':
                cursor.execute('SELECT 1 / 0')
        with ThreadPoolExecutor(max_workers=1) as pool:
            pending = pool.submit(client.post, evidence.removesuffix('/evidence') + '/queries',
                                  headers={**auth, 'Idempotency-Key': str(uuid4())})
            try:
                assert entered.wait(5)
                stopping = client.post(cancel).json()
                assert canceled.wait(5)
                event.listen(Engine, 'before_cursor_execute', fail)
                release.set()
                assert pending.result(10).status_code == 503
            finally:
                release.set()
                event.remove(Engine, 'before_cursor_execute', fail)
        after = client.get(path).json()
        assert after == stopping
        assert after['attempts'][0]['queries'][0]['status'] == 'running'
        assert client.post(cancel).json() == after
    with configured_client(database_url, tmp_path, executor_credential=credential) as restarted:
        assert restarted.get(path).json() == after
        assert restarted.post(cancel).json() == after


def test_idle_cancel_is_confirmed_persistent_idempotent_and_fences_replays(database_url, tmp_path):
    credential = uuid4().hex
    with configured_client(database_url, tmp_path, executor_credential=credential) as client:
        path, before, evidence, auth = started(client, credential)
        saved = client.post(evidence, json=material(), headers=auth).json()
        endpoint = evidence.removesuffix('/evidence')
        headers = {**auth, 'Idempotency-Key': str(uuid4())}
        analysis = proposal(finish=False)
        assert client.post(endpoint + '/conclusions', json=analysis, headers=headers).status_code == 201
        cancel = path + '/attempts/' + before['attempts'][0]['id'] + '/cancel'
        reply = client.post(cancel)
        assert reply.status_code == 200
        after = reply.json()
        attempt = after['attempts'][0]
        assert after['status'] == '已取消'
        assert attempt['execution_phase'] == 'stopped'
        assert attempt['cancel_requested_at'] and attempt['stop_confirmed_at']
        assert attempt['stop_confirmation'] == 'controlled_queries_exited_and_writes_fenced'
        assert attempt['end_reason'] == 'user_canceled'
        assert attempt['result'] is None
        assert attempt['evidence'] == [saved]
        assert len(attempt['conclusions']) == 1
        assert after['retention_expires_at'] is None
        assert client.post(cancel).json() == after
        assert client.post(evidence, json=material(), headers=auth).status_code == 403
        assert client.post(endpoint + '/conclusions', json=analysis, headers=headers).status_code == 403
        assert client.post(endpoint + '/queries', headers=headers).status_code == 403
        assert client.get(path).json() == after
    with configured_client(database_url, tmp_path, executor_credential=credential) as restarted:
        assert restarted.get(path).json() == after
        assert restarted.post(cancel).json() == after
