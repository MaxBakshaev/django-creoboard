# CreoBoard — архитектура

Версия: 1.0 (проектный документ до начала разработки). При изменении решений документ обновляется в том же PR.

## 1. Цель и границы

Веб-сервис для постановки, отслеживания и контроля задач между медиабайерами и креативщиками (см. исходное ТЗ).

**В объёме:** роли, форма ТЗ, Kanban, взятие задачи, сдача и приёмка, правки, автоматический учёт времени, учёт AI-кредитов, аналитика для руководителя, история действий, демо-данные, автодеплой.

**Вне объёма (v1):** самостоятельная регистрация, уведомления (опционально в конце), переназначение и отмена задач, комментарии-чат, мультиязычность, мобильное приложение.

## 2. Стек

| Слой | Технология | Зачем |
|---|---|---|
| Backend | Django 5.2 LTS, DRF, django-filter, drf-spectacular | Встроенные auth, CSRF, админка; OpenAPI-схема |
| БД | PostgreSQL 16 | Транзакции, агрегаты, JSONB |
| Frontend | React, Vite, TypeScript | SPA |
| UI | Tailwind, shadcn/ui, dnd-kit, Recharts | Интерфейс, drag-and-drop, графики |
| Данные на клиенте | TanStack Query, типы из OpenAPI | Кэш, типобезопасность |
| Сервер приложений | gunicorn, whitenoise | WSGI, статика админки |
| Контейнеры | Docker, Docker Compose | Единая среда |
| CI/CD | GitHub Actions, GHCR | Тесты, сборка, деплой |
| Тесты | pytest, pytest-django, factory-boy, Vitest, Playwright (1–2 сценария) | |
| Зависимости | Poetry (источник), `requirements*.txt` (экспорт командой `make req`) | Привычный вам процесс, простая установка в Docker и CI |
| Команды | Makefile (стиль django-dds-manager и FastAPI-QA-Service) | Единые короткие команды локально и в CI |
| Качество | ruff (линт и формат), bandit, pre-commit, gitleaks | |

## 3. Схема

```
Браузер ──HTTPS──► nginx Elestio (TLS, поддомен сервиса)
                       │  HTTP, внутренняя сеть Docker (172.17.0.1:WEB_PORT)
                       ▼
                  контейнер web (nginx)
                   ├─ /            → статика SPA (index.html, assets)
                   ├─ /api/, /admin/ → proxy → backend:8000
                   └─ заголовки безопасности, лимит запросов на login
                       │
                       ▼
                  контейнер backend (gunicorn + Django)
                       │            └─ volume media (загрузки)
                       ▼
                  контейнер db (PostgreSQL, volume pgdata, порт наружу не открыт)
```

Фронтенд и API отдаются с одного origin, поэтому CORS не нужен, а cookie работают без хитростей. В разработке Vite проксирует `/api` на `localhost:8000`.

## 4. Структура репозитория

```
django-creoboard/
├── backend/
│   ├── manage.py
│   ├── config/settings/{base,dev,prod}.py, urls.py, wsgi.py
│   ├── apps/
│   │   ├── users/        # User(role), логин/логаут/me
│   │   ├── tasks/        # Task, references, attachments, events, services.py
│   │   ├── ai_usage/     # AIService, AIUsage
│   │   └── analytics/    # агрегирующие эндпоинты
│   ├── pyproject.toml, poetry.lock
│   ├── requirements.txt, requirements-dev.txt   # экспорт: make req
│   ├── tests/
│   └── Dockerfile
├── frontend/             # Vite + React + TS, Dockerfile (сборка + nginx)
├── docker/               # docker-compose.yml (dev, только БД), docker-compose.prod.yml, nginx.conf
├── Makefile              # команды разработки и проверок
├── .github/              # workflows, dependabot.yml, PR-шаблон
├── .claude/              # агенты и настройки Claude Code
├── docs/
├── CLAUDE.md
└── README.md
```

## 5. Роли и права

| Действие | buyer | creative | lead |
|---|---|---|---|
| Видимость задач | свои | со статусом «Новые» и назначенные ему | все |
| Создать задачу | да | нет | нет |
| Редактировать задачу | свою, пока «Новые» | нет | нет |
| Добавить референсы | свои, до «Готово» | нет | нет |
| Взять в работу | нет | да | нет |
| Прикрепить результат, сдать на проверку | нет | свои | нет |
| Принять или отправить на правки | свои | нет | нет |
| Указать AI-расходы | нет | свои, кроме «Новые» и «Готово» | нет |
| Просмотр истории задачи | своих | видимых | всех |
| Аналитика | нет | нет | да |
| Пользователи и справочник AI-сервисов | нет | нет | да (Django admin) |

Правила реализуются двумя слоями: `get_queryset` ограничивает видимость (чужая задача отвечает 404, чтобы не раскрывать существование), а object-level permission проверяет право на действие (403).

Руководитель получает `is_staff` и группу «Lead» с правами только на модели `User` и `AIService`. Суперпользователь один и принадлежит владельцу проекта.

## 6. Модель данных

### users.User (AbstractUser)
`username` (логин), `first_name`, `last_name`, `role` ∈ {buyer, creative, lead} (пустая допускается только у суперпользователя, это гарантирует CheckConstraint), `is_active`. Хеш пароля Argon2. Модель создана в шаге 1 без `role`, чтобы `AUTH_USER_MODEL` не менялся после первых миграций; `role` добавляется в шаге 3.

### tasks.Task
| Поле | Тип | Примечание |
|---|---|---|
| id | bigint | |
| buyer | FK User | автор, PROTECT |
| assignee | FK User, null | исполнитель, PROTECT |
| offer | varchar(200) | оффер |
| geo | varchar(64) | страна или список через запятую |
| creative_type | choices | static, video, html5, ugc, copy, other |
| quantity | int | 1..500, CheckConstraint |
| description | text | |
| priority | choices | low, normal, high, urgent |
| deadline | timestamptz | |
| status | choices | new, in_progress, review, revision, done |
| revision_count | smallint | сколько раз отправляли на правки |
| created_at | timestamptz | авто |
| taken_at | timestamptz, null | первое взятие |
| submitted_at | timestamptz, null | последняя сдача на проверку |
| completed_at | timestamptz, null | приёмка |
| updated_at | timestamptz | |

Индексы: `(status)`, `(assignee, status)`, `(buyer)`, `(deadline)`, `(completed_at)`.

### tasks.TaskReference
`task`, `kind` ∈ {link, file}, `url` (только http/https, до 2000 символов, без логина и пароля) или `file`, для файла ещё `original_name`, `size`, `content_type` (определяется по содержимому), `note` (до 500 символов), `created_at`. `CheckConstraint`: ссылка без файла или файл без ссылки. Не больше 20 референсов на задачу.

### tasks.TaskAttachment (результаты работы)
`task`, `uploaded_by`, `file`, `original_name`, `size`, `content_type`, `round` (0 — первая сдача, N — после N-й правки; сервер берёт его из `Task.revision_count` под блокировкой строки задачи, клиент не передаёт), `created_at`. Не больше 20 файлов на раунд.

### tasks.TaskEvent (журнал, только добавление)
`task`, `actor` (null для системных), `action` ∈ {created, updated, taken, submitted, accepted, revision_requested, reference_added, attachment_added, ai_usage_added, ai_usage_changed, ai_usage_deleted}, `from_status`, `to_status`, `payload` (JSONB, например комментарий к правкам), `created_at`. Методы `save` (для существующей записи) и `delete` запрещены, в админке запись только для чтения.

### ai_usage.AIService
`name` (уникальное), `is_active`. Ведёт руководитель.

### ai_usage.AIUsage
`task`, `user`, `service` FK, `credits` Decimal(12,2) с ограничением ≥ 0, `round` (сервер ставит `task.revision_count` на момент записи), `comment`, `created_at`.

## 7. Статусы и переходы

```
Новые ──take──► В работе ──submit──► На проверке ──accept──► Готово
                                          │
                                  request-revision
                                          ▼
                                       Правки ──submit──► На проверке
```

| Действие | Из | В | Кто | Условия | Побочные эффекты |
|---|---|---|---|---|---|
| take | new | in_progress | creative | assignee пуст | assignee, `taken_at` |
| submit | in_progress, revision | review | assignee | есть вложение текущего раунда (`round = revision_count`), иначе 409; проверяется после роли и статуса | `submitted_at` |
| accept | review | done | buyer-автор | — | `completed_at` |
| request_revision | review | revision | buyer-автор | комментарий обязателен | `revision_count += 1` |

Реализация (`apps/tasks/services.py`):

- каждая функция выполняется в `transaction.atomic()`;
- `take` использует `Task.objects.filter(pk=..., status=NEW, assignee__isnull=True).update(...)`, при 0 затронутых строк бросает конфликт (HTTP 409);
- остальные переходы блокируют строку через `select_for_update()` и проверяют текущий статус;
- `TaskEvent` пишется в той же транзакции;
- роль и владелец проверяются раньше статуса: чужое действие даёт 403, неверный статус или проигранная гонка 409;
- сериализатор отдаёт `allowed_actions` для текущего пользователя (чистая функция `services.allowed_actions` без запросов к БД, те же правила, что и у самих переходов), а Kanban использует это поле для подсветки допустимых колонок при перетаскивании.

Перетаскивание карточки вызывает соответствующее действие API. Недопустимый drop отменяется, а сервер всё равно проверяет переход сам.

## 8. Определения метрик

| Метрика | Определение |
|---|---|
| Время ожидания | `taken_at − created_at` |
| Время выполнения | `completed_at − taken_at` (включая проверки и правки) |
| Полный цикл | `completed_at − created_at` |
| Просрочена (открытая) | `status ≠ done` и `deadline < now` |
| Сдана с опозданием | `status = done` и `completed_at > deadline` |
| Загрузка креативщика | число его задач в `in_progress`, `revision`, `review` |
| AI-расход | сумма `credits` по сервисам, исполнителям, задачам и раундам |

Все метрики фильтруются по периоду (для задач по `created_at` либо `completed_at`, для AI по `AIUsage.created_at`), агрегаты считаются в БД (`Count(filter=...)`, `Avg`, `Sum`).

## 9. API (`/api/v1`)

| Метод и путь | Доступ | Описание |
|---|---|---|
| `GET /health/` | все | проверка живости, без данных |
| `GET /auth/csrf/` | все | выставляет cookie csrftoken |
| `POST /auth/login/`, `POST /auth/logout/`, `GET /auth/me/` | см. | вход с лимитом попыток, выход, текущий пользователь |
| `GET, POST /tasks/` | по роли | список с фильтрами (status, assignee, priority, geo, overdue, search, ordering), создание (buyer) |
| `GET, PATCH /tasks/{id}/` | по роли | карточка, правка (автор-buyer, пока new; видимая, но не своя или не в `new` задача даёт 403, гонка со взятием даёт 409) |
| `POST /tasks/{id}/take/` | creative | взять задачу; существующая задача не в «Новые» даёт 409 без деталей (см. исключение ниже) |
| `POST /tasks/{id}/submit/` `/accept/` `/request-revision/` | creative (submit), buyer-автор (остальные) | переходы; `request-revision` принимает `{"comment": "..."}`, 1..2000 символов; чужая невидимая задача даёт 404 |
| `GET /tasks/{id}/events/` | по видимости | история от старых к новым, без пагинации |
| `GET /tasks/{id}/references/`, `GET /tasks/{id}/attachments/` | по видимости задачи | списки без пагинации (размер ограничен 20 референсами и 20 файлами на раунд) |
| `POST /tasks/{id}/references/` | buyer-автор | ссылка (JSON: `kind=link`, `url`, `note`) или файл (multipart: `kind=file`, `file`, `note`); до статуса «Готово»; creative и lead 403, чужая задача 404, «Готово» и превышение лимита 409 |
| `POST /tasks/{id}/attachments/` | creative-исполнитель | результат (multipart, поле `file`); только в «В работе» и «Правки» (иначе 409); другой creative на видимой задаче 403, невидимая задача 404; `round` от клиента не принимается |
| `GET /attachments/{id}/download/`, `GET /references/{id}/download/` | по видимости задачи | скачивание с проверкой прав (чужое 404, без входа 401); для ссылки-референса 404 |
| `GET, POST /tasks/{id}/ai-usage/`, `PATCH, DELETE /ai-usage/{id}/` | creative | AI-расходы |
| `GET /ai-services/` | авторизованные | справочник |
| `GET /analytics/summary/`, `/workload/`, `/durations/`, `/ai-costs/` | lead | аналитика |
| `GET /schema/`, `/docs/` | только при DEBUG | OpenAPI и Swagger; доступ для lead в prod добавляется в шаге 13 |

Списки возвращают страницы (`count`, `next`, `previous`, `results`): `?page=N&page_size=M`, по умолчанию 20, максимум 100. Пользователь без роли (суперпользователь) получает 403 на все эндпоинты задач.

Коды ошибок: 400 валидация, 401 не авторизован, 403 нет права, 404 нет такого объекта (в том числе чужого), 409 конфликт перехода или гонка, 413 слишком большой файл, 429 лимит запросов.

Порядок проверок при загрузке: 401 → роль (403) → поиск видимой задачи (404) → владелец (403) → разбор и проверка файла (400, 413) → статус и лимиты количества под блокировкой строки (409). Файл с неверным расширением, подменённым содержимым или пустой даёт 400; `submit` без вложения текущего раунда даёт 409.

**Исключение из правила «чужое — 404»: действие `take`.** Креативщик, который берёт существующую задачу не в статусе «Новые» (её уже взял он сам или другой креативщик, либо она ушла дальше по статусам), получает 409, а не 404. Так устаревшая доска получает понятный ответ, а гонка двух креативщиков даёт одинаковый результат и при параллельных, и при последовательных запросах. Ограничения исключения:

- тело ответа фиксированное (`{"detail": "Задачу сейчас нельзя взять."}`) и одинаковое для всех статусов: без исполнителя, статуса и других данных задачи;
- исключение действует только для роли creative; buyer, lead и пользователь без роли получают 403 (до поиска задачи, в том числе для несуществующего id), аноним 401;
- несуществующий id по-прежнему даёт 404; для остальных действий (`submit`, `accept`, `request-revision`) чужая невидимая задача остаётся 404.

## 10. Безопасность

| Область | Меры |
|---|---|
| Пароли | Argon2 первым в `PASSWORD_HASHERS`, валидаторы Django |
| Сессии | cookie `HttpOnly`, `Secure`, `SameSite=Lax`, ограничение срока |
| CSRF | встроенный механизм Django, SPA отправляет `X-CSRFToken`; `CSRF_TRUSTED_ORIGINS` из env |
| Брутфорс | Два слоя. 1) DRF throttle на login: отдельно по IP и по логину, номинал 5 попыток в минуту (`LOGIN_THROTTLE_IP`, `LOGIN_THROTTLE_USER`). Счётчики лежат в локальном кэше процесса, поэтому при 3 воркерах gunicorn реальный лимит в приложении **до 3 раз выше номинала** (до 15 попыток в минуту). 2) Строгий лимит обеспечивает nginx `limit_req` на `/api/v1/auth/login/`. IP клиента берётся из `X-Forwarded-For` с учётом `DJANGO_NUM_PROXIES` (на сервере 2: nginx Elestio и nginx `web`), без этой настройки заголовок можно подделать. Побочный эффект throttle по логину: известный логин можно ненадолго заблокировать. Опционально django-axes |
| Вход | Неверный пароль, неизвестный и отключённый пользователь дают одинаковый ответ 400; превышение лимита даёт 429. CSRF проверяется и на login (защита от login CSRF). Пользователь без роли (суперпользователь) входит, но прав в API не получает, так как проверки сравнивают роль точно |
| Авторизация | роль + владелец на каждом эндпоинте, queryset по роли, негативные тесты (IDOR) |
| За прокси | `SECURE_PROXY_SSL_HEADER`, `ALLOWED_HOSTS`, заголовок `X-Forwarded-Proto` пробрасывается от Elestio |
| Заголовки | CSP, `X-Content-Type-Options`, `Referrer-Policy`, `frame-ancestors 'none'`, HSTS |
| Валидация | сериализаторы DRF с ограничениями длины, ORM вместо сырого SQL |
| Админка | путь из `DJANGO_ADMIN_URL`, доступ только `is_staff`, создание пользователей только руководителем |
| Файлы | см. раздел 11 |
| Секреты | только env на сервере и GitHub Secrets, gitleaks в pre-commit и CI |
| Контейнеры | backend не от root, минимальные образы, версии закреплены, БД без публикации портов |
| Зависимости | Dependabot, `pip-audit`, `npm audit`, Trivy, CodeQL |
| Конфигурация | `manage.py check --deploy` в CI |
| Демо | демо-пользователи создаются только при `DEMO_MODE=1`, пароль из `DEMO_PASSWORD` |

## 11. Загрузка файлов

- Расширения из белого списка: `jpg, jpeg, png, gif, webp, mp4, mov, webm, pdf, zip, psd` (SVG и HTML исключены: в них можно спрятать скрипт). Расширение берётся из последнего суффикса имени, содержимое проверяется по сигнатуре библиотекой `filetype` и должно соответствовать расширению (`.png` с телом exe, `.exe.png`, `.png.exe` дают 400). Пустой файл даёт 400. Архивы не распаковываются.
- Размер ограничен `MAX_UPLOAD_MB` (по умолчанию 100), ответ 413. Слоёв три: 1) nginx `client_max_body_size` не меньше `MAX_UPLOAD_MB` + 1 МБ на multipart-обёртку; 2) приложение до разбора тела смотрит `Content-Length` (`UploadSizeLimit`); 3) после разбора проверяется `file.size`. Настройки Django `DATA_UPLOAD_MAX_MEMORY_SIZE` (1 МиБ) и `FILE_UPLOAD_MAX_MEMORY_SIZE` (2,5 МиБ) размер файла не ограничивают: первая считает тело запроса без файлов, вторая задаёт порог, после которого файл уходит во временный файл на диске.
- Имя на диске случайное (UUID) с расширением, которое выбрал сервер. Оригинальное имя хранится только как метаданные и очищается: без пути, управляющих символов и bidi-override (U+202E), не длиннее 150 символов.
- Файлы лежат в `MEDIA_ROOT` (volume `media`), вне статики. У `MEDIA_URL` нет маршрута, nginx каталог не отдаёт, whitenoise раздаёт только статику.
- Скачивание через `/attachments/{id}/download/` и `/references/{id}/download/`: проверка видимости задачи, `Content-Disposition: attachment` (имя кодируется по RFC 6266), `X-Content-Type-Options: nosniff`, `Cache-Control: private, no-store`, тип берётся из сохранённого определённого типа.
- Файл, записанный на диск в транзакции, которая откатилась, удаляется; повторная загрузка с тем же именем создаёт новую запись с новым именем на диске.
- Ссылки-референсы принимают только `http` и `https` (без логина и пароля), отображаются с `rel="noopener noreferrer"`.
- Нужно проверить лимит размера тела запроса в nginx Elestio: он может быть меньше нашего.

## 12. CI/CD

| Workflow | Триггер | Содержание |
|---|---|---|
| `ci.yml` | PR, push в main | backend: `make lint`, `make check`, `check --deploy`, `make test` с Postgres-сервисом и покрытием; frontend: eslint, tsc, vitest, build; docker build без push |
| `security.yml` | PR, раз в неделю | gitleaks, pip-audit, npm audit, bandit, CodeQL, Trivy (файловая система) |
| `deploy.yml` | push в main после зелёного CI | сборка и push образов в GHCR (тег SHA), Trivy по образам, SSH на сервер, `docker compose pull`, миграции, `up -d`, healthcheck, откат на предыдущий тег при ошибке |
| `dependabot.yml` | еженедельно | pip, npm, docker, github-actions |

Требования ко всем workflow: минимальные `permissions:`, сторонние actions закреплены по SHA, секреты только из GitHub Secrets, окружение `production` с возможностью ручного подтверждения.

Секреты репозитория: `SSH_HOST`, `SSH_USER`, `SSH_PRIVATE_KEY` (отдельный ключ только для деплоя), `SSH_KNOWN_HOSTS` (закреплённый отпечаток сервера).

## 13. Деплой на Elestio

Сервер: Ubuntu, 4 ГБ RAM, режим custom Docker (CI/CD-таргет). Каталог приложения: `/opt/app/<имя_пайплайна>/`.

`docker/docker-compose.prod.yml` (состав):

| Сервис | Образ | Особенности |
|---|---|---|
| `db` | postgres:16-alpine | volume `pgdata`, healthcheck, порт не публикуется |
| `migrate` | backend | разовый запуск `manage.py migrate --noinput` |
| `backend` | backend | gunicorn, не root, `depends_on: migrate (service_completed_successfully)`, volume `media` |
| `web` | web | `ports: "172.17.0.1:${WEB_PORT}:80"` |

- HTTPS и поддомен даёт Elestio; свой домен не используется.
- Порт, на который nginx Elestio проксирует сервис, проверяется в панели при первом деплое.
- Файл `.env` лежит на сервере рядом с compose-файлом и не попадает в репозиторий.
- Образы собираются в GitHub Actions, сервер только скачивает их.
- Бэкап: ежедневный `pg_dump` по cron в `/opt/app/<pipeline>/backups` с хранением 7 дней; дополнительно изучить встроенные бэкапы Elestio.
- Откат: `IMAGE_TAG=<предыдущий_sha> docker compose up -d`.

## 14. Демо-данные

Команда `python manage.py seed_demo` (работает только при `DEMO_MODE=1`, идемпотентна):

- 1 руководитель, 2 байера, 3 креативщика (логины `lead`, `buyer1`, `buyer2`, `creative1`..`creative3`);
- 5 AI-сервисов;
- около 30 задач во всех статусах, включая просроченные и с несколькими раундами правок, с историей событий и AI-расходами.

## 15. Принятые решения и компромиссы

| Решение | Причина | Цена |
|---|---|---|
| Django + DRF вместо FastAPI | Auth, CSRF, админка из коробки: быстрее и безопаснее за 24 часа | Менее «лёгкий» стек |
| Сессии вместо JWT | Проще защитить (HttpOnly + CSRF), нет хранения токена в JS | Нужен один origin |
| Статусы через сервисный слой | Один источник правил, легко тестировать | Нельзя менять статус напрямую через ORM в коде |
| Денормализованные метки времени + журнал событий | Быстрая аналитика и полная история | Метки нужно обновлять только через сервисы |
| Нет переназначения и отмены задач | Не требуется ТЗ | Возможное расширение |
| Админка Django для пользователей и справочников | Экономит время на фронтенде | Отдельный интерфейс для руководителя |
