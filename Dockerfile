# Lead Scanner — container image (used for local Docker dev; prod stays on Render).
FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

# Install deps first (cached layer) — requirements.txt rarely changes.
COPY requirements.txt .
RUN pip install -r requirements.txt

# App code (overlaid by a bind mount in docker-compose for hot-reload dev).
COPY . .

# Run as a non-root user.
RUN useradd -m appuser && chown -R appuser /app
USER appuser

EXPOSE 8000

# Prod-style default; docker-compose overrides with --reload for dev.
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000"]
