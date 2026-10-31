"""Synthetic field conditions through the investigation API and external HTTP source."""
import json
from uuid import uuid4

import pytest

from app.mock_logs import create_mock_logs
from test_investigations import database_url
from test_log_sources import serve_app
from test_incident_scenarios import scenario_client
from test_agent_runtime import QueryModel, agent_config, wait_finished
from test_attempts import create


@pytest.mark.parametrize('scenario,count,categories', [
    ('order-noise', 3, {'permission'}),
    ('order-multiple', 3, {'permission', 'resource'}),
    ('order-conflicting', 3, {'dependency'}),
    ('order-missing', 2, set()),
])
def test_edge_cases_preserve_independent_facts_gaps_and_history(
        database_url, tmp_path, serve_app, scenario, count, categories):
    seen = []

    class OverconfidentModel(QueryModel):
        async def respond(self, messages):
            seen.extend(message.content for message in messages)
            reply = await super().respond(messages)
            if not reply.tool_calls:
                body = json.loads(reply.content)
                body.update(kind='confirmed', explanation='Redis caused every failed request.',
                            contradictions='All alternatives excluded.',
                            recommendations=['Restart the service.'])
                reply.content = json.dumps(body)
            return reply

    credential = uuid4().hex
    source_url = serve_app(create_mock_logs(scenario))
    with scenario_client(database_url, tmp_path, source_url, credential,
                         agent_settings=agent_config(), agent_model=OverconfidentModel()) as client:
        record, _ = create(client)
        path = '/api/investigations/' + record['id']
        assert client.post(path + '/attempts', json={'service_id': 'orders',
            'starts_at': '2026-11-01T00:00:00Z', 'ends_at': '2026-11-01T01:00:00Z'},
            headers={'Idempotency-Key': str(uuid4())}).status_code == 201
        detail = wait_finished(client, path)
        assert detail['status'] == '已完成'
        assert detail['evidence_count'] == count
        attempt = detail['attempts'][0]
        conclusion = attempt['conclusion']
        assert conclusion['kind'] == 'hypothesis'
        assert {item['category'] for item in conclusion['observed_directions']} == categories
        if len(categories) != 1:
            assert conclusion['category'] == 'unknown'
        assert 'Redis caused every' not in conclusion['causal_chain']
        assert 'All alternatives excluded' not in conclusion['contradictions']
        assert 'Restart' not in str(conclusion['recommendations'])
        if scenario == 'order-noise':
            fields = [item['snippet']['fields'] for item in attempt['evidence']]
            assert len({item['correlation_id'] for item in fields}) == 2
        if scenario == 'order-conflicting':
            assert '未解决矛盾' in conclusion['contradictions']
        if scenario == 'order-missing':
            assert '具体依赖身份' in conclusion['causal_chain']
            assert attempt['query_coverage']['missing_sources']
        outbound = json.dumps(seen)
        assert scenario not in outbound
        assert 'synthetic-secret' not in outbound
        assert 'synthetic@example.invalid' not in outbound
        assert client.get(path).json() == detail
    with scenario_client(database_url, tmp_path, source_url, credential) as restarted:
        assert restarted.get(path).json() == detail
