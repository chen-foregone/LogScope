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
def test_real_bailian_with_simulated_http_logs(database_url, tmp_path, serve_app):
    from app.core.config import AgentSettings
    from app.log_sources import SimulatedLogSource
    from app.mock_logs import create_mock_logs

    config = AgentSettings(_env_file=None, enabled=True, region_confirmed=True,
        base_url='https://dashscope.aliyuncs.com/compatible-mode/v1',
        input_microyuan_per_million_tokens=800000, output_microyuan_per_million_tokens=2000000,
        price_version='bailian-beijing-public-2026-11-06', max_calls=3,
        max_output_tokens=2048, request_timeout_seconds=30)
    source = SimulatedLogSource(serve_app(create_mock_logs()))
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
        summary = {'source': 'simulated-http', 'model': 'real-qwen-plus',
                   'status': detail['status'], 'evidence_count': detail['evidence_count'],
                   'run': attempt.get('agent_run'), 'conclusion': attempt.get('conclusion')}
        if os.environ.get('AGENT_LIVE_REPORT'):
            Path(os.environ['AGENT_LIVE_REPORT']).write_text(json.dumps(summary, ensure_ascii=True, indent=2), encoding='utf-8')
        assert detail['status'] == '已完成', attempt.get('agent_run')
        assert detail['evidence_count'] == 2
        assert attempt['conclusion']['kind'] in ('hypothesis', 'undetermined')
        assert attempt['conclusion']['evidence_ids']
        assert 'synthetic-secret' not in json.dumps(detail)
        assert attempt['agent_run']['spent_microyuan'] + attempt['agent_run']['reserved_microyuan'] < 1_000_000
