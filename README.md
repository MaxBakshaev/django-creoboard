# 🎨 CreoBoard

Тестовое задание: **мини-сервис управления креативным отделом**. Байеры ставят ТЗ на креативы, креативщики берут задачи и сдают результат, руководитель видит аналитику по задачам, времени и расходам AI-кредитов.

> 🚧 Проект в разработке по плану из [docs/DEVELOPMENT_PLAN.md](docs/DEVELOPMENT_PLAN.md). Разделы с пометкой 🚧 заполняются по мере готовности шагов.

## 🌐 Демо

🚧 Ссылка: `https://<поддомен-сервиса>.vm.elestio.app` (будет добавлена после шага 2)

Тестовые пользователи (демо-режим, данные вымышленные):

| Роль | Логин | Пароль |
|---|---|---|
| Руководитель | `lead` | 🚧 |
| Байер | `buyer1`, `buyer2` | 🚧 |
| Креативщик | `creative1`, `creative2`, `creative3` | 🚧 |

> Пароль демо-пользователей задаётся переменной `DEMO_PASSWORD` на сервере. Демо-пользователи создаются только при `DEMO_MODE=1`.

## ⚙️ Функциональность

- 👥 **Роли:** байер, креативщик, руководитель
- 📝 **Постановка задач:** оффер, GEO, тип креатива, количество, описание, референсы, приоритет, дедлайн
- 🗂️ **Kanban:** Новые → В работе → На проверке → Правки → Готово (перетаскивание с проверкой прав на сервере)
- 🔄 **Работа с задачами:** креативщик сам берёт задачу и прикрепляет результат, байер принимает или отправляет на правки
- ⏱️ **Учёт времени:** автоматически фиксируются создание, взятие, сдача, завершение, считается длительность
- 🤖 **Учёт AI-расходов:** сервисы и кредиты по каждой задаче, включая правки
- 📊 **Аналитика для руководителя:** количество задач, загрузка сотрудников, среднее время, просрочки, расходы AI по сервисам и исполнителям
- 🕓 **История:** журнал всех действий и смен статусов по каждой задаче

## 🛠️ Используемые технологии

- 🐍 Django 5.2 LTS, Django REST Framework
- 🐘 PostgreSQL 16
- ⚛️ React, Vite, TypeScript, Tailwind, shadcn/ui, dnd-kit, Recharts
- 🐳 Docker, Docker Compose
- 🧪 pytest, Vitest, Playwright
- 🔁 GitHub Actions (CI, проверки безопасности, автодеплой), GHCR
- 🔒 Argon2, CSRF, Content-Security-Policy, gitleaks, pip-audit, Trivy, CodeQL

## 📚 Документация

- [Архитектура](docs/ARCHITECTURE.md) — модель данных, статусы, права, API, безопасность, деплой
- [План разработки](docs/DEVELOPMENT_PLAN.md)
- [Подготовка окружения (Windows 10)](docs/SETUP_WINDOWS.md)
- [Использование AI в разработке](docs/AI_WORKFLOW.md)
- 🚧 Swagger: `/api/docs/` (доступен руководителю)

## 🧑‍💻 Локальный запуск (Windows, PowerShell) 🚧

Повседневные команды вынесены в [Makefile](Makefile). Нужны Git, Python 3.12, Node.js LTS, Docker Desktop и `make` (установка: [docs/SETUP_WINDOWS.md](docs/SETUP_WINDOWS.md)).

🔹 1. Клонируйте репозиторий:

```powershell
git clone https://github.com/MaxBakshaev/django-creoboard.git
cd django-creoboard
```

🔹 2. Создайте файл окружения и заполните значения для разработки:

```powershell
Copy-Item .env.example .env
make secret        # значение для DJANGO_SECRET_KEY
```

🔹 3. Запустите базу данных:

```powershell
make db
```

🔹 4. Backend (виртуальное окружение в корне репозитория):

```powershell
make venv          # .venv на Python 3.12 + зависимости; активировать его не нужно
make migrate
make seed
make run
```

🔹 5. Frontend (в другом окне PowerShell):

```powershell
make front-install
make front
```

Приложение: http://localhost:5173 · API: http://localhost:8000/api/v1/

## 🧰 Команды Makefile

| Команда | Что делает |
|---|---|
| `make venv` / `make install` | создать `.venv` (Python 3.12) / переустановить зависимости |
| `make db` | поднять локальную БД (Docker, `127.0.0.1:5433`) |
| `make run` / `make front` | сервер Django / сервер Vite |
| `make migrate` / `make mm` | применить / создать миграции |
| `make seed` | демо-данные (при `DEMO_MODE=1`) |
| `make test` | тесты backend (нужна БД) |
| `make lint` / `make fmt` | проверка / форматирование кода (ruff) |
| `make lt` | тесты + линт |
| `make check` | `manage.py check` и проверка забытых миграций |
| `make front-lint` / `front-test` / `front-build` | проверки frontend |
| `make check-deploy` | `manage.py check --deploy` на prod-настройках |
| `make ci-back` / `make ci-front` | проверки backend / frontend как в CI |
| `make ci` | `ci-back` + `ci-front` + `config`, все проверки как в CI |
| `make req` | экспорт зависимостей poetry в `requirements*.txt` |
| `make secret` | сгенерировать `DJANGO_SECRET_KEY` |
| `make down` / `make downv` | остановить контейнеры / вместе с данными БД |
| `make sync` / `make rebase` | обновить `main` после merge / ребейз ветки на `main` |

## 🧪 Тесты

```powershell
make db
make test          # backend
make front-test    # frontend
make ci-back       # проверки backend как в CI
make ci            # всё сразу, как в CI (когда есть frontend)
```

## 🚀 Деплой

Автодеплой при push в `main` после зелёного CI: GitHub Actions собирает образы, публикует их в GHCR и по SSH обновляет `docker compose` на сервере. Подробности: [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md#13-деплой-на-elestio).

## 🔐 Безопасность

Краткий перечень мер и причины решений: [раздел 10 архитектуры](docs/ARCHITECTURE.md#10-безопасность). Секреты в репозиторий не попадают, есть только `.env.example`.

## 🤖 AI в разработке

Проект разработан с помощью Claude Code с несколькими специализированными агентами. Подробности: [docs/AI_WORKFLOW.md](docs/AI_WORKFLOW.md).

## ✅ Соответствие ТЗ

| Требование ТЗ | Статус |
|---|---|
| Роли: байер, креативщик, руководитель | 🚧 |
| Форма ТЗ (оффер, GEO, тип, количество, описание, референсы, приоритет, дедлайн) | 🚧 |
| Kanban-доска из 5 статусов | 🚧 |
| Взятие задачи, результат, приёмка или правки | 🚧 |
| Автоматический учёт времени и длительности | 🚧 |
| Учёт AI-расходов по задачам и правкам | 🚧 |
| Аналитика руководителя | 🚧 |
| История действий и смен статусов | 🚧 |
| Рабочая демо-версия с тестовыми данными | 🚧 |
