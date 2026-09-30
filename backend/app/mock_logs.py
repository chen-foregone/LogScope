"""Local synthetic read-only protocol. Scenario selection is startup-only."""
import asyncio
from datetime import datetime, timezone

from fastapi import FastAPI, HTTPException, Query, Request, Response


def create_mock_logs(scenario='normal'):
    allowed = {'normal', 'empty', 'unidentified', 'conflict', 'invalid', 'permission',
               'credentials', 'parameters', 'temporary', 'limited', 'timeout'}
    if scenario not in allowed:
        raise ValueError('Unknown mock scenario')
    app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)
    app.state.disconnected_requests = 0
    calls = 0

    @app.get('/v1/logs')
    async def logs(request: Request, source_id: str = Query(pattern=r'^[a-zA-Z0-9_-]{1,120}$'),
                   starts_at: datetime = Query(), ends_at: datetime = Query()):
        nonlocal calls
        if (starts_at.tzinfo is None or ends_at.tzinfo is None or
                not 0 < (ends_at - starts_at).total_seconds() <= 86400):
            raise HTTPException(422, 'Invalid range')
        calls += 1
        status = {'permission': 403, 'credentials': 401, 'parameters': 422, 'limited': 429}.get(scenario)
        if status:
            raise HTTPException(status, 'Synthetic source failure', headers={'Retry-After': '60'} if status == 429 else None)
        if scenario == 'temporary' and calls <= 2:
            raise HTTPException(503, 'Temporary source failure')
        if scenario == 'timeout':
            for _ in range(350):
                if await request.is_disconnected():
                    app.state.disconnected_requests += 1
                    return Response(status_code=499)
                await asyncio.sleep(.1)
        occurred = datetime(2026, 11, 1, 0, 15, tzinfo=timezone.utc)
        items = []
        if scenario != 'empty' and starts_at <= occurred < ends_at:
            for record_id in ('demo-1', 'demo-1', 'demo-2'):
                item = {'format': 'controlled-event-v1', 'source_id': source_id,
                    'occurred_at': occurred.isoformat(),
                    'query': {'starts_at': starts_at.isoformat(), 'ends_at': ends_at.isoformat(), 'fields': {}},
                    'record': {'event': 'request_failed', 'error_code': 'ETIMEDOUT',
                               'fields': {'token': 'synthetic-secret', 'email': 'synthetic@example.invalid'}},
                    'identity': {'kind': 'record', 'record_id': record_id}}
                if scenario == 'unidentified':
                    item.pop('identity')
                if scenario == 'conflict' and len(items) == 1:
                    item['record']['error_code'] = 'EACCES'
                if scenario == 'invalid' and len(items) == 1:
                    item['record']['message'] = 'synthetic-secret'
                items.append(item)
        return {'protocol': 'simulated-logs-v1', 'items': items}

    return app


def main():
    import argparse
    import uvicorn
    parser = argparse.ArgumentParser()
    parser.add_argument('--port', type=int, default=8091)
    parser.add_argument('--scenario', default='normal')
    args = parser.parse_args()
    uvicorn.run(create_mock_logs(args.scenario), host='127.0.0.1', port=args.port, access_log=False)


if __name__ == '__main__':
    main()
