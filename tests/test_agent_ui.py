"""Real browser/API/database; only the paid model is replaced."""
import pytest
import httpx
import time
from playwright.sync_api import sync_playwright, expect

from test_ui import BASE


@pytest.mark.parametrize('evidence_executor', ['agent', 'agent_error'], indirect=True)
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
            expected = '模型调用或产物校验失败' if evidence_executor.legacy_mode == 'agent_error' else '正常结束'
            path = '/api/investigations/' + page.url.rsplit('/', 1)[-1]
            with httpx.Client(base_url=BASE, trust_env=False) as client:
                deadline = time.monotonic() + 10
                while client.get(path).json()['status'] == '调查中':
                    assert time.monotonic() < deadline
                    time.sleep(.1)
            page.reload()
            expect(run).to_contain_text(expected)
            expect(run).to_contain_text('模拟日志来源')
            expect(run).to_contain_text('模型调用 1 次')
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
