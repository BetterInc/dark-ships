FROM python:3.12-slim
WORKDIR /code
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY app ./app
# Deployed image SHA, surfaced at /api/health for the CI smoke test.
ARG APP_VERSION=dev
ENV APP_VERSION=$APP_VERSION
EXPOSE 8000
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
