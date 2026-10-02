from datetime import datetime, timedelta, timezone
from uuid import UUID, uuid4

import pytest

from test_deadlines import ManualTicks
from test_evidence import started, material
from test_conclusions import proposal

from test_investigations import database_url
from test_attempts import configured_client, create


def test_pending_retention_boundaries_are_read_only_and_survive_restart(database_url, tmp_path):
    now = [datetime(2026, 11, 4, 12, tzinfo=timezone.utc)]
    with configured_client(database_url, tmp_path, clock=lambda: now[0]) as client:
        record, _ = create(client)
        path = '/api/investigations/' + record['id']
        assert record['retention']['phase'] == 'retained'
        record = client.post(path + '/archive', json={'expected_version': 0}).json()
        for instant, phase in [
            ('2026-11-27T11:59:59.999999+00:00', 'archived'),
            ('2026-11-27T12:00:00+00:00', 'expiring'),
            ('2026-12-04T11:59:59.999999+00:00', 'expiring'),
            ('2026-12-04T12:00:00+00:00', 'awaiting_cleanup'),
            ('2027-01-04T12:00:00+00:00', 'awaiting_cleanup'),
        ]:
            now[0] = datetime.fromisoformat(instant)
            after = client.get(path).json()
            assert after['retention']['phase'] == phase
            assert after['updated_at'] == record['updated_at']
            assert after['retention_expires_at'] == '2026-12-04T12:00:00Z'
            assert client.get('/api/investigations', params={'q': record['title']}).status_code == 200
    with configured_client(database_url, tmp_path, clock=lambda: now[0]) as restarted:
        assert restarted.get(path).json() == after


@pytest.mark.parametrize('ending', ['completed', 'canceled', 'timeout'])
def test_terminal_retention_and_new_round_recalculate_without_losing_material(database_url, tmp_path, ending):
    now = [datetime(2026, 11, 4, 12, tzinfo=timezone.utc)]
    credential = uuid4().hex
    with configured_client(database_url, tmp_path, clock=lambda: now[0], executor_credential=credential,
                           deadline_schedule=ManualTicks()) as client:
        path, running, endpoint, auth = started(client, credential)
        assert running['retention']['phase'] == 'running'
        assert client.post(endpoint, json=material(), headers=auth).status_code == 201
        now[0] += timedelta(minutes=10) if ending == 'timeout' else timedelta(minutes=1)
        if ending == 'completed':
            reply = client.post(endpoint.removesuffix('/evidence') + '/conclusions', json=proposal(),
                                headers={**auth, 'Idempotency-Key': str(uuid4())})
            assert reply.status_code == 201
        elif ending == 'canceled':
            assert client.post(path + '/attempts/' + running['attempts'][0]['id'] + '/cancel').status_code == 200
        else:
            assert client.post(endpoint, json=material(), headers=auth).status_code == 403
        ended = client.get(path).json()
        assert ended['status'] == {'completed': '已完成', 'canceled': '已取消', 'timeout': '失败'}[ending]
        assert ended['retention_expires_at'] is None
        now[0] += timedelta(days=31)
        expired = client.get(path).json()
        assert expired['retention']['phase'] == 'retained'
        assert expired['attempts'] == ended['attempts']
        reopened = client.post(path + '/attempts', json={'service_id': 'orders'},
                               headers={'Idempotency-Key': str(uuid4())}).json()
        assert reopened['retention']['phase'] == 'running'
        assert reopened['retention_expires_at'] is None
        assert reopened['attempts'][0] == ended['attempts'][0]
        now[0] += timedelta(minutes=1)
        latest_id = reopened['attempts'][-1]['id']
        second = client.post(path + '/attempts/' + latest_id + '/cancel').json()
        assert second['retention']['phase'] == 'retained'
        assert second['retention_expires_at'] is None
        assert second['attempts'][0] == ended['attempts'][0]


@pytest.mark.parametrize('missing', [False, True])
@pytest.mark.parametrize('running', [False, True])
def test_artificial_inconsistent_deadline_is_not_repaired_on_read(database_url, tmp_path, running, missing):
    from sqlalchemy import create_engine, update
    from app.investigations import investigations
    now = datetime(2026, 11, 4, 12, tzinfo=timezone.utc)
    with configured_client(database_url, tmp_path, clock=lambda: now, deadline_schedule=ManualTicks()) as client:
        record, _ = create(client)
        path = '/api/investigations/' + record['id']
        if running:
            record = client.post(path + '/attempts', json={'service_id': 'orders'},
                                 headers={'Idempotency-Key': str(uuid4())}).json()
        engine = create_engine(database_url)
        with engine.begin() as connection:
            connection.execute(update(investigations).where(investigations.c.id == UUID(record['id']))
                               .values(archived_at=now, retention_expires_at=None if missing else now - timedelta(days=1)))
        engine.dispose()
        after = client.get(path).json()
        assert after['retention']['phase'] == ('running' if running else 'inconsistent')
        assert after['retention_expires_at'] == (None if missing else '2026-11-03T12:00:00Z')
        assert after['updated_at'] == record['updated_at']


def test_end_time_does_not_define_archive_deadline(database_url, tmp_path):
    from sqlalchemy import create_engine, update
    from app.attempts import attempts
    with configured_client(database_url, tmp_path, deadline_schedule=ManualTicks()) as client:
        record, _ = create(client)
        path = '/api/investigations/' + record['id']
        running = client.post(path + '/attempts', json={'service_id': 'orders'}, headers={'Idempotency-Key': str(uuid4())}).json()
        attempt_id = running['attempts'][0]['id']
        client.post(path + '/attempts/' + attempt_id + '/cancel')
        engine = create_engine(database_url)
        with engine.begin() as connection:
            connection.execute(update(attempts).where(attempts.c.id == UUID(attempt_id)).values(ended_at=None))
        engine.dispose()
        assert client.get(path).json()['retention']['phase'] == 'retained'


@pytest.mark.parametrize('phase', ['stopping', 'confirming_execution'])
def test_unconfirmed_stop_keeps_running_retention(database_url, tmp_path, phase):
    from app.attempts import ControlledExecutionAdapter

    class UnknownExecution(ControlledExecutionAdapter):
        pass

    credential = uuid4().hex
    with configured_client(database_url, tmp_path, executor_credential=credential,
                           execution_adapter=UnknownExecution(), deadline_schedule=ManualTicks()) as client:
        path, running, endpoint, auth = started(client, credential)
        if phase == 'stopping':
            response = client.post(path + '/attempts/' + running['attempts'][0]['id'] + '/cancel')
        else:
            response = client.post(endpoint.removesuffix('/evidence') + '/lost', headers=auth)
        assert response.status_code == 200
        after = response.json()
        assert after['attempts'][0]['execution_phase'] == phase
        assert after['retention']['phase'] == 'running'
        assert after['retention_expires_at'] is None
        assert client.get(path).json() == after
