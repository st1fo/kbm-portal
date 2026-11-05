FROM python:3.12-slim
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
WORKDIR /app
RUN apt-get update && apt-get install -y --no-install-recommends fonts-dejavu-core tzdata && rm -rf /var/lib/apt/lists/* && useradd --system --no-create-home logbook
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY app ./app
COPY alembic.ini .
COPY alembic ./alembic
USER logbook
CMD ["sh", "-c", "alembic upgrade head && python -m app.portal_init && exec uvicorn app.main:app --host 0.0.0.0 --port 8000 --no-proxy-headers"]
