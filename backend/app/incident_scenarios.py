"""Deterministic synthetic observations. Operator answers are kept in documentation."""
from datetime import datetime, timezone

SOURCES = ('sim-gateway', 'sim-orders', 'sim-database', 'sim-redis')
SCENARIOS = ('order-correlation', 'redis-timeout', 'redis-propagation', 'redis-contradiction',
             'database-pool', 'configuration-missing', 'permission-denied')
SCENARIOS += ('order-empty', 'order-partial', 'order-unavailable')
OCCURRED = datetime(2026, 11, 1, 0, 15, tzinfo=timezone.utc)


def collect(scenario, source_id, starts_at, ends_at):
    if source_id not in SOURCES:
        raise ValueError('Unknown synthetic source')
    if scenario == 'order-empty':
        return []
    if not starts_at <= OCCURRED < ends_at:
        return []
    observations = {
        'sim-gateway': ('request_failed', 'NONE', {'component': 'gateway', 'signal': 'http_response', 'http_status': 504}),
        'sim-orders': ('request_failed', 'ETIMEDOUT', {'component': 'application', 'signal': 'request_timeout'}),
    }
    if scenario.startswith('redis-'):
        observations['sim-redis'] = ('dependency_failed', 'ETIMEDOUT',
                                    {'component': 'redis', 'signal': 'dependency_timeout'})
    if scenario == 'database-pool':
        observations['sim-database'] = ('dependency_failed', 'NONE',
            {'component': 'database', 'signal': 'pool_capacity', 'used': 100, 'capacity': 100, 'unit': 'connections'})
    if scenario == 'configuration-missing':
        observations['sim-orders'] = ('request_failed', 'NONE',
                                     {'component': 'application', 'signal': 'configuration_missing'})
    if scenario == 'permission-denied':
        observations['sim-orders'] = ('request_failed', 'EACCES',
                                     {'component': 'application', 'signal': 'permission_denied'})
    if source_id not in observations:
        return []
    event, error, observation = observations[source_id]
    item = {'format': 'controlled-event-v2', 'source_id': source_id, 'occurred_at': OCCURRED.isoformat(),
            'query': {'starts_at': starts_at.isoformat(), 'ends_at': ends_at.isoformat(), 'fields': {}},
            'record': {'event': event, 'error_code': error, 'observation': observation,
                       'fields': {'correlation_id': 'synthetic-request-1', 'email': 'synthetic@example.invalid',
                                  'token': 'synthetic-secret'}},
            'identity': {'kind': 'record', 'record_id': 'record-1'}}
    if source_id == 'sim-orders' and scenario in ('redis-propagation', 'redis-contradiction'):
        item['record']['causal_trace'] = {
            'format': 'observed-propagation-v1', 'dependency_error': 'ETIMEDOUT',
            'propagated_error': 'ETIMEDOUT', 'request_outcome': 'failed_by_dependency',
            'local_rejection': 'excluded_by_trace', 'independent_failure': 'excluded_by_trace',
            'contradiction': 'unresolved' if scenario == 'redis-contradiction' else 'none_observed'}
    return [item, item]
