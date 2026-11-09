from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.core.config import get_settings

settings = get_settings()
engine = create_async_engine(settings.database_url)
async_session_factory = async_sessionmaker(
    engine,
    class_=AsyncSession,
    expire_on_commit=False,
)


async def init_db() -> None:
    from app.db.base import Base
    from app.models import (  # noqa: F401
        audit_artifact,
        audit_job,
        audit_step,
        cross_recovery_candidate,
        cross_recovery_experience,
        project_recovery_memory,
    )

    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
