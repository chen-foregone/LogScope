from datetime import datetime, timedelta
from typing import Literal

from pydantic import BaseModel


class RetentionView(BaseModel):
    phase: Literal['running', 'retained', 'expiring', 'awaiting_cleanup', 'inconsistent']
    issue: str | None = None


def retention_view(row, attempts, now: datetime):
    # A running round always protects materials, including malformed legacy rows.
    if any(attempt['status'] == '调查中' for attempt in attempts):
        return RetentionView(phase='running')
    latest = attempts[-1] if attempts else None
    anchor = latest['ended_at'] if latest else row['created_at']
    if isinstance(anchor, str):
        anchor = datetime.fromisoformat(anchor.replace('Z', '+00:00'))
    expires = row['retention_expires_at']
    if anchor is None or expires is None or expires != anchor + timedelta(days=30):
        return RetentionView(phase='inconsistent', issue='保留期限与生命周期事实不一致，请核查；不能据此确认清理。')
    phase = ('awaiting_cleanup' if now >= expires else
             'expiring' if now >= expires - timedelta(days=7) else 'retained')
    return RetentionView(phase=phase)
