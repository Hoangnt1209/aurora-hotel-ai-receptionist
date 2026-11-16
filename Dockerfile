FROM python:3.11-slim

WORKDIR /app

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

COPY pyproject.toml alembic.ini ./
COPY src ./src
COPY alembic ./alembic

RUN pip install --no-cache-dir -e .

EXPOSE 8000

CMD ["sh", "-c", "alembic upgrade head && python -m aurora.db.seed && uvicorn aurora.main:app --host 0.0.0.0 --port 8000"]
