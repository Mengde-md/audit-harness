from sqlalchemy.ext.asyncio import AsyncSession

from app.models.audit_artifact import AuditArtifact


class ArtifactRepository:
    def __init__(self, session: AsyncSession):
        self.session = session

    async def create(self, artifact: AuditArtifact) -> AuditArtifact:
        self.session.add(artifact)
        await self.session.commit()
        await self.session.refresh(artifact)
        return artifact
