import os
from datetime import datetime, timedelta
from uuid import uuid4

import httpx
import pytest
from playwright.sync_api import sync_playwright, expect


BASE = os.environ.get("UI_BASE_URL", "http://127.0.0.1:5173")


def test_capacity_ui_explains_no_start_and_only_manual_retry(evidence_executor):
    with httpx.Client(base_url=BASE, trust_env=False, timeout=20) as client, sync_playwright() as p:
        records = [client.post('/api/investigations', json={'title': '容量-' + uuid4().hex[:8]},
                              headers={'Idempotency-Key': str(uuid4())}).json() for _ in range(4)]
        running = [client.post(f'/api/investigations/{record["id"]}/attempts', json={'service_id': 'orders'},
                               headers={'Idempotency-Key': str(uuid4())}).json() for record in records[:3]]
        target = records[3]
        path = f'/api/investigations/{target["id"]}'
        browser = p.chromium.launch(headless=True)
        try:
            page = browser.new_page()
            page.goto(BASE + '/#' + path.removeprefix('/api'))
            expect(page.get_by_role('status').filter(has_text='实例最多同时运行3个调查')).to_contain_text('满额不排队')
            page.get_by_label('被调查服务', exact=True).select_option('orders')
            page.get_by_role('button', name='启动调查', exact=True).click()
            expect(page.get_by_role('alert')).to_contain_text('尚未启动，不排队，请稍后手动重试')
            expect(page.get_by_label('被调查服务', exact=True)).to_be_enabled()
            assert client.get(path).json() == target
            first = running[0]
            assert client.post(f'/api/investigations/{first["id"]}/attempts/{first["attempts"][0]["id"]}/cancel').json()['status'] == '已取消'
            page.reload()
            expect(page.get_by_role('button', name='启动调查', exact=True)).to_be_visible()
            assert client.get(path).json() == target
            page.get_by_label('被调查服务', exact=True).select_option('orders')
            page.get_by_role('button', name='启动调查', exact=True).click()
            expect(page.get_by_role('button', name='取消调查', exact=True)).to_be_visible()
            assert len(client.get(path).json()['attempts']) == 1
        finally:
            browser.close()


def test_lost_execution_ui_keeps_waiting_and_refresh_is_read_only(evidence_executor):
    with httpx.Client(base_url=BASE, trust_env=False, timeout=20) as client, sync_playwright() as p:
        record = client.post('/api/investigations', json={'title': '失联确认-' + uuid4().hex[:8]},
                             headers={'Idempotency-Key': str(uuid4())}).json()
        path = '/api/investigations/' + record['id']
        started = client.post(path + '/attempts', json={'service_id': 'orders'},
                              headers={'Idempotency-Key': str(uuid4())}).json()
        attempt = started['attempts'][0]
        endpoint = f'/api/executor/investigations/{record["id"]}/attempts/{attempt["id"]}/executions'
        execution = client.get(endpoint, headers=evidence_executor).json()['executions'][0]['id']
        waiting = client.post(endpoint + '/' + execution + '/lost', headers=evidence_executor).json()
        browser = p.chromium.launch(headless=True)
        try:
            page = browser.new_page()
            page.goto(BASE + '/#/investigations/' + record['id'])
            expect(page.get_by_role('status').filter(has_text='正在确认执行状态')).to_contain_text('正在确认执行状态')
            expect(page.get_by_role('button', name='取消调查', exact=True)).to_be_disabled()
            expect(page.get_by_role('button', name='启动新一轮', exact=True)).to_have_count(0)
            page.reload()
            expect(page.get_by_role('status').filter(has_text='正在确认执行状态')).to_contain_text('不能启动新轮')
            assert client.get(path).json() == waiting
        finally:
            browser.close()


def test_interruption_failure_reason_is_visible_in_round_history(evidence_executor):
    import subprocess
    import socket
    import sys
    import time
    with socket.socket() as listener:
        listener.bind(('127.0.0.1', 0))
        port = listener.getsockname()[1]
    process = subprocess.Popen([sys.executable, '-m', 'uvicorn', 'app.main:app', '--host', '127.0.0.1', '--port', str(port)],
        env={**os.environ, 'DATABASE_URL': evidence_executor.database_url,
             'SERVICES_FILE': 'backend/services.example.json'}, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        with httpx.Client(base_url=f'http://127.0.0.1:{port}', trust_env=False, timeout=10) as owner:
            limit = time.monotonic() + 20
            while True:
                assert process.poll() is None
                try:
                    if owner.get('/api/health').status_code == 200:
                        break
                except httpx.TransportError:
                    pass
                assert time.monotonic() < limit
            record = owner.post('/api/investigations', json={'title': '中断历史-' + uuid4().hex[:8]},
                                headers={'Idempotency-Key': str(uuid4())}).json()
            path = '/api/investigations/' + record['id']
            assert owner.post(path + '/attempts', json={'service_id': 'orders'},
                              headers={'Idempotency-Key': str(uuid4())}).status_code == 201
        process.terminate()
        process.wait(timeout=15)
        assert process.poll() is not None
        with httpx.Client(base_url=BASE, trust_env=False, timeout=20) as client, sync_playwright() as p:
            limit = time.monotonic() + 10
            while client.get(path).json()['status'] == '调查中':
                assert time.monotonic() < limit
            browser = p.chromium.launch(headless=True)
            try:
                page = browser.new_page()
                page.goto(BASE + '/#/investigations/' + record['id'])
                expect(page.get_by_role('region', name='调查详情')).to_contain_text('服务中断，已确认旧执行退出并隔离写入')
                page.get_by_label('被调查服务', exact=True).select_option('orders')
                page.get_by_role('button', name='启动新一轮', exact=True).click()
                expect(page.get_by_role('region', name='调查详情')).to_contain_text('服务中断，已确认旧执行退出并隔离写入')
                page.reload()
                expect(page.get_by_role('region', name='调查详情')).to_contain_text('服务中断，已确认旧执行退出并隔离写入')
            finally:
                browser.close()
    finally:
        if process.poll() is None:
            process.terminate()
        process.wait(timeout=15)


def test_each_confirmed_round_resets_absolute_form_to_recent_hour():
    with httpx.Client(base_url=BASE, trust_env=False, timeout=20) as client, sync_playwright() as p:
        record = client.post('/api/investigations', json={'title': '每轮默认-' + uuid4().hex[:8]},
                             headers={'Idempotency-Key': str(uuid4())}).json()
        browser = p.chromium.launch(headless=True)
        try:
            page = browser.new_page()
            page.goto(BASE + '/#/investigations/' + record['id'])
            page.get_by_label('被调查服务', exact=True).select_option('orders')
            page.get_by_label('范围模式').select_option('absolute')
            page.get_by_label('日志开始时间').fill('2026-11-01T00:00:00Z')
            page.get_by_label('日志结束时间').fill('2026-11-01T01:00:00Z')
            page.get_by_role('button', name='启动调查', exact=True).click()
            page.get_by_role('button', name='取消调查', exact=True).click()
            expect(page.get_by_label('范围模式')).to_have_value('recent')
            page.get_by_label('范围模式').select_option('absolute')
            expect(page.get_by_label('日志开始时间')).to_have_value('')
            expect(page.get_by_label('日志结束时间')).to_have_value('')
            page.get_by_label('范围模式').select_option('recent')
            page.get_by_label('被调查服务', exact=True).select_option('orders')
            with page.expect_response('**/attempts') as reply:
                page.get_by_role('button', name='启动新一轮', exact=True).click()
            latest = reply.value.json()['attempts'][-1]
            assert reply.value.request.post_data_json == {'service_id': 'orders'}
            assert (datetime.fromisoformat(latest['ends_at'].replace('Z', '+00:00')) -
                    datetime.fromisoformat(latest['starts_at'].replace('Z', '+00:00'))).total_seconds() == 3600
        finally:
            browser.close()


@pytest.mark.parametrize('refresh', ['spa', 'reload', 'navigation', 'uppercase'])
def test_unconfirmed_start_survives_fast_terminal_refresh_and_isolates_investigations(refresh, evidence_executor):
    with httpx.Client(base_url=BASE, trust_env=False, timeout=20) as client, sync_playwright() as p:
        records = [client.post('/api/investigations', json={'title': '待确认-' + uuid4().hex[:8]},
                              headers={'Idempotency-Key': str(uuid4())}).json() for _ in range(2)]
        path = '/api/investigations/' + records[0]['id']
        browser = p.chromium.launch(headless=True)
        try:
            page = browser.new_page()
            requests = []
            def lose_after_terminal(route):
                requests.append((route.request.headers['idempotency-key'], route.request.post_data))
                reply = route.fetch()
                assert reply.status == 201
                attempt = reply.json()['attempts'][0]
                # 仅测试进程持有执行凭据，经真实受控API令已接纳轮快速结束。
                base = f'/api/executor/investigations/{records[0]["id"]}/attempts/{attempt["id"]}/executions'
                identity = client.get(base, headers=evidence_executor).json()['executions'][0]['id']
                assert client.post(base + '/' + identity + '/queries',
                    headers={**evidence_executor, 'Idempotency-Key': str(uuid4())}).status_code == 201
                assert client.get(path).json()['attempts'][0]['status'] == '失败'
                route.abort('failed')
            page.route('**/attempts', lose_after_terminal, times=1)
            route_path = path.removeprefix('/api')
            if refresh == 'uppercase':
                route_path = '/investigations/' + records[0]['id'].upper()
            page.goto(BASE + '/#' + route_path)
            page.get_by_label('被调查服务', exact=True).select_option('orders')
            page.get_by_label('范围模式').select_option('absolute')
            page.get_by_label('日志开始时间').fill('2026-11-01T00:00:00Z')
            page.get_by_label('日志结束时间').fill('2026-11-01T01:00:00Z')
            page.get_by_role('button', name='启动调查', exact=True).click()
            expect(page.get_by_role('alert')).to_be_visible()
            if refresh == 'spa':
                page.get_by_role('button', name='刷新调查状态').click()
            elif refresh in ('reload', 'uppercase'):
                page.reload()
            else:
                page.evaluate('(id) => location.hash = "/investigations/" + id', records[1]['id'])
                expect(page.get_by_role('heading', name=records[1]['title'], exact=True)).to_be_visible()
                expect(page.get_by_role('button', name='重试原请求并确认结果')).to_have_count(0)
                page.get_by_label('被调查服务', exact=True).select_option('query-mixed')
                page.get_by_role('button', name='启动调查', exact=True).click()
                expect(page.get_by_role('button', name='取消调查', exact=True)).to_be_visible()
                page.evaluate('(id) => location.hash = "/investigations/" + id', records[0]['id'])
            expect(page.get_by_role('heading', name=records[0]['title'], exact=True)).to_be_visible()
            expect(page.get_by_role('region', name='调查详情')).to_contain_text('启动结果待确认')
            expect(page.get_by_label('被调查服务', exact=True)).to_have_value('orders')
            expect(page.get_by_label('被调查服务', exact=True)).to_be_disabled()
            expect(page.get_by_label('范围模式')).to_have_value('absolute')
            expect(page.get_by_label('范围模式')).to_be_disabled()
            expect(page.get_by_label('日志开始时间')).to_have_value('2026-11-01T00:00:00Z')
            expect(page.get_by_label('日志结束时间')).to_be_disabled()
            expect(page.get_by_role('button', name='启动新一轮', exact=True)).to_have_count(0)
            with page.expect_response('**/attempts') as replay:
                page.get_by_role('button', name='重试原请求并确认结果').click()
            assert replay.value.status == 200
            assert (replay.value.request.headers['idempotency-key'], replay.value.request.post_data) == requests[0]
            assert len(client.get(path).json()['attempts']) == 1
            expect(page.get_by_role('button', name='启动新一轮', exact=True)).to_be_visible()
            expect(page.get_by_label('范围模式')).to_have_value('recent')
            storage = page.evaluate('JSON.stringify(sessionStorage)')
            assert evidence_executor['Authorization'] not in storage
            assert 'executions' not in storage
            page.get_by_label('被调查服务', exact=True).select_option('orders')
            with page.expect_response('**/attempts') as next_round:
                page.get_by_role('button', name='启动新一轮', exact=True).click()
            assert next_round.value.status == 201
            assert next_round.value.request.headers['idempotency-key'] != requests[0][0]
            assert len(client.get(path).json()['attempts']) == 2
        finally:
            browser.close()


@pytest.mark.parametrize(('starts_at', 'ends_at'), [
    ('2026-11-01T00:00Z', '2026-11-01T01:00Z'),
    ('2026-11-01T00:00+08:00', '2026-11-01T01:00+08:00'),
    ('2026-11-01T00:00+0800', '2026-11-01T01:00+0800'),
    ('2026-11-01T00:00z', '2026-11-01T01:00z'),
    ('2026-11-01T00:00:00Z', '2026-11-01T01:00:00Z'),
    ('2026-11-01T00:00:00,1234567Z', '2026-11-01T01:00:00,1234567Z'),
    ('2026-11-01T00:00:00.000001Z', '2026-11-01T00:00:00.000002Z'),
    ('2026-11-01T00:00:00.123456789Z', '2026-11-02T00:00:00.123456999Z'),
])
def test_supported_absolute_pending_survives_lost_response_reload(starts_at, ends_at):
    with httpx.Client(base_url=BASE, trust_env=False, timeout=20) as client, sync_playwright() as p:
        record = client.post('/api/investigations', json={'title': '时间格式恢复-' + uuid4().hex[:8]},
                             headers={'Idempotency-Key': str(uuid4())}).json()
        path = '/api/investigations/' + record['id']
        browser = p.chromium.launch(headless=True)
        try:
            page = browser.new_page()
            sent = []
            def lose(route):
                sent.append((route.request.headers['idempotency-key'], route.request.post_data))
                reply = route.fetch()
                assert reply.status == 201
                route.abort('failed')
            page.route('**/attempts', lose, times=1)
            page.goto(BASE + '/#/investigations/' + record['id'])
            page.get_by_label('被调查服务', exact=True).select_option('orders')
            page.get_by_label('范围模式').select_option('absolute')
            page.get_by_label('日志开始时间').fill(starts_at)
            page.get_by_label('日志结束时间').fill(ends_at)
            page.get_by_role('button', name='启动调查', exact=True).click()
            expect(page.get_by_role('alert')).to_be_visible()
            page.reload()
            expect(page.get_by_label('日志开始时间')).to_have_value(starts_at)
            expect(page.get_by_label('日志结束时间')).to_have_value(ends_at)
            expect(page.get_by_label('日志开始时间')).to_be_disabled()
            with page.expect_response('**/attempts') as replay:
                page.get_by_role('button', name='重试原请求并确认结果').click()
            assert replay.value.status == 200
            assert (replay.value.request.headers['idempotency-key'], replay.value.request.post_data) == sent[0]
            assert len(replay.value.json()['attempts']) == 1
            assert len(client.get(path).json()['attempts']) == 1
            assert page.evaluate('(id) => sessionStorage.getItem("logscope:pending-start:" + id)', record['id']) is None
        finally:
            browser.close()


@pytest.mark.parametrize('catalog', ['failed', 'empty', 'removed'])
def test_pending_confirmation_does_not_depend_on_current_catalog(catalog):
    with httpx.Client(base_url=BASE, trust_env=False, timeout=20) as client, sync_playwright() as p:
        record = client.post('/api/investigations', json={'title': '目录独立确认-' + uuid4().hex[:8]},
                             headers={'Idempotency-Key': str(uuid4())}).json()
        path = '/api/investigations/' + record['id']
        browser = p.chromium.launch(headless=True)
        try:
            page = browser.new_page()
            sent = []
            def lose(route):
                sent.append((route.request.headers['idempotency-key'], route.request.post_data))
                reply = route.fetch()
                assert reply.status == 201
                assert client.post(path + '/attempts/' + reply.json()['attempts'][0]['id'] + '/cancel').status_code == 200
                route.abort('failed')
            page.route('**/attempts', lose, times=1)
            page.goto(BASE + '/#/investigations/' + record['id'])
            page.get_by_label('被调查服务', exact=True).select_option('orders')
            page.get_by_role('button', name='启动调查', exact=True).click()
            expect(page.get_by_role('alert')).to_be_visible()
            def directory(route):
                route.fulfill(status=503 if catalog == 'failed' else 200, json={
                    'items': [{'id': 'query-mixed', 'name': '其他服务'}] if catalog == 'removed' else []})
            page.route('**/api/services', directory)
            page.reload()
            confirm = page.get_by_role('button', name='重试原请求并确认结果')
            expect(confirm).to_be_enabled()
            with page.expect_response('**/attempts') as reply:
                confirm.click()
            assert reply.value.status == 200
            assert (reply.value.request.headers['idempotency-key'], reply.value.request.post_data) == sent[0]
            expect(confirm).to_have_count(0)
            assert len(client.get(path).json()['attempts']) == 1
        finally:
            browser.close()


@pytest.mark.parametrize('damage', [
    'broken-json', 'null', 'empty-body', 'bad-key', 'body-not-string', 'extra-field',
    'empty-service', 'long-service', 'partial-range', 'naive-range', 'reversed-range',
    'oversized-range', 'invalid-date', 'read-denied', 'write-denied',
    'hour-only', 'minute-fraction', 'equal-range', 'submicrosecond-equal',
    'microsecond-over-24h', 'invalid-offset', 'invalid-hour', 'invalid-leap-day',
    'zero-year', 'unknown-body-field',
])
def test_unrecoverable_pending_blocks_start_but_keeps_details_readable(damage):
    import json
    with httpx.Client(base_url=BASE, trust_env=False, timeout=20) as client, sync_playwright() as p:
        record = client.post('/api/investigations', json={'title': '安全阻断-' + uuid4().hex[:8]},
                             headers={'Idempotency-Key': str(uuid4())}).json()
        pending = {'key': str(uuid4()), 'body': json.dumps({'service_id': 'orders'})}
        bodies = {
            'empty-body': {}, 'empty-service': {'service_id': ''}, 'long-service': {'service_id': 'x' * 81},
            'partial-range': {'service_id': 'orders', 'starts_at': '2026-11-01T00:00:00Z'},
            'naive-range': {'service_id': 'orders', 'starts_at': '2026-11-01T00:00:00', 'ends_at': '2026-11-01T01:00:00'},
            'reversed-range': {'service_id': 'orders', 'starts_at': '2026-11-01T01:00:00Z', 'ends_at': '2026-11-01T00:00:00Z'},
            'oversized-range': {'service_id': 'orders', 'starts_at': '2026-11-01T00:00:00Z', 'ends_at': '2026-11-03T00:00:00Z'},
            'invalid-date': {'service_id': 'orders', 'starts_at': 'not-a-date', 'ends_at': '2026-11-01T01:00:00Z'},
        }
        invalid_ranges = {
            'hour-only': ('2026-11-01T00Z', '2026-11-01T01Z'),
            'minute-fraction': ('2026-11-01T00:00.5Z', '2026-11-01T01:00.5Z'),
            'equal-range': ('2026-11-01T00:00Z', '2026-11-01T00:00Z'),
            'submicrosecond-equal': ('2026-11-01T00:00:00.1234561Z', '2026-11-01T00:00:00.1234569Z'),
            'microsecond-over-24h': ('2026-11-01T00:00:00.000001Z', '2026-11-02T00:00:00.000002Z'),
            'invalid-offset': ('2026-11-01T00:00+00:60', '2026-11-01T01:00+00:60'),
            'invalid-hour': ('2026-11-01T24:00Z', '2026-11-02T01:00Z'),
            'invalid-leap-day': ('2026-02-29T00:00Z', '2026-02-29T01:00Z'),
            'zero-year': ('0000-11-01T00:00Z', '0000-11-01T01:00Z'),
        }
        if damage in invalid_ranges:
            starts_at, ends_at = invalid_ranges[damage]
            bodies[damage] = {'service_id': 'orders', 'starts_at': starts_at, 'ends_at': ends_at}
        bodies['unknown-body-field'] = {'service_id': 'orders', 'unexpected': True}
        if damage in bodies:
            assert client.post('/api/investigations/' + record['id'] + '/attempts', json=bodies[damage],
                               headers={'Idempotency-Key': pending['key']}).status_code == 422
            pending['body'] = json.dumps(bodies[damage])
        if damage == 'bad-key':
            pending['key'] = 'not-a-uuid'
        if damage == 'body-not-string':
            pending['body'] = {'service_id': 'orders'}
        if damage == 'extra-field':
            pending['unexpected'] = True
        saved = '{' if damage == 'broken-json' else 'null' if damage == 'null' else json.dumps(pending)
        browser = p.chromium.launch(headless=True)
        try:
            page = browser.new_page()
            page.add_init_script('''(() => {
                const damage = %s;
                if (!damage.endsWith('denied')) sessionStorage.setItem(%s, %s);
                if (damage === 'read-denied' || damage === 'write-denied') {
                    Storage.prototype[damage === 'read-denied' ? 'getItem' : 'setItem'] = () => { throw new DOMException('disabled', 'SecurityError') };
                }
            })()''' % (json.dumps(damage), json.dumps('logscope:pending-start:' + record['id']), json.dumps(saved)))
            posts = []
            page.on('request', lambda request: posts.append(request) if request.method == 'POST' and request.url.endswith('/attempts') else None)
            page.goto(BASE + '/#/investigations/' + record['id'])
            expect(page.get_by_role('heading', name=record['title'], exact=True)).to_be_visible()
            if damage == 'write-denied':
                page.get_by_label('被调查服务', exact=True).select_option('orders')
                page.get_by_role('button', name='启动调查', exact=True).click()
            expect(page.get_by_role('alert')).to_contain_text('原启动身份无法恢复')
            expect(page.get_by_role('button', name='启动调查', exact=True)).to_be_disabled()
            page.locator('form').first.evaluate('(form) => form.requestSubmit()')
            page.get_by_role('button', name='刷新调查状态').click()
            expect(page.get_by_role('heading', name=record['title'], exact=True)).to_be_visible()
            expect(page.get_by_role('alert')).to_contain_text('原启动身份无法恢复')
            assert posts == []
            assert client.get('/api/investigations/' + record['id']).json()['attempts'] == []
        finally:
            browser.close()


@pytest.mark.parametrize('cleanup', ['read-denied', 'remove-denied', 'corrupt'])
def test_late_start_cleanup_failure_stays_with_original_investigation(cleanup):
    with httpx.Client(base_url=BASE, trust_env=False, timeout=20) as client, sync_playwright() as p:
        records = [client.post('/api/investigations', json={'title': '清理归属-' + uuid4().hex[:8]},
                              headers={'Idempotency-Key': str(uuid4())}).json() for _ in range(2)]
        browser = p.chromium.launch(headless=True)
        held = []
        try:
            page = browser.new_page()
            page.route('**/attempts', lambda route: held.append((route, route.fetch())), times=1)
            page.goto(BASE + '/#/investigations/' + records[0]['id'])
            page.get_by_label('被调查服务', exact=True).select_option('orders')
            page.get_by_role('button', name='启动调查', exact=True).click()
            page.get_by_role('button', name='刷新调查状态').click()
            expect(page.get_by_role('button', name='取消调查', exact=True)).to_be_visible()
            assert held[0][1].status == 201
            page.evaluate('''({id, cleanup}) => {
                if (cleanup === 'corrupt') sessionStorage.setItem('logscope:pending-start:' + id, '{');
                else Storage.prototype[cleanup === 'read-denied' ? 'getItem' : 'removeItem'] = () => { throw new DOMException('disabled', 'SecurityError') };
            }''', {'id': records[0]['id'], 'cleanup': cleanup})
            page.evaluate('(id) => location.hash = "/investigations/" + id', records[1]['id'])
            expect(page.get_by_role('heading', name=records[1]['title'], exact=True)).to_be_visible()
            alerts = page.get_by_role('alert').count()
            with page.expect_response('**/attempts'):
                held[0][0].fulfill(response=held[0][1])
            page.evaluate('() => new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve)))')
            expect(page.get_by_role('heading', name=records[1]['title'], exact=True)).to_be_visible()
            expect(page.get_by_role('alert')).to_have_count(alerts)
            expect(page.get_by_role('heading', name='启动结果待确认')).to_have_count(0)
            page.evaluate('(id) => location.hash = "/investigations/" + id', records[0]['id'])
            expect(page.get_by_role('heading', name=records[0]['title'], exact=True)).to_be_visible()
            expect(page.get_by_role('alert')).to_contain_text('原启动身份无法恢复')
            assert len(client.get('/api/investigations/' + records[0]['id']).json()['attempts']) == 1
            assert client.get('/api/investigations/' + records[1]['id']).json()['attempts'] == []
        finally:
            browser.close()


def test_held_accepted_start_refresh_confirms_same_request_before_late_response():
    with httpx.Client(base_url=BASE, trust_env=False, timeout=20) as client, sync_playwright() as p:
        record = client.post('/api/investigations', json={'title': '暂扣确认-' + uuid4().hex[:8]},
                             headers={'Idempotency-Key': str(uuid4())}).json()
        path = '/api/investigations/' + record['id']
        browser = p.chromium.launch(headless=True)
        held = []
        try:
            page = browser.new_page()
            def hold(route):
                held.append((route, route.fetch()))
            page.route('**/attempts', hold, times=1)
            page.goto(BASE + '/#/investigations/' + record['id'])
            page.get_by_label('被调查服务', exact=True).select_option('orders')
            page.get_by_role('button', name='启动调查', exact=True).click()
            expect(page.get_by_label('被调查服务', exact=True)).to_be_disabled()
            page.get_by_role('button', name='刷新调查状态').click()
            expect(page.get_by_role('heading', name='启动结果待确认')).to_be_visible()
            assert len(held) == 1 and held[0][1].status == 201
            assert client.post(path + '/attempts/' + held[0][1].json()['attempts'][0]['id'] + '/cancel').status_code == 200
            page.get_by_role('button', name='刷新调查状态').click()
            with page.expect_response('**/attempts') as replay:
                page.get_by_role('button', name='重试原请求并确认结果').click()
            assert replay.value.status == 200
            assert replay.value.request.headers['idempotency-key'] == held[0][0].request.headers['idempotency-key']
            assert replay.value.request.post_data == held[0][0].request.post_data
            expect(page.get_by_label('范围模式')).to_have_value('recent')
            page.get_by_label('被调查服务', exact=True).select_option('query-mixed')
            with page.expect_response('**/attempts') as new_round:
                page.get_by_role('button', name='启动新一轮', exact=True).click()
            assert new_round.value.status == 201
            with page.expect_response('**/attempts'):
                held[0][0].fulfill(response=held[0][1])
            page.evaluate('() => new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve)))')
            expect(page.get_by_role('heading', name='第2轮 · 当前轮', exact=True)).to_be_visible()
            assert len(client.get(path).json()['attempts']) == 2
        finally:
            browser.close()


def test_cancel_reopen_browses_readonly_round_history(evidence_executor):
    with httpx.Client(base_url=BASE, trust_env=False, timeout=20) as client:
        pending = client.post('/api/investigations', json={'title': '逐轮历史-' + uuid4().hex[:8]},
            headers={'Idempotency-Key': str(uuid4())}).json()
        path = '/api/investigations/' + pending['id']
        body = {'service_id': 'orders', 'starts_at': '2026-11-01T00:00:00Z', 'ends_at': '2026-11-01T01:00:00Z'}
        first = client.post(path + '/attempts', json=body, headers={'Idempotency-Key': str(uuid4())}).json()['attempts'][0]
        base = f'/api/executor/investigations/{pending["id"]}/attempts/{first["id"]}/executions'
        identity = client.get(base, headers=evidence_executor).json()['executions'][0]['id']
        evidence = {'format': 'controlled-event-v1', 'source_id': 'controlled-orders',
            'occurred_at': '2026-11-01T00:15:00Z', 'query': {'starts_at': body['starts_at'], 'ends_at': body['ends_at'], 'fields': {}},
            'record': {'event': 'request_failed', 'error_code': 'ETIMEDOUT', 'fields': {}}, 'locator': None}
        saved = client.post(base + '/' + identity + '/evidence', json=evidence, headers=evidence_executor)
        assert saved.status_code == 201
        proposal = {'format': 'controlled-analysis-v1', 'kind': 'undetermined', 'mechanism': None,
            'evidence_ids': [saved.json()['id']], 'missing': ['causal_trace'], 'expected_version': 0, 'finish': False}
        assert client.post(base + '/' + identity + '/conclusions', json=proposal,
            headers={**evidence_executor, 'Idempotency-Key': str(uuid4())}).status_code == 201
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            try:
                page = browser.new_page()
                page.goto(BASE + '/#/investigations/' + pending['id'])
                detail = page.get_by_role('region', name='调查详情')
                detail.get_by_role('button', name='取消调查', exact=True).click()
                expect(detail).to_contain_text('已取消')
                expect(detail.get_by_role('button', name='启动新一轮', exact=True)).to_be_visible()
                page.get_by_label('被调查服务', exact=True).select_option('query-mixed')
                detail.get_by_role('button', name='启动新一轮', exact=True).click()
                expect(detail.get_by_role('heading', name='第2轮 · 当前轮', exact=True)).to_be_visible()
                record = client.get(path).json()
                assert len(record['attempts']) == 2
                assert record['attempts'][0]['status'] == '已取消'
                assert record['attempts'][1]['service_id'] == 'query-mixed'
                assert record['evidence_count'] == 0
                history = detail.get_by_role('region', name='第1轮历史')
                expect(history).to_contain_text('orders')
                expect(history).to_contain_text('本轮证据条数：1')
                expect(history.get_by_role('article', name='结论版本')).to_have_count(1)
                history.get_by_role('link', name='查看证据 ' + saved.json()['id']).click()
                assert page.url.endswith('/#/investigations/' + pending['id'])
                expect(history.locator('[id="evidence-' + first['id'] + '-' + saved.json()['id'] + '"]')).to_be_in_viewport()
                expect(detail.get_by_role('region', name='第2轮当前')).not_to_contain_text('ETIMEDOUT')
                page.reload()
                expect(detail.get_by_role('heading', name='第2轮 · 当前轮', exact=True)).to_be_visible()
                expect(detail.get_by_role('region', name='第1轮历史')).to_contain_text('本轮证据条数：1')
                assert client.get(path).json() == record
                page.get_by_role('button', name='返回列表').click()
                expect(page.get_by_role('region', name='调查详情')).to_have_count(0)
            finally:
                browser.close()


def test_deadline_ui_distinguishes_timeout_wait_from_failure_and_navigation(evidence_executor):
    from concurrent.futures import ThreadPoolExecutor
    import time
    with httpx.Client(base_url=BASE, trust_env=False, timeout=20) as client:
        record = client.post('/api/investigations', json={'title': 'UI期限-' + uuid4().hex[:8]},
                             headers={'Idempotency-Key': str(uuid4())}).json()
        path = '/api/investigations/' + record['id']
        attempt = client.post(path + '/attempts', json={'service_id': 'deadline-ui'},
                              headers={'Idempotency-Key': str(uuid4())}).json()['attempts'][0]
        base = f'/api/executor/investigations/{record["id"]}/attempts/{attempt["id"]}/executions'
        identity = client.get(base, headers=evidence_executor).json()['executions'][0]['id']
        with sync_playwright() as p, ThreadPoolExecutor(max_workers=1) as pool:
            browser = p.chromium.launch(headless=True)
            try:
                page = browser.new_page()
                page.goto(BASE + '/#/investigations/' + record['id'])
                detail = page.get_by_role('region', name='调查详情')
                expect(detail).to_contain_text('单轮截止时间')
                pending = pool.submit(client.post, base + '/' + identity + '/queries',
                                      headers={**evidence_executor, 'Idempotency-Key': str(uuid4())})
                limit = time.monotonic() + 5
                while client.get(path).json()['attempts'][0]['execution_phase'] != 'timeout_stopping':
                    assert time.monotonic() < limit
                page.reload()
                expect(detail).to_contain_text('总时限已到，等待执行退出确认')
                expect(detail.get_by_role('button', name='取消调查', exact=True)).to_be_disabled()
                expect(detail).not_to_contain_text('用户主动停止')
                assert pending.result(15).status_code == 403
                detail.get_by_role('button', name='刷新调查状态').click()
                expect(detail).to_contain_text('单轮10分钟总时限已到')
                expect(detail).to_contain_text('技术失败，未完成排查')
                expect(detail).not_to_contain_text('正常排查结束')
                page.reload()
                expect(detail).to_contain_text('单轮10分钟总时限已到')
                page.get_by_role('button', name='返回列表').click()
                expect(page.get_by_role('region', name='调查详情')).to_have_count(0)
            finally:
                browser.close()


def test_cancel_navigation_isolates_pending_errors_and_old_finally():
    with httpx.Client(base_url=BASE, trust_env=False, timeout=20) as client:
        records = []
        for label in ('A', 'B'):
            record = client.post('/api/investigations', json={'title': '取消归属-' + label + uuid4().hex[:8]},
                                 headers={'Idempotency-Key': str(uuid4())}).json()
            record = client.post('/api/investigations/' + record['id'] + '/attempts',
                                 json={'service_id': 'orders'}, headers={'Idempotency-Key': str(uuid4())}).json()
            records.append(record)
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        held = []
        try:
            page = browser.new_page()
            page.route('**/cancel', lambda route: held.append(route))
            detail = page.get_by_role('region', name='调查详情')
            button = detail.get_by_role('button', name='取消调查', exact=True)
            page.goto(BASE + '/#/investigations/' + records[0]['id'])
            button.click()
            expect(button).to_be_disabled()
            page.wait_for_function('true')
            assert len(held) == 1
            page.evaluate('(id) => location.hash = "/investigations/" + id', records[1]['id'])
            expect(detail.get_by_role('heading', name=records[1]['title'], exact=True)).to_be_visible()
            expect(button).to_be_enabled()
            button.click()
            expect(button).to_be_disabled()
            page.wait_for_function('true')
            assert len(held) == 2
            held[0].fulfill(status=503, json={'detail': 'A旧取消错误'})
            expect(button).to_be_disabled()
            expect(detail).not_to_contain_text('A旧取消错误')
            held[1].fulfill(status=503, json={'detail': 'B当前取消错误'})
            expect(detail).to_contain_text('B当前取消错误')
            expect(button).to_be_enabled()
            page.evaluate('(id) => location.hash = "/investigations/" + id', records[0]['id'])
            expect(detail.get_by_role('heading', name=records[0]['title'], exact=True)).to_be_visible()
            expect(detail).not_to_contain_text('B当前取消错误')
            expect(button).to_be_enabled()
        finally:
            browser.close()


def test_cancel_ui_distinguishes_stopping_from_confirmed_and_refreshes(evidence_executor):
    from concurrent.futures import ThreadPoolExecutor
    with httpx.Client(base_url=BASE, trust_env=False, timeout=20) as client:
        record = client.post('/api/investigations', json={'title': 'UI取消-' + uuid4().hex[:8]},
            headers={'Idempotency-Key': str(uuid4())}).json()
        path = '/api/investigations/' + record['id']
        attempt = client.post(path + '/attempts', json={'service_id': 'cancel-ui'},
            headers={'Idempotency-Key': str(uuid4())}).json()['attempts'][0]
        base = f'/api/executor/investigations/{record["id"]}/attempts/{attempt["id"]}/executions'
        identity = client.get(base, headers=evidence_executor).json()['executions'][0]['id']
        with sync_playwright() as p, ThreadPoolExecutor(max_workers=1) as pool:
            browser = p.chromium.launch(headless=True)
            try:
                page = browser.new_page()
                page.goto(BASE + '/#/investigations/' + record['id'])
                detail = page.get_by_role('region', name='调查详情')
                expect(detail.get_by_role('button', name='取消调查', exact=True)).to_be_visible()
                pending = pool.submit(client.post, base + '/' + identity + '/queries',
                    headers={**evidence_executor, 'Idempotency-Key': str(uuid4())})
                # 通过公开API等待查询实际在途，不注入业务状态。
                import time
                deadline = time.monotonic() + 5
                while not client.get(path).json()['attempts'][0]['queries']:
                    assert time.monotonic() < deadline
                detail.get_by_role('button', name='取消调查', exact=True).click()
                expect(detail).to_contain_text('正在停止')
                expect(detail).to_contain_text('尚未确认执行终止')
                expect(detail.get_by_role('button', name='取消调查', exact=True)).to_be_disabled()
                page.reload()
                expect(detail).to_contain_text('正在停止')
                expect(detail.get_by_role('button', name='启动调查')).to_have_count(0)
                assert pending.result(15).status_code == 403
                detail.get_by_role('button', name='刷新调查状态').click()
                expect(detail).to_contain_text('已取消')
                expect(detail).to_contain_text('用户主动停止，已确认执行终止')
                expect(detail).not_to_contain_text('正常排查结束')
                page.reload()
                expect(detail).to_contain_text('已取消')
            finally:
                browser.close()


def test_query_failure_is_persistent_safe_and_not_completed(evidence_executor):
    with httpx.Client(base_url=BASE, trust_env=False, timeout=20) as client:
        record = client.post('/api/investigations', json={'title': 'UI查询故障-' + uuid4().hex[:8]},
            headers={'Idempotency-Key': str(uuid4())}).json()
        path = '/api/investigations/' + record['id']
        attempt = client.post(path + '/attempts', json={'service_id': 'orders'},
            headers={'Idempotency-Key': str(uuid4())}).json()['attempts'][0]
        base = f'/api/executor/investigations/{record["id"]}/attempts/{attempt["id"]}/executions'
        identity = client.get(base, headers=evidence_executor).json()['executions'][0]['id']
        reply = client.post(base + '/' + identity + '/queries',
            headers={**evidence_executor, 'Idempotency-Key': str(uuid4())})
        assert reply.status_code == 201
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            try:
                page = browser.new_page()
                page.goto(BASE + '/#/investigations/' + record['id'])
                detail = page.get_by_role('region', name='调查详情')
                expect(detail).to_contain_text('覆盖不完整')
                expect(detail).to_contain_text('未配置受控查询适配器')
                expect(detail).to_contain_text('实际查询次数：0')
                expect(detail).to_contain_text('必要来源不可用，无法继续')
                expect(detail).to_contain_text('查不到不表示没有异常')
                expect(detail).not_to_contain_text('结束原因：正常结束')
                page.reload()
                expect(detail).to_contain_text('未配置受控查询适配器')
            finally:
                browser.close()


def test_mixed_query_coverage_renders_skipped_source_and_survives_refresh(evidence_executor):
    with httpx.Client(base_url=BASE, trust_env=False, timeout=15) as client:
        record = client.post('/api/investigations', json={'title': 'UI混合覆盖-' + uuid4().hex[:8]},
            headers={'Idempotency-Key': str(uuid4())}).json()
        path = '/api/investigations/' + record['id']
        attempt = client.post(path + '/attempts', json={'service_id': 'query-mixed'},
            headers={'Idempotency-Key': str(uuid4())}).json()['attempts'][0]
        assert len(attempt['query_coverage']['missing_sources']) == 2
        base = f'/api/executor/investigations/{record["id"]}/attempts/{attempt["id"]}/executions'
        identity = client.get(base, headers=evidence_executor).json()['executions'][0]['id']
        endpoint = base + '/' + identity + '/queries'
        for _ in range(2):
            assert client.post(endpoint, headers={**evidence_executor, 'Idempotency-Key': str(uuid4())}).status_code == 201
        after = client.get(path).json()['attempts'][0]
        assert len(after['query_coverage']['missing_sources']) == 1
        missing = after['query_coverage']['missing_sources'][0]
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            try:
                page = browser.new_page()
                page.goto(BASE + '/#/investigations/' + record['id'])
                detail = page.get_by_role('region', name='调查详情')
                expect(detail).to_contain_text('缺失来源：' + missing)
                expect(detail).to_contain_text('跳过查询')
                expect(detail).to_contain_text('来源限流')
                expect(detail).to_contain_text('查询成功')
                expect(detail).to_contain_text('实际查询次数：0')
                expect(detail).to_contain_text('覆盖不完整')
                page.reload()
                expect(detail).to_contain_text('跳过查询')
                assert 'ui-limited' not in page.content()
                assert 'ui-healthy' not in page.content()
            finally:
                browser.close()


def test_unqueried_sources_stay_missing_before_and_after_normal_finish(evidence_executor):
    with httpx.Client(base_url=BASE, trust_env=False, timeout=20) as client:
        record = client.post('/api/investigations', json={'title': 'UI未扫描-' + uuid4().hex[:8]},
            headers={'Idempotency-Key': str(uuid4())}).json()
        path = '/api/investigations/' + record['id']
        attempt = client.post(path + '/attempts', json={'service_id': 'orders'},
            headers={'Idempotency-Key': str(uuid4())}).json()['attempts'][0]
        base = f'/api/executor/investigations/{record["id"]}/attempts/{attempt["id"]}/executions'
        identity = client.get(base, headers=evidence_executor).json()['executions'][0]['id']
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            try:
                page = browser.new_page()
                page.goto(BASE + '/#/investigations/' + record['id'])
                detail = page.get_by_role('region', name='调查详情')
                expect(detail).to_contain_text('缺失来源：来源代号-')
                expect(detail).to_contain_text('覆盖不完整')
                expect(detail).to_contain_text('尚未查询所有来源')
                payload = {'format': 'controlled-analysis-v1', 'kind': 'undetermined', 'mechanism': None,
                    'evidence_ids': [], 'missing': ['causal_trace'], 'expected_version': 0, 'finish': True}
                assert client.post(base + '/' + identity + '/conclusions', json=payload,
                    headers={**evidence_executor, 'Idempotency-Key': str(uuid4())}).status_code == 201
                page.reload()
                expect(detail).to_contain_text('已完成')
                expect(detail).to_contain_text('未确定根因')
                expect(detail).to_contain_text('缺失来源：来源代号-')
                expect(detail).to_contain_text('覆盖不完整')
                expect(detail).to_contain_text('查不到不表示没有异常')
                assert 'controlled-orders' not in page.content()
            finally:
                browser.close()


def test_create_refresh_list_detail_and_validation():
    title = f"UI调查-{uuid4().hex[:8]}"
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page()
        errors = []
        page.on("pageerror", lambda error: errors.append(str(error)))
        page.goto(BASE)
        page.get_by_role("button", name="新建调查", exact=True).first.click()
        page.get_by_label("调查标题").fill("   ")
        page.get_by_role("button", name="保存待处理调查").click()
        expect(page.get_by_role("alert")).to_contain_text("请输入调查标题")
        page.get_by_label("调查标题").fill(title)
        page.get_by_label("问题描述").fill("登记现象，不启动排查")
        with page.expect_response(lambda r: r.url.endswith("/api/investigations") and r.request.method == "POST") as response:
            page.get_by_role("button", name="保存待处理调查").click()
        assert response.value.status == 201
        record = response.value.json()
        expect(page.get_by_role("heading", name=title)).to_be_visible()
        detail = page.get_by_role("region", name="调查详情")
        expect(detail).to_contain_text("待处理")
        expect(detail).to_contain_text("未评估")
        expect(detail).to_contain_text("尚无调查尝试")
        expect(detail).to_contain_text("证据条数：0")
        created = datetime.fromisoformat(record["created_at"].replace("Z", "+00:00"))
        local = (created + timedelta(hours=8)).strftime("%Y-%m-%d %H:%M:%S")
        expect(detail).to_contain_text(local)
        expect(detail).to_contain_text("UTC+8")
        page.reload()
        expect(page.get_by_role("heading", name=title)).to_be_visible()
        page.get_by_role("button", name="返回列表").click()
        page.get_by_placeholder("搜索调查标题或编号").fill(title)
        page.get_by_role("button", name="搜索", exact=True).click()
        page.get_by_role("button", name=title, exact=True).first.click()
        expect(page.get_by_role("heading", name=title)).to_be_visible()
        with httpx.Client() as client:
            assert client.get(f'{BASE}/api/investigations/{record["id"]}').json() == record
        assert errors == []
        browser.close()


def test_start_fixed_range_and_server_error():
    title = f"UI启动-{uuid4().hex[:8]}"
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page()
        page.goto(BASE)
        page.get_by_role("button", name="新建调查", exact=True).click()
        page.get_by_label("调查标题").fill(title)
        page.get_by_role("button", name="保存待处理调查").click()
        expect(page.get_by_role("heading", name=title)).to_be_visible()
        page.get_by_label("被调查服务", exact=True).select_option("orders")
        expect(page.get_by_label("范围模式")).to_have_value("recent")
        page.get_by_label("范围模式").select_option("absolute")
        page.get_by_label("日志开始时间").fill("2026-10-29T08:00:00+08:00")
        page.get_by_label("日志结束时间").fill("2026-10-31T08:00:00+08:00")
        page.get_by_role("button", name="启动调查", exact=True).click()
        expect(page.get_by_role("alert")).to_contain_text("24小时")
        page.get_by_label("日志开始时间").fill("2026-10-30T08:00:00+08:00")
        with page.expect_response(lambda r: r.url.endswith("/attempts") and r.request.method == "POST") as response:
            page.get_by_role("button", name="启动调查", exact=True).click()
        assert response.value.status == 201
        record = response.value.json()
        detail = page.get_by_role("region", name="调查详情")
        expect(detail).to_contain_text("调查中")
        expect(detail).to_contain_text("orders")
        expect(detail).to_contain_text("2026-10-30 08:00:00 UTC+8")
        expect(detail).to_contain_text("2026-10-31 08:00:00 UTC+8")
        expect(detail).to_contain_text("可控执行适配器")
        expect(detail).to_contain_text("运行中不清理")
        expect(page.get_by_role("button", name="启动调查", exact=True)).to_have_count(0)
        page.reload()
        expect(detail).to_contain_text("2026-10-30 08:00:00 UTC+8")
        with httpx.Client() as client:
            assert client.get(f'{BASE}/api/investigations/{record["id"]}').json() == record
        browser.close()


def test_default_start_lost_response_replays_one_fixed_attempt():
    title = f"UI启动重放-{uuid4().hex[:8]}"
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page()
        page.goto(BASE)
        page.get_by_role('button', name='新建调查', exact=True).click()
        page.get_by_label('调查标题').fill(title)
        page.get_by_role('button', name='保存待处理调查').click()
        expect(page.get_by_role('heading', name=title)).to_be_visible()
        page.get_by_label('被调查服务', exact=True).select_option('orders')
        saved = []
        def lose_response(route):
            response = route.fetch()
            saved.append(response.json())
            route.abort('failed')
        page.route('**/attempts', lose_response, times=1)
        page.get_by_role('button', name='启动调查', exact=True).click()
        expect(page.get_by_role('alert')).to_be_visible()
        with page.expect_response(lambda r: r.url.endswith('/attempts') and r.request.method == 'POST') as response:
            page.get_by_role('button', name='重试原请求并确认结果', exact=True).click()
        assert response.value.status == 200
        assert response.value.json() == saved[0]
        attempt = saved[0]['attempts'][0]
        assert (datetime.fromisoformat(attempt['ends_at'].replace('Z', '+00:00')) - datetime.fromisoformat(attempt['starts_at'].replace('Z', '+00:00'))).total_seconds() == 3600
        expect(page.get_by_role('region', name='调查详情')).to_contain_text('调查中')
        browser.close()


@pytest.mark.parametrize('late_status', [201, 422])
def test_late_start_response_cannot_replace_new_route_or_clear_its_inflight(late_status):
    with httpx.Client(base_url=BASE, trust_env=False, timeout=20) as client:
        records = []
        for label in ('A', 'B'):
            reply = client.post('/api/investigations', json={'title': f'延迟导航{label}-{uuid4().hex[:8]}'},
                                headers={'Idempotency-Key': str(uuid4())})
            assert reply.status_code == 201
            records.append(reply.json())
    a, b = records
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page()
        errors = []
        page.on('pageerror', lambda error: errors.append(str(error)))
        held = []

        def hold(route):
            response = route.fetch()
            held.append((route, response))

        page.route('**/attempts', hold)
        page.goto(f'{BASE}/#/investigations/{a["id"]}')
        page.get_by_label('被调查服务', exact=True).select_option('orders')
        if late_status == 422:
            page.get_by_label('范围模式').select_option('absolute')
            page.get_by_label('日志开始时间').fill('2026-10-29T08:00:00+08:00')
            page.get_by_label('日志结束时间').fill('2026-10-31T08:00:00+08:00')
        with page.expect_request('**/attempts'):
            page.get_by_role('button', name='启动调查', exact=True).click()
        page.get_by_role('button', name='返回列表').click()
        page.get_by_role('button', name=b['title'], exact=True).first.click()
        expect(page.get_by_role('heading', name=b['title'], exact=True)).to_be_visible()
        # A 尚未返回，B 必须仍可发起自己的请求。
        expect(page.get_by_label('被调查服务', exact=True)).to_be_enabled()
        page.get_by_label('被调查服务', exact=True).select_option('orders')
        with page.expect_request('**/attempts'):
            page.get_by_role('button', name='启动调查', exact=True).click()
        expect(page.get_by_label('被调查服务', exact=True)).to_be_disabled()
        assert held[0][1].status == late_status
        with page.expect_response(lambda r: r.url.endswith(f'/investigations/{a["id"]}/attempts')):
            held[0][0].fulfill(response=held[0][1])
        page.evaluate('() => new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve)))')
        expect(page).to_have_url(f'{BASE}/#/investigations/{b["id"]}')
        expect(page.get_by_role('heading', name=b['title'], exact=True)).to_be_visible()
        expect(page.get_by_role('alert')).to_have_count(0)
        expect(page.get_by_label('被调查服务', exact=True)).to_be_disabled()
        held[1][0].abort('failed')
        expect(page.get_by_role('alert')).to_be_visible()
        page.unroute('**/attempts', hold)
        with page.expect_response(lambda r: r.url.endswith(f'/investigations/{b["id"]}/attempts')) as replay:
            page.get_by_role('button', name='重试原请求并确认结果', exact=True).click()
        assert replay.value.status == 200
        assert replay.value.json() == held[1][1].json()
        expect(page.get_by_role('region', name='调查详情')).to_contain_text('调查中')
        assert errors == []
        browser.close()


def test_late_start_failure_after_return_to_same_route_does_not_block_retry():
    with httpx.Client(base_url=BASE, trust_env=False, timeout=20) as client:
        reply = client.post('/api/investigations', json={'title': f'原路重试-{uuid4().hex[:8]}'},
                            headers={'Idempotency-Key': str(uuid4())})
        assert reply.status_code == 201
        record = reply.json()
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page()
        held = []

        def hold(route):
            held.append((route, route.fetch()))

        page.route('**/attempts', hold, times=1)
        url = f'{BASE}/#/investigations/{record["id"]}'
        page.goto(url)
        page.get_by_label('被调查服务', exact=True).select_option('orders')
        page.get_by_label('范围模式').select_option('absolute')
        page.get_by_label('日志开始时间').fill('2026-10-29T08:00:00+08:00')
        page.get_by_label('日志结束时间').fill('2026-10-31T08:00:00+08:00')
        with page.expect_request('**/attempts'):
            page.get_by_role('button', name='启动调查', exact=True).click()
        page.get_by_role('button', name='返回列表').click()
        page.get_by_role('button', name=record['title'], exact=True).first.click()
        expect(page.get_by_role('heading', name=record['title'], exact=True)).to_be_visible()
        assert held[0][1].status == 422
        with page.expect_response('**/attempts'):
            held[0][0].fulfill(response=held[0][1])
        page.evaluate('() => new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve)))')
        expect(page.get_by_role('alert')).to_have_count(0)
        expect(page.get_by_label('被调查服务', exact=True)).to_be_enabled()
        page.get_by_label('被调查服务', exact=True).select_option('orders')
        with page.expect_response('**/attempts') as response:
            page.get_by_role('button', name='启动调查', exact=True).click()
        assert response.value.status == 201
        expect(page.get_by_role('region', name='调查详情')).to_contain_text('调查中')
        browser.close()


def test_evidence_cards_refresh_without_raw_sensitive_content(evidence_executor):
    marker = uuid4().hex
    with httpx.Client(base_url=BASE, trust_env=False, timeout=20) as client:
        pending = client.post('/api/investigations', json={'title': 'UI证据-' + uuid4().hex[:8]},
                              headers={'Idempotency-Key': str(uuid4())}).json()
        path = '/api/investigations/' + pending['id']
        record = client.post(path + '/attempts', json={'service_id': 'orders',
            'starts_at': '2026-11-01T00:00:00Z', 'ends_at': '2026-11-01T01:00:00Z'},
            headers={'Idempotency-Key': str(uuid4())}).json()
        attempt = record['attempts'][0]['id']
        base = f'/api/executor/investigations/{pending["id"]}/attempts/{attempt}/executions'
        identity = client.get(base, headers=evidence_executor).json()['executions'][0]['id']
        endpoint = base + '/' + identity + '/evidence'
        payload = {'format': 'controlled-event-v1', 'source_id': 'controlled-orders',
            'occurred_at': '2026-11-01T00:15:00Z',
            'query': {'starts_at': '2026-11-01T00:00:00Z', 'ends_at': '2026-11-01T01:00:00Z',
                      'fields': {'email': marker + '@example.invalid'}},
            'record': {'event': 'request_failed', 'error_code': 'ETIMEDOUT',
                       'fields': {'email': marker + '@example.invalid', 'authorization': marker}},
            'locator': 'https://' + marker + ':' + marker + '@synthetic.invalid/' + marker + '?token=' + marker}
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            page = browser.new_page()
            page.goto(BASE + '/#/investigations/' + pending['id'])
            detail = page.get_by_role('region', name='调查详情')
            expect(detail).to_contain_text('尚无已保存证据')
            reply = client.post(endpoint, json=payload, headers=evidence_executor)
            assert reply.status_code == 201
            safe = reply.json()
            rejected = client.post(endpoint, json={**payload, 'message': marker}, headers=evidence_executor)
            assert rejected.status_code == 422
            page.reload()
            card = page.get_by_role('article', name='脱敏证据')
            expect(card).to_contain_text(safe['source'])
            expect(card).to_contain_text('2026-11-01 08:15:00 UTC+8')
            expect(card).to_contain_text('查询条件')
            expect(card).to_contain_text('ETIMEDOUT')
            expect(card).to_contain_text('[已屏蔽]')
            expect(card).to_contain_text(safe['redaction_note'])
            expect(detail).to_contain_text('部分材料不可用')
            expect(detail).to_contain_text('证据条数：1')
            expect(detail).to_contain_text('可能重复')
            expect(detail).to_contain_text('本轮证据条数：1')
            expect(detail).not_to_contain_text('尚未实施去重')
            assert marker not in page.locator('body').inner_text(), '可见内容含原敏感值'
            assert marker not in page.content(), '隐藏DOM含原敏感值'
            assert evidence_executor['Authorization'] not in page.content(), '浏览器含执行凭证'
            page.reload()
            expect(card).to_contain_text(safe['snippet']['fields']['email'])
            reliable = {**payload, 'identity': {'kind': 'record', 'record_id': marker}}
            first = client.post(endpoint, json=reliable, headers=evidence_executor)
            repeated = client.post(endpoint, json=reliable, headers=evidence_executor)
            assert first.status_code == 201 and repeated.status_code == 200
            assert first.json() == repeated.json()
            assert client.post(endpoint, json=payload, headers=evidence_executor).status_code == 201
            page.reload()
            expect(card).to_have_count(3)
            expect(detail).to_contain_text('本轮证据条数：3')
            expect(detail).to_contain_text('可能重复')
            assert marker not in page.content(), '可靠身份原值进入浏览器'
            page.get_by_role('button', name='返回列表').click()
            row = page.get_by_role('row').filter(has=page.get_by_role('button', name=pending['title'], exact=True))
            expect(row).to_contain_text('3')
            expect(row).to_contain_text('可能重复')
            browser.close()


@pytest.mark.parametrize('confirmed', [False, True])
def test_conclusions_history_completed_result_and_evidence_navigation(evidence_executor, confirmed):
    with httpx.Client(base_url=BASE, trust_env=False, timeout=20) as client:
        pending = client.post('/api/investigations', json={'title': 'UI结论-' + uuid4().hex[:8]},
                              headers={'Idempotency-Key': str(uuid4())}).json()
        path = '/api/investigations/' + pending['id']
        record = client.post(path + '/attempts', json={'service_id': 'orders',
            'starts_at': '2026-11-01T00:00:00Z', 'ends_at': '2026-11-01T01:00:00Z'},
            headers={'Idempotency-Key': str(uuid4())}).json()
        attempt = record['attempts'][0]['id']
        base = f'/api/executor/investigations/{pending["id"]}/attempts/{attempt}/executions'
        identity = client.get(base, headers=evidence_executor).json()['executions'][0]['id']
        endpoint = base + '/' + identity
        payload = {'format': 'controlled-event-v1', 'source_id': 'controlled-orders',
            'occurred_at': '2026-11-01T00:15:00Z',
            'query': {'starts_at': '2026-11-01T00:00:00Z', 'ends_at': '2026-11-01T01:00:00Z', 'fields': {}},
            'record': {'event': 'request_failed', 'error_code': 'ETIMEDOUT', 'fields': {}}}
        saved = client.post(endpoint + '/evidence', json=payload, headers=evidence_executor).json()
        analysis = {'format': 'controlled-analysis-v1', 'kind': 'hypothesis',
            'mechanism': 'dependency_error_propagation', 'evidence_ids': [saved['id']],
            'missing': ['causal_trace'], 'expected_version': 0, 'finish': False}
        assert client.post(endpoint + '/conclusions', json=analysis,
            headers={**evidence_executor, 'Idempotency-Key': str(uuid4())}).status_code == 201
        if confirmed:
            payload['record']['causal_trace'] = {'format': 'observed-propagation-v1', 'dependency_error': 'ETIMEDOUT',
                'propagated_error': 'ETIMEDOUT', 'request_outcome': 'failed_by_dependency',
                'local_rejection': 'excluded_by_trace', 'independent_failure': 'excluded_by_trace',
                'contradiction': 'none_observed'}
            causal = client.post(endpoint + '/evidence', json=payload, headers=evidence_executor).json()
            analysis.update(kind='confirmed', expected_version=1, finish=True, evidence_ids=[causal['id']], missing=[])
        else:
            analysis.update(kind='undetermined', mechanism=None, expected_version=1, finish=True)
        assert client.post(endpoint + '/conclusions', json=analysis,
            headers={**evidence_executor, 'Idempotency-Key': str(uuid4())}).status_code == 201
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            page = browser.new_page()
            page.goto(BASE + '/#/investigations/' + pending['id'])
            detail = page.get_by_role('region', name='调查详情')
            expect(detail).to_contain_text('已确认根因' if confirmed else '未确定根因')
            expect(detail).to_contain_text('已完成')
            expect(detail).to_contain_text('结束时间')
            expect(detail).to_contain_text('结论版本 1')
            expect(detail).to_contain_text('结论版本 2')
            expect(detail).to_contain_text('待验证假设')
            expect(detail).to_contain_text('缺失证据')
            if confirmed:
                expect(detail).to_contain_text('因果机制')
                expect(detail).to_contain_text('替代解释')
                expect(detail).to_contain_text('关键矛盾')
                expect(detail).to_contain_text('非科学证明')
            page.get_by_role('link', name='查看证据 ' + saved['id']).first.click()
            evidence_card = page.locator('[id="evidence-' + attempt + '-' + saved['id'] + '"]')
            expect(evidence_card).to_be_in_viewport()
            expect(evidence_card).to_contain_text('ETIMEDOUT')
            expect(page.get_by_role('button', name='强制确认根因')).to_have_count(0)
            assert identity not in page.content()
            assert evidence_executor['Authorization'] not in page.content()
            page.reload()
            expect(detail).to_contain_text('已确认根因' if confirmed else '未确定根因')
            browser.close()


def test_lost_create_response_can_retry_without_duplicate():
    title = f"UI重放-{uuid4().hex[:8]}"
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page()
        page.goto(BASE)
        page.get_by_role("button", name="新建调查", exact=True).click()
        page.get_by_label("调查标题").fill(title)
        saved = []

        def lose_response(route):
            # 真实请求已经由 API/PG 提交，仅丢弃浏览器收到的响应。
            response = route.fetch()
            saved.append(response.json())
            route.abort("failed")

        page.route("**/api/investigations", lose_response, times=1)
        page.get_by_role("button", name="保存待处理调查").click()
        expect(page.get_by_role("alert")).to_be_visible()
        with page.expect_response(lambda r: r.url.endswith("/api/investigations") and r.request.method == "POST") as response:
            page.get_by_role("button", name="保存待处理调查").click()
        assert response.value.status == 200
        assert response.value.json() == saved[0]
        expect(page.get_by_role("heading", name=title)).to_be_visible()
        with httpx.Client() as client:
            assert client.get(f"{BASE}/api/investigations", params={"q": title}).json() == {"items": saved, "total": 1}
        browser.close()
