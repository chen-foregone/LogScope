"""Bounded single-agent graph. Business state, not checkpoints, authorizes execution."""
import asyncio
import json
import re
from typing import TypedDict
from uuid import uuid4

from fastapi import HTTPException, Response
from sqlalchemy import insert, select, update

from app.agent_storage import runs, checkpoints, reserve, settle, BudgetUnavailable
from app.attempts import attempts, executions
from app.investigations import investigations


class AgentExecutionAdapter:
    def __init__(self, config):
        self.config = config

    def accept(self, connection, attempt_id):
        from app.processes import current_process
        identity = current_process()
        if identity is None:
            raise ValueError('Agent requires verifiable Windows process identity')
        execution_id = uuid4()
        connection.execute(insert(executions).values(id=execution_id, attempt_id=attempt_id,
            valid=True, process_identity=identity, stop_protocol='agent_graph_v1'))
        # Pending work is owned by this API lifetime; never adopted after restart.
        connection.execute(insert(runs).values(id=uuid4(), attempt_id=attempt_id,
            execution_id=execution_id, model=self.config.model, phase='pending', active=True,
            calls=0, spent_microyuan=0, reserved_microyuan=0))


class Stopped(Exception):
    pass


def locked_attempt(connection, run, clock):
    parent_id = connection.execute(select(attempts.c.investigation_id).where(
        attempts.c.id == run['attempt_id'])).scalar_one()
    connection.execute(select(investigations.c.id).where(investigations.c.id == parent_id).with_for_update()).one()
    row = connection.execute(select(attempts).where(attempts.c.id == run['attempt_id']).with_for_update()).mappings().one()
    from app.deadlines import enforce_deadline
    enforce_deadline(connection, row, clock())
    valid = connection.execute(select(executions.c.valid).where(executions.c.id == run['execution_id'])).scalar_one()
    if (not valid or row['status'] != '调查中' or row['cancel_requested_at']
            or row['timeout_requested_at'] or row['lost_requested_at']):
        raise Stopped()
    return row


def evidence_context(connection, attempt_id):
    from app.evidence import included_evidence
    from app.verification import evidence_usable
    result = []
    for item in connection.execute(included_evidence(attempt_id)).mappings():
        if len(result) == 32:
            break
        if evidence_usable(connection, item):
            material = item['material']
            result.append({'id': str(item['id']), 'source': material['source'], 'source_mode': material.get('source_mode'),
                           'occurred_at': material['occurred_at'], 'snippet': material.get('snippet')})
    return result


class GraphState(TypedDict):
    messages: list
    steps: int
    done: bool


def case_context(connection, row):
    report = connection.execute(select(investigations.c.title, investigations.c.description).where(
        investigations.c.id == row['investigation_id'])).one()
    text = report.title + '\n' + report.description
    # Emit canonical hints only: arbitrary user text has no reliable redaction contract.
    signals = {'timeout': r'超时|\btimeout\b|\bETIMEDOUT\b',
               'request_failure': r'请求失败|\brequest failed\b',
               'latency': r'延迟|\blatency\b',
               'permission_denied': r'权限拒绝|权限不足|\bpermission denied\b',
               'resource_exhaustion': r'资源耗尽|内存不足|\bout of memory\b',
               'configuration': r'配置|\bconfiguration\b'}
    return {'scope': {'service_id': row['service_id'], 'starts_at': row['starts_at'].isoformat(),
                      'ends_at': row['ends_at'].isoformat()},
            'reported_symptom_signals': [key for key, pattern in signals.items() if re.search(pattern, text, re.I)],
            'context_boundary': 'Unverified report topic hints, possibly negated; not observations or causes. '
                                'Detailed free-text problem context is withheld for privacy.'}


async def run_agent(engine, run, config, model, sources, schedule, clock):
    from langchain_core.messages import SystemMessage, HumanMessage, ToolMessage
    from langgraph.graph import StateGraph, START, END
    from app.agent_model import AgentAnalysis, SYSTEM
    from app.conclusions import save_agent_analysis
    from app.queries import execute_query, wait_for_exit
    reason = 'model_error'

    async def controlled(operation):
        task = asyncio.create_task(operation)
        try:
            while True:
                done, _ = await asyncio.wait({task}, timeout=.1)
                with engine.begin() as connection:
                    locked_attempt(connection, run, clock)
                if done:
                    return task.result()
        finally:
            await wait_for_exit(task)

    async def analyze(state):
        nonlocal reason
        if state['steps'] >= config.max_calls:
            reason = 'call_limit'
            raise Stopped()
        encoded = json.dumps([m.model_dump(mode='json') for m in state['messages']], ensure_ascii=True)
        if len(encoded.encode()) > config.max_input_bytes:
            reason = 'input_limit'
            raise Stopped()
        with engine.begin() as connection:
            locked_attempt(connection, run, clock)
            current = connection.execute(select(runs).where(runs.c.id == run['id']).with_for_update()).mappings().one()
            # UTF-8 byte count plus framing overhead is a deliberately loose input-token bound.
            call_id = reserve(connection, current, config, len(encoded.encode()) + 4096, clock())
            connection.execute(update(runs).where(runs.c.id == run['id']).values(phase='analyzing'))
        reply = await controlled(asyncio.wait_for(model.respond(state['messages']), config.request_timeout_seconds))
        with engine.begin() as connection:
            # Financial reconciliation is allowed after fencing; it creates no business product.
            settle(connection, call_id, reply.usage_metadata, config)
            locked_attempt(connection, run, clock)
            connection.execute(insert(checkpoints).values(id=uuid4(), run_id=run['id'], step=state['steps'],
                material={'phase': 'model_returned', 'tool_requested': bool(reply.tool_calls)}, created_at=clock()))
        if reply.tool_calls:
            if len(reply.tool_calls) != 1 or reply.tool_calls[0]['name'] != 'query_logs' or reply.tool_calls[0]['args'] != {}:
                reason = 'tool_rejected'
                raise Stopped()
            tool = reply.tool_calls[0]
            if not isinstance(tool['id'], str) or not tool['id'] or len(tool['id']) > 200:
                raise ValueError('Invalid tool identity')
            # Discard all model free text accompanying a tool call.
            from langchain_core.messages import AIMessage
            safe_reply = AIMessage(content='', tool_calls=[tool])
            with engine.begin() as connection:
                row = locked_attempt(connection, run, clock)
                investigation_id = row['investigation_id']
                connection.execute(update(runs).where(runs.c.id == run['id']).values(phase='querying'))
            await execute_query(engine, sources, clock, schedule, investigation_id, run['attempt_id'],
                                run['execution_id'], Response(), uuid4())
            with engine.begin() as connection:
                row = locked_attempt(connection, run, clock)
                materials = evidence_context(connection, run['attempt_id'])
                from app.queries import query_views, query_coverage, coverage_aliases
                coverage = query_coverage(query_views(connection, run['attempt_id']),
                    coverage_aliases(connection, run['attempt_id'], row['source_ids']))
                aliases = coverage_aliases(connection, run['attempt_id'], row['source_ids'])
                registered = [{'source': alias, 'component': getattr(sources.get(source), 'component', None)}
                              for source, alias in zip(row['source_ids'], aliases)]
            return {'messages': state['messages'] + [safe_reply, ToolMessage(
                content=json.dumps({'evidence': materials, 'coverage': coverage, 'registered_sources': registered},
                                   ensure_ascii=True), tool_call_id=tool['id'])],
                'steps': state['steps'] + 1, 'done': False}
        if not isinstance(reply.content, str) or len(reply.content.encode()) > 32768:
            raise ValueError('Invalid model output')
        # No raw response is logged or checkpointed, even on validation failure.
        analysis = AgentAnalysis.model_validate_json(reply.content)
        if config.api_key and config.api_key.get_secret_value() in reply.content:
            raise ValueError('Secret in output')
        with engine.begin() as connection:
            row = locked_attempt(connection, run, clock)
            investigation_id = row['investigation_id']
        save_agent_analysis(engine, clock, investigation_id, run['attempt_id'], run['execution_id'], analysis)
        reason = 'normal_completion'
        return {'messages': [], 'steps': state['steps'] + 1, 'done': True}

    graph = StateGraph(GraphState)
    graph.add_node('analyze', analyze)
    graph.add_edge(START, 'analyze')
    graph.add_conditional_edges('analyze', lambda state: END if state['done'] else 'analyze')
    try:
        with engine.begin() as connection:
            row = locked_attempt(connection, run, clock)
            materials = evidence_context(connection, run['attempt_id'])
            context = case_context(connection, row)
        from langsmith import tracing_context
        with tracing_context(enabled=False):
            await graph.compile().ainvoke({'messages': [SystemMessage(content=SYSTEM), HumanMessage(
                content=json.dumps({**context, 'evidence': materials, 'source_mode': 'simulated',
                                    'task': 'Investigate the fixed scope; collect evidence if needed.'}))],
                'steps': 0, 'done': False}, config={'recursion_limit': config.max_calls + 2, 'callbacks': []})
    except BudgetUnavailable:
        reason = 'budget_exhausted'
    except (Stopped, HTTPException):
        pass
    except asyncio.CancelledError:
        reason = 'service_stopping'
        raise
    except Exception:
        pass
    finally:
        finish_run(engine, run, clock, reason)


def finish_run(engine, run, clock, reason):
    from app.queries import queries
    with engine.begin() as connection:
        investigation_id = connection.execute(select(attempts.c.investigation_id).where(attempts.c.id == run['attempt_id'])).scalar_one()
        connection.execute(select(investigations.c.id).where(investigations.c.id == investigation_id).with_for_update()).one()
        row = connection.execute(select(attempts).where(attempts.c.id == run['attempt_id']).with_for_update()).mappings().one()
        current = connection.execute(select(runs).where(runs.c.id == run['id'])).mappings().one()
        if row['status'] == '已完成' and current['phase'] == 'completed' and not current['active']:
            # save_agent_analysis already atomically finished this run and fenced writes.
            return
        if connection.execute(select(queries.c.id).where(queries.c.attempt_id == run['attempt_id'], queries.c.active.is_(True))).first():
            # Unknown query exit: retain the active run and slot for process-based recovery.
            return
        now = clock()
        stopped = row['cancel_requested_at'] or row['timeout_requested_at'] or row['lost_requested_at']
        phase = 'completed' if row['status'] == '已完成' else 'stopped' if stopped else 'failed'
        connection.execute(update(runs).where(runs.c.id == run['id']).values(active=False, phase=phase,
            reason=reason if not stopped else 'execution_fenced', updated_at=now))
        connection.execute(update(executions).where(executions.c.id == run['execution_id']).values(valid=False))
        if row['status'] == '调查中':
            connection.execute(update(attempts).where(attempts.c.id == run['attempt_id']).values(
                status='已取消' if row['cancel_requested_at'] else '失败', ended_at=now,
                end_reason='user_canceled' if row['cancel_requested_at'] else 'attempt_deadline_exceeded'
                if row['timeout_requested_at'] else 'service_interrupted' if row['lost_requested_at'] else reason,
                stop_confirmed_at=now, stop_confirmation='agent_exited_and_writes_fenced'))
        connection.execute(update(investigations).where(investigations.c.id == investigation_id).values(updated_at=now))


async def agent_coordinator(engine, config, model, sources, schedule, clock):
    from app.processes import current_process
    owner = current_process()
    tasks = set()
    try:
        while True:
            with engine.begin() as connection:
                candidates = connection.execute(select(runs, executions.c.process_identity).join(
                    executions, executions.c.id == runs.c.execution_id).where(
                    runs.c.phase == 'pending', runs.c.active.is_(True))
                    .with_for_update(of=runs, skip_locked=True)).mappings().all()
                pending = [run for run in candidates if owner is not None and run['process_identity'] == owner]
                for run in pending:
                    connection.execute(update(runs).where(runs.c.id == run['id']).values(phase='claimed',
                        created_at=clock(), updated_at=clock()))
            for run in pending:
                task = asyncio.create_task(run_agent(engine, run, config, model, sources, schedule, clock))
                tasks.add(task)
                task.add_done_callback(tasks.discard)
            await asyncio.sleep(.1)
    finally:
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
