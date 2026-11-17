FROM python:3.12.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY cinema/ cinema/
COPY schemas/ schemas/
COPY db/ db/
COPY tests/ tests/

USER 65534:65534
