from datetime import datetime, timedelta, timezone
from uuid import uuid4

from test_investigations import database_url
from test_attempts import configured_client, create
import pytest


def test_archive_unarchive_and_stale_requests(database_url, tmp_path):
    now = [datetime(2026, 11, 5, 12, tzinfo=timezone.utc)]
    with configured_client(database_url, tmp_path, clock=lambda: now[0]) as client:
        record, _ = create(client)
        path = '/api/investigations/' + record['id']
        assert record['retention_expires_at'] is None
        assert record['archive_version'] == 0
        archived = client.post(path + '/archive', json={'expected_version': 0})
        assert archived.status_code == 200, archived.text
        record = archived.json()
        assert record['archived_at'] == '2026-11-05T12:00:00Z'
        assert record['retention_expires_at'] == '2026-12-05T12:00:00Z'
        assert record['archive_version'] == 1
        now[0] += timedelta(days=1)
        repeat = client.post(path + '/archive', json={'expected_version': 1})
        assert repeat.json() == record
        assert client.post(path + '/attempts', json={'service_id': 'orders'},
                           headers={'Idempotency-Key': str(uuid4())}).status_code == 409
        restored = client.post(path + '/unarchive', json={'expected_version': 1})
        assert restored.status_code == 200, restored.text
        assert restored.json()['archived_at'] is None
        assert restored.json()['retention_expires_at'] is None
        assert restored.json()['archive_version'] == 2
        assert client.post(path + '/archive', json={'expected_version': 0}).status_code == 409
        archived = client.post(path + '/archive', json={'expected_version': 2}).json()
        assert archived['retention_expires_at'] == '2026-12-06T12:00:00Z'
    with configured_client(database_url, tmp_path, clock=lambda: now[0]) as restarted:
        assert restarted.get(path).json() == archived


def test_running_cannot_archive_and_ended_has_no_implicit_deadline(database_url, tmp_path):
    with configured_client(database_url, tmp_path) as client:
        record, _ = create(client)
        path = '/api/investigations/' + record['id']
        running = client.post(path + '/attempts', json={'service_id': 'orders'},
                              headers={'Idempotency-Key': str(uuid4())}).json()
        assert client.post(path + '/archive', json={'expected_version': 0}).status_code == 409
        ended = client.post(path + '/attempts/' + running['attempts'][0]['id'] + '/cancel')
        assert ended.status_code == 200, ended.text
        assert ended.json()['retention_expires_at'] is None
        for body in ({}, {'expected_version': -1}, {'expected_version': True},
                     {'expected_version': 0, 'archived_at': '2020-01-01'}):
            assert client.post(path + '/archive', json=body).status_code == 422
        assert client.post(path + '/archive', json={'expected_version': 0}).status_code == 200


def test_manual_cleanup_requires_current_preview_and_leaves_no_record(database_url, tmp_path):
    with configured_client(database_url, tmp_path) as client:
        record, _ = create(client)
        path = '/api/investigations/' + record['id']
        preview = client.post(path + '/cleanup-preview')
        assert preview.status_code == 200, preview.text
        preview = preview.json()
        assert preview['counts']['investigations'] == 1
        assert preview['irreversible'] is True
        client.post(path + '/archive', json={'expected_version': 0})
        body = {'scope_version': preview['scope_version'], 'confirm': True}
        assert client.post(path + '/cleanup', json=body).status_code == 409
        preview = client.post(path + '/cleanup-preview').json()
        body['scope_version'] = preview['scope_version']
        assert client.post(path + '/cleanup', json={**body, 'confirm': False}).status_code == 422
        result = client.post(path + '/cleanup', json=body)
        assert result.status_code == 200, result.text
        assert result.json()['deleted_counts'] == preview['counts']
        assert client.get(path).status_code == 404
        assert client.post(path + '/cleanup', json=body).status_code == 404
        assert client.get('/api/investigations').json()['total'] == 0


def test_expiration_exact_boundary_unarchive_and_complete_material_cleanup(database_url, tmp_path):
    from app.cleanup import clear_expired_archives
    from sqlalchemy import create_engine, select, func
    from app.investigations import metadata
    from test_evidence import started, material
    from test_conclusions import proposal
    now = [datetime(2026, 11, 5, 12, tzinfo=timezone.utc)]
    credential = uuid4().hex
    engine = create_engine(database_url)
    with configured_client(database_url, tmp_path, clock=lambda: now[0], executor_credential=credential) as client:
        path, running, endpoint, auth = started(client, credential)
        assert client.post(endpoint, json=material(), headers=auth).status_code == 201
        assert client.post(endpoint.removesuffix('/evidence') + '/conclusions', json=proposal(),
                           headers={**auth, 'Idempotency-Key': str(uuid4())}).status_code == 201
        assert client.post(path + '/archive', json={'expected_version': 0}).status_code == 200
        other, _ = create(client)
        other_path = '/api/investigations/' + other['id']
        client.post(other_path + '/archive', json={'expected_version': 0})
        client.post(other_path + '/unarchive', json={'expected_version': 1})
        now[0] = datetime(2026, 12, 5, 11, 59, 59, 999999, tzinfo=timezone.utc)
        clear_expired_archives(engine, lambda: now[0])
        assert client.get(path).status_code == 200
        now[0] = datetime(2026, 12, 5, 12, tzinfo=timezone.utc)
        clear_expired_archives(engine, lambda: now[0])
        assert client.get(path).status_code == 404
        assert client.get(other_path).status_code == 200
        with engine.connect() as connection:
            for name in ('attempts', 'controlled_executions', 'evidence', 'attempt_evidence',
                         'conclusions', 'investigation_redaction_keys', 'evidence_checks', 'conclusion_checks'):
                assert connection.execute(select(func.count()).select_from(metadata.tables[name])).scalar_one() == 0
    engine.dispose()


def test_external_reference_refuses_whole_cleanup(database_url, tmp_path):
    from sqlalchemy import create_engine, insert, delete
    from uuid import UUID
    from app.evidence import attempt_evidence
    from test_evidence import started, material
    credential = uuid4().hex
    with configured_client(database_url, tmp_path, executor_credential=credential) as client:
        path, running, endpoint, auth = started(client, credential)
        evidence = client.post(endpoint, json=material(), headers=auth).json()
        client.post(path + '/attempts/' + running['attempts'][0]['id'] + '/cancel')
        other, _ = create(client)
        other_path = '/api/investigations/' + other['id']
        target = client.post(other_path + '/attempts', json={'service_id': 'orders'},
                             headers={'Idempotency-Key': str(uuid4())}).json()['attempts'][0]['id']
        evidence_id = client.get(path).json()['attempts'][0]['evidence'][0]['id']
        engine = create_engine(database_url)
        with engine.begin() as connection:
            connection.execute(insert(attempt_evidence).values(attempt_id=UUID(target),
                evidence_id=UUID(evidence_id), included_at=datetime.now(timezone.utc)))
        assert client.post(path + '/cleanup-preview').status_code == 409
        assert client.get(path).json()['attempts'][0]['evidence'][0]['id'] == evidence_id
        with engine.begin() as connection:
            connection.execute(delete(attempt_evidence).where(attempt_evidence.c.attempt_id == UUID(target)))
        assert client.post(path + '/cleanup-preview').status_code == 200
        engine.dispose()


def test_cleanup_and_start_serialize(database_url, tmp_path):
    from concurrent.futures import ThreadPoolExecutor
    with configured_client(database_url, tmp_path) as client:
        for _ in range(3):
            record, _ = create(client)
            path = '/api/investigations/' + record['id']
            preview = client.post(path + '/cleanup-preview').json()
            with ThreadPoolExecutor(max_workers=2) as pool:
                deleting = pool.submit(client.post, path + '/cleanup', json={
                    'scope_version': preview['scope_version'], 'confirm': True})
                starting = pool.submit(client.post, path + '/attempts', json={'service_id': 'orders'},
                                       headers={'Idempotency-Key': str(uuid4())})
                removed, started_reply = deleting.result(), starting.result()
            assert (removed.status_code, started_reply.status_code) in {(200, 404), (409, 201)}
            if started_reply.status_code == 201:
                current = client.get(path).json()
                assert current['status'] == '调查中'
                client.post(path + '/attempts/' + current['attempts'][0]['id'] + '/cancel')


def test_unarchive_and_expiration_serialize(database_url, tmp_path):
    from concurrent.futures import ThreadPoolExecutor
    from app.cleanup import clear_expired_archives
    from sqlalchemy import create_engine
    now = [datetime(2026, 11, 5, 12, tzinfo=timezone.utc)]
    engine = create_engine(database_url)
    with configured_client(database_url, tmp_path, clock=lambda: now[0]) as client:
        record, _ = create(client)
        path = '/api/investigations/' + record['id']
        client.post(path + '/archive', json={'expected_version': 0})
        now[0] = datetime(2026, 12, 5, 12, tzinfo=timezone.utc)
        with ThreadPoolExecutor(max_workers=2) as pool:
            sweeping = pool.submit(clear_expired_archives, engine, lambda: now[0])
            restored = pool.submit(client.post, path + '/unarchive', json={'expected_version': 1})
            reply = restored.result()
            sweeping.result()
        assert reply.status_code in {200, 404}
        current = client.get(path)
        if reply.status_code == 200:
            assert current.status_code == 200
            assert current.json()['archived_at'] is None
        else:
            assert current.status_code == 404
    engine.dispose()


def test_cross_site_archive_and_cleanup_are_rejected(database_url, tmp_path):
    with configured_client(database_url, tmp_path) as client:
        record, _ = create(client)
        path = '/api/investigations/' + record['id']
        for endpoint, body in [('/archive', {'expected_version': 0}), ('/cleanup-preview', None),
                               ('/cleanup', {'scope_version': '0' * 64, 'confirm': True})]:
            assert client.post(path + endpoint, json=body,
                               headers={'Origin': 'https://untrusted.example'}).status_code == 403
        assert client.get(path).status_code == 200


def test_start_replay_survives_archive(database_url, tmp_path):
    with configured_client(database_url, tmp_path) as client:
        record, _ = create(client)
        path = '/api/investigations/' + record['id']
        key = {'Idempotency-Key': str(uuid4())}
        payload = {'service_id': 'orders'}
        running = client.post(path + '/attempts', json=payload, headers=key).json()
        client.post(path + '/attempts/' + running['attempts'][0]['id'] + '/cancel')
        archived = client.post(path + '/archive', json={'expected_version': 0}).json()
        replay = client.post(path + '/attempts', json=payload, headers=key)
        assert replay.status_code == 200, replay.text
        assert replay.json() == archived


def test_failed_cleanup_rolls_back_whole_scope(database_url, tmp_path):
    from sqlalchemy import create_engine, text
    with configured_client(database_url, tmp_path) as client:
        record, _ = create(client)
        path = '/api/investigations/' + record['id']
        archived = client.post(path + '/archive', json={'expected_version': 0}).json()
        preview = client.post(path + '/cleanup-preview').json()
        engine = create_engine(database_url)
        with engine.begin() as connection:
            connection.execute(text("CREATE FUNCTION fail_cleanup() RETURNS trigger LANGUAGE plpgsql AS $$ BEGIN RAISE EXCEPTION 'isolated rollback test'; END $$"))
            connection.execute(text('CREATE TRIGGER fail_cleanup BEFORE DELETE ON investigations FOR EACH ROW EXECUTE FUNCTION fail_cleanup()'))
        assert client.post(path + '/cleanup', json={'scope_version': preview['scope_version'], 'confirm': True}).status_code == 503
        assert client.get(path).json() == archived
        with engine.begin() as connection:
            connection.execute(text('DROP TRIGGER fail_cleanup ON investigations'))
        assert client.post(path + '/cleanup', json={'scope_version': preview['scope_version'], 'confirm': True}).status_code == 200
        engine.dispose()


def test_json_reference_protects_external_material(database_url, tmp_path):
    from sqlalchemy import create_engine, insert
    from app.severity import severity_events
    from test_evidence import started, material
    from uuid import UUID
    credential = uuid4().hex
    with configured_client(database_url, tmp_path, executor_credential=credential) as client:
        path, running, endpoint, auth = started(client, credential)
        client.post(endpoint, json=material(), headers=auth)
        client.post(path + '/attempts/' + running['attempts'][0]['id'] + '/cancel')
        evidence_id = client.get(path).json()['attempts'][0]['evidence'][0]['id']
        other, _ = create(client)
        engine = create_engine(database_url)
        with engine.begin() as connection:
            connection.execute(insert(severity_events).values(id=uuid4(), investigation_id=UUID(other['id']),
                request_key=uuid4(), request_body='{}', sequence=1,
                material={'nested': {'evidence_ids': [evidence_id]}}))
        assert client.post(path + '/cleanup-preview').status_code == 409
        assert client.get(path).status_code == 200
        engine.dispose()


def test_expiration_coordinator_drains_database_worker_on_shutdown(database_url, tmp_path):
    import asyncio
    from threading import Event
    from sqlalchemy import create_engine, event
    from app.cleanup import cleanup_coordinator
    from test_deadlines import ManualTicks
    now = [datetime(2026, 11, 5, 12, tzinfo=timezone.utc)]
    with configured_client(database_url, tmp_path, clock=lambda: now[0], deadline_schedule=ManualTicks()) as client:
        record, _ = create(client)
        path = '/api/investigations/' + record['id']
        client.post(path + '/archive', json={'expected_version': 0})
        now[0] = datetime(2026, 12, 5, 12, tzinfo=timezone.utc)
        engine = create_engine(database_url)
        entered, release = Event(), Event()
        def hold_delete(connection, cursor, statement, parameters, context, executemany):
            if statement.startswith('DELETE FROM investigations'):
                entered.set()
                assert release.wait(15)
        event.listen(engine, 'before_cursor_execute', hold_delete)
        async def exercise():
            task = asyncio.create_task(cleanup_coordinator(engine, lambda: now[0], interval=0.001))
            try:
                assert await asyncio.to_thread(entered.wait, 10)
                task.cancel()
                await asyncio.sleep(0.05)
                assert not task.done()
                release.set()
                try:
                    await task
                except asyncio.CancelledError:
                    pass
            finally:
                release.set()
                if not task.done():
                    task.cancel()
                    await asyncio.gather(task, return_exceptions=True)
        asyncio.run(exercise())
        assert client.get(path).status_code == 404
        event.remove(engine, 'before_cursor_execute', hold_delete)
        engine.dispose()


@pytest.mark.parametrize('database_url', ['0016'], indirect=True)
def test_migration_clears_old_deadline_without_archiving(database_url, tmp_path):
    import os
    import subprocess
    import sys
    from sqlalchemy import create_engine, text
    from test_investigations import ROOT
    identity = uuid4()
    engine = create_engine(database_url)
    with engine.begin() as connection:
        connection.execute(text("INSERT INTO investigations (id,title,description,created_at,updated_at,retention_expires_at) VALUES (:id,'legacy','',now(),now(),now()+interval '30 days')"), {'id': identity})
    result = subprocess.run([sys.executable, '-m', 'alembic', '-c', 'backend/alembic.ini', 'upgrade', 'head'],
        cwd=ROOT, env={**os.environ, 'DATABASE_URL': database_url, 'PYTHONPATH': str(ROOT / 'backend')}, capture_output=True)
    assert result.returncode == 0, result.stderr
    with configured_client(database_url, tmp_path) as client:
        record = client.get('/api/investigations/' + str(identity)).json()
        assert record['archived_at'] is None
        assert record['retention_expires_at'] is None
        assert record['archive_version'] == 0
        assert record['retention']['phase'] == 'retained'
    engine.dispose()


def test_expiration_sweeps_more_than_one_batch(database_url, tmp_path):
    from app.cleanup import clear_expired_archives
    from sqlalchemy import create_engine
    now = [datetime(2026, 11, 5, 12, tzinfo=timezone.utc)]
    with configured_client(database_url, tmp_path, clock=lambda: now[0]) as client:
        for _ in range(101):
            record, _ = create(client)
            assert client.post('/api/investigations/' + record['id'] + '/archive',
                               json={'expected_version': 0}).status_code == 200
        engine = create_engine(database_url)
        now[0] = datetime(2026, 12, 5, 12, tzinfo=timezone.utc)
        clear_expired_archives(engine, lambda: now[0])
        assert client.get('/api/investigations').json()['total'] == 0
        engine.dispose()
