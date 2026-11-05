FROM python:3.12-slim
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt && useradd --create-home workplan
COPY . .
USER workplan
EXPOSE 8000
CMD ["sh", "-c", "python -m alembic upgrade head && python -m app.init_data && python -m app.portal_init && exec python -m uvicorn app.main:app --host 0.0.0.0 --port 8000 --workers 1 --no-proxy-headers"]
