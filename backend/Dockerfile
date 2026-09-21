ARG PYTHON_BASE_IMAGE=python:3.12-slim@sha256:78387bc3881b8273120a12ebe6c1ab22b018ccc2c9adf565ae1ac9b536e184ea

FROM ${PYTHON_BASE_IMAGE} AS builder

WORKDIR /build

RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip wheel --no-cache-dir --wheel-dir /wheels -r requirements.txt

FROM ${PYTHON_BASE_IMAGE}

WORKDIR /app

ARG DEBIAN_SECURITY_REFRESH=2026-09-14
RUN echo "Debian security refresh: ${DEBIAN_SECURITY_REFRESH}" \
    && apt-get update \
    && apt-get upgrade -y \
    && apt-get install -y --no-install-recommends curl libssh2-1t64 \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
COPY --from=builder /wheels /wheels
RUN pip install --no-cache-dir --no-index --find-links=/wheels -r requirements.txt \
    && rm -rf /wheels

COPY alembic.ini .
COPY alembic/ ./alembic/
COPY src/ ./src/

ENV PYTHONPATH=/app/src
ENV PYTHONUNBUFFERED=1

EXPOSE 8000

CMD ["uvicorn", "cyber_team.api:app", "--host", "0.0.0.0", "--port", "8000", "--log-level", "info"]
