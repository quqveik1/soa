# Marketplace: ДЗ 2

Нужны Docker и Docker Compose. Команды выполняются из папки `soa/hw2`.

```bash
cp .env.example .env
docker compose up --build -d --wait
curl -i http://localhost:8000/health
```

Health-check должен вернуть `200 OK` и `{"status":"ok"}`.
Swagger: http://localhost:8000/docs.

Для Colima перед запуском выполни `export DOCKER_CONTEXT=colima`.

Остановить сервисы:

```bash
docker compose down
```

Данные в базе сохраняются после остановки.
