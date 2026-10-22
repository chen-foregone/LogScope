"""Scenario acceptance through real HTTP collection and business API persistence."""
import json
import os
from pathlib import Path
from uuid import uuid4

import httpx
import pytest
from fastapi.testclient import TestClient

from app.main import create_app
from app.log_sources import SimulatedLogSource
from app.mock_logs import create_mock_logs
from test_investigations import database_url
from test_log_sources import serve_app
from test_evidence import started

SOURCES = ['sim-gateway', 'sim-orders', 'sim-database', 'sim-redis']
ROLES = dict(zip(SOURCES, ['gateway', 'application', 'database', 'redis']))


def scenario_client(url, tmp_path, source_url, credential, **options):
    config = tmp_path / 'scenario-services.json'
    config.write_text(json.dumps({'services': [{'id': 'orders', 'name': '合成订单链路',
                                              'source_ids': SOURCES, 'source_roles': ROLES}]}), encoding='utf-8')
    return TestClient(create_app(database_url=url, services_file=config, executor_credential=credential,
        query_sources={source: SimulatedLogSource(source_url, component=ROLES[source]) for source in SOURCES}, **options))


def test_gateway_correlation_is_scoped_redacted_distinct_and_persistent(database_url, tmp_path, serve_app):
    source_url = serve_app(create_mock_logs('order-correlation'))
    credential = uuid4().hex
    with scenario_client(database_url, tmp_path, source_url, credential) as client:
        path, _, endpoint, auth = started(client, credential)
        queries = endpoint.removesuffix('/evidence') + '/queries'
        for _ in range(2):
            reply = client.post(queries, headers={**auth, 'Idempotency-Key': str(uuid4())})
            assert reply.status_code == 201
            assert all(source['state'] == 'succeeded' for source in reply.json()['sources'])
            detail = client.get(path).json()
            assert detail['evidence_count'] == 2
        evidence = detail['attempts'][0]['evidence']
        assert {item['snippet']['observation']['component'] for item in evidence} == {'gateway', 'application'}
        assert next(item for item in evidence if item['snippet']['observation']['component'] == 'gateway')[
            'snippet']['observation']['http_status'] == 504
        assert all(item['source_mode'] == 'simulated' for item in evidence)
        assert len({item['source'] for item in evidence}) == 2
        assert 'synthetic-secret' not in json.dumps(detail)
        assert all(item['verification'] == 'retained_structured_material' for item in evidence)
    with scenario_client(database_url, tmp_path, source_url, credential) as restarted:
        assert restarted.get(path).json() == detail
    with httpx.Client(base_url=source_url, trust_env=False) as external:
        params = {'source_id': 'sim-gateway', 'starts_at': '2026-11-01T00:15:00Z',
                  'ends_at': '2026-11-01T00:16:00Z'}
        assert len(external.get('/v1/logs', params=params).json()['items']) == 2
        params.update(starts_at='2026-11-01T00:14:00Z', ends_at='2026-11-01T00:15:00Z')
        assert external.get('/v1/logs', params=params).json()['items'] == []
        params['source_id'] = 'foreign'
        assert external.get('/v1/logs', params=params).status_code == 422


@pytest.mark.parametrize('scenario,contradiction', [('redis-timeout', None),
    ('redis-propagation', 'none_observed'), ('redis-contradiction', 'unresolved')])
def test_redis_observations_reach_agent_without_answer_leak(database_url, tmp_path, serve_app, scenario, contradiction):
    from test_agent_runtime import QueryModel, agent_config, wait_finished
    seen = []

    class ObservingModel(QueryModel):
        async def respond(self, messages):
            seen.extend(message.content for message in messages)
            reply = await super().respond(messages)
            if not reply.tool_calls:
                body = json.loads(reply.content)
                body['missing_evidence'] = ['Restart Redis to prove the root cause.']
                reply.content = json.dumps(body)
            return reply

    credential = uuid4().hex
    with scenario_client(database_url, tmp_path, serve_app(create_mock_logs(scenario)), credential,
                         agent_settings=agent_config(), agent_model=ObservingModel()) as client:
        from test_attempts import create
        record, _ = create(client)
        path = '/api/investigations/' + record['id']
        assert client.post(path + '/attempts', json={'service_id': 'orders',
            'starts_at': '2026-11-01T00:00:00Z', 'ends_at': '2026-11-01T01:00:00Z'},
            headers={'Idempotency-Key': str(uuid4())}).status_code == 201
        detail = wait_finished(client, path)
        attempt = detail['attempts'][0]
        assert detail['status'] == '已完成'
        assert detail['evidence_count'] == 3
        assert attempt['conclusion']['kind'] == 'hypothesis'
        assert len(attempt['conclusion']['evidence_ids']) == 3
        application = next(item['snippet'] for item in attempt['evidence']
                           if item['snippet']['observation']['component'] == 'application')
        if contradiction:
            assert application['causal_trace']['contradiction'] == contradiction
        else:
            assert 'causal_trace' not in application
        if contradiction == 'unresolved':
            assert '未解决矛盾' in attempt['conclusion']['contradictions']
            assert 'Restart' not in str(attempt['conclusion']['missing'])
        outbound = json.dumps(seen)
        assert 'synthetic-secret' not in outbound
        assert scenario not in outbound
        assert 'dependency_timeout' in outbound
        tool_context = next(json.loads(content) for content in reversed(seen)
                            if content.startswith('{') and '"coverage"' in content)
        assert {item['source'] for item in tool_context['evidence']} == {
            item['source'] for item in attempt['evidence']}


@pytest.mark.parametrize('scenario,component,signal,category,count', [
    ('database-pool', 'database', 'pool_capacity', 'resource', 3),
    ('configuration-missing', 'application', 'configuration_missing', 'configuration', 2),
    ('permission-denied', 'application', 'permission_denied', 'permission', 2)])
def test_resource_and_access_facts_are_persisted_without_confirming_cause(
        database_url, tmp_path, serve_app, scenario, component, signal, category, count):
    from test_agent_runtime import QueryModel, agent_config, wait_finished
    seen = []

    class CategorizingModel(QueryModel):
        async def respond(self, messages):
            seen.extend(message.content for message in messages)
            reply = await super().respond(messages)
            if not reply.tool_calls:
                body = json.loads(reply.content)
                body.update(kind='confirmed', category=category,
                            explanation='The database pool is the proven root cause of all failures.',
                            alternatives='No other cause is possible.',
                            recommendations=['Increase connection pool capacity and restart the service.'])
                reply.content = json.dumps(body)
            return reply

    with scenario_client(database_url, tmp_path, serve_app(create_mock_logs(scenario)), uuid4().hex,
                         agent_settings=agent_config(), agent_model=CategorizingModel()) as client:
        from test_attempts import create
        record, _ = create(client)
        path = '/api/investigations/' + record['id']
        assert client.post(path + '/attempts', json={'service_id': 'orders',
            'starts_at': '2026-11-01T00:00:00Z', 'ends_at': '2026-11-01T01:00:00Z'},
            headers={'Idempotency-Key': str(uuid4())}).status_code == 201
        detail = wait_finished(client, path)
        attempt = detail['attempts'][0]
        assert detail['status'] == '已完成'
        assert detail['evidence_count'] == count
        observation = next(item['snippet']['observation'] for item in attempt['evidence']
                           if item['snippet']['observation']['signal'] == signal)
        assert observation['component'] == component
        if signal == 'pool_capacity':
            assert observation == {'component': 'database', 'signal': 'pool_capacity',
                                   'used': 100, 'capacity': 100, 'unit': 'connections'}
        assert attempt['conclusion']['category'] == category
        assert attempt['conclusion']['kind'] == 'hypothesis'
        assert 'proven root cause' not in attempt['conclusion']['causal_chain']
        assert 'No other cause' not in attempt['conclusion']['alternatives']
        assert not any('restart' in item for item in attempt['conclusion']['recommendations'])
        assert attempt['conclusion']['grounding'] == 'structured-observations-v1'
        assert signal in json.dumps(seen)


@pytest.mark.parametrize('observation,changes', [
    ({'component': 'gateway', 'signal': 'http_response', 'http_status': 600}, {}),
    ({'component': 'gateway', 'signal': 'http_response', 'http_status': True}, {}),
    ({'component': 'database', 'signal': 'pool_capacity', 'used': 101, 'capacity': 100, 'unit': 'connections'}, {}),
    ({'component': 'database', 'signal': 'pool_capacity', 'used': -1, 'capacity': 100, 'unit': 'connections'}, {}),
    ({'component': 'database', 'signal': 'pool_capacity', 'used': 100, 'capacity': 100, 'unit': 'bytes'}, {}),
    ({'component': 'application', 'signal': 'configuration_missing', 'secret': 'do-not-persist'}, {}),
    ({'component': 'foreign', 'signal': 'permission_denied'}, {}),
    ({'component': 'application', 'signal': 'request_timeout'}, {'format': 'controlled-event-v1'}),
    ({'component': 'application', 'signal': 'permission_denied'}, {}),
])
def test_invalid_v2_observations_are_rejected_without_raw_values(database_url, tmp_path, observation, changes):
    from test_attempts import configured_client
    from test_evidence import material
    credential = uuid4().hex
    with configured_client(database_url, tmp_path, executor_credential=credential) as client:
        path, _, endpoint, auth = started(client, credential)
        payload = material()
        payload.update({'format': 'controlled-event-v2', **changes})
        payload['record']['observation'] = observation
        reply = client.post(endpoint, json=payload, headers=auth)
        assert reply.status_code == 422
        assert 'do-not-persist' not in reply.text
        assert client.get(path).json()['evidence_count'] == 0


@pytest.mark.parametrize('scenario,count,status,missing', [
    ('order-empty', 0, '已完成', False), ('order-partial', 2, '已完成', True),
    ('order-unavailable', 0, '失败', True)])
def test_empty_partial_and_unavailable_have_distinct_outcomes(database_url, tmp_path, serve_app, scenario, count, status, missing):
    from test_agent_runtime import QueryModel, agent_config, wait_finished
    observed = []

    class CoverageModel(QueryModel):
        async def respond(self, messages):
            observed.extend(json.loads(message.content) for message in messages if message.type == 'tool')
            return await super().respond(messages)

    with scenario_client(database_url, tmp_path, serve_app(create_mock_logs(scenario)), uuid4().hex,
                         agent_settings=agent_config(), agent_model=CoverageModel()) as client:
        from test_attempts import create
        record, _ = create(client)
        path = '/api/investigations/' + record['id']
        assert client.post(path + '/attempts', json={'service_id': 'orders',
            'starts_at': '2026-11-01T00:00:00Z', 'ends_at': '2026-11-01T01:00:00Z'},
            headers={'Idempotency-Key': str(uuid4())}).status_code == 201
        detail = wait_finished(client, path)
        attempt = detail['attempts'][0]
        assert detail['status'] == status
        assert detail['evidence_count'] == count
        assert bool(attempt['query_coverage']['missing_sources']) == missing
        if status == '已完成':
            assert attempt['result'] == '未确定根因'
            assert bool(observed[-1]['coverage']['missing_sources']) == missing
            if scenario == 'order-partial':
                missing_sources = observed[-1]['coverage']['missing_sources']
                assert {item['component'] for item in observed[-1]['registered_sources']
                        if item['source'] in missing_sources} == {'database', 'redis'}
        else:
            assert attempt['end_reason'] == 'necessary_sources_unavailable'
        assert client.get(path).json() == detail


def test_temporary_failures_are_counted_per_source(serve_app):
    url = serve_app(create_mock_logs('temporary'))
    with httpx.Client(base_url=url, trust_env=False) as client:
        params = {'starts_at': '2026-11-01T00:00:00Z', 'ends_at': '2026-11-01T01:00:00Z'}
        assert [client.get('/v1/logs', params={**params, 'source_id': 'sim-orders'}).status_code
                for _ in range(3)] == [503, 503, 200]
        assert [client.get('/v1/logs', params={**params, 'source_id': 'sim-redis'}).status_code
                for _ in range(3)] == [503, 503, 200]


@pytest.mark.parametrize('selection', ['empty', 'legacy-only'])
def test_v2_grounding_cannot_be_bypassed_by_evidence_selection(database_url, tmp_path, serve_app, selection):
    from fastapi import FastAPI
    from app.incident_scenarios import collect
    from datetime import datetime, timezone
    from test_agent_runtime import QueryModel, agent_config, wait_finished
    app = FastAPI()

    @app.get('/v1/logs')
    def logs(source_id: str):
        items = collect('order-correlation', source_id, datetime(2026, 11, 1, tzinfo=timezone.utc),
                        datetime(2026, 11, 1, 1, tzinfo=timezone.utc))
        if source_id == 'sim-gateway':
            for item in items:
                item['format'] = 'controlled-event-v1'
                item['record'].pop('observation', None)
        return {'protocol': 'simulated-logs-v1', 'items': items}

    class UncitedModel(QueryModel):
        async def respond(self, messages):
            reply = await super().respond(messages)
            if not reply.tool_calls:
                context = json.loads(messages[-1].content)
                body = json.loads(reply.content)
                ids = [item['id'] for item in context['evidence'] if 'observation' not in item['snippet']]
                body.update(evidence_ids=[] if selection == 'empty' else ids,
                            explanation='The proven root cause is a database failure.',
                            recommendations=['Restart the service.'])
                reply.content = json.dumps(body)
            return reply

    with scenario_client(database_url, tmp_path, serve_app(app), uuid4().hex,
                         agent_settings=agent_config(), agent_model=UncitedModel()) as client:
        from test_attempts import create
        record, _ = create(client)
        path = '/api/investigations/' + record['id']
        client.post(path + '/attempts', json={'service_id': 'orders',
            'starts_at': '2026-11-01T00:00:00Z', 'ends_at': '2026-11-01T01:00:00Z'},
            headers={'Idempotency-Key': str(uuid4())})
        detail = wait_finished(client, path)
        conclusion = detail['attempts'][0]['conclusion']
        assert detail['status'] == '已完成'
        assert conclusion['grounding'] == 'structured-observations-v1'
        assert '未引用' in conclusion['causal_chain']
        assert 'proven root cause' not in conclusion['causal_chain']
        assert 'Restart' not in str(conclusion['recommendations'])


def test_source_cannot_claim_another_configured_component(database_url, tmp_path, serve_app):
    from fastapi import FastAPI
    from app.incident_scenarios import collect
    from datetime import datetime, timezone
    app = FastAPI()

    @app.get('/v1/logs')
    def logs(source_id: str):
        items = collect('order-correlation', 'sim-gateway', datetime(2026, 11, 1, tzinfo=timezone.utc),
                        datetime(2026, 11, 1, 1, tzinfo=timezone.utc))
        for item in items:
            item['source_id'] = source_id
        return {'protocol': 'simulated-logs-v1', 'items': items}

    credential = uuid4().hex
    with scenario_client(database_url, tmp_path, serve_app(app), credential) as client:
        path, _, endpoint, auth = started(client, credential)
        reply = client.post(endpoint.removesuffix('/evidence') + '/queries',
                            headers={**auth, 'Idempotency-Key': str(uuid4())})
        assert reply.status_code == 201
        assert [source['state'] for source in reply.json()['sources']].count('succeeded') == 1
        assert client.get(path).json()['evidence_count'] == 1


def test_scenario_switch_preserves_history_and_rejects_changed_identity(database_url, tmp_path, serve_app):
    from test_conclusions import proposal
    source = SimulatedLogSource(serve_app(create_mock_logs('order-correlation')))
    credential = uuid4().hex
    # The mapping is an external adapter seam; switching its endpoint models an operator restart.
    with scenario_client(database_url, tmp_path, source.base_url, credential) as client:
        path, _, endpoint, auth = started(client, credential)
        query = endpoint.removesuffix('/evidence') + '/queries'
        assert client.post(query, headers={**auth, 'Idempotency-Key': str(uuid4())}).status_code == 201
        before = client.get(path).json()
        assert client.post(endpoint.removesuffix('/evidence') + '/conclusions', json=proposal(),
                           headers={**auth, 'Idempotency-Key': str(uuid4())}).status_code == 201
        finished = client.get(path).json()
    changed_url = serve_app(create_mock_logs('configuration-missing'))
    with scenario_client(database_url, tmp_path, changed_url, credential) as client:
        assert client.get(path).json() == finished
        running = client.post(path + '/attempts', json={'service_id': 'orders',
            'starts_at': '2026-11-01T00:00:00Z', 'ends_at': '2026-11-01T01:00:00Z'},
            headers={'Idempotency-Key': str(uuid4())}).json()
        latest = running['attempts'][-1]
        base = '/api/executor' + path.removeprefix('/api') + '/attempts/' + latest['id'] + '/executions'
        execution_id = client.get(base, headers=auth).json()['executions'][0]['id']
        reply = client.post(base + '/' + execution_id + '/queries',
                            headers={**auth, 'Idempotency-Key': str(uuid4())})
        assert reply.status_code == 201
        current = client.get(path).json()
        assert current['attempts'][0] == finished['attempts'][0]
        assert current['evidence_count'] == 1
        assert any(state['reason'] == 'adapter_error' for state in reply.json()['sources'])
        assert current['attempts'][-1]['evidence'][0]['snippet'] == before['attempts'][0]['evidence'][0]['snippet']


@pytest.mark.skipif(os.environ.get('RUN_BAILIAN_INCIDENTS') != '1', reason='Paid scenario acceptance is opt-in')
@pytest.mark.parametrize('scenario,count', [('order-correlation', 2), ('redis-propagation', 3),
    ('redis-contradiction', 3), ('database-pool', 3), ('configuration-missing', 2),
    ('permission-denied', 2), ('order-empty', 0), ('order-partial', 2)])
def test_real_bailian_incident_scenario(database_url, tmp_path, serve_app, scenario, count):
    from app.core.config import AgentSettings
    from test_agent_runtime import wait_finished
    config = AgentSettings(_env_file=None, enabled=True, model='qwen-plus', region_confirmed=True,
        base_url='https://dashscope.aliyuncs.com/compatible-mode/v1', budget_microyuan=100_000,
        input_microyuan_per_million_tokens=800000, output_microyuan_per_million_tokens=2000000,
        price_version='beijing-public-nonthinking-2026-11-07', max_calls=3, max_output_tokens=2048)
    report_dir = Path('.scratch/simulated-incident-scenarios/live')
    report_dir.mkdir(exist_ok=True)
    used = 31_547 + sum((json.loads(path.read_text(encoding='utf-8'))['run']['spent_microyuan']
                         + json.loads(path.read_text(encoding='utf-8'))['run']['reserved_microyuan'])
                        for path in report_dir.glob('*.json'))
    assert used + config.budget_microyuan <= 10_000_000, 'Cumulative development budget exhausted'
    with scenario_client(database_url, tmp_path, serve_app(create_mock_logs(scenario)), uuid4().hex,
                         agent_settings=config) as client:
        from test_attempts import create
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
        report = {'scenario': scenario, 'source': 'simulated-http', 'model': 'real-qwen-plus',
                  'status': detail['status'], 'evidence': attempt['evidence'],
                  'coverage': attempt['query_coverage'], 'conclusion': attempt['conclusion'],
                  'run': attempt['agent_run']}
        (report_dir / (scenario + '-' + uuid4().hex[:8] + '.json')).write_text(json.dumps(report, ensure_ascii=True, indent=2), encoding='utf-8')
        assert detail['status'] == '已完成', attempt['agent_run']
        assert detail['evidence_count'] == count
        assert attempt['conclusion']['kind'] in ('hypothesis', 'undetermined')
        if count:
            assert attempt['conclusion']['evidence_ids']
        if scenario == 'redis-contradiction':
            assert '未解决矛盾' in attempt['conclusion']['contradictions']
        assert 'synthetic-secret' not in json.dumps(report)
