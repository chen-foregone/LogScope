"""Persistent execution summaries and conservative, shared monetary reservations."""
from uuid import uuid4

from sqlalchemy import (BigInteger, Boolean, Column, DateTime, ForeignKey, Integer,
                        JSON, String, Table, Uuid, insert, select, update)
from sqlalchemy.dialects.postgresql import insert as pg_insert

from app.investigations import metadata

budgets = Table('agent_budget', metadata, Column('id', Integer, primary_key=True),
    Column('spent', BigInteger, nullable=False), Column('reserved', BigInteger, nullable=False))
runs = Table('agent_runs', metadata, Column('id', Uuid, primary_key=True),
    Column('attempt_id', Uuid, ForeignKey('attempts.id'), unique=True, nullable=False),
    Column('execution_id', Uuid, ForeignKey('controlled_executions.id'), unique=True, nullable=False),
    Column('model', String(80)), Column('phase', String(40)), Column('reason', String(64)),
    Column('active', Boolean), Column('calls', Integer), Column('spent_microyuan', BigInteger),
    Column('reserved_microyuan', BigInteger), Column('created_at', DateTime(timezone=True)),
    Column('updated_at', DateTime(timezone=True)))
calls = Table('agent_calls', metadata, Column('id', Uuid, primary_key=True),
    Column('run_id', Uuid, ForeignKey('agent_runs.id'), nullable=False),
    Column('number', Integer), Column('status', String(32)), Column('reserved', BigInteger),
    Column('charged', BigInteger), Column('input_tokens', Integer), Column('output_tokens', Integer),
    Column('price_version', String(100)), Column('created_at', DateTime(timezone=True)))
checkpoints = Table('agent_checkpoints', metadata, Column('id', Uuid, primary_key=True),
    Column('run_id', Uuid, ForeignKey('agent_runs.id'), nullable=False),
    Column('step', Integer), Column('material', JSON), Column('created_at', DateTime(timezone=True)))


class BudgetUnavailable(Exception):
    pass


def reserve(connection, run, config, input_bound, now):
    amount = ((input_bound * config.input_microyuan_per_million_tokens +
               config.max_output_tokens * config.output_microyuan_per_million_tokens + 999_999) // 1_000_000)
    connection.execute(pg_insert(budgets).values(id=1, spent=0, reserved=0).on_conflict_do_nothing())
    budget = connection.execute(select(budgets).where(budgets.c.id == 1).with_for_update()).mappings().one()
    if budget['spent'] + budget['reserved'] + amount > config.budget_microyuan:
        raise BudgetUnavailable()
    number = run['calls'] + 1
    identity = uuid4()
    connection.execute(insert(calls).values(id=identity, run_id=run['id'], number=number,
        status='reserved', reserved=amount, price_version=config.price_version, created_at=now))
    connection.execute(update(budgets).where(budgets.c.id == 1).values(reserved=budgets.c.reserved + amount))
    connection.execute(update(runs).where(runs.c.id == run['id']).values(
        calls=number, reserved_microyuan=runs.c.reserved_microyuan + amount, updated_at=now))
    return identity


def settle(connection, call_id, usage, config):
    call = connection.execute(select(calls).where(calls.c.id == call_id).with_for_update()).mappings().one()
    if call['status'] != 'reserved':
        return
    # Missing or malformed usage stays reserved; transport success does not prove price.
    if (not isinstance(usage, dict) or any(type(usage.get(k)) is not int or usage[k] < 0
            for k in ('input_tokens', 'output_tokens'))):
        return
    charged = (usage['input_tokens'] * config.input_microyuan_per_million_tokens +
               usage['output_tokens'] * config.output_microyuan_per_million_tokens + 999_999) // 1_000_000
    connection.execute(select(budgets.c.id).where(budgets.c.id == 1).with_for_update()).one()
    connection.execute(update(calls).where(calls.c.id == call_id).values(status='settled',
        charged=charged, input_tokens=usage['input_tokens'], output_tokens=usage['output_tokens']))
    connection.execute(update(budgets).where(budgets.c.id == 1).values(
        reserved=budgets.c.reserved - call['reserved'], spent=budgets.c.spent + charged))
    connection.execute(update(runs).where(runs.c.id == call['run_id']).values(
        reserved_microyuan=runs.c.reserved_microyuan - call['reserved'],
        spent_microyuan=runs.c.spent_microyuan + charged))


def run_view(connection, attempt_id):
    row = connection.execute(select(runs).where(runs.c.attempt_id == attempt_id)).mappings().first()
    if row is None:
        return None
    return {key: row[key] for key in ('model', 'phase', 'reason', 'calls', 'spent_microyuan',
                                    'reserved_microyuan', 'created_at', 'updated_at')}
