# Single-stage image: the scientific wheels are all manylinux, so nothing is
# compiled here and a builder stage would save no space worth the complexity.
FROM python:3.11-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /srv

# opencv-python-headless still wants libgomp (via scipy/sklearn threading)
RUN apt-get update && apt-get install -y --no-install-recommends libgomp1 \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY engine ./engine
COPY app ./app

# Cloud Run and Hugging Face Spaces both inject PORT; 8080 is the local default.
ENV PORT=8080
EXPOSE 8080

# One worker process: the pipeline is CPU- and RAM-bound, so concurrency is
# controlled inside the app (MAX_WORKERS) rather than by forking uvicorn.
CMD exec uvicorn app.main:app --host 0.0.0.0 --port ${PORT} --workers 1
