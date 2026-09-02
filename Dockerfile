# Backend image. Lives at the repo root because Hugging Face Spaces builds from
# the root of the Space repo; everything it needs is under api/.
FROM python:3.11-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /srv

# opencv-python-headless still wants libgomp (scipy/sklearn threading)
RUN apt-get update && apt-get install -y --no-install-recommends libgomp1 \
    && rm -rf /var/lib/apt/lists/*

COPY api/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY api/engine ./engine
COPY api/app ./app

# Spaces runs as a non-root user and gives it /data; the local fallback store
# writes there when Supabase is not configured.
ENV LOCAL_DATA_DIR=/data
RUN mkdir -p /data && chmod 777 /data

# Cloud Run injects PORT; Spaces expects 7860. Both honour the variable.
ENV PORT=8080
EXPOSE 8080

# One worker process: the pipeline is CPU- and RAM-bound, so concurrency is
# controlled inside the app (MAX_WORKERS) rather than by forking uvicorn.
CMD exec uvicorn app.main:app --host 0.0.0.0 --port ${PORT} --workers 1
