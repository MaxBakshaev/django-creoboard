.SILENT:
MAKEFLAGS += --no-print-directory

# Переменные с вызовом docker compose (Docker Desktop: "docker compose").
# --project-directory . нужен, чтобы .env читался из корня репозитория, а не из docker/
# Если у вас старый бинарь, запускайте так: make DC="docker-compose --project-directory . -f docker/docker-compose.yml" up
DC ?= docker compose --project-directory . -f docker/docker-compose.yml
DCP ?= docker compose --project-directory . -f docker/docker-compose.prod.yml --env-file .env.example

# 🐍 Python-окружение находится автоматически, активировать .venv вручную не нужно.
# Если в корне есть .venv, его каталог со скриптами ставится первым в PATH, и обычные
# python / ruff / pytest берутся оттуда. Нет .venv (CI, уже активированное окружение):
# используется то, что лежит в PATH. Создать окружение: make venv
VENV ?= $(CURDIR)/.venv
ifeq ($(OS),Windows_NT)
VENV_BIN := $(VENV)/Scripts
PATHSEP := ;
PY_BOOT ?= py -3.12
else
VENV_BIN := $(VENV)/bin
PATHSEP := :
PY_BOOT ?= python3.12
endif
ifneq ($(wildcard $(VENV_BIN)),)
ifeq ($(OS),Windows_NT)
export PATH := $(subst /,\,$(VENV_BIN))$(PATHSEP)$(PATH)
else
export PATH := $(VENV_BIN)$(PATHSEP)$(PATH)
endif
export VIRTUAL_ENV := $(VENV)
endif

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

# 🐍 Создать .venv в корне (Python 3.12) и поставить зависимости разработки
venv:
	$(PY_BOOT) -m venv "$(VENV)" && "$(VENV_BIN)/python" -m pip install -r backend/requirements-dev.txt

# 📦 Переустановить зависимости в существующий .venv (после make req или pull)
install:
	"$(VENV_BIN)/python" -m pip install -r backend/requirements-dev.txt

# 🐍 Django (нужны .venv: make venv, и БД: make db):

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

# 🛡️ Проверка настроек для продакшена (manage.py check --deploy) на prod-настройках.
# Значения переменных нужны только для проверки и не зависят от вашего .env
check-deploy: export DJANGO_SETTINGS_MODULE = config.settings.prod
check-deploy: export DJANGO_DEBUG = 0
check-deploy: export DJANGO_SECRET_KEY = $(shell python -c "import secrets; print(secrets.token_urlsafe(50))")
check-deploy: export DJANGO_ALLOWED_HOSTS = example.com
check-deploy: export DATABASE_URL = postgres://check:check@localhost:5432/check
check-deploy:
	cd backend && python manage.py check --deploy --fail-level WARNING && echo "Check deploy: SUCCESS" || (echo "Check deploy: FAIL" && exit 1)

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
	cd backend && poetry export --without-hashes -f requirements.txt -o requirements.txt && poetry export --without-hashes --with dev -f requirements.txt -o requirements-dev.txt && python -c "import pathlib; [p.write_bytes(p.read_bytes().replace(b'\r\n', b'\n')) for p in map(pathlib.Path, ('requirements.txt', 'requirements-dev.txt'))]"

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

# ✅ Проверки backend, как в CI (на шагах до появления frontend/ запускать перед PR именно её)
ci-back:
	make check && make check-deploy && make lt

# ✅ Проверки frontend, как в CI (с шага 10)
ci-front:
	make front-lint && make front-test && make front-build

# ✅ Все проверки, как в CI (перед созданием PR, когда есть и frontend, и prod-compose)
ci:
	make ci-back && make ci-front && make config

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
