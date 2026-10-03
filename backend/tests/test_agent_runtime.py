import json
import time
from uuid import uuid4

from langchain_core.messages import AIMessage

from test_investigations import database_url
from test_attempts import configured_client, create
import pytest


def agent_config(**changes):
    from app.core.config import AgentSettings
    return AgentSettings(_env_file=None, enabled=True, api_key='test-only', region_confirmed=True,
        input_microyuan_per_million_tokens=1_000_000,
        output_microyuan_per_million_tokens=1_000_000, price_version='test-only', **changes)


class Model:
    async def respond(self, messages):
        return AIMessage(content=json.dumps({
            'kind': 'hypothesis', 'category': 'dependency',
            'phenomenon': '请求失败', 'explanation': '依赖超时可能导致请求失败',
            'evidence_ids': [], 'alternatives': '仍需排除本地错误',
            'contradictions': '尚无充分材料', 'missing_evidence': ['需要因果证据'],
            'recommendations': ['核查依赖状态'],
        }), usage_metadata={'input_tokens': 100, 'output_tokens': 100, 'total_tokens': 200})


def wait_finished(client, path):
    limit = time.monotonic() + 12
    while time.monotonic() < limit:
        detail = client.get(path).json()
        if detail['status'] != '调查中':
            return detail
        time.sleep(.05)
    raise AssertionError('Agent did not finish')


def test_agent_persists_analysis_and_cost_without_raw_question(database_url, tmp_path):
    with configured_client(database_url, tmp_path, agent_settings=agent_config(), agent_model=Model()) as client:
        record, _ = create(client)
        path = '/api/investigations/' + record['id']
        response = client.post(path + '/attempts', json={'service_id': 'orders'},
                               headers={'Idempotency-Key': str(uuid4())})
        assert response.status_code == 201, response.text
        result = wait_finished(client, path)
        assert result['status'] == '已完成'
        attempt = result['attempts'][-1]
        assert attempt['result'] == '未确定根因'
        assert attempt['conclusion']['category'] == 'dependency'
        assert attempt['conclusion']['kind'] == 'undetermined'
        assert attempt['agent_run']['phase'] == 'completed'
        assert attempt['agent_run']['calls'] == 1
        assert attempt['agent_run']['spent_microyuan'] == 200
        assert 'test-only' not in str(result)


class QueryModel(Model):
    async def respond(self, messages):
        if not any(message.type == 'tool' for message in messages):
            return AIMessage(content='', tool_calls=[{'name': 'query_logs', 'args': {}, 'id': 'query-1'}],
                usage_metadata={'input_tokens': 100, 'output_tokens': 20, 'total_tokens': 120})
        reply = await super().respond(messages)
        result = json.loads(reply.content)
        context = json.loads(messages[-1].content)
        result['evidence_ids'] = [item['id'] for item in context['evidence']]
        reply.content = json.dumps(result)
        return reply


def test_agent_tools_collect_only_scoped_redacted_simulated_evidence(database_url, tmp_path):
    from app.log_sources import CollectedLogs
    from app.mock_logs import create_mock_logs
    from httpx import AsyncClient, ASGITransport

    class Source:
        mode = 'simulated'
        async def query(self, *, source_id, starts_at, ends_at):
            async with AsyncClient(transport=ASGITransport(app=create_mock_logs()), base_url='http://source') as client:
                response = await client.get('/v1/logs', params={'source_id': source_id,
                    'starts_at': starts_at.isoformat(), 'ends_at': ends_at.isoformat()})
            return CollectedLogs(response.json()['items'])

    with configured_client(database_url, tmp_path, agent_settings=agent_config(), agent_model=QueryModel(),
            query_sources={'controlled-orders': Source()}) as client:
        record, _ = create(client)
        path = '/api/investigations/' + record['id']
        assert client.post(path + '/attempts', json={'service_id': 'orders',
                           'starts_at': '2026-11-01T00:00:00Z', 'ends_at': '2026-11-01T01:00:00Z'},
                           headers={'Idempotency-Key': str(uuid4())}).status_code == 201
        result = wait_finished(client, path)
        attempt = result['attempts'][-1]
        assert result['status'] == '已完成', attempt
        assert result['evidence_count'] == 2
        assert attempt['conclusion']['kind'] == 'hypothesis'
        assert len(attempt['conclusion']['evidence_ids']) == 2
        assert attempt['agent_run']['calls'] == 2
        assert 'synthetic-secret' not in str(result)


@pytest.mark.parametrize('mode,reason', [('budget', 'budget_exhausted'), ('tool', 'tool_rejected'), ('invalid', 'model_error')])
def test_agent_rejects_unfunded_calls_unsafe_tools_and_invalid_products(database_url, tmp_path, mode, reason):
    class UnsafeModel(Model):
        async def respond(self, messages):
            if mode == 'budget':
                raise AssertionError('Unfunded model call')
            if mode == 'tool':
                return AIMessage(content='', tool_calls=[{'name': 'shell', 'args': {'cmd': 'secret'}, 'id': 'bad'}])
            return AIMessage(content='not-json-secret')
    config = agent_config(**({'budget_microyuan': 1} if mode == 'budget' else {}))
    with configured_client(database_url, tmp_path, agent_settings=config, agent_model=UnsafeModel()) as client:
        record, _ = create(client)
        path = '/api/investigations/' + record['id']
        client.post(path + '/attempts', json={'service_id': 'orders'}, headers={'Idempotency-Key': str(uuid4())})
        result = wait_finished(client, path)
        assert result['status'] == '失败'
        run = result['attempts'][-1]['agent_run']
        assert run['reason'] == reason
        assert run['calls'] == (0 if mode == 'budget' else 1)
        assert result['attempts'][-1]['conclusions'] == []
        assert 'not-json-secret' not in str(result)


def test_cancel_waits_for_model_exit_and_fences_late_result(database_url, tmp_path):
    import threading
    import asyncio
    entered, exited = threading.Event(), threading.Event()
    class SlowModel(Model):
        async def respond(self, messages):
            entered.set()
            try:
                await asyncio.sleep(60)
            except asyncio.CancelledError:
                await asyncio.sleep(.5)
                exited.set()
                return await super().respond(messages)
    with configured_client(database_url, tmp_path, agent_settings=agent_config(), agent_model=SlowModel()) as client:
        record, _ = create(client)
        path = '/api/investigations/' + record['id']
        started = client.post(path + '/attempts', json={'service_id': 'orders'},
                              headers={'Idempotency-Key': str(uuid4())}).json()
        assert entered.wait(5)
        cancel = client.post(path + '/attempts/' + started['attempts'][-1]['id'] + '/cancel').json()
        assert cancel['status'] == '调查中'
        assert cancel['attempts'][-1]['execution_phase'] == 'stopping'
        assert not exited.is_set()
        result = wait_finished(client, path)
        assert exited.is_set()
        assert result['status'] == '已取消'
        assert result['attempts'][-1]['conclusions'] == []
        assert result['attempts'][-1]['agent_run']['reserved_microyuan'] > 0


def test_model_impact_requires_human_confirmation_after_completion(database_url, tmp_path):
    class ImpactModel(Model):
        async def respond(self, messages):
            reply = await super().respond(messages)
            content = json.loads(reply.content)
            content['impact'] = {'format': 'controlled-impact-v1', 'business': 'core_unavailable',
                'users': 'broad', 'duration': 'ongoing', 'alternative': 'none'}
            reply.content = json.dumps(content)
            return reply
    with configured_client(database_url, tmp_path, agent_settings=agent_config(), agent_model=ImpactModel()) as client:
        record, _ = create(client)
        path = '/api/investigations/' + record['id']
        client.post(path + '/attempts', json={'service_id': 'orders'}, headers={'Idempotency-Key': str(uuid4())})
        result = wait_finished(client, path)
        assert result['severity'] == '未评估'
        suggestion = result['severity_suggestions'][-1]
        assert suggestion['status'] == 'pending'
        assert suggestion['severity'] == '高'
        confirmation = client.post(path + '/severity/suggestions/' + suggestion['id'] + '/confirm',
            json={'expected_version': result['severity_version']}, headers={'Idempotency-Key': str(uuid4())})
        assert confirmation.status_code == 201, confirmation.text
        assert client.get(path).json()['severity_source'] == 'human'
        stale = client.post(path + '/severity/suggestions/' + suggestion['id'] + '/confirm',
            json={'expected_version': result['severity_version']}, headers={'Idempotency-Key': str(uuid4())})
        assert stale.status_code == 409


def test_missing_usage_retains_reservation_after_success(database_url, tmp_path):
    class NoUsage(Model):
        async def respond(self, messages):
            reply = await super().respond(messages)
            reply.usage_metadata = None
            return reply
    with configured_client(database_url, tmp_path, agent_settings=agent_config(), agent_model=NoUsage()) as client:
        record, _ = create(client)
        path = '/api/investigations/' + record['id']
        client.post(path + '/attempts', json={'service_id': 'orders'}, headers={'Idempotency-Key': str(uuid4())})
        result = wait_finished(client, path)
        assert result['status'] == '已完成'
        assert result['attempts'][-1]['agent_run']['reserved_microyuan'] > 0
        assert result['attempts'][-1]['agent_run']['spent_microyuan'] == 0
