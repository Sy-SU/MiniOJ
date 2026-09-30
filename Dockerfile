FROM python:3.12-slim

WORKDIR /app
COPY pyproject.toml README.md ./
COPY src ./src
COPY templates ./templates
COPY static ./static
RUN pip install --no-cache-dir --retries 10 --timeout 120 .
COPY docker ./docker
COPY data ./data
COPY database ./database

EXPOSE 8000
CMD ["uvicorn", "minioj.server.main:app", "--host", "0.0.0.0", "--port", "8000"]

