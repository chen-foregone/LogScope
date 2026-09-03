import os
from datetime import datetime, timedelta
from uuid import uuid4

import httpx
import pytest
from playwright.sync_api import sync_playwright, expect


BASE = os.environ.get("UI_BASE_URL", "http://127.0.0.1:5173")


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
            page.get_by_role('button', name='启动调查', exact=True).click()
        assert response.value.status == 200
        assert response.value.json() == saved[0]
        attempt = saved[0]['attempts'][0]
        assert (datetime.fromisoformat(attempt['ends_at'].replace('Z', '+00:00')) - datetime.fromisoformat(attempt['starts_at'].replace('Z', '+00:00'))).total_seconds() == 3600
        expect(page.get_by_role('region', name='调查详情')).to_contain_text('调查中')
        browser.close()


@pytest.mark.parametrize('late_status', [201, 422])
def test_late_start_response_cannot_replace_new_route_or_clear_its_inflight(late_status):
    with httpx.Client(base_url=BASE) as client:
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
            page.get_by_role('button', name='启动调查', exact=True).click()
        assert replay.value.status == 200
        assert replay.value.json() == held[1][1].json()
        expect(page.get_by_role('region', name='调查详情')).to_contain_text('调查中')
        assert errors == []
        browser.close()


def test_late_start_failure_after_return_to_same_route_does_not_block_retry():
    with httpx.Client(base_url=BASE) as client:
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
    with httpx.Client(base_url=BASE, trust_env=False) as client:
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
            assert marker not in page.locator('body').inner_text(), '可见内容含原敏感值'
            assert marker not in page.content(), '隐藏DOM含原敏感值'
            assert evidence_executor['Authorization'] not in page.content(), '浏览器含执行凭证'
            page.reload()
            expect(card).to_contain_text(safe['snippet']['fields']['email'])
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
