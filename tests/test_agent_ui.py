"""Real browser/API/database; only the paid model is replaced."""
import pytest
import httpx
import time
from playwright.sync_api import sync_playwright, expect

from test_ui import BASE


def test_page_load_and_business_read_do_not_wait_for_external_fonts(evidence_executor):
    import threading
    from playwright.sync_api import TimeoutError as BrowserTimeout
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        release = threading.Event()
        try:
            page = browser.new_page()
            external = []

            def unavailable_font(route):
                external.append(route.request.url)
                release.wait(10)
                route.abort()

            page.route('https://fonts.googleapis.com/**', unavailable_font)
            page.route('https://fonts.gstatic.com/**', unavailable_font)
            try:
                page.goto(BASE, timeout=5000)
            except BrowserTimeout:
                pytest.fail('Business page load waits for an unavailable external font')
            expect(page.get_by_role('button', name='新建调查', exact=True)).to_be_visible()
            assert not external, 'Local application must not depend on external font requests'
        finally:
            release.set()
            browser.close()


@pytest.mark.parametrize('evidence_executor', ['agent', 'agent_error', 'agent_budget'], indirect=True)
def test_agent_run_and_failure_survive_browser_reload(evidence_executor):
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        try:
            page = browser.new_page(viewport={'width': 390, 'height': 844})
            page.goto(BASE)
            page.get_by_role('button', name='新建调查', exact=True).click()
            page.get_by_label('调查标题', exact=True).fill('Agent release acceptance')
            page.get_by_role('button', name='保存待处理调查', exact=True).click()
            page.get_by_label('被调查服务', exact=True).select_option('orders')
            page.get_by_role('button', name='启动调查', exact=True).click()
            run = page.get_by_role('region', name='Agent运行状态')
            expect(run).to_contain_text('qwen-plus')
            expected = {'agent_error': '模型调用或产物校验失败', 'agent_budget': '累计预算不足'}.get(evidence_executor.legacy_mode, '正常结束')
            path = '/api/investigations/' + page.url.rsplit('/', 1)[-1]
            with httpx.Client(base_url=BASE, trust_env=False) as client:
                deadline = time.monotonic() + 10
                while client.get(path).json()['status'] == '调查中':
                    assert time.monotonic() < deadline
                    time.sleep(.1)
            page.reload()
            expect(run).to_contain_text(expected)
            expect(run).to_contain_text('模拟日志来源')
            expect(run).to_contain_text('模型调用 0 次' if evidence_executor.legacy_mode == 'agent_budget' else '模型调用 1 次')
            page.reload()
            expect(run).to_contain_text(expected)
            expect(page.get_by_text('未接真实日志或 Agent', exact=False)).to_have_count(0)
            expect(page.get_by_text('synthetic-model-secret', exact=False)).to_have_count(0)
            if evidence_executor.legacy_mode == 'agent_error':
                expect(page.get_by_text('正常排查结束', exact=False)).to_have_count(0)
            assert page.evaluate('document.documentElement.scrollWidth <= window.innerWidth'), page.evaluate('''
                ({width: innerWidth, document: document.documentElement.scrollWidth,
                  elements: [...document.querySelectorAll('body *')].filter(element =>
                    element.scrollWidth > element.clientWidth || element.getBoundingClientRect().right > innerWidth)
                    .slice(0, 25).map(element => ({tag: element.tagName, cls: element.className,
                    right: element.getBoundingClientRect().right, scroll: element.scrollWidth, client: element.clientWidth}))})
            ''')
        finally:
            browser.close()


@pytest.mark.parametrize('evidence_executor', ['agent_impact'], indirect=True)
def test_completed_agent_suggestion_requires_explicit_human_confirmation(evidence_executor):
    from uuid import uuid4
    with httpx.Client(base_url=BASE, trust_env=False) as client, sync_playwright() as playwright:
        record = client.post('/api/investigations', json={'title': 'Agent impact confirmation'},
            headers={'Idempotency-Key': str(uuid4())}).json()
        path = '/api/investigations/' + record['id']
        assert client.post(path + '/severity', json={'expected_version': 0, 'basis': {
            'format': 'controlled-impact-v1', 'business': 'core_unavailable', 'users': 'broad',
            'duration': 'ongoing', 'alternative': 'none'}}, headers={'Idempotency-Key': str(uuid4())}).status_code == 201
        human = client.get(path).json()
        assert client.post(path + '/attempts', json={'service_id': 'orders'},
            headers={'Idempotency-Key': str(uuid4())}).status_code == 201
        deadline = time.monotonic() + 10
        while client.get(path).json()['status'] == '调查中':
            assert time.monotonic() < deadline
            time.sleep(.1)
        before = client.get(path).json()
        assert before['severity'] == '高'
        assert before['severity_assessed_at'] == human['severity_assessed_at']
        browser = playwright.chromium.launch(headless=True)
        try:
            page = browser.new_page(viewport={'width': 390, 'height': 844})
            page.goto(BASE + '/#/investigations/' + record['id'])
            panel = page.locator('[data-testid="severity-panel"]')
            expect(panel).to_contain_text('当前人工等级：高')
            expect(panel).to_contain_text('待确认建议：中')
            page.get_by_role('button', name='确认应用建议', exact=True).click()
            expect(panel).to_contain_text('当前人工等级：中')
            confirmed = client.get(path).json()
            assert confirmed['severity_version'] == human['severity_version'] + 1
            page.reload()
            expect(panel).to_contain_text('当前人工等级：中')
            assert client.get(path).json() == confirmed
        finally:
            browser.close()
