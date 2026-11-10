# CreoBoard — подготовка окружения (Windows 10)

Документ описывает, что установить и настроить до шага 0, и как подготовить сервер Elestio и GitHub к деплою. Команды выполняются в **PowerShell**.

## 1. Проверка Windows

1. Нажмите `Win+R`, введите `winver`. Нужна версия **22H2 (сборка 19045)**. Docker Desktop требует её для Windows 10. Если версия ниже, обновите систему через Центр обновления Windows.
2. Включите виртуализацию в BIOS/UEFI (Intel VT-x или AMD-V). Проверка: Диспетчер задач → Производительность → ЦП → «Виртуализация: включено».
3. Windows 10 уже вышла из основной поддержки Microsoft. Docker поддерживает Docker Desktop только для версий Windows, находящихся в сроке обслуживания, поэтому актуальные требования проверьте на https://docs.docker.com/desktop/setup/install/windows-install/. Если установщик откажется работать, смотрите раздел 6.

## 2. Установка инструментов

Откройте PowerShell и выполните:

```powershell
winget install --id Git.Git -e
winget install --id GitHub.cli -e
winget install --id Python.Python.3.12 -e
winget install --id OpenJS.NodeJS.LTS -e
winget install --id Docker.DockerDesktop -e
winget install --id ezwinports.make -e              # make для Makefile
winget install --id Microsoft.VisualStudioCode -e   # по желанию
```

Если `winget` не находит пакет `ezwinports.make`, посмотрите `winget search make` или поставьте `make` через Chocolatey: `choco install make`.

Poetry нужен, чтобы менять зависимости и выполнять `make req` (для простого запуска проекта достаточно `pip` и экспортированных `requirements*.txt`):

```powershell
python -m pip install --user pipx
python -m pipx ensurepath
```

Откройте новое окно PowerShell и выполните:

```powershell
pipx install poetry
pipx inject poetry poetry-plugin-export    # плагин нужен для команды poetry export
```

После установки **закройте и снова откройте PowerShell**, затем проверьте:

```powershell
git --version ; gh --version ; python --version ; node --version ; npm --version
make --version ; poetry --version
wsl --version        # версия WSL должна быть 2.1.5 или новее
docker --version ; docker compose version
```

Если WSL не установлен: `wsl --install`, затем перезагрузка. В Docker Desktop при установке оставьте включённым WSL 2 backend.

Ограничьте память WSL (чтобы Docker не съел весь компьютер): создайте файл `%UserProfile%\.wslconfig`

```
[wsl2]
memory=4GB
processors=2
```

и выполните `wsl --shutdown`.

## 3. Настройка Git и GitHub

```powershell
git config --global user.name "Ваше Имя"
git config --global user.email "ваш@email"
git config --global core.autocrlf input
git config --global core.longpaths true
git config --global init.defaultBranch main
gh auth login          # GitHub.com → HTTPS → вход через браузер
```

Репозиторий называется `django-creoboard` (как и ваша папка проекта). Название продукта остаётся CreoBoard.

Если папка `django-creoboard` у вас уже создана, но это ещё не git-репозиторий:

```powershell
cd $HOME\django-creoboard          # замените путь на свой
git init -b main
gh repo create MaxBakshaev/django-creoboard --public --source=. --remote=origin
```

Если репозиторий на GitHub уже существует, достаточно `git clone https://github.com/MaxBakshaev/django-creoboard.git`.

Распакуйте архив с документами так, чтобы содержимое его папки (`CLAUDE.md`, `README.md`, `Makefile`, `.claude\`, `docs\`, `.env.example`) оказалось в корне `django-creoboard`. Затем сделайте стартовый коммит напрямую в `main` (дальше только через PR):

```powershell
git add .
git commit -m "docs: add project documentation and tooling"
git push -u origin main
```

Защита ветки `main` (GitHub → Settings → Branches / Rulesets → Add rule для `main`):

- Require a pull request before merging
- Require status checks to pass (добавьте проверки после первого запуска CI)
- Block force pushes

## 4. Claude Code

1. Нужен аккаунт Claude с платной подпиской либо API-ключ.
2. Для Windows рекомендуется установить Git for Windows (уже поставлен в разделе 2).
3. Установка в PowerShell:

```powershell
irm https://claude.ai/install.ps1 | iex
claude --version
```

Альтернативы: расширение Claude Code для VS Code или десктоп-приложение (вкладка Code). Если команда `claude` не находится, откройте новое окно PowerShell. Подробности: https://code.claude.com/docs/en/setup

4. Запуск:

```powershell
cd $HOME\django-creoboard
claude
```

При первом запуске войдите в аккаунт. `/init` запускать не нужно, `CLAUDE.md` уже подготовлен. Проверьте, что он подхвачен: команда `/memory`.

5. Рекомендации по работе:
   - Начинайте каждый шаг в **plan mode** (`Shift+Tab` до режима плана или команда `/plan`), проверяйте план, затем разрешайте реализацию.
   - Права проверьте командой `/permissions`. В `.claude/settings.json` уже запрещено чтение `.env` и ключей. Синтаксис правил зависит от версии, поэтому после запуска убедитесь, что запреты показаны.
   - Агенты лежат в `.claude/agents/`, список: `/agents`.
   - Между шагами используйте `/clear`, при длинной сессии `/compact`.
   - Не используйте `--dangerously-skip-permissions` на личном компьютере.
   - Не вставляйте в чат секреты и приватные ключи.
   - Перед каждым PR запускайте `/security-review`.

Первый запрос к Claude Code:

```
Прочитай CLAUDE.md и docs/ARCHITECTURE.md. Выполни шаг 0 из docs/DEVELOPMENT_PLAN.md.
Сначала предложи план, код пока не пиши.
```

## 5. Сервер Elestio

Что известно из документации Elestio и что нужно проверить на месте:

1. Создайте сервис типа **CI/CD с custom Docker** на вашей VM (Ubuntu, 4 ГБ). Названия пунктов в панели могут отличаться, ориентируйтесь на смысл.
2. Рабочий каталог приложений: `/opt/app/<имя_пайплайна>/`. Запомните имя пайплайна, оно понадобится в `deploy.yml`.
3. Свой домен **не нужен**. Используйте поддомен сервиса, который даёт Elestio. HTTPS на нём настраивается автоматически.
4. Приложение публикуется на внутреннем адресе Docker: `172.17.0.1:PORT`, а не `0.0.0.0`. Выберите свободный `PORT` (переменная `WEB_PORT`). **Проверьте в панели, на какой порт nginx Elestio проксирует ваш сервис**, при необходимости настройте это в разделе Security → Nginx configuration.
5. Firewall: по умолчанию Elestio открывает только нужные порты. Лишние порты не открывайте. Порт 22 нужен для SSH-деплоя.
6. Лимит размера загружаемого тела запроса в nginx Elestio проверьте и при необходимости увеличьте до значения `MAX_UPLOAD_MB` плюс запас.
7. Подключитесь по SSH и проверьте Docker:

```bash
docker --version
docker compose version      # если команда не найдена, попробуйте: docker-compose --version
```

В `deploy.yml` используется та команда, которая есть на сервере. Ubuntu 26.04 очень свежая, поэтому убедитесь, что Docker Engine там работает штатно.

### Ключ для деплоя

Создайте отдельный ключ только для GitHub Actions:

```powershell
ssh-keygen -t ed25519 -f "$env:USERPROFILE\.ssh\creoboard_deploy" -C "creoboard-deploy" -N '""'
Get-Content "$env:USERPROFILE\.ssh\creoboard_deploy.pub"
```

Добавьте публичный ключ в панели Elestio (раздел SSH keys). Elestio просит только тип и тело ключа, то есть без комментария в конце (`creoboard-deploy`). Проверьте вход:

```powershell
ssh -i "$env:USERPROFILE\.ssh\creoboard_deploy" root@<IP_сервера>
```

Отпечаток хоста для `known_hosts`:

```powershell
ssh-keyscan -H <IP_сервера>
```

Сверьте отпечаток с тем, что показывает панель Elestio, и только потом сохраните.

### Файл `.env` на сервере

На сервере создайте `/opt/app/<pipeline>/.env` по образцу `.env.example` (значения prod), права `chmod 600`. Секретный ключ Django:

```powershell
python -c "import secrets; print(secrets.token_urlsafe(50))"
```

Пароль БД сгенерируйте так же. Файл `.env` не копируется в репозиторий и не передаётся Claude Code.

## 6. Если Docker Desktop не установился

Варианты, от простого к сложному:

1. Установите PostgreSQL 16 для Windows обычным установщиком и в `.env` укажите `DATABASE_URL` на `localhost`. Остальная разработка идёт без Docker, а образы всё равно собирает GitHub Actions.
2. Поставьте Ubuntu в WSL 2 (`wsl --install -d Ubuntu`) и установите там Docker Engine по инструкции Docker.

Вариант 1 достаточен для всех шагов, кроме локальной проверки `docker compose` для продакшена. Её можно заменить проверкой в CI.

## 7. Секреты GitHub

```powershell
cd $HOME\django-creoboard
gh secret set SSH_HOST --body "<IP_сервера>"
gh secret set SSH_USER --body "root"
Get-Content -Raw "$env:USERPROFILE\.ssh\creoboard_deploy" | gh secret set SSH_PRIVATE_KEY
ssh-keyscan -H <IP_сервера> | gh secret set SSH_KNOWN_HOSTS
```

Дополнительно:

- Создайте окружение `production` (Settings → Environments) и при желании включите обязательное подтверждение деплоя.
- После первого push образов в GHCR сделайте пакеты публичными (в образах нет секретов) либо настройте на сервере `docker login ghcr.io` с токеном `read:packages`.
- Ключ деплоя даёт доступ к серверу. Храните его только в GitHub Secrets. Если он утёк, отзовите его в панели Elestio и создайте новый.

## 8. Первый локальный запуск (после шага 1)

```powershell
cd $HOME\django-creoboard
Copy-Item .env.example .env          # заполните значения для разработки
make secret                          # вставьте результат в DJANGO_SECRET_KEY
make db                              # PostgreSQL в Docker
make venv                            # .venv на Python 3.12 + зависимости, активация не нужна
make migrate
make run
```

Если вы работаете через Poetry: `cd backend ; poetry install ; poetry shell`, а затем `make`-команды из корня.

Список команд смотрите в `Makefile` и в разделе «Команды Makefile» файла `README.md`. Перед каждым PR запускайте `make ci` (до появления frontend: `make ci-back`).

БД из Docker публикуется на `127.0.0.1:5433`, потому что порт 5432 часто занят локальным PostgreSQL. В `.env` укажите `POSTGRES_PORT=5433` и `DATABASE_URL=postgres://...@127.0.0.1:5433/...`. Адрес `127.0.0.1` надёжнее `localhost`: на Windows `localhost` может сначала разрешиться в недоступный `::1`, и подключение будет подвисать.

## 9. Частые проблемы на Windows

| Проблема | Решение |
|---|---|
| `Activate.ps1` не запускается («выполнение сценариев отключено») | `Set-ExecutionPolicy -Scope CurrentUser RemoteSigned` |
| Ошибки `\r` в shell-скриптах внутри Docker | Окончания строк LF: проверьте `.gitattributes` и `core.autocrlf input`, пересоздайте файлы |
| Порт БД занят | Смените `POSTGRES_PORT` в `.env` (по умолчанию 5433) и порт в `DATABASE_URL` |
| `make` не распознан | Установите по разделу 2, откройте новое окно PowerShell, проверьте `make --version` |
| `docker compose` не найден, есть только `docker-compose` | `make DC="docker-compose --project-directory . -f docker/docker-compose.yml" db` |
| В `make`-цели не находится `ruff` или `pytest` | Создайте окружение: `make venv` (активировать его не нужно). Нужен Python 3.12: `py -3.12 --version` |
| `&&` не работает | Используйте `;` в Windows PowerShell 5.1 |
| Docker жрёт память | Ограничения в `.wslconfig` (раздел 2) |
| `claude` не найден после установки | Откройте новое окно PowerShell, при необходимости перезайдите в систему |
