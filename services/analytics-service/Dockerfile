# Build từ THƯ MỤC GỐC repo:  docker build -f services/analytics-service/Dockerfile -t analytics-service .
FROM python:3.12-slim
WORKDIR /app
COPY services/analytics-service/requirements.txt /app/requirements.txt
RUN pip install --no-cache-dir -r /app/requirements.txt
COPY packages/contracts/python /app/packages/contracts/python
COPY packages/observability/src /app/packages/observability/src
COPY services/analytics-service/src /app/src
COPY services/analytics-service/migrations /app/migrations
RUN useradd --system --uid 10001 --no-create-home app && chown -R app /app
USER app
ENV PYTHONPATH=/app/src:/app/packages/observability/src:/app/packages/contracts/python
EXPOSE 8432
HEALTHCHECK --interval=15s --timeout=3s --retries=3 CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8432/health/live').status==200 else 1)"
CMD ["uvicorn", "analytics_service.main:app", "--host", "0.0.0.0", "--port", "8432"]
