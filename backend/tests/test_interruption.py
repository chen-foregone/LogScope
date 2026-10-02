from uuid import uuid4
import os
import subprocess
import sys
import socket
import time
from contextlib import contextmanager

import httpx
import pytest
from itertools import permutations
from datetime import datetime, timedelta


@contextmanager
def live_api(database_url, credential, *, query=False, rate_limited=False, coordinate=True):
    with socket.socket() as listener:
        listener.bind(('127.0.0.1', 0))
        port = listener.getsockname()[1]
    startup = '''
import asyncio
import uvicorn
from app.main import create_app
class Source:
    async def query(self, **scope):
        try:
            await asyncio.Future()
        except asyncio.CancelledError:
            await asyncio.Future()
uvicorn.run(create_app(query_sources={'controlled-orders': Source()}), host='127.0.0.1', port=PORT, access_log=False)
'''.replace('PORT', str(port))
    if rate_limited:
        startup = startup.replace('class Source:', 'from app.queries import SourceFailure\nclass Source:').replace(
            '        try:\n            await asyncio.Future()\n        except asyncio.CancelledError:\n            await asyncio.Future()',
            "        raise SourceFailure('rate_limited', retry_after=30)")
    if not coordinate:
        startup = startup.replace('class Source:', 'class NoTicks:\n    async def wait(self):\n        await asyncio.Future()\nclass Source:').replace(
            "query_sources={'controlled-orders': Source()}",
            "query_sources={'controlled-orders': Source()}, deadline_schedule=NoTicks()")
    command = ([sys.executable, '-c', startup] if query or rate_limited or not coordinate else
               [sys.executable, '-m', 'uvicorn', 'app.main:app', '--host', '127.0.0.1', '--port', str(port), '--no-access-log'])
    process = subprocess.Popen(command,
        env={**os.environ, 'DATABASE_URL': database_url, 'EXECUTOR_CREDENTIAL': credential,
             'SERVICES_FILE': 'backend/services.example.json'}, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
    try:
        with httpx.Client(base_url=f'http://127.0.0.1:{port}', trust_env=False, timeout=10) as client:
            limit = time.monotonic() + 20
            while True:
                assert process.poll() is None, '受控API启动失败'
                try:
                    if client.get('/api/health').status_code == 200:
                        break
                except httpx.TransportError:
                    pass
                assert time.monotonic() < limit
            yield client, process
    finally:
        if process.poll() is None:
            process.terminate()
        process.communicate(timeout=15)
        assert process.returncode is not None


def await_view(client, path, predicate):
    limit = time.monotonic() + 10
    while True:
        view = client.get(path).json()
        if predicate(view):
            return view
        assert time.monotonic() < limit, view


def test_real_owner_exit_is_required_and_restart_preserves_history(database_url, tmp_path):
    credential = uuid4().hex
    with configured_client(database_url, tmp_path, executor_credential=credential) as observer:
        with live_api(database_url, credential) as (owner, process):
            path, before, evidence, auth = started(owner, credential)
            assert owner.post(evidence, json=material(), headers=auth).status_code == 201
            lost = observer.post(evidence.removesuffix('/evidence') + '/lost', headers=auth)
            assert lost.status_code == 200
            assert process.poll() is None, '真实承载进程仍存活'
            assert observer.post(path + '/attempts', json={'service_id': 'orders'},
                                 headers={'Idempotency-Key': str(uuid4())}).status_code == 409
            assert observer.get(path).json()['status'] == '调查中'
        assert process.poll() is not None, '内核已观察到原进程退出'
        ended = await_view(observer, path, lambda v: v['status'] == '失败')
        attempt = ended['attempts'][0]
        assert attempt['end_reason'] == 'service_interrupted'
        assert attempt['stop_confirmed_at']
        assert attempt['interruption_confirmation']['processes'][0]['state'] == 'exited'
        assert len(attempt['evidence']) == 1
        assert observer.post(evidence, json=material(), headers=auth).status_code == 403
        with live_api(database_url, credential) as (restarted, new_process):
            assert new_process.poll() is None
            assert restarted.get(path).json() == ended
            new_round = restarted.post(path + '/attempts', json={'service_id': 'orders'},
                                      headers={'Idempotency-Key': str(uuid4())})
            assert new_round.status_code == 201
            assert new_round.json()['attempts'][0] == attempt
            assert restarted.post(evidence, json=material(), headers=auth).status_code == 403


from test_investigations import database_url
from test_attempts import configured_client
from test_evidence import started, material
from test_conclusions import proposal


def test_live_query_carrier_blocks_recovery_until_its_real_exit(database_url, tmp_path):
    from concurrent.futures import ThreadPoolExecutor
    credential = uuid4().hex
    with configured_client(database_url, tmp_path, executor_credential=credential) as observer:
        with live_api(database_url, credential, query=True) as (carrier, carrier_process):
            with live_api(database_url, credential) as (owner, owner_process):
                path, _, evidence, auth = started(owner, credential)
                endpoint = evidence.removesuffix('/evidence')
                with ThreadPoolExecutor(max_workers=1) as pool:
                    pending = pool.submit(carrier.post, endpoint + '/queries',
                                          headers={**auth, 'Idempotency-Key': str(uuid4())})
                    try:
                        await_view(observer, path, lambda v: bool(v['attempts'][0]['queries']))
                        owner_process.terminate()
                        owner_process.wait(timeout=10)
                        waiting = await_view(observer, path, lambda v: v['attempts'][0]['execution_phase'] == 'confirming_execution')
                        assert carrier_process.poll() is None
                        assert waiting['status'] == '调查中'
                        assert waiting['attempts'][0]['stop_confirmed_at'] is None
                        assert observer.post(path + '/attempts', json={'service_id': 'orders'},
                            headers={'Idempotency-Key': str(uuid4())}).status_code == 409
                    finally:
                        carrier_process.terminate()
                        carrier_process.wait(timeout=10)
                    try:
                        pending.result(15)
                    except httpx.TransportError:
                        pass
            ended = await_view(observer, path, lambda v: v['status'] == '失败')
            assert ended['attempts'][0]['queries'][0]['status'] == 'interrupted'
            assert len(ended['attempts'][0]['interruption_confirmation']['processes']) == 2


@pytest.mark.parametrize('gate_state,new_limit', [
    ('material', True), ('stricter', True), ('indefinite', True), ('other_query', True),
    ('stricter', False), ('indefinite', False), ('other_query', False)])
def test_dead_waiting_query_preserves_persisted_source_rate_limit(database_url, tmp_path, gate_state, new_limit):
    from concurrent.futures import ThreadPoolExecutor
    from sqlalchemy import create_engine, update
    from app.queries import source_gates
    credential = uuid4().hex
    with configured_client(database_url, tmp_path, executor_credential=credential) as observer:
        with live_api(database_url, credential, rate_limited=new_limit, query=not new_limit) as (owner, process):
            path, _, evidence, auth = started(owner, credential)
            with ThreadPoolExecutor(max_workers=1) as pool:
                pending = pool.submit(owner.post, evidence.removesuffix('/evidence') + '/queries',
                                      headers={**auth, 'Idempotency-Key': str(uuid4())})
                try:
                    waiting = await_view(observer, path, lambda v: bool(v['attempts'][0]['queries']) and
                        v['attempts'][0]['queries'][0]['sources'][0]['state'] == ('waiting' if new_limit else 'querying'))
                    retry_at = (waiting['attempts'][0]['queries'][0]['sources'][0]['retry_not_before'] if new_limit
                                else datetime.now().astimezone().isoformat())
                    if gate_state != 'material':
                        # 只安排更严格的既存闸门/不同所有权；限流材料仍由真实来源和进程产生。
                        engine = create_engine(database_url)
                        try:
                            with engine.begin() as connection:
                                values = ({'query_id': uuid4()} if gate_state == 'other_query' else
                                          {'blocked': True, 'retry_at': None if gate_state == 'indefinite' else
                                           datetime.fromisoformat(retry_at) + timedelta(minutes=5)})
                                connection.execute(update(source_gates).values(**values))
                        finally:
                            engine.dispose()
                finally:
                    process.terminate()
                    process.wait(timeout=10)
                with pytest.raises(httpx.TransportError):
                    pending.result(15)
        ended = await_view(observer, path, lambda v: v['status'] == '失败')
        assert ended['attempts'][0]['queries'][0]['status'] == 'interrupted'
        _, _, second_evidence, second_auth = started(observer, credential)
        reply = observer.post(second_evidence.removesuffix('/evidence') + '/queries',
                              headers={**second_auth, 'Idempotency-Key': str(uuid4())})
        assert reply.status_code == 409, reply.text
        source = reply.json()['detail']['sources'][0]
        assert source['reason'] == ('inflight' if gate_state == 'other_query' else 'rate_limited')
        if gate_state in {'indefinite', 'other_query'}:
            assert source['retry_not_before'] is None
        else:
            expected = datetime.fromisoformat(retry_at) + (timedelta(minutes=5) if gate_state == 'stricter' else timedelta())
            assert datetime.fromisoformat(source['retry_not_before']) == expected


@pytest.mark.parametrize('gate_state', ['indefinite', 'stricter', 'other_query'])
def test_live_lost_waiting_query_keeps_stricter_gate_via_other_investigation(database_url, tmp_path, gate_state):
    from concurrent.futures import ThreadPoolExecutor
    from sqlalchemy import create_engine, update
    from app.queries import source_gates
    credential = uuid4().hex
    with configured_client(database_url, tmp_path, executor_credential=credential) as observer:
        with live_api(database_url, credential, rate_limited=True) as (owner, process):
            path, _, evidence, auth = started(owner, credential)
            endpoint = evidence.removesuffix('/evidence')
            with ThreadPoolExecutor(max_workers=1) as pool:
                pending = pool.submit(owner.post, endpoint + '/queries',
                                      headers={**auth, 'Idempotency-Key': str(uuid4())})
                waiting = await_view(observer, path, lambda v: bool(v['attempts'][0]['queries']) and
                    v['attempts'][0]['queries'][0]['sources'][0]['state'] == 'waiting')
                expected_retry = datetime.fromisoformat(waiting['attempts'][0]['queries'][0]['sources'][0]['retry_not_before']) + timedelta(minutes=5)
                engine = create_engine(database_url)
                try:
                    with engine.begin() as connection:
                        values = {'blocked': True, 'retry_at': None if gate_state == 'indefinite' else expected_retry}
                        if gate_state == 'other_query':
                            values['query_id'] = uuid4()
                        connection.execute(update(source_gates).values(**values))
                finally:
                    engine.dispose()
                assert observer.post(endpoint + '/lost', headers=auth).status_code == 200
                assert pending.result(10).status_code == 403
            assert process.poll() is None
            assert observer.get(path).json()['attempts'][0]['queries'][0]['status'] == 'interrupted'
            _, _, other_evidence, other_auth = started(observer, credential)
            reply = observer.post(other_evidence.removesuffix('/evidence') + '/queries',
                                  headers={**other_auth, 'Idempotency-Key': str(uuid4())})
            assert reply.status_code == 409, reply.text
            source = reply.json()['detail']['sources'][0]
            assert source['reason'] == ('inflight' if gate_state == 'other_query' else 'rate_limited')
            if gate_state == 'indefinite':
                assert source['retry_not_before'] is None
            else:
                assert datetime.fromisoformat(source['retry_not_before']) == expected_retry


@pytest.mark.parametrize('exit_path', ['normal', 'cancel', 'timeout', 'lost'])
@pytest.mark.parametrize('gate_state', ['indefinite', 'stricter', 'other_query'])
@pytest.mark.parametrize('new_limit', ['none', 'finite', 'unknown'])
def test_all_live_release_paths_preserve_shared_gate_via_api(database_url, tmp_path, exit_path, gate_state, new_limit):
    import asyncio
    from threading import Event
    from concurrent.futures import ThreadPoolExecutor
    from sqlalchemy import create_engine, update
    from app.queries import source_gates, SourceFailure
    from test_deadlines import ManualTicks
    entered, release = Event(), Event()
    now = [datetime.now().astimezone()]
    class Source:
        async def query(self, **scope):
            entered.set()
            try:
                while not release.is_set():
                    await asyncio.sleep(0.01)
            except asyncio.CancelledError:
                if new_limit == 'none':
                    raise
            if new_limit != 'none':
                raise SourceFailure('rate_limited', retry_after=60 if new_limit == 'finite' else None)
    credential = uuid4().hex
    with configured_client(database_url, tmp_path, executor_credential=credential, clock=lambda: now[0],
                           deadline_schedule=ManualTicks(), query_sources={'controlled-orders': Source()}) as client:
        path, initial, evidence, auth = started(client, credential)
        endpoint = evidence.removesuffix('/evidence')
        expected_retry = now[0] + timedelta(minutes=5)
        engine = create_engine(database_url)
        with ThreadPoolExecutor(max_workers=1) as pool:
            pending = pool.submit(client.post, endpoint + '/queries',
                                  headers={**auth, 'Idempotency-Key': str(uuid4())})
            try:
                assert entered.wait(5)
                # SQL仅安排既存限制/归属；最终可用性和期限全部从另一调查API观察。
                with engine.begin() as connection:
                    values = {'blocked': True, 'retry_at': None if gate_state == 'indefinite' else expected_retry}
                    if gate_state == 'other_query':
                        values['query_id'] = uuid4()
                    connection.execute(update(source_gates).values(**values))
                if exit_path == 'cancel':
                    assert client.post(path + '/attempts/' + initial['attempts'][0]['id'] + '/cancel').status_code == 200
                elif exit_path == 'timeout':
                    now[0] += timedelta(minutes=11)
                    assert client.post(evidence, json=material(), headers=auth).status_code == 403
                    # 另一调查使用当前时间；安排的有限限制仍应有效。
                    expected_retry = now[0] + timedelta(minutes=5)
                    with engine.begin() as connection:
                        connection.execute(update(source_gates).values(
                            retry_at=None if gate_state == 'indefinite' else expected_retry))
                elif exit_path == 'lost':
                    assert client.post(endpoint + '/lost', headers=auth).status_code == 200
            finally:
                release.set()
                engine.dispose()
            reply = pending.result(10)
            assert reply.status_code == (201 if exit_path == 'normal' else 403), reply.text
        _, _, other_evidence, other_auth = started(client, credential)
        reply = client.post(other_evidence.removesuffix('/evidence') + '/queries',
                            headers={**other_auth, 'Idempotency-Key': str(uuid4())})
        assert reply.status_code == 409, reply.text
        source = reply.json()['detail']['sources'][0]
        assert source['reason'] == ('inflight' if gate_state == 'other_query' else 'rate_limited')
        if gate_state == 'indefinite' or (new_limit == 'unknown' and gate_state != 'other_query'):
            assert source['retry_not_before'] is None
        else:
            assert datetime.fromisoformat(source['retry_not_before']) == expected_retry


@pytest.mark.parametrize('unknown_retry', [None, 0, -1, 'unknown', float('inf'), float('nan')])
def test_unknown_new_source_retry_does_not_reuse_previous_finite_limit(database_url, tmp_path, unknown_retry):
    from app.queries import SourceFailure
    from test_queries import Schedule
    class Source:
        calls = 0
        async def query(self, **scope):
            self.calls += 1
            raise SourceFailure('rate_limited', retry_after=30 if self.calls == 1 else unknown_retry)
    credential = uuid4().hex
    with configured_client(database_url, tmp_path, executor_credential=credential, query_schedule=Schedule(),
                           query_sources={'controlled-orders': Source()}) as client:
        _, _, evidence, auth = started(client, credential)
        reply = client.post(evidence.removesuffix('/evidence') + '/queries',
                            headers={**auth, 'Idempotency-Key': str(uuid4())})
        assert reply.status_code == 201, reply.text
        _, _, other_evidence, other_auth = started(client, credential)
        reply = client.post(other_evidence.removesuffix('/evidence') + '/queries',
                            headers={**other_auth, 'Idempotency-Key': str(uuid4())})
        assert reply.status_code == 409, reply.text
        assert reply.json()['detail']['sources'][0]['retry_not_before'] is None


@pytest.mark.parametrize('events', list(permutations(('complete', 'cancel', 'timeout', 'lost'))))
def test_four_terminal_events_follow_persisted_ownership(database_url, tmp_path, events):
    from test_deadlines import ManualTicks
    credential = uuid4().hex
    with live_api(database_url, credential) as (owner, process):
        path, initial, evidence, auth = started(owner, credential)
        now = [datetime.fromisoformat(initial['attempts'][0]['created_at'].replace('Z', '+00:00'))]
        endpoint = evidence.removesuffix('/evidence')
        with configured_client(database_url, tmp_path, executor_credential=credential,
                               clock=lambda: now[0], deadline_schedule=ManualTicks()) as observer:
            for index, event in enumerate(events):
                if event == 'complete':
                    reply = observer.post(endpoint + '/conclusions', json=proposal(),
                                          headers={**auth, 'Idempotency-Key': str(uuid4())})
                    assert reply.status_code == (201 if index == 0 else 403), reply.text
                elif event == 'cancel':
                    reply = observer.post(path + '/attempts/' + initial['attempts'][0]['id'] + '/cancel')
                    assert reply.status_code == 200, reply.text
                    assert reply.json() == observer.get(path).json()
                elif event == 'timeout':
                    now[0] += timedelta(minutes=11)
                    reply = observer.post(evidence, json=material(), headers=auth)
                    assert reply.status_code == 403, reply.text
                else:
                    reply = observer.post(endpoint + '/lost', headers=auth)
                    assert reply.status_code == 200, reply.text
                    assert reply.json() == observer.get(path).json()
                observed = observer.get(path).json()['attempts'][0]
                assert observed['cancel_requested_at'] is None if events[0] != 'cancel' else observed['cancel_requested_at'] is not None
                assert observed['timeout_requested_at'] is None if events[0] != 'timeout' else observed['timeout_requested_at'] is not None
            before_exit = observer.get(path).json()
            if events[0] == 'lost':
                assert before_exit['status'] == '调查中'
                assert before_exit['attempts'][0]['execution_phase'] == 'confirming_execution'
            assert process.poll() is None
            process.terminate()
            process.wait(timeout=10)
            # 真实退出后的后台协调由新应用执行；clock保持本测试推进值。
        with configured_client(database_url, tmp_path, executor_credential=credential, clock=lambda: now[0]) as observer:
            final = await_view(observer, path, lambda v: v['status'] != '调查中')
            expected = {'complete': ('已完成', 'normal_completion'), 'cancel': ('已取消', 'user_canceled'),
                        'timeout': ('失败', 'attempt_deadline_exceeded'), 'lost': ('失败', 'service_interrupted')}
            assert (final['status'], final['attempts'][0]['end_reason']) == expected[events[0]]
            assert len(final['attempts']) == 1
            assert observer.post(endpoint + '/lost', headers=auth).json() == final
            assert observer.get(path).json() == final


@pytest.mark.parametrize('other', ['complete', 'cancel', 'timeout'])
@pytest.mark.parametrize('lost_first', [False, True])
def test_lost_competes_under_real_investigation_lock(database_url, tmp_path, other, lost_first):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Event, current_thread
    from sqlalchemy import event
    from sqlalchemy.engine import Engine
    from test_deadlines import ManualTicks
    credential = uuid4().hex
    with live_api(database_url, credential, coordinate=False) as (owner, process):
        path, initial, evidence, auth = started(owner, credential)
        now = [datetime.fromisoformat(initial['attempts'][0]['created_at'].replace('Z', '+00:00'))]
        endpoint = evidence.removesuffix('/evidence')
        with configured_client(database_url, tmp_path, executor_credential=credential,
                               clock=lambda: now[0], deadline_schedule=ManualTicks()) as observer, \
                configured_client(database_url, tmp_path, executor_credential=credential,
                                  clock=lambda: now[0], deadline_schedule=ManualTicks()) as rival:
            acquired, competing, release = Event(), Event(), Event()
            holder = []
            def pause(connection, cursor, statement, parameters, context, executemany):
                if statement.startswith('SELECT investigations.') and 'FOR UPDATE' in statement:
                    if not acquired.is_set():
                        holder.append(current_thread().ident)
                        acquired.set()
                        assert release.wait(15)
            def attempted(connection, cursor, statement, parameters, context, executemany):
                if (acquired.is_set() and statement.startswith('SELECT investigations.') and 'FOR UPDATE' in statement
                        and current_thread().ident != holder[0]):
                    competing.set()
            def advance_after_lost_sample(connection):
                if lost_first and other == 'timeout' and holder and current_thread().ident == holder[0]:
                    # 最终lost采样已完成，COMMIT边界推进时钟；竞争者仍须等待真实PG行锁。
                    now[0] = datetime.fromisoformat(initial['attempts'][0]['created_at'].replace('Z', '+00:00')) + timedelta(minutes=11)
            def send(client, kind):
                if kind == 'lost':
                    return client.post(endpoint + '/lost', headers=auth)
                if kind == 'complete':
                    return client.post(endpoint + '/conclusions', json=proposal(),
                                       headers={**auth, 'Idempotency-Key': str(uuid4())})
                if kind == 'cancel':
                    return client.post(path + '/attempts/' + initial['attempts'][0]['id'] + '/cancel')
                return client.post(evidence, json=material(), headers=auth)
            if other == 'timeout' and not lost_first:
                now[0] += timedelta(minutes=11)
            event.listen(Engine, 'after_cursor_execute', pause)
            event.listen(Engine, 'before_cursor_execute', attempted)
            event.listen(Engine, 'commit', advance_after_lost_sample)
            try:
                with ThreadPoolExecutor(max_workers=2) as pool:
                    first = pool.submit(send, observer, 'lost' if lost_first else other)
                    try:
                        assert acquired.wait(5)
                        second = pool.submit(send, rival, other if lost_first else 'lost')
                        assert competing.wait(5)
                        assert not second.done(), '第二请求必须真实等待PG行锁'
                    finally:
                        release.set()
                    replies = [first.result(15), second.result(15)]
            finally:
                release.set()
                event.remove(Engine, 'after_cursor_execute', pause)
                event.remove(Engine, 'before_cursor_execute', attempted)
                event.remove(Engine, 'commit', advance_after_lost_sample)
            expected_codes = ([200, 403 if other in {'complete', 'timeout'} else 200] if lost_first
                              else [201 if other == 'complete' else 403 if other == 'timeout' else 200, 200])
            assert [reply.status_code for reply in replies] == expected_codes, [r.text for r in replies]
            view = observer.get(path).json()
            if lost_first:
                assert view['status'] == '调查中'
                assert view['attempts'][0]['execution_phase'] == 'confirming_execution'
                assert view['attempts'][0]['cancel_requested_at'] is None
                assert view['attempts'][0]['timeout_requested_at'] is None
            else:
                assert view['attempts'][0]['end_reason'] == {'complete': 'normal_completion',
                    'cancel': 'user_canceled', 'timeout': 'attempt_deadline_exceeded'}[other]
        process.terminate()
        process.wait(timeout=10)
        with configured_client(database_url, tmp_path, executor_credential=credential, clock=lambda: now[0]) as observer:
            ended = await_view(observer, path, lambda v: v['status'] != '调查中')
            assert ended['attempts'][0]['end_reason'] == ('service_interrupted' if lost_first else
                {'complete': 'normal_completion', 'cancel': 'user_canceled', 'timeout': 'attempt_deadline_exceeded'}[other])
            assert len(ended['attempts']) == 1


def test_lost_signal_stops_live_inflight_adapter_without_pretending_process_exit(database_url, tmp_path):
    import asyncio
    from threading import Event
    from concurrent.futures import ThreadPoolExecutor
    entered, exited = Event(), Event()
    class Source:
        async def query(self, **scope):
            entered.set()
            try:
                await asyncio.Future()
            finally:
                exited.set()
    credential = uuid4().hex
    with configured_client(database_url, tmp_path, executor_credential=credential,
                           query_sources={'controlled-orders': Source()}) as client:
        path, _, evidence, auth = started(client, credential)
        endpoint = evidence.removesuffix('/evidence')
        with ThreadPoolExecutor(max_workers=1) as pool:
            pending = pool.submit(client.post, endpoint + '/queries', headers={**auth, 'Idempotency-Key': str(uuid4())})
            try:
                assert entered.wait(5)
                assert client.post(endpoint + '/lost', headers=auth).status_code == 200
                assert exited.wait(3), '持久失联必须停止安排并通知实际在途适配器'
            finally:
                client.post(path + '/attempts/' + client.get(path).json()['attempts'][0]['id'] + '/cancel')
            assert pending.result(35).status_code == 403
        view = client.get(path).json()
        assert view['status'] == '调查中'
        assert view['attempts'][0]['execution_phase'] == 'confirming_execution'
        assert view['attempts'][0]['queries'][0]['status'] == 'interrupted'
        assert view['attempts'][0]['cancel_requested_at'] is None


def test_database_failure_during_isolation_is_not_reported_as_success(database_url, tmp_path):
    from sqlalchemy import event
    from sqlalchemy.engine import Engine
    from test_deadlines import ManualTicks
    credential = uuid4().hex
    with configured_client(database_url, tmp_path, executor_credential=credential,
                           deadline_schedule=ManualTicks()) as client:
        path, _, evidence, auth = started(client, credential)
        before = client.get(path).json()
        def fail(connection, cursor, statement, parameters, context, executemany):
            if statement.startswith('UPDATE attempts') and 'lost_requested_at' in parameters:
                cursor.execute('SELECT 1 / 0')
        event.listen(Engine, 'before_cursor_execute', fail)
        try:
            reply = client.post(evidence.removesuffix('/evidence') + '/lost', headers=auth)
        finally:
            event.remove(Engine, 'before_cursor_execute', fail)
        assert reply.status_code == 503
        assert client.get(path).json() == before
        assert client.post(evidence, json=material(), headers=auth).status_code == 201


def test_lost_signal_crossing_deadline_uses_final_arbitration_sample(database_url, tmp_path):
    from sqlalchemy import event
    from sqlalchemy.engine import Engine
    from datetime import timezone
    from test_deadlines import ManualTicks
    now = [datetime(2026, 11, 3, 12, tzinfo=timezone.utc)]
    credential = uuid4().hex
    with configured_client(database_url, tmp_path, executor_credential=credential, clock=lambda: now[0],
                           deadline_schedule=ManualTicks()) as client:
        path, _, evidence, auth = started(client, credential)
        now[0] += timedelta(seconds=599)
        def advance(connection, cursor, statement, parameters, context, executemany):
            if statement.startswith('UPDATE attempts') and 'lost_requested_at' in parameters:
                now[0] += timedelta(seconds=2)
        event.listen(Engine, 'before_cursor_execute', advance)
        try:
            reply = client.post(evidence.removesuffix('/evidence') + '/lost', headers=auth)
        finally:
            event.remove(Engine, 'before_cursor_execute', advance)
        assert reply.status_code == 200
        attempt = client.get(path).json()['attempts'][0]
        assert attempt['timeout_requested_at'] is not None
        assert attempt['lost_requested_at'] is not None
        assert attempt['execution_phase'] == 'confirming_execution'


def test_background_lost_crossing_deadline_uses_final_sample(database_url, tmp_path):
    from sqlalchemy import event
    from sqlalchemy.engine import Engine
    from threading import Event
    import asyncio
    class Ticks:
        def __init__(self):
            self.tick = Event()
        async def wait(self):
            while not self.tick.is_set():
                await asyncio.sleep(0.01)
            self.tick.clear()
    ticks = Ticks()
    credential = uuid4().hex
    with live_api(database_url, credential) as (owner, process):
        path, initial, _, _ = started(owner, credential)
        now = [datetime.fromisoformat(initial['attempts'][0]['created_at'].replace('Z', '+00:00')) + timedelta(seconds=599)]
        with configured_client(database_url, tmp_path, executor_credential=credential,
                               clock=lambda: now[0], deadline_schedule=ticks) as observer:
            process.terminate()
            process.wait(timeout=10)
            def advance(connection, cursor, statement, parameters, context, executemany):
                if statement.startswith('UPDATE attempts') and 'lost_requested_at' in parameters:
                    now[0] += timedelta(seconds=2)
            event.listen(Engine, 'before_cursor_execute', advance)
            try:
                ticks.tick.set()
                isolated = await_view(observer, path, lambda v: v['attempts'][0]['lost_requested_at'] is not None)
            finally:
                event.remove(Engine, 'before_cursor_execute', advance)
            assert isolated['attempts'][0]['timeout_requested_at'] is not None
            ticks.tick.set()
            ended = await_view(observer, path, lambda v: v['status'] == '失败')
            assert ended['attempts'][0]['end_reason'] == 'attempt_deadline_exceeded'


def test_unknown_legacy_protocol_never_confirms_on_restart_or_invalid_identity(database_url, tmp_path):
    from app.attempts import ControlledExecutionAdapter
    class Unknown(ControlledExecutionAdapter):
        pass
    credential = uuid4().hex
    with configured_client(database_url, tmp_path, executor_credential=credential,
                           execution_adapter=Unknown()) as client:
        path, initial, evidence, auth = started(client, credential)
        endpoint = evidence.removesuffix('/evidence')
        waiting = client.post(endpoint + '/lost', headers=auth).json()
    with configured_client(database_url, tmp_path, executor_credential=credential) as restarted:
        assert restarted.get(path).json() == waiting
        assert restarted.post(path + '/attempts/' + initial['attempts'][0]['id'] + '/cancel').json() == waiting
        assert restarted.post(endpoint + '/lost', headers=auth).json() == waiting
        assert restarted.post(path + '/attempts', json={'service_id': 'orders'},
            headers={'Idempotency-Key': str(uuid4())}).status_code == 409


def test_confirmation_transaction_failure_keeps_fence_and_material(database_url, tmp_path):
    from sqlalchemy import event
    from sqlalchemy.engine import Engine
    from threading import Event
    failed = Event()
    credential = uuid4().hex
    with configured_client(database_url, tmp_path, executor_credential=credential) as observer:
        with live_api(database_url, credential) as (owner, process):
            path, _, evidence, auth = started(owner, credential)
            observer.post(evidence.removesuffix('/evidence') + '/lost', headers=auth)
            def fail(connection, cursor, statement, parameters, context, executemany):
                if statement.startswith('UPDATE attempts') and parameters.get('end_reason') == 'service_interrupted':
                    failed.set()
                    cursor.execute('SELECT 1 / 0')
            event.listen(Engine, 'before_cursor_execute', fail)
            try:
                process.terminate()
                process.wait(timeout=10)
                assert failed.wait(5)
                waiting = observer.get(path).json()
                assert waiting['status'] == '调查中'
                assert waiting['attempts'][0]['stop_confirmed_at'] is None
                assert waiting['retention_expires_at'] is None
                assert observer.post(evidence, json=material(), headers=auth).status_code == 403
            finally:
                event.remove(Engine, 'before_cursor_execute', fail)
        ended = await_view(observer, path, lambda v: v['status'] == '失败')
        assert ended['attempts'][0]['lost_requested_at'] == waiting['attempts'][0]['lost_requested_at']
        ended_at = datetime.fromisoformat(ended['attempts'][0]['ended_at'].replace('Z', '+00:00'))
        assert ended['retention_expires_at'] is None


@pytest.mark.parametrize('other_event', ['deadline', 'process_exit'])
def test_failed_confirmation_does_not_starve_other_investigation(database_url, tmp_path, other_event):
    from sqlalchemy import event
    from sqlalchemy.engine import Engine
    from threading import Event
    failed = Event()
    credential = uuid4().hex
    now = [datetime.now().astimezone()]
    with configured_client(database_url, tmp_path, executor_credential=credential, clock=lambda: now[0]) as observer:
        with live_api(database_url, credential) as (owner, process):
            bad_path, bad_initial, evidence, auth = started(owner, credential)
            assert observer.post(evidence.removesuffix('/evidence') + '/lost', headers=auth).status_code == 200
            bad_id = bad_initial['attempts'][0]['id']
            def fail(connection, cursor, statement, parameters, context, executemany):
                if (statement.startswith('UPDATE attempts') and parameters.get('end_reason') == 'service_interrupted'
                        and str(parameters.get('id_1')) == bad_id):
                    failed.set()
                    cursor.execute('SELECT 1 / 0')
            event.listen(Engine, 'before_cursor_execute', fail)
            try:
                process.terminate()
                process.wait(timeout=10)
                assert failed.wait(5)
                if other_event == 'deadline':
                    other_path, _, _, _ = started(observer, credential)
                    now[0] += timedelta(minutes=11)
                    expected = 'attempt_deadline_exceeded'
                    ended = await_view(observer, other_path, lambda v: v['status'] == '失败')
                else:
                    with live_api(database_url, credential, coordinate=False) as (other_owner, other_process):
                        other_path, _, _, _ = started(other_owner, credential)
                    assert other_process.poll() is not None
                    expected = 'service_interrupted'
                    ended = await_view(observer, other_path, lambda v: v['status'] == '失败')
                assert ended['attempts'][0]['end_reason'] == expected
                waiting = observer.get(bad_path).json()
                assert waiting['status'] == '调查中'
                assert waiting['attempts'][0]['lost_requested_at']
                assert waiting['attempts'][0]['stop_confirmed_at'] is None
                assert observer.post(evidence, json=material(), headers=auth).status_code == 403
            finally:
                event.remove(Engine, 'before_cursor_execute', fail)
        assert await_view(observer, bad_path, lambda v: v['status'] == '失败')['attempts'][0]['end_reason'] == 'service_interrupted'


@pytest.mark.parametrize('database_url', ['0011'], indirect=True)
def test_real_upgrade_does_not_invent_exit_proof_for_existing_execution(database_url, tmp_path):
    from sqlalchemy import create_engine, text
    from uuid import UUID
    # 旧版本SQL仅安排升级输入，不伪造本次运行成功或停止事实。
    engine = create_engine(database_url)
    parent, attempt, execution = uuid4(), uuid4(), uuid4()
    with engine.begin() as connection:
        connection.execute(text("INSERT INTO investigations (id, title, description, created_at, updated_at) VALUES (:id, '旧轮', '', now(), now())"), {'id': parent})
        connection.execute(text("INSERT INTO attempts (id, investigation_id, request_key, request_body, attempt_number, service_id, source_ids, starts_at, ends_at, created_at, deadline_at, status) VALUES (:id, :parent, :key, '{}', 1, 'orders', '[]', now()-interval '1 hour', now(), now(), now()+interval '10 minutes', '调查中')"), {'id': attempt, 'parent': parent, 'key': uuid4()})
        connection.execute(text("INSERT INTO controlled_executions (id, attempt_id, valid, stop_protocol) VALUES (:id, :attempt, true, 'controlled_queries_v1')"), {'id': execution, 'attempt': attempt})
        connection.execute(text('INSERT INTO investigation_redaction_keys (investigation_id, secret) VALUES (:id, :secret)'), {'id': parent, 'secret': os.urandom(32)})
    engine.dispose()
    result = subprocess.run([sys.executable, '-m', 'alembic', '-c', 'backend/alembic.ini', 'upgrade', 'head'],
        env={**os.environ, 'DATABASE_URL': database_url}, capture_output=True)
    assert result.returncode == 0, result.stderr
    credential = uuid4().hex
    with configured_client(database_url, tmp_path, executor_credential=credential) as client:
        path = '/api/investigations/' + str(parent)
        endpoint = f'/api/executor/investigations/{parent}/attempts/{attempt}/executions/{execution}/lost'
        view = client.post(endpoint, headers={'Authorization': 'Bearer ' + credential}).json()
        assert view['status'] == '调查中'
        assert view['attempts'][0]['stop_confirmed_at'] is None
        assert view['attempts'][0]['execution_phase'] == 'confirming_execution'
        assert client.get(path).json() == view


def test_owner_crash_is_detected_without_page_or_lost_request(database_url, tmp_path):
    credential = uuid4().hex
    with configured_client(database_url, tmp_path, executor_credential=credential) as observer:
        with live_api(database_url, credential) as (owner, process):
            path, _, _, _ = started(owner, credential)
            original = observer.get(path).json()
            assert process.poll() is None
            assert observer.get(path).json() == original
        assert process.returncode is not None
        ended = await_view(observer, path, lambda v: v['status'] == '失败')
        assert ended['attempts'][0]['lost_requested_at']
        assert ended['attempts'][0]['end_reason'] == 'service_interrupted'


def test_lost_signal_fences_writes_but_live_process_keeps_running_qualification(database_url, tmp_path):
    credential = uuid4().hex
    with configured_client(database_url, tmp_path, executor_credential=credential) as client:
        path, before, evidence, auth = started(client, credential)
        endpoint = evidence.removesuffix('/evidence')
        assert client.post(evidence, json=material(), headers=auth).status_code == 201
        headers = {**auth, 'Idempotency-Key': str(uuid4())}
        assert client.post(endpoint + '/conclusions', json=proposal(finish=False), headers=headers).status_code == 201
        lost = client.post(endpoint + '/lost', headers=auth)
        assert lost.status_code == 200, lost.text
        view = lost.json()
        attempt = view['attempts'][0]
        assert view['status'] == '调查中'
        assert attempt['execution_phase'] == 'confirming_execution'
        assert attempt['lost_requested_at']
        assert attempt['stop_confirmed_at'] is None
        assert view['retention_expires_at'] is None
        assert client.get(path).json() == view
        assert client.post(endpoint + '/lost', headers=auth).json() == view
        assert client.post(evidence, json=material(), headers=auth).status_code == 403
        assert client.post(endpoint + '/conclusions', json=proposal(finish=False), headers=headers).status_code == 403
        assert client.post(endpoint + '/queries', headers=headers).status_code == 403
        assert client.post(path + '/attempts', json={'service_id': 'orders'},
                           headers={'Idempotency-Key': str(uuid4())}).status_code == 409
        assert len(client.get(path).json()['attempts'][0]['evidence']) == 1
