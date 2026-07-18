# Future EC2/ECS image — same app code as Lambda (uvicorn instead of Mangum).
FROM python:3.12-slim

WORKDIR /app

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY app ./app
COPY config ./config
COPY frontend ./frontend

EXPOSE 8000

# Local/ECS/EC2 entrypoint. Lambda uses the container image with
# CMD overridden to the Mangum handler (see deploy/README.md).
CMD ["uvicorn", "app.api.server:app", "--host", "0.0.0.0", "--port", "8000"]
