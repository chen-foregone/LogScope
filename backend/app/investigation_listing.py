from typing import Literal

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, field_validator, model_validator


class InvestigationFilterQuery(BaseModel):
    model_config = ConfigDict(extra='forbid', str_strip_whitespace=True)

    q: str = Field(default='', max_length=200)
    service_id: str | None = Field(default=None, pattern=r'^[a-zA-Z0-9_-]{1,80}$')
    relative_range: Literal['1h', '24h', '7d'] | None = None
    updated_from: AwareDatetime | None = None
    updated_before: AwareDatetime | None = None

    @field_validator('q')
    @classmethod
    def valid_query(cls, value: str) -> str:
        if any(char == '\x00' or 0xD800 <= ord(char) <= 0xDFFF for char in value):
            raise ValueError('Search contains characters that cannot be stored')
        return value

    @field_validator('updated_from', 'updated_before', mode='before')
    @classmethod
    def absolute_time(cls, value):
        if value is not None and (not isinstance(value, str) or 'T' not in value):
            raise ValueError('Use an absolute timestamp with a timezone')
        return value

    @model_validator(mode='after')
    def valid_range(self):
        if (self.updated_from is None) != (self.updated_before is None):
            raise ValueError('Both updated time boundaries are required')
        if self.relative_range is not None and self.updated_from is not None:
            raise ValueError('Relative and absolute ranges cannot be combined')
        if self.updated_from is not None and self.updated_before <= self.updated_from:
            raise ValueError('The end boundary must be after the start boundary')
        return self


class InvestigationPageQuery(InvestigationFilterQuery):
    page: int = Field(default=1, ge=1)
    page_size: int = Field(default=20, ge=1, le=100)


class InvestigationOverview(BaseModel):
    investigation_count: int
    active_count: int
    pending_count: int
    evidence_count: int


class ResolvedInvestigationFilter(BaseModel):
    q: str
    service_id: str | None
    updated_from: AwareDatetime | None
    updated_before: AwareDatetime | None
    resolved_at: AwareDatetime


class InvestigationSummary(BaseModel):
    id: str
    title: str
    service: str | None
    status: Literal['待处理', '调查中', '已完成', '失败', '已取消']
    severity: Literal['未评估', '高', '中', '低']
    evidence_count: int
    possible_duplicate_evidence: bool
    created_at: AwareDatetime
    updated_at: AwareDatetime


class InvestigationOverviewResponse(BaseModel):
    overview: InvestigationOverview
    filter: ResolvedInvestigationFilter


class InvestigationPage(InvestigationOverviewResponse):
    items: list[InvestigationSummary]
    total: int
    page: int
    page_size: int


def read_investigation_listing(engine, filters, clock, *, overview_only=False):
    from datetime import timedelta, timezone
    from sqlalchemy import select, func, cast, String, or_, exists
    from app.investigations import investigations
    from app.attempts import attempts
    from app.evidence import attempt_evidence, evidence

    now = clock().astimezone(timezone.utc)
    lower, upper = filters.updated_from, filters.updated_before
    if filters.relative_range:
        upper = now
        lower = now - {'1h': timedelta(hours=1), '24h': timedelta(days=1),
                       '7d': timedelta(days=7)}[filters.relative_range]
    snapshot = dict(q=filters.q, service_id=filters.service_id, updated_from=lower,
                    updated_before=upper, resolved_at=now)
    latest_id = (select(attempts.c.id).where(attempts.c.investigation_id == investigations.c.id)
                 .order_by(attempts.c.attempt_number.desc()).limit(1).correlate(investigations).scalar_subquery())
    joined = investigations.outerjoin(attempts, attempts.c.id == latest_id)
    statement = select(investigations.c.id, investigations.c.title, investigations.c.severity,
                       investigations.c.created_at, investigations.c.updated_at,
                       attempts.c.id.label('attempt_id'), attempts.c.service_id.label('service'),
                       func.coalesce(attempts.c.status, '待处理').label('status')).select_from(joined)
    if filters.q:
        statement = statement.where(or_(investigations.c.title.icontains(filters.q, autoescape=True),
            cast(investigations.c.id, String).icontains(filters.q, autoescape=True),
            attempts.c.service_id.icontains(filters.q, autoescape=True)))
    if filters.service_id is not None:
        statement = statement.where(attempts.c.service_id == filters.service_id)
    if lower is not None:
        statement = statement.where(investigations.c.updated_at >= lower, investigations.c.updated_at < upper)
    matched = statement.cte('matched_investigations')
    evidence_count = (select(func.count()).select_from(attempt_evidence)
                      .where(attempt_evidence.c.attempt_id == matched.c.attempt_id)
                      .correlate(matched).scalar_subquery())
    counts = select(func.count().label('investigation_count'),
        func.count().filter(matched.c.status == '调查中').label('active_count'),
        func.count().filter(matched.c.status == '待处理').label('pending_count'),
        func.coalesce(func.sum(evidence_count), 0).label('evidence_count')).select_from(matched)
    with engine.connect().execution_options(isolation_level='REPEATABLE READ') as connection:
        overview = dict(connection.execute(counts).mappings().one())
        result = dict(overview=overview, filter=snapshot)
        if not overview_only:
            duplicate = exists(select(1).select_from(attempt_evidence.join(evidence,
                evidence.c.id == attempt_evidence.c.evidence_id)).where(
                    attempt_evidence.c.attempt_id == matched.c.attempt_id,
                    evidence.c.identity_status != 'reliable'))
            page_query = (select(matched, evidence_count.label('evidence_count'),
                                 duplicate.label('possible_duplicate_evidence'))
                          .order_by(matched.c.updated_at.desc(), matched.c.id)
                          .limit(filters.page_size).offset((filters.page - 1) * filters.page_size))
            items = [dict(row) for row in connection.execute(page_query).mappings()]
            for item in items:
                item['id'] = str(item['id'])
            result.update(items=items, total=overview['investigation_count'],
                          page=filters.page, page_size=filters.page_size)
    return result
