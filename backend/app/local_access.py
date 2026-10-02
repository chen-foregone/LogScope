"""Archive/cleanup is local-only until a user authorization model is integrated."""
from urllib.parse import urlsplit

from fastapi import HTTPException, Request


def require_local_operation(request: Request):
    local_hosts = {'127.0.0.1', '::1', 'localhost', 'testclient'}
    if request.client is None or request.client.host not in local_hosts:
        raise HTTPException(403, '归档和清除仅允许受保护的本地访问')
    origin = request.headers.get('origin')
    if origin:
        try:
            origin_url = urlsplit(origin)
            valid = origin_url.scheme in {'http', 'https'} and origin_url.hostname in local_hosts
        except ValueError:
            valid = False
        if not valid:
            raise HTTPException(403, '拒绝非本地网页发起的归档或清除')
    if request.headers.get('sec-fetch-site') == 'cross-site':
        raise HTTPException(403, '拒绝跨站归档或清除请求')
