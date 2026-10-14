"""Opt-in paid integration. Ordinary test runs never contact Bailian."""
import json
import os
import time
from pathlib import Path
from uuid import uuid4

import pytest

from test_investigations import database_url
from test_attempts import configured_client, create
from test_log_sources import serve_app


@pytest.mark.skipif(os.environ.get('RUN_BAILIAN_LIVE') != '1', reason='Paid integration requires explicit opt-in')
@pytest.mark.parametrize('scenario', ['normal', 'empty', 'causal', 'contradictory'])
def test_real_bailian_with_simulated_http_logs(database_url, tmp_path, serve_app, scenario):
    from app.core.config import AgentSettings
    from app.log_sources import SimulatedLogSource
    from app.mock_logs import create_mock_logs

    config = AgentSettings(_env_file=None, enabled=True, region_confirmed=True,
        base_url='https://dashscope.aliyuncs.com/compatible-mode/v1',
        input_microyuan_per_million_tokens=800000, output_microyuan_per_million_tokens=2000000,
        price_version='bailian-beijing-public-2026-11-06', max_calls=3,
        max_output_tokens=2048, request_timeout_seconds=30)
    if scenario in ('causal', 'contradictory'):
        from fastapi import FastAPI
        from test_evidence import material
        source_app = FastAPI()

        @source_app.get('/v1/logs')
        def logs():
            payload = material()
            payload['identity'] = {'kind': 'record', 'record_id': 'trace-example'}
            payload['record']['causal_trace'] = {
                'format': 'observed-propagation-v1', 'dependency_error': 'ETIMEDOUT',
                'propagated_error': 'ETIMEDOUT', 'request_outcome': 'failed_by_dependency',
                'local_rejection': 'excluded_by_trace', 'independent_failure': 'excluded_by_trace',
                'contradiction': 'none_observed' if scenario == 'causal' else 'unresolved'}
            return {'protocol': 'simulated-logs-v1', 'items': [payload]}
    else:
        source_app = create_mock_logs(scenario)
    source = SimulatedLogSource(serve_app(source_app))
    with configured_client(database_url, tmp_path, agent_settings=config,
                           query_sources={'controlled-orders': source}) as client:
        record, _ = create(client)
        path = '/api/investigations/' + record['id']
        started = client.post(path + '/attempts', json={'service_id': 'orders',
            'starts_at': '2026-11-01T00:00:00Z', 'ends_at': '2026-11-01T01:00:00Z'},
            headers={'Idempotency-Key': str(uuid4())})
        assert started.status_code == 201
        deadline = time.monotonic() + 110
        while time.monotonic() < deadline:
            detail = client.get(path).json()
            if detail['status'] != '调查中':
                break
            time.sleep(.25)
        attempt = detail['attempts'][-1]
        summary = {'source': 'simulated-http', 'model': 'real-qwen-plus', 'scenario': scenario,
                   'status': detail['status'], 'evidence_count': detail['evidence_count'],
                   'evidence': attempt['evidence'],
                   'run': attempt.get('agent_run'), 'conclusion': attempt.get('conclusion')}
        if os.environ.get('AGENT_LIVE_REPORT'):
            report = Path(os.environ['AGENT_LIVE_REPORT'])
            if scenario != 'normal':
                report = report.with_name(report.stem + '-' + scenario + report.suffix)
            report.write_text(json.dumps(summary, ensure_ascii=True, indent=2), encoding='utf-8')
        assert detail['status'] == '已完成', attempt.get('agent_run')
        assert detail['evidence_count'] == {'normal': 2, 'empty': 0, 'causal': 1, 'contradictory': 1}[scenario]
        assert attempt['conclusion']['kind'] in ('hypothesis', 'undetermined')
        if scenario != 'empty':
            assert attempt['conclusion']['evidence_ids']
        if scenario == 'contradictory':
            assert '未解决矛盾' in attempt['conclusion']['contradictions']
        assert 'synthetic-secret' not in json.dumps(detail)
        assert attempt['agent_run']['spent_microyuan'] + attempt['agent_run']['reserved_microyuan'] < 1_000_000
