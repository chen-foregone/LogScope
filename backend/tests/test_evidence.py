import json
from uuid import uuid4

from test_investigations import database_url
from test_attempts import configured_client, create


def started(client, credential):
    pending, _ = create(client)
    path = f'/api/investigations/{pending["id"]}'
    reply = client.post(path + '/attempts', json={
        'service_id': 'orders', 'starts_at': '2026-11-01T00:00:00Z',
        'ends_at': '2026-11-01T01:00:00Z',
    }, headers={'Idempotency-Key': str(uuid4())})
    assert reply.status_code == 201
    record = reply.json()
    attempt = record['attempts'][0]
    base = f'/api/executor/investigations/{pending["id"]}/attempts/{attempt["id"]}/executions'
    auth = {'Authorization': f'Bearer {credential}'}
    execution = client.get(base, headers=auth).json()['executions'][0]['id']
    return path, record, base + f'/{execution}/evidence', auth


def material():
    # 随机合成标记；断言失败时只输出安全理由，不输出注入内容。
    marker = uuid4().hex
    return {
        'format': 'controlled-event-v1', 'source_id': 'controlled-orders',
        'occurred_at': '2026-11-01T00:15:00Z',
        'query': {'starts_at': '2026-11-01T00:00:00Z', 'ends_at': '2026-11-01T01:00:00Z',
                  'fields': {'email': f'{marker}@example.invalid'}},
        'record': {'event': 'request_failed', 'error_code': 'ETIMEDOUT',
                   'fields': {'name': '合成人物' + marker, 'email': f'{marker}@example.invalid',
                              'phone': marker, 'document': marker, 'correlation_id': marker,
                              'password': marker, 'token': marker, 'key': marker, 'session': marker}},
        'locator': f'https://synthetic.invalid/{marker}?token={marker}',
    }


def test_save_redacted_material_updates_real_detail_and_survives_restart(database_url, tmp_path, caplog):
    credential = uuid4().hex
    payload = material()
    with configured_client(database_url, tmp_path, executor_credential=credential) as client:
        path, before, endpoint, auth = started(client, credential)
        reply = client.post(endpoint, json=payload, headers=auth)
        assert reply.status_code == 201, '受控证据保存未成功'
        evidence = reply.json()
        assert evidence['source'].startswith('来源代号-')
        assert evidence['occurred_at'] == payload['occurred_at']
        assert evidence['query']['fields']['email'] == evidence['snippet']['fields']['email']
        assert evidence['snippet']['error_code'] == 'ETIMEDOUT'
        assert evidence['snippet']['event'] == 'request_failed'
        for field in ('password', 'token', 'key', 'session'):
            assert evidence['snippet']['fields'][field] == '[已屏蔽]'
        for field in ('name', 'email', 'phone', 'document', 'correlation_id'):
            assert evidence['snippet']['fields'][field].startswith('代号-')
        assert evidence['locator'].startswith('定位代号-')
        assert evidence['redaction_note']
        after = client.get(path).json()
        assert after['updated_at'] > before['updated_at']
        assert after['evidence_count'] == 1
        assert after['attempts'][0]['evidence'] == [evidence]
        assert client.get(path).json() == after
        assert client.get('/api/investigations').json()['items'][0] == after
        public = json.dumps(after, ensure_ascii=False) + reply.text + caplog.text
        for raw in payload['record']['fields'].values():
            assert raw not in public, '输出含未脱敏合成值'
        assert credential not in public, '输出含执行凭证'
        import subprocess
        from urllib.parse import urlsplit, parse_qs
        schema = parse_qs(urlsplit(database_url).query)['options'][0].split('=')[1]
        dumped = subprocess.run(['docker', 'exec', 'logscope-01-test-pg17', 'pg_dump',
                                 '-U', 'logscope_test', '-d', 'logscope_test', '--data-only', '--schema', schema],
                                capture_output=True)
        assert dumped.returncode == 0, '隔离测试schema安全审计导出失败'
        for raw in payload['record']['fields'].values():
            assert raw.encode() not in dumped.stdout, '持久产物含原敏感值'
        assert credential.encode() not in dumped.stdout, '持久产物含执行凭证'
    with configured_client(database_url, tmp_path, executor_credential=credential) as restarted:
        assert restarted.get(path).json() == after
        duplicate = restarted.post(endpoint, json=payload, headers=auth)
        assert duplicate.status_code == 201
        assert duplicate.json()['snippet'] == evidence['snippet']
        assert restarted.get(path).json()['evidence_count'] == 2


def test_unsupported_material_fails_closed_with_safe_visible_notice(database_url, tmp_path, caplog):
    credential = uuid4().hex
    with configured_client(database_url, tmp_path, executor_credential=credential) as client:
        path, before, endpoint, auth = started(client, credential)
        payload = material()
        secret = payload['record']['fields']['token']
        payload['record']['message'] = secret
        reply = client.post(endpoint, json=payload, headers=auth)
        assert reply.status_code == 422
        after = client.get(path).json()
        assert after['evidence_count'] == 0
        assert after['updated_at'] == before['updated_at']
        notices = after['attempts'][0]['evidence_notices']
        assert len(notices) == 1
        assert '不可用' in notices[0]
        assert secret not in reply.text + json.dumps(after) + caplog.text, '拒绝输出含原文'


def test_source_configuration_is_never_copied_as_raw_locator(database_url, tmp_path):
    credential = uuid4().hex
    with configured_client(database_url, tmp_path, executor_credential=credential) as client:
        path, _, endpoint, auth = started(client, credential)
        payload = material()
        reply = client.post(endpoint, json=payload, headers=auth)
        assert reply.status_code == 201
        assert 'controlled-orders' not in reply.text, '来源标识应整体代号化而非透传'


def test_invalid_path_and_unknown_keys_never_echo_sensitive_input(database_url, tmp_path, caplog):
    credential = uuid4().hex
    with configured_client(database_url, tmp_path, executor_credential=credential) as client:
        path, _, endpoint, auth = started(client, credential)
        secret = uuid4().hex
        payload = material()
        payload[secret] = secret
        bad_body = client.post(endpoint, json=payload, headers=auth)
        bad_path = client.post(endpoint.rsplit('/', 2)[0] + '/invalid-' + secret + '/evidence', json=payload, headers=auth)
        for reply in (bad_body, bad_path):
            assert reply.status_code == 422
            assert secret not in reply.text + caplog.text, '校验错误回显敏感输入或动态字段名'


def test_identity_scope_and_missing_auth_cannot_write(database_url, tmp_path):
    credential = uuid4().hex
    with configured_client(database_url, tmp_path, executor_credential=credential) as client:
        path, before, endpoint, auth = started(client, credential)
        payload = material()
        for headers in ({}, {'Authorization': 'Bearer wrong'}):
            assert client.post(endpoint, json=payload, headers=headers).status_code == 401
        for invalid in (endpoint.replace(path.split('/')[-1], str(uuid4())),
                        endpoint.replace(before['attempts'][0]['id'], str(uuid4())),
                        endpoint.rsplit('/', 2)[0] + '/' + str(uuid4()) + '/evidence'):
            assert client.post(invalid, json=payload, headers=auth).status_code == 403
        assert client.post(path + '/evidence', json=payload).status_code in (404, 405)
        assert client.get(path).json() == before


def test_source_and_range_rejection_preserve_material_and_update_time(database_url, tmp_path):
    from copy import deepcopy
    credential = uuid4().hex
    with configured_client(database_url, tmp_path, executor_credential=credential) as client:
        path, before, endpoint, auth = started(client, credential)
        for change in ({'source_id': 'unknown'}, {'occurred_at': '2026-11-01T01:00:00Z'},
                       {'occurred_at': '2026-10-31T23:59:59Z'},
                       {'query': {'starts_at': '2026-10-31T23:00:00Z', 'ends_at': '2026-11-01T01:00:00Z', 'fields': {}}}):
            payload = deepcopy(material())
            payload.update(change)
            assert client.post(endpoint, json=payload, headers=auth).status_code == 422
            assert client.get(path).json() == before


def test_concurrent_writes_share_persistent_aliases_and_separate_investigations(database_url, tmp_path):
    from concurrent.futures import ThreadPoolExecutor
    credential = uuid4().hex
    payload = material()
    with configured_client(database_url, tmp_path, executor_credential=credential) as client:
        path, _, endpoint, auth = started(client, credential)
        with ThreadPoolExecutor(max_workers=4) as pool:
            replies = list(pool.map(lambda _: client.post(endpoint, json=payload, headers=auth), range(4)))
        assert all(r.status_code == 201 for r in replies)
        snippets = [r.json()['snippet'] for r in replies]
        assert all(s == snippets[0] for s in snippets)
        assert client.get(path).json()['evidence_count'] == 4
        other_path, _, other_endpoint, _ = started(client, credential)
        other = client.post(other_endpoint, json=payload, headers=auth)
        assert other.status_code == 201
        assert other.json()['snippet']['fields']['email'] != snippets[0]['fields']['email']
        assert client.get(other_path).json()['evidence_count'] == 1


def test_expired_execution_rejects_evidence(database_url, tmp_path):
    from app.attempts import ControlledExecutionAdapter, executions, attempts
    from sqlalchemy import update
    identity = uuid4()
    class UnwritableAcceptance(ControlledExecutionAdapter):
        def accept(self, connection, attempt_id):
            super().accept(connection, attempt_id)
            connection.execute(update(executions).where(executions.c.attempt_id == attempt_id)
                               .values(id=identity, valid=False))
    credential = uuid4().hex
    with configured_client(database_url, tmp_path, executor_credential=credential,
                           execution_adapter=UnwritableAcceptance()) as client:
        pending, _ = create(client)
        path = f'/api/investigations/{pending["id"]}'
        record = client.post(path + '/attempts', json={'service_id': 'orders'},
                             headers={'Idempotency-Key': str(uuid4())}).json()
        attempt = record['attempts'][0]['id']
        endpoint = f'/api/executor/investigations/{pending["id"]}/attempts/{attempt}/executions/{identity}/evidence'
        reply = client.post(endpoint, json=material(), headers={'Authorization': f'Bearer {credential}'})
        assert reply.status_code == 403
        assert client.get(path).json() == record


def test_pg_write_failure_rolls_back_with_safe_error(database_url, tmp_path, caplog):
    from sqlalchemy import event
    from sqlalchemy.engine import Engine
    credential = uuid4().hex
    payload = material()
    with configured_client(database_url, tmp_path, executor_credential=credential) as client:
        path, before, endpoint, auth = started(client, credential)
        def break_write(connection, cursor, statement, parameters, context, executemany):
            if statement.startswith('INSERT INTO evidence '):
                cursor.execute('SELECT 1 / 0')
        event.listen(Engine, 'before_cursor_execute', break_write)
        try:
            reply = client.post(endpoint, json=payload, headers=auth)
        finally:
            event.remove(Engine, 'before_cursor_execute', break_write)
        assert reply.status_code == 503
        assert client.get(path).json() == before
        for raw in payload['record']['fields'].values():
            assert raw not in reply.text + caplog.text, '故障诊断含原敏感值'
        assert client.post(endpoint, json=payload, headers=auth).status_code == 201


def test_unexpected_ingress_exception_never_reaches_diagnostics(database_url, tmp_path, caplog):
    from sqlalchemy import event
    from sqlalchemy.engine import Engine
    credential = uuid4().hex
    payload = material()
    with configured_client(database_url, tmp_path, executor_credential=credential) as client:
        path, before, endpoint, auth = started(client, credential)
        def break_write(connection, cursor, statement, parameters, context, executemany):
            if statement.startswith('INSERT INTO evidence '):
                raise RuntimeError(payload['record']['fields']['token'])
        event.listen(Engine, 'before_cursor_execute', break_write)
        try:
            try:
                reply = client.post(endpoint, json=payload, headers=auth)
            except Exception:
                raise AssertionError('证据边界意外异常未经安全处理') from None
        finally:
            event.remove(Engine, 'before_cursor_execute', break_write)
        assert reply.status_code == 503
        assert client.get(path).json() == before
        assert payload['record']['fields']['token'] not in reply.text + caplog.text, '异常输出含原文'


def test_unknown_formats_fields_and_free_text_fail_closed(database_url, tmp_path):
    from copy import deepcopy
    credential = uuid4().hex
    with configured_client(database_url, tmp_path, executor_credential=credential) as client:
        path, _, endpoint, auth = started(client, credential)
        payload = material()
        variants = []
        for key, value in (('format', 'arbitrary-json'), ('record', {'message': uuid4().hex}),
                           ('occurred_at', '2026-11-01T00:15:00'), ('query', {'url': uuid4().hex}),
                           ('locator', {'url': uuid4().hex})):
            variant = deepcopy(payload)
            variant[key] = value
            variants.append(variant)
        for field in ('message', 'url', 'unexpected'):
            variant = deepcopy(payload)
            variant['record']['fields'][field] = uuid4().hex
            variants.append(variant)
        variants += [{**payload, 'record': {**payload['record'], 'error_code': uuid4().hex}},
                     {**payload, 'record': {**payload['record'], 'event': uuid4().hex}}]
        for variant in variants:
            reply = client.post(endpoint, json=variant, headers=auth)
            assert reply.status_code == 422
            assert client.get(path).json()['evidence_count'] == 0
        for content in (b'{invalid', b'x' * 65537, b'[]'):
            assert client.post(endpoint, content=content, headers=auth).status_code == 422
        assert client.get(path).json()['evidence_count'] == 0
