"""Remaining tool and shared-budget acceptance at the business API boundary."""
import asyncio
import json
import threading
from uuid import uuid4

import pytest
from langchain_core.messages import AIMessage

from app.mock_logs import create_mock_logs
from test_investigations import database_url
from test_log_sources import serve_app
from test_incident_scenarios import scenario_client
from test_agent_runtime import Model, QueryModel, agent_config, wait_finished
from test_attempts import configured_client, create


def begin(client):
    record, _ = create(client)
    path = '/api/investigations/' + record['id']
    assert client.post(path + '/attempts', json={'service_id': 'orders',
        'starts_at': '2026-11-01T00:00:00Z', 'ends_at': '2026-11-01T01:00:00Z'},
        headers={'Idempotency-Key': str(uuid4())}).status_code == 201
    return path


@pytest.mark.parametrize('finish', [False, True])
def test_repeated_readonly_queries_stop_at_limit_and_keep_deduplicated_evidence(database_url, tmp_path, serve_app, finish):
    class LoopingModel(QueryModel):
        async def respond(self, messages):
            if finish and sum(message.type == 'tool' for message in messages) == 2:
                return await super().respond(messages)
            return AIMessage(content='', tool_calls=[{'name': 'query_logs', 'args': {}, 'id': uuid4().hex}],
                usage_metadata={'input_tokens': 100, 'output_tokens': 20, 'total_tokens': 120})

    with scenario_client(database_url, tmp_path, serve_app(create_mock_logs('order-correlation')), uuid4().hex,
                         agent_settings=agent_config(max_calls=3), agent_model=LoopingModel()) as client:
        path = begin(client)
        detail = wait_finished(client, path)
        attempt = detail['attempts'][0]
        assert detail['status'] == ('已完成' if finish else '失败')
        assert attempt['agent_run']['reason'] == ('normal_completion' if finish else 'call_limit')
        assert attempt['agent_run']['calls'] == 3
        assert len(attempt['queries']) == (2 if finish else 3)
        assert detail['evidence_count'] == 2
        if finish:
            assert attempt['conclusion']['kind'] == 'hypothesis'
        else:
            assert attempt['conclusions'] == []
        assert client.get(path).json() == detail


def test_three_investigations_share_one_budget_and_unknown_usage_stays_reserved(database_url, tmp_path):
    entered, release = threading.Event(), threading.Event()

    class UnknownUsageModel(Model):
        calls = 0

        async def respond(self, messages):
            self.calls += 1
            entered.set()
            while not release.is_set():
                await asyncio.sleep(.05)
            reply = await super().respond(messages)
            reply.usage_metadata = None
            return reply

    model = UnknownUsageModel()
    config = agent_config(budget_microyuan=15_000)
    with configured_client(database_url, tmp_path, agent_settings=config, agent_model=model) as client:
        try:
            first = begin(client)
            assert entered.wait(5)
            second, third = begin(client), begin(client)
            failed = [wait_finished(client, path)['attempts'][0]['agent_run'] for path in (second, third)]
            assert all(run['reason'] == 'budget_exhausted' and run['calls'] == 0 for run in failed)
            assert model.calls == 1
            held = client.get(first).json()['attempts'][0]['agent_run']['reserved_microyuan']
            assert 0 < held <= config.budget_microyuan
        finally:
            release.set()
        completed = wait_finished(client, first)
        assert completed['status'] == '已完成'
        assert completed['attempts'][0]['agent_run']['reserved_microyuan'] == held
        again = wait_finished(client, begin(client))
        assert again['attempts'][0]['agent_run']['reason'] == 'budget_exhausted'
        assert model.calls == 1


def test_legacy_agent_recommendations_cannot_display_environment_writes(database_url, tmp_path, serve_app):
    class WritingModel(QueryModel):
        async def respond(self, messages):
            reply = await super().respond(messages)
            if not reply.tool_calls:
                body = json.loads(reply.content)
                body['recommendations'] = ['Restart the service and increase the connection pool capacity.']
                reply.content = json.dumps(body)
            return reply

    with scenario_client(database_url, tmp_path, serve_app(create_mock_logs('normal')), uuid4().hex,
                         agent_settings=agent_config(), agent_model=WritingModel()) as client:
        result = wait_finished(client, begin(client))
        assert result['status'] == '已完成'
        assert all(item['snippet'].get('observation') is None for item in result['attempts'][0]['evidence'])
        recommendations = result['attempts'][0]['conclusion']['recommendations']
        assert recommendations
        assert 'Restart' not in str(recommendations)
        assert '只读' in str(recommendations)
