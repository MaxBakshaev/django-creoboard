.SILENT:
MAKEFLAGS += --no-print-directory

# Переменные с вызовом docker compose (Docker Desktop: "docker compose").
# --project-directory . нужен, чтобы .env читался из корня репозитория, а не из docker/
# Если у вас старый бинарь, запускайте так: make DC="docker-compose --project-directory . -f docker/docker-compose.yml" up
DC ?= docker compose --project-directory . -f docker/docker-compose.yml
DCP ?= docker compose --project-directory . -f docker/docker-compose.prod.yml --env-file .env.example

# 🐳 Команды с docker compose (локально поднимается только БД):

# Запуск docker compose без аргументов
dc:
	$(DC)

# Поднятие только базы данных в фоновом режиме
db:
	$(DC) up -d --wait db

# Запуск контейнеров
start:
	$(DC) start

# Поднятие с пересборкой в фоновом режиме и удалением образов <none>
restart:
	$(DC) down && $(DC) up --build -d && docker image prune -f

# Остановка контейнеров
stop:
	$(DC) stop

# Поднятие контейнеров в фоновом режиме
upnolog:
	$(DC) up -d

# Поднятие контейнеров с логами
up:
	$(DC) up

# Сборка образов
build:
	$(DC) build

# Поднятие с пересборкой в фоновом режиме
bupnolog:
	$(DC) up --build -d

# Поднятие с пересборкой и логами
bup:
	$(DC) up --build

# Удаление всех контейнеров, сетей, томов и образов
clean:
	$(DC) down --volumes --rmi all

# Остановка и удаление контейнеров
down:
	$(DC) down

# Остановка и удаление контейнеров и томов (БД будет очищена)
downv:
	$(DC) down -v

# Удаление образов <none>
none:
	docker image prune -f

# Просмотр логов в реальном времени
logs:
	$(DC) logs -f

# Просмотр запущенных контейнеров
ps:
	$(DC) ps

# 🔎 Проверка синтаксиса prod-compose (на значениях из .env.example)
config:
	$(DCP) config -q && echo "Compose: SUCCESS" || (echo "Compose: FAIL" && exit 1)

# 🐍 Django (необходимо активировать виртуальное окружение и поднять БД: make db):

# Запуск сервера разработки
run:
	cd backend && python manage.py runserver

# Применить миграции
migrate:
	cd backend && python manage.py migrate

# Создать миграции
mm:
	cd backend && python manage.py makemigrations

# Заполнить БД демо-данными (работает только при DEMO_MODE=1)
seed:
	cd backend && python manage.py seed_demo

# Создать суперпользователя
superuser:
	cd backend && python manage.py createsuperuser

# Консоль Django
shell:
	cd backend && python manage.py shell

# Проверка конфигурации Django и забытых миграций
check:
	cd backend && python manage.py check && python manage.py makemigrations --check --dry-run

# 🔑 Сгенерировать значение для DJANGO_SECRET_KEY
secret:
	python -c "import secrets; print(secrets.token_urlsafe(50))"

# 🐍 Проверка кода по ruff (линтер и формат; заменяет flake8)
lint:
	cd backend && ruff check . && ruff format --check . && echo "Lint: SUCCESS" || (echo "Lint: FAIL" && exit 1)

# ✨ Автоисправление и форматирование кода по ruff
fmt:
	cd backend && ruff check . --fix && ruff format .

# 🧪 Запуск тестов (нужна БД: make db)
test:
	cd backend && pytest --disable-warnings -v

# 🧪 ✚ 🐍 Запуск тестов и проверка кода по ruff
lt:
	make test && make lint

# ➤ 📄 Экспорт зависимостей poetry в requirements.txt и requirements-dev.txt
req:
	cd backend && poetry export --without-hashes -f requirements.txt -o requirements.txt && poetry export --without-hashes --with dev -f requirements.txt -o requirements-dev.txt

# ⚛️ Frontend:

# Установка зависимостей по lock-файлу
front-install:
	cd frontend && npm ci

# Запуск dev-сервера
front:
	cd frontend && npm run dev

# Проверка кода и типов
front-lint:
	cd frontend && npm run lint && npm run typecheck && echo "Front lint: SUCCESS" || (echo "Front lint: FAIL" && exit 1)

# Запуск тестов
front-test:
	cd frontend && npm test

# Сборка
front-build:
	cd frontend && npm run build

# ✅ Все проверки, как в CI (перед созданием PR)
ci:
	make check && make lt && make front-lint && make front-test && make front-build && make config

# 🔄 Перейти на main и подтянуть изменения (после merge PR)
sync:
	@git checkout main
	@git pull origin main
	@git fetch --prune

# 🔄 Ребейз текущей ветки на свежий main и пуш
rebase:
	@git fetch origin
	@git rebase origin/main
	@git push --force-with-lease
