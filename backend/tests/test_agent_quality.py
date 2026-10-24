"""Evidence classification through the business API and separate HTTP sources."""
import json
import os
from pathlib import Path
from uuid import uuid4

import pytest

from test_investigations import database_url
from test_log_sources import serve_app
from test_incident_scenarios import scenario_client
from test_agent_runtime import QueryModel, agent_config, wait_finished
from test_attempts import create
from app.mock_logs import create_mock_logs


@pytest.mark.parametrize('scenario,expected,signal', [
    ('permission-denied', 'permission', 'permission_denied'),
    ('database-pool', 'resource', 'pool_capacity'),
    ('configuration-missing', 'configuration', 'configuration_missing'),
    ('redis-timeout', 'dependency', 'dependency_timeout'),
    ('order-correlation', 'unknown', None)])
def test_observed_directions_have_evidence_without_adopting_model_category(
        database_url, tmp_path, serve_app, scenario, expected, signal):
    class MisclassifiedModel(QueryModel):
        async def respond(self, messages):
            reply = await super().respond(messages)
            if not reply.tool_calls:
                body = json.loads(reply.content)
                body['category'] = 'application'
                reply.content = json.dumps(body)
            return reply

    with scenario_client(database_url, tmp_path, serve_app(create_mock_logs(scenario)), uuid4().hex,
                         agent_settings=agent_config(), agent_model=MisclassifiedModel()) as client:
        record, _ = create(client)
        path = '/api/investigations/' + record['id']
        assert client.post(path + '/attempts', json={'service_id': 'orders',
            'starts_at': '2026-11-01T00:00:00Z', 'ends_at': '2026-11-01T01:00:00Z'},
            headers={'Idempotency-Key': str(uuid4())}).status_code == 201
        result = wait_finished(client, path)
        attempt = result['attempts'][0]
        conclusion = attempt['conclusion']
        assert conclusion['category'] == expected
        assert conclusion['model_category'] == 'application'
        assert conclusion['kind'] == 'hypothesis'
        if signal:
            direction, = conclusion['observed_directions']
            assert direction['category'] == expected
            matching = [item['id'] for item in attempt['evidence']
                        if item['snippet']['observation']['signal'] == signal]
            assert direction['evidence_ids'] == matching
        else:
            assert conclusion['observed_directions'] == []
        assert client.get(path).json() == result


def test_agent_hypothesis_losing_cited_material_requires_manual_reverification(database_url, tmp_path, serve_app):
    from test_verification import report
    credential = uuid4().hex
    with scenario_client(database_url, tmp_path, serve_app(create_mock_logs('permission-denied')), credential,
                         agent_settings=agent_config(), agent_model=QueryModel()) as client:
        record, _ = create(client)
        path = '/api/investigations/' + record['id']
        client.post(path + '/attempts', json={'service_id': 'orders',
            'starts_at': '2026-11-01T00:00:00Z', 'ends_at': '2026-11-01T01:00:00Z'},
            headers={'Idempotency-Key': str(uuid4())})
        before = wait_finished(client, path)
        original = before['attempts'][0]['conclusion']
        auth = {'Authorization': 'Bearer ' + credential}
        assert report(client, path, auth, original['evidence_ids'][0], source_access='reachable',
            material_status='invalid', reason='material_integrity_failed').status_code == 201
        after = client.get(path).json()
        conclusion = after['attempts'][0]['conclusion']
        assert conclusion['verification'] == 'pending_reverification'
        assert conclusion['kind'] == 'hypothesis'
        assert conclusion['evidence_ids'] == original['evidence_ids']
        assert after['status'] == '已完成'
        assert len(after['attempts']) == 1
        assert client.get(path).json() == after
    with scenario_client(database_url, tmp_path, serve_app(create_mock_logs('permission-denied')), credential,
                         agent_settings=agent_config().model_copy(update={'enabled': False})) as client:
        from test_verification import causal_material
        from test_conclusions import proposal
        reply = client.post(path + '/attempts', json={'service_id': 'orders',
            'starts_at': '2026-11-01T00:00:00Z', 'ends_at': '2026-11-01T01:00:00Z',
            'reverify_conclusion_id': original['id']}, headers={'Idempotency-Key': str(uuid4())})
        assert reply.status_code == 201
        attempt_id = reply.json()['attempts'][-1]['id']
        base = '/api/executor' + path.removeprefix('/api') + '/attempts/' + attempt_id + '/executions'
        execution_id = client.get(base, headers=auth).json()['executions'][0]['id']
        endpoint = base + '/' + execution_id
        payload = causal_material()
        payload['source_id'] = 'sim-orders'
        saved = client.post(endpoint + '/evidence', json=payload, headers=auth)
        assert saved.status_code == 201
        evidence_id = saved.json()['id']
        assert client.post(endpoint + '/conclusions', json=proposal(kind='confirmed',
            mechanism='dependency_error_propagation', evidence_ids=[evidence_id], missing=[]),
            headers={**auth, 'Idempotency-Key': str(uuid4())}).status_code == 201
        restored = client.get(path).json()['attempts'][0]['conclusion']
        assert restored['verification'] == 'supported_controlled_trace'
        assert restored['kind'] == 'hypothesis'
        assert restored['causal_chain'] == original['causal_chain']
        assert report(client, path, auth, evidence_id, source_access='reachable',
            material_status='invalid', reason='material_integrity_failed').status_code == 201
        lost_again = client.get(path).json()['attempts'][0]['conclusion']
        assert lost_again['verification'] == 'pending_reverification'
        assert len(lost_again['verification_history']) == 3
        assert len(client.get(path).json()['attempts']) == 2


def test_multiple_observed_directions_keep_separate_citations_without_choosing_a_cause(
        database_url, tmp_path, serve_app):
    from datetime import datetime
    from fastapi import FastAPI
    from app.incident_scenarios import collect
    source = FastAPI()

    @source.get('/v1/logs')
    def logs(source_id: str, starts_at: datetime, ends_at: datetime):
        scenario = 'permission-denied' if source_id == 'sim-orders' else 'database-pool'
        return {'protocol': 'simulated-logs-v1', 'items': collect(scenario, source_id, starts_at, ends_at)}

    with scenario_client(database_url, tmp_path, serve_app(source), uuid4().hex,
                         agent_settings=agent_config(), agent_model=QueryModel()) as client:
        record, _ = create(client)
        path = '/api/investigations/' + record['id']
        assert client.post(path + '/attempts', json={'service_id': 'orders',
            'starts_at': '2026-11-01T00:00:00Z', 'ends_at': '2026-11-01T01:00:00Z'},
            headers={'Idempotency-Key': str(uuid4())}).status_code == 201
        attempt = wait_finished(client, path)['attempts'][0]
        conclusion = attempt['conclusion']
        assert conclusion['category'] == 'unknown'
        directions = {item['category']: item['evidence_ids'] for item in conclusion['observed_directions']}
        assert set(directions) == {'permission', 'resource'}
        for category, signal in [('permission', 'permission_denied'), ('resource', 'pool_capacity')]:
            assert directions[category] == [item['id'] for item in attempt['evidence']
                                           if item['snippet']['observation']['signal'] == signal]


@pytest.mark.skipif(os.environ.get('RUN_BAILIAN_QUALITY') != '1', reason='Paid quality matrix is opt-in')
@pytest.mark.parametrize('scenario,category,count', [
    ('order-correlation', 'unknown', 2), ('redis-propagation', 'dependency', 3),
    ('redis-contradiction', 'dependency', 3), ('database-pool', 'resource', 3),
    ('configuration-missing', 'configuration', 2), ('permission-denied', 'permission', 2),
    ('order-empty', 'unknown', 0), ('order-partial', 'unknown', 2)])
def test_real_model_quality_matrix(database_url, tmp_path, serve_app, scenario, category, count):
    from app.agent_model import AgentAnalysis, BailianModel
    from app.core.config import AgentSettings
    config = AgentSettings(_env_file=None, enabled=True, model='qwen-plus', region_confirmed=True,
        base_url='https://dashscope.aliyuncs.com/compatible-mode/v1', budget_microyuan=100_000,
        input_microyuan_per_million_tokens=800000, output_microyuan_per_million_tokens=2000000,
        price_version='beijing-public-nonthinking-2026-11-07', max_calls=3, max_output_tokens=2048)
    report_dir = Path('.scratch/agent-quality-followup/live')
    report_dir.mkdir(parents=True, exist_ok=True)
    previous = list(Path('.scratch/simulated-incident-scenarios/live').glob('*.json'))
    previous += list(report_dir.glob('*.json'))
    total = 31_547 + sum(json.loads(p.read_text())['run']['spent_microyuan']
                        + json.loads(p.read_text())['run']['reserved_microyuan'] for p in previous)
    assert total + config.budget_microyuan <= 10_000_000, 'Cumulative budget exhausted'

    class RecordingModel(BailianModel):
        def __init__(self):
            super().__init__(config)
            self.analysis = None
            self.usage = []

        async def respond(self, messages):
            reply = await super().respond(messages)
            self.usage.append({key: value for key, value in (reply.usage_metadata or {}).items()
                               if key in ('input_tokens', 'output_tokens', 'total_tokens')
                               and type(value) is int and value >= 0})
            if not reply.tool_calls:
                # Persist only text accepted by the same strict sensitive-data boundary as the app.
                if config.api_key.get_secret_value() in reply.content:
                    raise ValueError('Secret in model output')
                self.analysis = AgentAnalysis.model_validate_json(reply.content).model_dump(mode='json')
            return reply

    model = RecordingModel()
    with scenario_client(database_url, tmp_path, serve_app(create_mock_logs(scenario)), uuid4().hex,
                         agent_settings=config, agent_model=model) as client:
        try:
            record, _ = create(client)
            path = '/api/investigations/' + record['id']
            assert client.post(path + '/attempts', json={'service_id': 'orders',
                'starts_at': '2026-11-01T00:00:00Z', 'ends_at': '2026-11-01T01:00:00Z'},
                headers={'Idempotency-Key': str(uuid4())}).status_code == 201
            import time
            deadline = time.monotonic() + 110
            while True:
                detail = client.get(path).json()
                if detail['status'] != '调查中':
                    break
                assert time.monotonic() < deadline, 'Bounded real Agent did not finish'
                time.sleep(.25)
            attempt = detail['attempts'][0]
            conclusion = attempt['conclusion']
            report = {'scenario': scenario, 'source': 'simulated-http', 'model': 'real-qwen-plus',
                'status': detail['status'], 'evidence': attempt['evidence'], 'coverage': attempt['query_coverage'],
                'run': attempt['agent_run'], 'conclusion': conclusion, 'model_analysis': model.analysis,
                'token_usage': model.usage,
                'expected_observed_category': category,
                'model_category_matches_observations': bool(model.analysis and model.analysis['category'] == category),
                'semantic_review': 'pending-human-review'}
            (report_dir / (scenario + '-' + uuid4().hex[:8] + '.json')).write_text(
                json.dumps(report, ensure_ascii=True, indent=2), encoding='utf-8')
            assert detail['status'] == '已完成'
            assert detail['evidence_count'] == count
            assert conclusion['category'] == category
            assert conclusion['kind'] in ('hypothesis', 'undetermined')
            assert set(conclusion['evidence_ids']).issubset(item['id'] for item in attempt['evidence'])
            if count:
                assert conclusion['grounding'] == 'structured-observations-v1'
            if scenario == 'redis-contradiction':
                assert '未解决矛盾' in conclusion['contradictions']
        finally:
            client.portal.call(model.close)
