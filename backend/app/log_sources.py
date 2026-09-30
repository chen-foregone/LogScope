"""Bounded loopback HTTP adapter; no endpoint or error body reaches material."""
import json
from dataclasses import dataclass
from urllib.parse import urlsplit

import httpx

from app.queries import SourceFailure


@dataclass(frozen=True)
class CollectedLogs:
    items: list


class SimulatedLogSource:
    mode = 'simulated'

    def __init__(self, base_url):
        parsed = urlsplit(base_url)
        if (parsed.scheme != 'http' or parsed.hostname != '127.0.0.1' or not parsed.port
                or parsed.username or parsed.password or parsed.path not in ('', '/')
                or parsed.query or parsed.fragment):
            raise ValueError('Mock source requires an explicit loopback HTTP origin')
        self.base_url = base_url.rstrip('/')

    async def query(self, *, source_id, starts_at, ends_at):
        try:
            async with httpx.AsyncClient(trust_env=False, follow_redirects=False, timeout=30) as client:
                async with client.stream('GET', self.base_url + '/v1/logs', params={
                        'source_id': source_id, 'starts_at': starts_at.isoformat(), 'ends_at': ends_at.isoformat()}) as reply:
                    if reply.status_code != 200:
                        reason = {401: 'credentials', 403: 'permission', 400: 'parameters', 422: 'parameters',
                                  429: 'rate_limited', 502: 'network', 503: 'network', 504: 'timeout'}.get(reply.status_code, 'adapter_error')
                        value = reply.headers.get('Retry-After', '')
                        retry = int(value) if value.isascii() and value.isdigit() and len(value) <= 8 else None
                        raise SourceFailure(reason, retry_after=retry)
                    body = bytearray()
                    async for chunk in reply.aiter_bytes():
                        body.extend(chunk)
                        if len(body) > 1048576:
                            raise SourceFailure('parameters')
            output = json.loads(body)
            if (not isinstance(output, dict) or set(output) != {'protocol', 'items'}
                    or output['protocol'] != 'simulated-logs-v1' or not isinstance(output['items'], list)
                    or len(output['items']) > 100):
                raise SourceFailure('parameters')
            return CollectedLogs(output['items'])
        except httpx.TimeoutException:
            raise SourceFailure('timeout') from None
        except httpx.RequestError:
            raise SourceFailure('network') from None
        except (ValueError, TypeError):
            raise SourceFailure('parameters') from None
