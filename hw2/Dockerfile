FROM openapitools/openapi-generator-cli:v7.25.0 AS codegen
WORKDIR /workspace
COPY openapi/ openapi/
RUN /usr/local/bin/docker-entrypoint.sh generate \
    -i openapi/marketplace.yaml -g python-fastapi \
    -c openapi/generator-config.yaml -t openapi/templates -o /generated \
    --global-property apiTests=false,modelTests=false,apiDocs=false,modelDocs=false

FROM python:3.12-slim AS runtime
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PYTHONPATH=/app/services/catalog:/app/.generated/src
WORKDIR /app
COPY services/catalog/requirements.txt services/catalog/requirements.txt
RUN pip install --no-cache-dir -r services/catalog/requirements.txt
COPY --from=codegen /generated/src .generated/src
COPY openapi/marketplace.yaml openapi/marketplace.yaml
COPY services/catalog/ services/catalog/

USER 10001:10001
EXPOSE 8001
HEALTHCHECK --interval=5s --timeout=3s --start-period=10s --retries=10 \
    CMD ["python", "-c", "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8001/health', timeout=2)"]
CMD ["python", "-m", "uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8001", "--no-access-log"]
