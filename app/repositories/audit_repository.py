from __future__ import annotations

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.models.audit_job import AuditJob


class AuditRepository:
    def __init__(self, session: AsyncSession):
        self.session = session

    async def create(self, job: AuditJob) -> AuditJob:
        self.session.add(job)
        await self.session.commit()
        await self.session.refresh(job)
        return job

    async def get(self, audit_id: str) -> AuditJob | None:
        result = await self.session.execute(
            select(AuditJob)
            .where(AuditJob.id == audit_id)
            .execution_options(populate_existing=True)
            .options(
                selectinload(AuditJob.steps),
                selectinload(AuditJob.artifacts),
            )
        )
        return result.scalar_one_or_none()

    async def list(
        self,
        *,
        requester_id: str | None = None,
        status: str | None = None,
        limit: int = 20,
        offset: int = 0,
    ) -> list[AuditJob]:
        statement = (
            select(AuditJob)
            .execution_options(populate_existing=True)
            .options(selectinload(AuditJob.steps), selectinload(AuditJob.artifacts))
            .order_by(AuditJob.created_at.desc())
            .limit(limit)
            .offset(offset)
        )
        if requester_id:
            statement = statement.where(AuditJob.requester_id == requester_id)
        if status:
            statement = statement.where(AuditJob.status == status)
        result = await self.session.execute(statement)
        return list(result.scalars().all())

    async def count(
        self,
        *,
        requester_id: str | None = None,
        status: str | None = None,
    ) -> int:
        statement = select(func.count()).select_from(AuditJob)
        if requester_id:
            statement = statement.where(AuditJob.requester_id == requester_id)
        if status:
            statement = statement.where(AuditJob.status == status)
        result = await self.session.execute(statement)
        return int(result.scalar_one())

    async def list_by_statuses(self, statuses: list[str]) -> list[AuditJob]:
        result = await self.session.execute(
            select(AuditJob)
            .where(AuditJob.status.in_(statuses))
            .execution_options(populate_existing=True)
            .options(
                selectinload(AuditJob.steps),
                selectinload(AuditJob.artifacts),
            )
            .order_by(AuditJob.created_at.asc())
        )
        return list(result.scalars().all())

    async def save(self, job: AuditJob) -> AuditJob:
        self.session.add(job)
        await self.session.commit()
        return job
