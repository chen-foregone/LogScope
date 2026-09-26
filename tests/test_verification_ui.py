from datetime import datetime, timedelta, timezone
from uuid import uuid4

import httpx
from playwright.sync_api import sync_playwright, expect

from test_ui import BASE


def test_verification_history_and_manual_read_only_round(evidence_executor):
    with httpx.Client(base_url=BASE, trust_env=False, timeout=20) as client, sync_playwright() as playwright:
        headers = {'Idempotency-Key': str(uuid4())}
        record = client.post('/api/investigations', json={'title': '核验变化-' + uuid4().hex[:8]}, headers=headers).json()
        path = '/api/investigations/' + record['id']
        end = datetime.now(timezone.utc)
        start = end - timedelta(hours=1)
        scope = {'service_id': 'orders', 'starts_at': start.isoformat(), 'ends_at': end.isoformat()}
        running = client.post(path + '/attempts', json=scope, headers={'Idempotency-Key': str(uuid4())}).json()
        attempt_id = running['attempts'][0]['id']
        base = '/api/executor/investigations/' + record['id'] + '/attempts/' + attempt_id + '/executions'
        execution_id = client.get(base, headers=evidence_executor).json()['executions'][0]['id']
        endpoint = base + '/' + execution_id
        payload = {'format': 'controlled-event-v1', 'source_id': 'controlled-orders',
            'occurred_at': (end - timedelta(minutes=10)).isoformat(),
            'query': {'starts_at': start.isoformat(), 'ends_at': end.isoformat(), 'fields': {}},
            'record': {'event': 'request_failed', 'error_code': 'ETIMEDOUT', 'fields': {}, 'causal_trace': {
                'format': 'observed-propagation-v1', 'dependency_error': 'ETIMEDOUT',
                'propagated_error': 'ETIMEDOUT', 'request_outcome': 'failed_by_dependency',
                'local_rejection': 'excluded_by_trace', 'independent_failure': 'excluded_by_trace',
                'contradiction': 'none_observed'}}}
        saved = client.post(endpoint + '/evidence', json=payload, headers=evidence_executor).json()
        proposal = {'format': 'controlled-analysis-v1', 'kind': 'confirmed', 'mechanism': 'dependency_error_propagation',
                    'evidence_ids': [saved['id']], 'missing': [], 'expected_version': 0, 'finish': True}
        assert client.post(endpoint + '/conclusions', json=proposal,
                           headers={**evidence_executor, 'Idempotency-Key': str(uuid4())}).status_code == 201
        check_url = '/api/executor/investigations/' + record['id'] + '/evidence/' + saved['id'] + '/verification'
        check = {'expected_version': 0, 'source_access': 'unreachable', 'material_status': 'retained', 'reason': 'source_unreachable'}
        assert client.post(check_url, json=check, headers={**evidence_executor, 'Idempotency-Key': str(uuid4())}).status_code == 201
        browser = playwright.chromium.launch(headless=True)
        try:
            page = browser.new_page(viewport={'width': 390, 'height': 844})
            page.goto(BASE + '/#/investigations/' + record['id'])
            evidence = page.get_by_label('脱敏证据', exact=True)
            expect(evidence).to_contain_text('来源状态：来源不可访问')
            expect(evidence).to_contain_text('已保留受控脱敏结构化核验材料')
            assert client.post(check_url, json={**check, 'expected_version': 1,
                'material_status': 'invalid', 'reason': 'material_integrity_failed'},
                headers={**evidence_executor, 'Idempotency-Key': str(uuid4())}).status_code == 201
            page.reload()
            expect(page.get_by_label('结论版本')).to_contain_text('核验状态：待重新核验')
            expect(page.get_by_label('结论核验变化')).to_contain_text('不表示原结论错误')
            page.get_by_role('button', name='准备手动新轮核验', exact=True).click()
            expect(page.get_by_label('日志开始时间')).to_have_value(running['attempts'][0]['starts_at'])
            page.get_by_role('button', name='启动新一轮', exact=True).click()
            expect(page.get_by_role('region', name='第2轮当前', exact=True)).to_be_visible()
            fresh = client.get(path).json()
            new_attempt = fresh['attempts'][-1]
            assert new_attempt['reverify_conclusion_id'] == fresh['attempts'][0]['conclusion']['id']
            assert fresh['attempts'][0]['status'] == '已完成'
            page.reload()
            expect(page.get_by_label('结论版本')).to_contain_text('待重新核验')
            new_base = '/api/executor/investigations/' + record['id'] + '/attempts/' + new_attempt['id'] + '/executions'
            new_execution = client.get(new_base, headers=evidence_executor).json()['executions'][0]['id']
            new_endpoint = new_base + '/' + new_execution
            first = client.post(new_endpoint + '/evidence', json=payload, headers=evidence_executor).json()
            second = client.post(new_endpoint + '/evidence', json=payload, headers=evidence_executor).json()
            assert client.post(new_endpoint + '/conclusions', json={**proposal, 'evidence_ids': [first['id']]},
                headers={**evidence_executor, 'Idempotency-Key': str(uuid4())}).status_code == 201
            page.reload()
            expect(page.get_by_label('结论版本').first).to_contain_text('手动新轮获得充分可核验因果支撑')
            new_check = '/api/executor/investigations/' + record['id'] + '/evidence/' + first['id'] + '/verification'
            assert client.post(new_check, json={**check, 'material_status': 'invalid', 'reason': 'material_integrity_failed'},
                headers={**evidence_executor, 'Idempotency-Key': str(uuid4())}).status_code == 201
            page.reload()
            expect(page.get_by_label('结论版本').first).to_contain_text('其他充分可核验因果轨迹继续支撑')
            current_history = page.get_by_label('结论版本').first.get_by_label('结论核验变化').last
            current_history.get_by_role('button', name='查看核验依据 ' + second['id'], exact=True).click()
            expect(page.locator('[id="evidence-' + new_attempt['id'] + '-' + second['id'] + '"]')).to_be_in_viewport()
        finally:
            browser.close()
