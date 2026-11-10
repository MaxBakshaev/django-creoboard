# CreoBoard — инструкции для Claude Code

Мини-сервис управления креативным отделом: байеры ставят ТЗ на креативы, креативщики берут задачи и сдают результат, руководитель видит аналитику. Это тестовое задание со сроком до 24 часов. Приоритеты: рабочая демо-версия, тесты, безопасность, аккуратная история коммитов.

Подробности: @docs/ARCHITECTURE.md · план работ: @docs/DEVELOPMENT_PLAN.md · окружение: @docs/SETUP_WINDOWS.md

## Стек

- Backend: Django 5.2 LTS + Django REST Framework, PostgreSQL 16, gunicorn, whitenoise
- Frontend: React + Vite + TypeScript, Tailwind + shadcn/ui, TanStack Query, dnd-kit, Recharts
- Auth: сессии Django (cookie HttpOnly) + CSRF, пароли Argon2
- Зависимости: Poetry (источник), экспорт в `requirements*.txt`; команды: Makefile
- Деплой: Docker Compose на сервере Elestio (Ubuntu), HTTPS обеспечивает nginx Elestio
- CI/CD: GitHub Actions → образы в GHCR → деплой по SSH
- Разработка на Windows 10, оболочка PowerShell

## Команды

Все повседневные команды вынесены в `Makefile` в корне репозитория. Используй `make`, а не длинные команды. Виртуальное окружение (`.venv` в корне, создаётся `make venv`) активировать не нужно: Makefile сам ставит его первым в PATH. Без `.venv` используется Python из PATH (так работает CI).

```powershell
make db             # локальная БД (Docker)
make run            # сервер разработки Django
make migrate ; make mm
make test           # pytest (нужна БД)
make lint ; make fmt
make lt             # тесты + линт
make check          # manage.py check + проверка забытых миграций
make front ; make front-lint ; make front-test ; make front-build
make ci-back ; make ci-front   # проверки backend / frontend как в CI
make ci             # ci-back + ci-front + config, все проверки как в CI
make venv ; make install       # создать .venv (Python 3.12) / переустановить зависимости
make req            # после изменения зависимостей poetry
```

- Зависимости управляются через Poetry (`backend/pyproject.toml`). После изменения запусти `make req`: Docker и CI ставят пакеты из `requirements.txt` и `requirements-dev.txt`.
- Не запускай без явной просьбы человека: `make clean`, `make downv` (удаляют данные и образы), `make rebase` (force push).
- Новые повторяющиеся команды добавляй в `Makefile` в том же стиле: комментарий с эмодзи на русском над целью, `.SILENT:` сверху.
- В Windows PowerShell 5.1 нет оператора `&&`, команды разделяй через `;` или запускай по отдельности.

## Структура

```
backend/config/settings/{base,dev,prod}.py   настройки
backend/apps/{users,tasks,ai_usage,analytics}/
backend/apps/tasks/services.py               ВСЯ бизнес-логика смены статусов
frontend/src/                                SPA
docker/                                      docker-compose.yml (dev), docker-compose.prod.yml, nginx.conf
Makefile                                     команды разработки
docs/                                        документация
```

## Рабочий процесс (обязательно)

1. Перед кодом прочитай план шага в `docs/DEVELOPMENT_PLAN.md` и **сначала предложи план** (plan mode). Код пиши после подтверждения.
2. Один шаг плана = одна ветка `feat/<номер>-<slug>` = один PR. Прямых коммитов в `main` нет.
3. Коммиты по Conventional Commits: `feat(tasks): atomic take`, `fix(auth): ...`, `test(...)`, `docs(...)`, `ci(...)`, `chore(...)`. Коммит небольшой и по одной теме.
4. После коммитов: `git push -u origin <ветка>`, затем `gh pr create`. Перед push запусти `make ci` (пока нет `frontend/` и prod-compose, только `make ci-back`). Ход CI смотри через `gh pr checks --watch`. Если CI красный, исправляй, а не отключай проверки.
5. Merge делает человек, не ты.
6. Если шаг оказался больше плана, остановись и скажи об этом, не раздувай PR.

## Правила кода

- Python: типы там, где это улучшает читаемость; форматирование и линт через ruff; строки кода до 100 символов.
- Бизнес-логика живёт в `services.py`, views и serializers тонкие.
- Никаких `print`, используем `logging`; в логи не попадают пароли, токены, содержимое файлов.
- Новые зависимости добавляй только при необходимости и с закреплённой версией.
- Тексты интерфейса и README на русском, идентификаторы, комментарии и сообщения коммитов на английском.

## Доменные правила (не нарушать)

1. Статус задачи меняется **только** через функции в `apps/tasks/services.py`. В views, serializers и admin нет присваиваний `task.status = ...`.
2. Временны́е метки `created_at`, `taken_at`, `submitted_at`, `completed_at` ставит только сервер. Клиент их не передаёт.
3. Каждое значимое действие пишет `TaskEvent` в той же транзакции. `TaskEvent` неизменяем (нет update и delete).
4. «Взять задачу» атомарно: `UPDATE ... WHERE status='new' AND assignee IS NULL`. Если затронуто 0 строк, ответ 409.
5. Источник истины для доступных действий (`allowed_actions`) — backend. Frontend только отображает.
6. AI-кредиты хранятся как `Decimal`, не `float`.
7. Время хранится в UTC (`USE_TZ=True`).

## Безопасность (обязательно)

- Секреты только в переменных окружения. Не читай, не печатай и не коммить `.env`. В репозитории только `.env.example`.
- `DEBUG=False` в prod; `python manage.py check --deploy` должен проходить в CI.
- Не используй `csrf_exempt`, `mark_safe`, сырой SQL без параметров, `AllowAny` (кроме login, csrf и health).
- Каждый новый эндпоинт: проверка роли **и** владельца (object-level), queryset ограничен по роли. К нему пишутся тесты на 401, 403 и IDOR.
- Загрузки файлов: белый список расширений, проверка сигнатуры, лимит размера, случайные имена, выдача только через авторизованный эндпоинт с `Content-Disposition: attachment`.
- Не добавляй домены и ключи в код, не отключай проверку TLS, не используй `--no-verify`.

## Definition of Done для PR

- [ ] Тесты написаны и проходят локально и в CI
- [ ] `make ci` (до шага 10 `make ci-back`) проходит (ruff, `check`, миграции, тесты, frontend), `check --deploy` проходит в CI
- [ ] После изменения зависимостей выполнен `make req`
- [ ] Миграции включены в PR
- [ ] Права доступа покрыты негативными тестами
- [ ] Документация обновлена (README, ARCHITECTURE или AI_WORKFLOW при необходимости)
- [ ] В diff нет секретов и временных файлов

## Уроки (дополняй после каждой ошибки)

- Windows: в `DATABASE_URL` использовать `127.0.0.1`, а не `localhost` (`localhost` может сначала уйти на недоступный `::1` и подвесить подключение). Порт БД из Docker 5433, потому что 5432 занят локальным PostgreSQL.
- Python в PATH на машине разработчика может быть не 3.12 (там 3.10): окружение создавать через `make venv`, а не голым `python -m venv`.
