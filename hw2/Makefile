.PHONY: up down generate logs sql

up:
	docker compose up --build -d --wait

down:
	docker compose down

generate:
	./scripts/generate.sh

logs:
	docker compose logs -f catalog

sql:
	docker compose exec postgres psql -U marketplace -d marketplace
