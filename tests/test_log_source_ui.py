import sys
from pathlib import Path
from uuid import uuid4

import pytest
from playwright.sync_api import sync_playwright, expect

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'backend' / 'tests'))
from test_log_sources import external_source
from test_attempts import configured_client
from test_evidence import started
from test_investigations import database_url


@pytest.fixture
def evidence_executor(database_url):
    # This suite owns its API and static server; do not share fixed UI ports.
    return database_url


@pytest.mark.parametrize('scenario,count', [('normal', 2), ('unidentified', 3), ('invalid', 1)])
def test_simulated_http_evidence_visible_after_reload(evidence_executor, tmp_path, external_source, scenario, count):
    from app.log_sources import SimulatedLogSource
    credential = uuid4().hex
    import functools
    import threading
    from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer

    class QuietHandler(SimpleHTTPRequestHandler):
        def log_message(self, *args):
            pass

    handler = functools.partial(QuietHandler, directory=str(Path(__file__).resolve().parents[1] / 'dist'))
    server = ThreadingHTTPServer(('127.0.0.1', 0), handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    with configured_client(evidence_executor, tmp_path, executor_credential=credential,
                           query_sources={'controlled-orders': SimulatedLogSource(external_source(scenario))}) as api:
        path, record, evidence, auth = started(api, credential)
        assert api.post(evidence.removesuffix('/evidence') + '/queries',
                        headers={**auth, 'Idempotency-Key': str(uuid4())}).status_code == 201
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            try:
                page = browser.new_page()

                def api_route(route):
                    from urllib.parse import urlsplit
                    url = urlsplit(route.request.url)
                    reply = api.request(route.request.method, url.path + ('?' + url.query if url.query else ''),
                                        content=route.request.post_data, headers={'Content-Type': 'application/json'})
                    route.fulfill(status=reply.status_code, body=reply.content,
                                  content_type=reply.headers.get('content-type', 'application/json'))

                page.route('**/api/**', api_route)
                page.goto(f'http://127.0.0.1:{server.server_port}/#/investigations/' + record['id'])
                for _ in range(2):
                    expect(page.get_by_role('note').filter(has_text='模拟外部日志来源')).to_be_visible()
                    expect(page.get_by_text(f'本轮证据条数：{count}', exact=False)).to_be_visible()
                    expect(page.get_by_label('脱敏证据', exact=True)).to_have_count(count)
                    if scenario == 'unidentified':
                        expect(page.get_by_role('status').filter(has_text='可能重复')).to_be_visible()
                    if scenario == 'invalid':
                        expect(page.get_by_text('原因：查询参数无效')).to_be_visible()
                        expect(page.get_by_text('缺失来源：', exact=False)).to_be_visible()
                    assert 'synthetic-secret' not in page.locator('body').inner_text()
                    page.reload()
            finally:
                browser.close()
                server.shutdown()
                server.server_close()
                thread.join(5)
