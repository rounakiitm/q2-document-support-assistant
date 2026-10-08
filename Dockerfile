FROM python:3.12-slim

WORKDIR /app

# System deps used by PDF/DOCX loaders
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY backend ./backend
COPY frontend ./frontend
COPY ui ./ui
COPY data/seed_docs ./data/seed_docs
COPY scripts ./scripts

# Cloud Run injects PORT (default 8080). Ephemeral /app/data is fine for demo:
# seed docs are loaded through the same upload path on first boot.
ENV PORT=8080 \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1

EXPOSE 8080

CMD ["sh", "-c", "uvicorn backend.main:app --host 0.0.0.0 --port ${PORT:-8080} --timeout-keep-alive 3600"]
