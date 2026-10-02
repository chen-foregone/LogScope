from datetime import datetime, timedelta, timezone
from uuid import UUID, uuid4

import httpx
import pytest
from playwright.sync_api import sync_playwright, expect
from sqlalchemy import create_engine, update

from test_ui import BASE


@pytest.mark.parametrize('age,notice', [(0, '已归档'), (24, '归档即将到期'), (31, '归档已到期'), (None, '保留状态异常')])
def test_retention_ui_uses_real_api_and_refresh_does_not_extend(evidence_executor, age, notice):
    with httpx.Client(base_url=BASE, trust_env=False, timeout=20) as client, sync_playwright() as p:
        record = client.post('/api/investigations', json={'title': '保留阶段-' + uuid4().hex[:8]},
                             headers={'Idempotency-Key': str(uuid4())}).json()
        # Arrange elapsed time in an isolated schema, not a mocked HTTP response.
        from app.investigations import investigations
        created = datetime.now(timezone.utc) - timedelta(days=age or 0)
        engine = create_engine(evidence_executor.database_url)
        with engine.begin() as connection:
            connection.execute(update(investigations).where(investigations.c.id == UUID(record['id']))
                               .values(archived_at=created, retention_expires_at=None if age is None else created + timedelta(days=30)))
        engine.dispose()
        path = '/api/investigations/' + record['id']
        before = client.get(path).json()
        browser = p.chromium.launch(headless=True)
        try:
            page = browser.new_page(viewport={'width': 390, 'height': 844})
            page.goto(BASE + '/#/investigations/' + record['id'])
            panel = page.get_by_test_id('retention-status')
            expect(panel).to_contain_text(notice)
            if age is None:
                expect(page.get_by_text('运行中不清理；结束后计算保留期', exact=True)).to_have_count(0)
            expect(panel).to_contain_text('查看不续期')
            page.reload()
            expect(panel).to_contain_text(notice)
            assert client.get(path).json() == before
            assert page.get_by_role('button', name='延期', exact=True).count() == 0
            assert page.evaluate('document.documentElement.scrollWidth <= window.innerWidth')
        finally:
            browser.close()
