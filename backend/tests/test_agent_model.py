import json

import httpx
import pytest

from app.agent_model import BailianModel
from test_agent_runtime import agent_config


def test_bailian_protocol_is_bounded_readonly_and_non_thinking():
    import asyncio
    from langchain_core.messages import HumanMessage
    observed = []

    async def handle(request):
        body = json.loads(request.content)
        observed.append(body)
        assert request.url.path == '/compatible-mode/v1/chat/completions'
        assert body['model'] == 'qwen-plus'
        assert body['enable_thinking'] is False
        assert body['max_completion_tokens'] == 2048
        assert [t['function']['name'] for t in body['tools']] == ['query_logs']
        return httpx.Response(200, json={'id': 'test', 'object': 'chat.completion', 'created': 1,
            'model': 'qwen-plus', 'choices': [{'index': 0, 'finish_reason': 'tool_calls', 'message': {
                'role': 'assistant', 'content': None, 'tool_calls': [{'id': 'tool-1', 'type': 'function',
                'function': {'name': 'query_logs', 'arguments': '{}'}}]}}],
            'usage': {'prompt_tokens': 10, 'completion_tokens': 2, 'total_tokens': 12}})

    async def run():
        model = BailianModel(agent_config())
        # The HTTP transport is the external system seam, not an internal model mock.
        model.client._transport = httpx.MockTransport(handle)
        model.model.async_client._client._transport = model.client._transport
        try:
            reply = await model.respond([HumanMessage(content='Synthetic test')])
            assert reply.tool_calls[0]['name'] == 'query_logs'
            assert reply.usage_metadata['input_tokens'] == 10
        finally:
            await model.close()
    asyncio.run(run())
    assert len(observed) == 1


@pytest.mark.parametrize('text', ['token=secret', 'https://internal.invalid', 'test@example.invalid', '13812345678'])
def test_sensitive_free_text_is_not_accepted(text):
    from app.agent_model import AgentAnalysis
    from pydantic import ValidationError
    with pytest.raises(ValidationError):
        AgentAnalysis.model_validate({'kind': 'hypothesis', 'category': 'unknown', 'phenomenon': text,
            'explanation': 'test', 'evidence_ids': [], 'alternatives': 'test', 'contradictions': 'test',
            'missing_evidence': ['test'], 'recommendations': []})
