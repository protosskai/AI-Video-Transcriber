# Reuse a verified runtime image (Python 3.12 + project dependencies).
# For a clean build: build the upstream Dockerfile as transcriber-runtime first.
ARG RUNTIME_IMAGE=transcriber-runtime
FROM ${RUNTIME_IMAGE}
WORKDIR /app
COPY backend /app/backend
COPY extensions /app/extensions
COPY static /app/static
COPY tests/task_center /app/tests/task_center
ENV PYTHONPATH=/app
HEALTHCHECK NONE
CMD ["python3", "-m", "uvicorn", "extensions.task_center.api:app", "--host", "0.0.0.0", "--port", "8000"]
