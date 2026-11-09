from sqlalchemy.ext.asyncio import AsyncSession

from app.models.audit_step import AuditStep


class StepRepository:
    def __init__(self, session: AsyncSession):
        self.session = session

    async def create(self, step: AuditStep) -> AuditStep:
        self.session.add(step)
        await self.session.commit()
        await self.session.refresh(step)
        return step

    async def save(self, step: AuditStep) -> AuditStep:
        self.session.add(step)
        await self.session.commit()
        return step

