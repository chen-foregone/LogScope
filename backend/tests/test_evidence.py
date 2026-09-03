import json
import pytest
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


def test_reliable_record_identity_replays_without_business_update(database_url, tmp_path):
    credential = uuid4().hex
    payload = material()
    payload['identity'] = {'kind': 'record', 'record_id': uuid4().hex}
    with configured_client(database_url, tmp_path, executor_credential=credential) as client:
        path, _, endpoint, auth = started(client, credential)
        first = client.post(endpoint, json=payload, headers=auth)
        assert first.status_code == 201, '可靠身份应通过受控入口接收'
        saved = client.get(path).json()
        repeated = client.post(endpoint, json=payload, headers=auth)
        assert repeated.status_code == 200
        assert repeated.json() == first.json()
        assert client.get(path).json() == saved
        assert saved['evidence_count'] == 1
        assert saved['attempts'][0]['evidence_count'] == 1
        assert first.json()['identity_status'] == 'reliable'
    with configured_client(database_url, tmp_path, executor_credential=credential) as restarted:
        assert restarted.post(endpoint, json=payload, headers=auth).json() == first.json()
        assert restarted.get(path).json() == saved


def test_file_identity_offset_and_concurrent_records(database_url, tmp_path):
    from concurrent.futures import ThreadPoolExecutor
    credential = uuid4().hex
    payload = material()
    payload['identity'] = {'kind': 'file', 'file_id': uuid4().hex, 'offset': 0}
    with configured_client(database_url, tmp_path, executor_credential=credential) as client:
        path, _, endpoint, auth = started(client, credential)
        with ThreadPoolExecutor(max_workers=6) as pool:
            replies = list(pool.map(lambda _: client.post(endpoint, json=payload, headers=auth), range(6)))
        assert sorted(r.status_code for r in replies) == [200, 200, 200, 200, 200, 201]
        assert len({r.json()['id'] for r in replies}) == 1
        saved = client.get(path).json()
        assert saved['evidence_count'] == 1
        assert client.post(endpoint, json=payload, headers=auth).status_code == 200
        assert client.get(path).json() == saved
        variants = [{**payload, 'identity': {**payload['identity'], 'offset': offset}}
                    for offset in (1, 2, 9223372036854775807)]
        with ThreadPoolExecutor(max_workers=3) as pool:
            added = list(pool.map(lambda item: client.post(endpoint, json=item, headers=auth), variants))
        assert all(r.status_code == 201 for r in added)
        after = client.get(path).json()
        assert after['evidence_count'] == 4
        assert after['updated_at'] > saved['updated_at']
        assert after['attempts'][0]['evidence_notices'] == []


def test_identity_conflict_validation_and_context_replay(database_url, tmp_path, caplog):
    from copy import deepcopy
    credential = uuid4().hex
    payload = material()
    payload['identity'] = {'kind': 'record', 'record_id': uuid4().hex}
    with configured_client(database_url, tmp_path, executor_credential=credential) as client:
        path, _, endpoint, auth = started(client, credential)
        first = client.post(endpoint, json=payload, headers=auth)
        assert first.status_code == 201
        saved = client.get(path).json()
        for change in ({'occurred_at': '2026-11-01T00:16:00Z'},
                       {'record': {**payload['record'], 'error_code': 'EACCES'}},
                       {'record': {**payload['record'], 'fields': {**payload['record']['fields'], 'token': uuid4().hex}}}):
            reply = client.post(endpoint, json={**payload, **change}, headers=auth)
            assert reply.status_code == 409
            assert client.get(path).json() == saved
            assert payload['identity']['record_id'] not in reply.text + caplog.text, '冲突输出含身份原值'
        context = deepcopy(payload)
        context['query']['starts_at'] = '2026-11-01T00:10:00Z'
        context['query']['fields'] = {}
        context['locator'] = uuid4().hex
        replay = client.post(endpoint, json=context, headers=auth)
        assert replay.status_code == 200
        assert replay.json() == first.json()
        assert client.get(path).json() == saved
        for invalid, headers, status in ((payload, {}, 401),
                ({**payload, 'source_id': 'unknown'}, auth, 422),
                ({**payload, 'occurred_at': '2026-11-01T01:00:00Z'}, auth, 422),
                ({**payload, 'query': {**payload['query'], 'starts_at': '2026-10-31T23:00:00Z'}}, auth, 422)):
            assert client.post(endpoint, json=invalid, headers=headers).status_code == status
            assert client.get(path).json() == saved
        wrong = endpoint.rsplit('/', 2)[0] + '/' + str(uuid4()) + '/evidence'
        assert client.post(wrong, json=payload, headers=auth).status_code == 403
        assert client.get(path).json() == saved


def test_identity_schema_is_strict_and_never_persists_raw_values(database_url, tmp_path, caplog):
    from urllib.parse import urlsplit, parse_qs
    import subprocess
    credential = uuid4().hex
    secret = uuid4().hex
    with configured_client(database_url, tmp_path, executor_credential=credential) as client:
        path, before, endpoint, auth = started(client, credential)
        payload = material()
        invalid = [False, [], {}, {'kind': secret}, {'kind': 'record', 'record_id': 1},
                   {'kind': 'record', 'record_id': ''}, {'kind': 'record', 'record_id': 'x' * 4097},
                   {'kind': 'record', 'record_id': secret + '\u0000'},
                   {'kind': 'record', 'record_id': secret + '\ud800'},
                   {'kind': 'record', 'record_id': secret, secret: secret},
                   {'kind': 'file', 'file_id': secret},
                   {'kind': 'file', 'file_id': '', 'offset': 0},
                   {'kind': 'file', 'file_id': 42, 'offset': 0}]
        invalid += [{'kind': 'file', 'file_id': secret, 'offset': value}
                    for value in (True, False, -1, 9223372036854775808, '0', 1.5, None)]
        for identity in invalid:
            reply = client.post(endpoint, content=json.dumps({**payload, 'identity': identity}), headers=auth)
            assert reply.status_code == 422, '非法身份必须关闭接收'
            assert secret not in reply.text + caplog.text, '拒绝诊断含原值'
            after = client.get(path).json()
            assert after['evidence_count'] == 0
            assert after['updated_at'] == before['updated_at']
        for identity in ({'kind': 'record', 'record_id': secret}, {'kind': 'file', 'file_id': secret, 'offset': 0}):
            reply = client.post(endpoint, json={**payload, 'identity': identity}, headers=auth)
            assert reply.status_code == 201
            assert secret not in reply.text + client.get(path).text + caplog.text, '公共输出含原始身份'
        schema = parse_qs(urlsplit(database_url).query)['options'][0].split('=')[1]
        dumped = subprocess.run(['docker', 'exec', 'logscope-01-test-pg17', 'pg_dump', '-U', 'logscope_test',
                                 '-d', 'logscope_test', '--data-only', '--schema', schema], capture_output=True)
        assert dumped.returncode == 0, '隔离schema内存审计失败'
        for raw in [secret, credential, *payload['record']['fields'].values()]:
            assert raw.encode() not in dumped.stdout, '持久化含原始身份或敏感值'


def test_record_identity_concurrency_and_independent_records(database_url, tmp_path):
    from concurrent.futures import ThreadPoolExecutor
    credential = uuid4().hex
    payload = material()
    payload['identity'] = {'kind': 'record', 'record_id': uuid4().hex}
    with configured_client(database_url, tmp_path, executor_credential=credential) as client:
        path, _, endpoint, auth = started(client, credential)
        with ThreadPoolExecutor(max_workers=6) as pool:
            replies = list(pool.map(lambda _: client.post(endpoint, json=payload, headers=auth), range(6)))
        assert sorted(r.status_code for r in replies) == [200, 200, 200, 200, 200, 201]
        assert len({r.json()['id'] for r in replies}) == 1
        variants = [{**payload, 'identity': {'kind': 'record', 'record_id': value}}
                    for value in ('a:b', 'a', 'record:a', 'file:a:0')]
        with ThreadPoolExecutor(max_workers=4) as pool:
            added = list(pool.map(lambda item: client.post(endpoint, json=item, headers=auth), variants))
        assert all(r.status_code == 201 for r in added)
        assert client.get(path).json()['evidence_count'] == 5
        other_path, _, other_endpoint, _ = started(client, credential)
        other = client.post(other_endpoint, json=payload, headers=auth)
        assert other.status_code == 201
        assert other.json()['id'] != replies[0].json()['id']
        assert client.get(other_path).json()['evidence_count'] == 1
        # 普通record_id只是关联材料；没有受控identity声明，逐条保留。
        ordinary = {**payload}
        ordinary.pop('identity')
        ordinary['record'] = {**payload['record'], 'fields': {'record_id': 'ordinary-correlation'}}
        for _ in range(2):
            reply = client.post(endpoint, json=ordinary, headers=auth)
            assert reply.status_code == 201
            assert reply.json()['identity_status'] == 'unavailable'
        after = client.get(path).json()
        assert after['evidence_count'] == 7
        assert '可能重复' in ' '.join(after['attempts'][0]['evidence_notices'])


def test_same_content_different_sources_and_identity_domains_are_independent(database_url, tmp_path):
    from app.main import create_app
    from fastapi.testclient import TestClient
    config = tmp_path / 'identity-sources.json'
    config.write_text(json.dumps({'services': [{'id': 'orders', 'name': '订单',
                      'source_ids': ['controlled-orders', 'controlled-other']}]}), encoding='utf-8')
    credential = uuid4().hex
    payload = material()
    with TestClient(create_app(database_url=database_url, services_file=config, executor_credential=credential)) as client:
        path, _, endpoint, auth = started(client, credential)
        identities = [{'kind': 'record', 'record_id': 'a:0'},
                      {'kind': 'file', 'file_id': 'a', 'offset': 0},
                      {'kind': 'file', 'file_id': 'a:0', 'offset': 0},
                      {'kind': 'file', 'file_id': 'a', 'offset': 1}]
        for source in ('controlled-orders', 'controlled-other'):
            for identity in identities:
                assert client.post(endpoint, json={**payload, 'source_id': source, 'identity': identity}, headers=auth).status_code == 201
        assert client.get(path).json()['evidence_count'] == 8
        # 无可靠身份，相同内容/相同时间与不同时间均保留。
        for occurred in (payload['occurred_at'], payload['occurred_at'], '2026-11-01T00:16:00Z'):
            assert client.post(endpoint, json={**payload, 'occurred_at': occurred}, headers=auth).status_code == 201
        after = client.get(path).json()
        assert after['evidence_count'] == 11
        assert after['attempts'][0]['evidence_count'] == 11
        assert client.get('/api/investigations').json()['items'] == [after]
        attempt_path = path + '/attempts/' + after['attempts'][0]['id']
        attempt = client.get(attempt_path).json()
        assert attempt['evidence_count'] == 11
        assert attempt['evidence'] == after['attempts'][0]['evidence']
        assert attempt['evidence_notices'] == after['attempts'][0]['evidence_notices']


@pytest.mark.parametrize('database_url', ['0004'], indirect=True)
def test_upgrade_keeps_legacy_evidence_without_guessing_identity(database_url, tmp_path):
    import os
    import subprocess
    import sys
    from datetime import datetime, timezone
    from sqlalchemy import create_engine, MetaData, insert
    from test_investigations import ROOT
    # 迁移夹具安排真实旧schema产物；升级后的业务结果只通过HTTP读取。
    engine = create_engine(database_url)
    old = MetaData()
    old.reflect(engine)
    investigation, attempt, execution = uuid4(), uuid4(), uuid4()
    ids = [uuid4(), uuid4()]
    now = datetime(2026, 11, 1, tzinfo=timezone.utc)
    safe = {'source': '来源代号-legacy', 'occurred_at': '2026-11-01T00:15:00Z',
            'query': {'starts_at': '2026-11-01T00:00:00Z', 'ends_at': '2026-11-01T01:00:00Z', 'fields': {}},
            'snippet': {'event': 'request_failed', 'error_code': 'ETIMEDOUT', 'fields': {}},
            'locator': None, 'redaction_note': '旧版已脱敏材料'}
    with engine.begin() as connection:
        connection.execute(insert(old.tables['investigations']).values(id=investigation, request_key=uuid4(),
            title='升级历史', description='', created_at=now, updated_at=now, retention_expires_at=None))
        connection.execute(insert(old.tables['attempts']).values(id=attempt, investigation_id=investigation,
            request_key=uuid4(), request_body='{}', service_id='orders', source_ids=['controlled-orders'],
            starts_at=now, ends_at=datetime(2026, 11, 1, 1, tzinfo=timezone.utc), created_at=now, status='调查中'))
        connection.execute(insert(old.tables['controlled_executions']).values(id=execution, attempt_id=attempt, valid=True))
        for identity in ids:
            connection.execute(insert(old.tables['evidence']).values(id=identity, attempt_id=attempt, created_at=now, material=safe))
    engine.dispose()
    migrated = subprocess.run([sys.executable, '-m', 'alembic', '-c', 'backend/alembic.ini', 'upgrade', 'head'],
        cwd=ROOT, env={**os.environ, 'DATABASE_URL': database_url, 'PYTHONPATH': str(ROOT / 'backend')}, capture_output=True)
    assert migrated.returncode == 0, '0004旧schema升级失败'
    credential = uuid4().hex
    with configured_client(database_url, tmp_path, executor_credential=credential) as client:
        path = '/api/investigations/' + str(investigation)
        detail = client.get(path).json()
        assert detail['evidence_count'] == 2
        saved = detail['attempts'][0]['evidence']
        assert {item['id'] for item in saved} == {str(identity) for identity in ids}
        assert all(item['identity_status'] == 'legacy_unknown' for item in saved)
        assert '升级前证据未声明可靠身份' in ' '.join(detail['attempts'][0]['evidence_notices'])
        assert all({key: item[key] for key in safe} == safe for item in saved)
        assert '可能重复' in ' '.join(detail['attempts'][0]['evidence_notices'])
        assert detail['updated_at'] == '2026-11-01T00:00:00Z'
        endpoint = f'/api/executor/investigations/{investigation}/attempts/{attempt}/executions/{execution}/evidence'
        payload = {**material(), 'identity': {'kind': 'record', 'record_id': uuid4().hex}}
        first = client.post(endpoint, json=payload, headers={'Authorization': 'Bearer ' + credential})
        assert first.status_code == 201
        assert client.post(endpoint, json=payload, headers={'Authorization': 'Bearer ' + credential}).status_code == 200
        assert client.get(path).json()['evidence_count'] == 3


def test_concurrent_identity_conflict_keeps_one_complete_evidence(database_url, tmp_path):
    from concurrent.futures import ThreadPoolExecutor
    credential = uuid4().hex
    payload = {**material(), 'identity': {'kind': 'record', 'record_id': uuid4().hex}}
    changed = {**payload, 'record': {**payload['record'], 'error_code': 'EACCES'}}
    with configured_client(database_url, tmp_path, executor_credential=credential) as client:
        path, _, endpoint, auth = started(client, credential)
        with ThreadPoolExecutor(max_workers=2) as pool:
            replies = list(pool.map(lambda item: client.post(endpoint, json=item, headers=auth), (payload, changed)))
        assert sorted(r.status_code for r in replies) == [201, 409]
        accepted = next(r.json() for r in replies if r.status_code == 201)
        after = client.get(path).json()
        assert after['evidence_count'] == 1
        assert after['attempts'][0]['evidence'] == [accepted]
        assert client.get('/api/investigations').json()['items'] == [after]


def test_failure_after_identity_insert_rolls_back_without_claiming_identity(database_url, tmp_path):
    from sqlalchemy import event
    from sqlalchemy.engine import Engine
    credential = uuid4().hex
    payload = {**material(), 'identity': {'kind': 'record', 'record_id': uuid4().hex}}
    with configured_client(database_url, tmp_path, executor_credential=credential) as client:
        path, before, endpoint, auth = started(client, credential)
        def fail_update(connection, cursor, statement, parameters, context, executemany):
            if statement.startswith('UPDATE investigations SET updated_at='):
                cursor.execute('SELECT 1 / 0')
        event.listen(Engine, 'before_cursor_execute', fail_update)
        try:
            assert client.post(endpoint, json=payload, headers=auth).status_code == 503
        finally:
            event.remove(Engine, 'before_cursor_execute', fail_update)
        assert client.get(path).json() == before
        recovered = client.post(endpoint, json=payload, headers=auth)
        assert recovered.status_code == 201
        after = client.get(path).json()
        assert after['evidence_count'] == 1
        assert after['attempts'][0]['evidence'] == [recovered.json()]
        assert client.post(endpoint, json=payload, headers=auth).status_code == 200
        assert client.get(path).json() == after


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
    payload['identity'] = {'kind': 'record', 'record_id': uuid4().hex}
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
    payload['identity'] = {'kind': 'record', 'record_id': uuid4().hex}
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
