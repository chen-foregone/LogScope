import os
from datetime import datetime, timedelta
from uuid import uuid4

import httpx
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
