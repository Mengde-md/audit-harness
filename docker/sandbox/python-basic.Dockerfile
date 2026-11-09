ARG BASE_IMAGE=docker.io/library/python:3.11-slim
FROM ${BASE_IMAGE}

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PYTHONPATH=/opt/audit-harness \
    PIP_INDEX_URL=https://pypi.org/simple

RUN pip install --no-cache-dir \
    aiosqlite \
    alembic \
    fastapi \
    httpx \
    pydantic-settings \
    pytest \
    ruff \
    sqlalchemy \
    uvicorn

RUN useradd --create-home --uid 1000 audit

COPY app /opt/audit-harness/app

WORKDIR /workspace
USER 1000:1000
