# CausalTwin backend: Python 3.11 slim, non-root user, health check, models and results baked in.
# Build:  docker build -t causaltwin-backend backend
# Run:    docker run -p 8000:8000 causaltwin-backend      (PORT, CORS and other settings: see .env.example)
FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    NUMBA_CACHE_DIR=/tmp/numba-cache \
    PORT=8000 \
    CAUSALTWIN_DISCOVERY_JOBS=1

# libgomp1 is the OpenMP runtime that XGBoost needs.
RUN apt-get update \
    && apt-get install -y --no-install-recommends libgomp1 \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Dependencies first (cached layer). XGBoost comes as the CPU-only build: the same Python
# package, without the large GPU libraries that the default Linux wheel pulls in.
COPY requirements.txt .
RUN grep -v "^xgboost==" requirements.txt > /tmp/requirements-cpu.txt \
    && XGBOOST_VERSION="$(grep "^xgboost==" requirements.txt | cut -d= -f3)" \
    && pip install -r /tmp/requirements-cpu.txt "xgboost-cpu==${XGBOOST_VERSION}"

COPY causaltwin ./causaltwin
COPY scripts ./scripts
COPY data ./data

# Train and cache the models and check the cached results, so the container starts ready.
RUN python scripts/precompute.py

RUN useradd --create-home --uid 10001 causaltwin && chown -R causaltwin:causaltwin /app
USER causaltwin

# One thread per numeric library. Containers see every host CPU but often get a fraction of one
# (Render's free plan: 0.1 CPU); idle math threads then spin and burn the quota. Measured locally at
# 0.1 CPU: the demo's drift beat took over 6 minutes with the defaults and 20 s with these settings.
ENV OMP_NUM_THREADS=1 \
    OPENBLAS_NUM_THREADS=1 \
    MKL_NUM_THREADS=1 \
    NUMBA_NUM_THREADS=1

EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=6s --start-period=120s --retries=3 CMD ["python", "scripts/healthcheck.py"]
# Shell form so ${PORT} (set by hosts such as Render) is expanded; exec keeps uvicorn as PID 1.
CMD ["sh", "-c", "exec uvicorn causaltwin.api.app:app --host 0.0.0.0 --port ${PORT:-8000} --proxy-headers --forwarded-allow-ips='*'"]
