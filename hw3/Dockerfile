FROM python:3.12-slim AS codegen
WORKDIR /build
COPY requirements-codegen.txt .
RUN pip install --no-cache-dir -r requirements-codegen.txt
COPY proto/ ./proto/
RUN mkdir /generated && python -m grpc_tools.protoc -Iproto \
    --python_out=/generated --grpc_python_out=/generated proto/flight.proto

FROM scratch AS generated
COPY --from=codegen /generated/ /

FROM python:3.12-slim AS runtime
ENV PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=/app:/app/generated
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY --from=codegen /generated/ ./generated/
COPY services/ ./
USER 10001
CMD ["uvicorn", "booking.app:app", "--host", "0.0.0.0", "--port", "8000", "--no-access-log"]
