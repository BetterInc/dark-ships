FROM python:3.12-slim
WORKDIR /code
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY app ./app
# The SAR ship-detection ONNX model is NOT baked into the image: the backend
# fetches it from the S3 bucket (MinIO locally, Wasabi in prod) at first use and
# caches it on disk - see services/shipdetect.py. Keeps the image slim and
# the model swappable without a rebuild.
# Deployed image SHA, surfaced at /api/health for the CI smoke test.
ARG APP_VERSION=dev
ENV APP_VERSION=$APP_VERSION
EXPOSE 8000
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
