from datetime import datetime, timedelta
from typing import Literal

from pydantic import BaseModel


class RetentionView(BaseModel):
    phase: Literal['running', 'retained', 'archived', 'expiring', 'awaiting_cleanup', 'inconsistent']
    issue: str | None = None


def retention_view(row, attempts, now: datetime):
    # A running round always protects materials, including malformed legacy rows.
    if any(attempt['status'] == '调查中' for attempt in attempts):
        return RetentionView(phase='running')
    anchor = row['archived_at']
    expires = row['retention_expires_at']
    if anchor is None:
        if expires is not None:
            return RetentionView(phase='inconsistent', issue='未归档调查不应有自动清除期限，请核查。')
        return RetentionView(phase='retained')
    if isinstance(anchor, str):
        anchor = datetime.fromisoformat(anchor.replace('Z', '+00:00'))
    if anchor is None or expires is None or expires != anchor + timedelta(days=30):
        return RetentionView(phase='inconsistent', issue='清除期限与归档时间不一致，请核查；不能据此清除。')
    phase = ('awaiting_cleanup' if now >= expires else
             'expiring' if now >= expires - timedelta(days=7) else 'archived')
    return RetentionView(phase=phase)
