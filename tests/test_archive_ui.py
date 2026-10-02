from uuid import uuid4

import httpx
from playwright.sync_api import sync_playwright, expect

from test_ui import BASE


def test_archive_unarchive_uses_real_api(evidence_executor):
    with httpx.Client(base_url=BASE, trust_env=False, timeout=20) as client, sync_playwright() as p:
        record = client.post('/api/investigations', json={'title': '归档-' + uuid4().hex[:8]},
                             headers={'Idempotency-Key': str(uuid4())}).json()
        path = '/api/investigations/' + record['id']
        browser = p.chromium.launch(headless=True)
        try:
            page = browser.new_page(viewport={'width': 390, 'height': 844})
            page.goto(BASE + '/#/investigations/' + record['id'])
            panel = page.get_by_role('region', name='调查归档操作')
            expect(panel).to_contain_text('归档后 30 天')
            panel.get_by_role('button', name='归档调查', exact=True).click()
            expect(panel.get_by_role('button', name='取消归档', exact=True)).to_be_visible()
            after = client.get(path).json()
            assert after['archived_at'] is not None
            assert after['retention_expires_at'] is not None
            page.reload()
            expect(panel.get_by_role('button', name='取消归档', exact=True)).to_be_visible()
            panel.get_by_role('button', name='取消归档', exact=True).click()
            expect(panel.get_by_role('button', name='归档调查', exact=True)).to_be_visible()
            assert client.get(path).json()['archived_at'] is None
            cleanup = page.get_by_role('region', name='永久删除调查')
            cleanup.get_by_role('button', name='预览删除范围').click()
            expect(cleanup).to_contain_text('本次实际清除范围')
            expect(cleanup).to_contain_text('不可恢复')
            expect(cleanup.get_by_role('button', name='永久删除调查', exact=True)).to_be_disabled()
            cleanup.get_by_role('checkbox').check()
            cleanup.get_by_role('button', name='永久删除调查', exact=True).click()
            expect(page.get_by_role('heading', name='调查记录', exact=True)).to_be_visible()
            assert client.get(path).status_code == 404
            assert page.evaluate('document.documentElement.scrollWidth <= window.innerWidth')
        finally:
            browser.close()


def test_expired_archive_shows_real_reference_block(evidence_executor):
    from datetime import datetime, timedelta, timezone
    from uuid import UUID
    from sqlalchemy import create_engine, insert, update
    from app.investigations import investigations
    from app.severity import severity_events
    from app.cleanup import clear_expired_archives
    with httpx.Client(base_url=BASE, trust_env=False, timeout=20) as client, sync_playwright() as p:
        record = client.post('/api/investigations', json={'title': '到期引用保护-' + uuid4().hex[:8]},
                             headers={'Idempotency-Key': str(uuid4())}).json()
        other = client.post('/api/investigations', json={'title': '其他调查'},
                            headers={'Idempotency-Key': str(uuid4())}).json()
        path = '/api/investigations/' + record['id']
        running = client.post(path + '/attempts', json={'service_id': 'orders'},
                             headers={'Idempotency-Key': str(uuid4())}).json()
        attempt_id = running['attempts'][0]['id']
        client.post(path + '/attempts/' + attempt_id + '/cancel')
        client.post(path + '/archive', json={'expected_version': 0})
        engine = create_engine(evidence_executor.database_url)
        with engine.begin() as connection:
            archived = datetime.now(timezone.utc) - timedelta(days=31)
            connection.execute(update(investigations).where(investigations.c.id == UUID(record['id']))
                               .values(archived_at=archived, retention_expires_at=archived + timedelta(days=30)))
            connection.execute(insert(severity_events).values(id=uuid4(), investigation_id=UUID(other['id']),
                request_key=uuid4(), request_body='{}', sequence=1,
                material={'references': [attempt_id]}))
        clear_expired_archives(engine, lambda: datetime.now(timezone.utc))
        assert client.get(path).status_code == 200
        browser = p.chromium.launch(headless=True)
        try:
            page = browser.new_page(viewport={'width': 390, 'height': 844})
            page.goto(BASE + '/#/investigations/' + record['id'])
            expect(page.get_by_test_id('retention-status')).to_contain_text('自动清除受阻')
            expect(page.get_by_test_id('retention-status')).to_contain_text('其他调查仍引用材料')
            page.get_by_role('region', name='永久删除调查').get_by_role('button', name='预览删除范围').click()
            expect(page.get_by_role('region', name='永久删除调查')).to_contain_text('本次清除全部拒绝')
        finally:
            browser.close()
            engine.dispose()
