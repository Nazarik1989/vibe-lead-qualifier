# Vibe Lead Qualifier

Vibe Lead Qualifier — FastAPI-сервис для автоматической квалификации входящих лидов через VibeMarketolog Agent API. Он собирает структурированный бриф из диалога и, когда данных достаточно, возвращает разрешённое CRM-действие `crm.deal.add` для Bitrix24.

Это законченный демонстрационный MVP, а не production-ready система. Он показывает работу с REST API, подписанными webhook, локальной идемпотентностью обработки, SQLite, типизацией, ошибками и тестами без подключения сторонней LLM и без автоматических платных генераций.

> Интеграционный контракт сверен с [официальной документацией Agent API](https://lk.vibemarketolog.ru/docs/agent-api) и [официальным OpenAPI 3.1](https://lk.vibemarketolog.ru/api/agent/openapi.json) 28.07.2026. На странице документации указано обновление от 27.07.2026. Внешние платные запросы при разработке и проверке проекта не выполнялись.

## Постоянное демо

- [Swagger UI](https://vibe-lead-qualifier.88-218-169-93.sslip.io/docs) —
  защищён Basic Auth; учётные данные передаются отдельно и не хранятся в Git;
- [health check](https://vibe-lead-qualifier.88-218-169-93.sslip.io/health) —
  публичный маршрут;
- постоянный webhook endpoint:
  `https://vibe-lead-qualifier.88-218-169-93.sslip.io/webhooks/vibe`.

`/docs`, `/openapi.json`, `/demo` и `/demo/*` защищены Basic Auth на уровне
Nginx. `/health` и `/webhooks/vibe` доступны без Basic Auth, но webhook
принимает payload только с корректной HMAC-подписью. Приложение на VPS слушает
только `127.0.0.1:18000`; неизвестные HTTPS-пути возвращают `404` и не открывают
другой сайт сервера.

Пошаговая воспроизводимая установка описана в
[DEPLOYMENT.md](DEPLOYMENT.md). Технический hostname зависит от неизменности IP
VPS и доступности DNS-сервиса `sslip.io`.

## Что подтверждено

- 61 автоматический тест проходит на Python 3.12;
- Ruff format, Ruff check, `pip check` и `compileall` проходят;
- реальный `GET /api/agent/me` подтвердил рабочий Bearer-токен;
- реальный бесплатный `POST /api/agent/generate/estimate` вернул валидный
  dry-run и цену без запуска генерации;
- постоянный HTTPS endpoint проверен внешними запросами;
- официальный бесплатный webhook self-test доставлен с HTTP `200` и стоимостью
  `0`;
- HMAC, replay, ограничение тела и rate limiting проверены на VPS;
- повторная доставка подтверждает локальную идемпотентность обработки, но не
  exactly-once исполнение CRM-action внешним мостом;
- `POST /generate` отсутствует в CLI, поэтому случайный платный запуск из
  проекта невозможен;
- секреты не включены в репозиторий или примеры.

Санитизированные результаты и честная граница end-to-end проверки webhook
зафиксированы в [LIVE_VALIDATION.md](LIVE_VALIDATION.md).

## Проблема

Лид редко присылает готовый бриф одним сообщением. Имя, задача, бюджет, срок и контакт появляются по частям, сообщения могут быть доставлены повторно, а неподписанный запрос нельзя считать доверенным. Если создавать сделку после каждого сообщения, CRM быстро наполнится дублями и неполными карточками.

## Решение

Сервис ведёт состояние диалога и на каждом сообщении:

1. для официального webhook проверяет HMAC-подпись на исходных байтах тела;
2. нормализует входящее событие и вычисляет стабильный ключ идемпотентности;
3. детерминированными правилами извлекает имя, задачу, бюджет, срок, контакт и комментарий;
4. объединяет новые данные с уже собранным брифом в SQLite;
5. задаёт один конкретный вопрос о следующем недостающем поле;
6. при первом переходе брифа в состояние ready формирует и сохраняет ответ с одним `crm.deal.add`.

Для готовности сделки нужны имя, задача, бюджет, срок и контакт. Комментарий сохраняется, если клиент его сообщил, но не блокирует передачу лида.

Детерминированный extractor выбран сознательно: поведение прозрачно, быстро, бесплатно и полностью воспроизводимо в тестах. Интерфейс извлечения позволяет позднее подключить LLM как отдельную реализацию, не меняя webhook и хранилище.

## Возможности

- события `webhook.test`, `agent.message`, `generation.complete`, `generation.error`;
- безопасное подтверждение неизвестного события без падения приложения;
- webhook-подпись новой схемы: hex HMAC-SHA256 от raw body с выделенным `VIBE_WEBHOOK_SECRET`;
- накопление брифа из нескольких сообщений;
- повторная доставка не изменяет локальное состояние и возвращает сохранённый ответ целиком, включая исходный `crm.deal.add`;
- отключённый по умолчанию unsigned demo-контур, не требующий токена Vibe;
- асинхронный типизированный клиент Vibe API;
- CLI для проверки токена, self-test webhook, регистрации URL, бесплатной оценки генерации и локального demo-сообщения;
- SQLite без отдельного сервера;
- unit/integration-тесты без реального списания средств.

## Архитектура

```text
VibeMarketolog / demo client
              │
              ▼
        FastAPI endpoints
              │
      signature + normalize
              │
              ▼
       qualification service
        ┌─────┴─────┐
        ▼           ▼
 deterministic   SQLite repository
   extractor      dialogs + processed events
        │
        ▼
 reply + optional whitelisted crm.deal.add
```

Границы компонентов, последовательности обработки и компромиссы описаны в [ARCHITECTURE.md](ARCHITECTURE.md). Продуктовая ценность и пять предложений к Agent API — в [PRODUCT_IDEA.md](PRODUCT_IDEA.md).

## HTTP API проекта

| Метод | Путь | Назначение |
| --- | --- | --- |
| `GET` | `/health` | Проверка состояния приложения |
| `POST` | `/webhooks/vibe` | Подписанный webhook VibeMarketolog |
| `POST` | `/demo/messages` | Локальная демонстрация; только при `ENABLE_DEMO_ENDPOINTS=true` |
| `GET` | `/demo/dialogs/{dialog_id}` | Состояние demo-диалога; только при `ENABLE_DEMO_ENDPOINTS=true` |

Тело локального demo-запроса:

```json
{
  "dialog_id": "demo-001",
  "message_id": "msg-001",
  "text": "Меня зовут Анна, нужен лендинг для онлайн-школы",
  "channel": "demo",
  "context": {"source": "readme"}
}
```

`channel` и `context` необязательны. Demo-endpoints намеренно отделены от `/webhooks/vibe`: они не проверяют HMAC, по умолчанию вообще не регистрируются и предназначены только для локальной демонстрации.

## Быстрый запуск на Windows

Требования: Python 3.12 и PowerShell 7 либо Windows PowerShell 5.1.

```powershell
py -3.12 -m venv .venv
Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
python -m pip install -e ".[dev]"

if (-not (Test-Path .env)) {
    Copy-Item .env.example .env
}

.\scripts\run.ps1
```

Скрипт запускает тот же контракт, что и явная команда:

```powershell
python -m uvicorn vibe_lead_qualifier.main:app --host 127.0.0.1 --port 8000
```

Проверка состояния во втором терминале:

```powershell
Invoke-RestMethod http://127.0.0.1:8000/health
```

## Настройка `.env`

Создайте локальный `.env` из `.env.example` и заполните секреты самостоятельно. Не присылайте токен или webhook secret в чат и не коммитьте `.env`.

| Переменная | Назначение | Значение по умолчанию |
| --- | --- | --- |
| `VIBE_API_TOKEN` | Bearer-токен для исходящих запросов и CLI | пусто |
| `VIBE_WEBHOOK_SECRET` | отдельный секрет проверки webhook | пусто |
| `VIBE_BASE_URL` | базовый URL платформы | `https://lk.vibemarketolog.ru` |
| `HTTP_TIMEOUT_SECONDS` | таймаут исходящих запросов CLI через `VibeClient` | `20` |
| `DATABASE_PATH` | файл локального состояния | `./data/vibe_leads.sqlite3` |
| `LOG_LEVEL` | уровень логирования | `INFO` |
| `ENABLE_DEMO_ENDPOINTS` | регистрация небезопасных `/demo/*` маршрутов | `false` |

Для локального `/demo/*` Vibe credentials не нужны. `VIBE_WEBHOOK_SECRET` нужен принимающему подписанный webhook, а `VIBE_API_TOKEN` — только исходящим сетевым CLI-командам. `HTTP_TIMEOUT_SECONDS` применяется ко всем таким запросам CLI через `VibeClient`.

## Локальная демонстрация

Установите `ENABLE_DEMO_ENDPOINTS=true` в локальном `.env` и перезапустите приложение. Затем выполните:

```powershell
.\scripts\demo.ps1
```

Скрипт использует новый `dialog_id`, отправляет несколько сообщений только на `127.0.0.1`, показывает ответы после каждого шага и затем читает итоговый бриф. Он не обращается к VibeMarketolog или Bitrix24 и ничего не списывает.

Ожидаемый сценарий по смыслу:

```text
Клиент: Меня зовут Анна. Нужен лендинг для онлайн-школы.
Агент:  Уточняет один из недостающих пунктов.

Клиент: Бюджет 120 000 рублей, готовность до 15 августа.
Агент:  Просит контакт.

Клиент: Контакт: +7 999 123-45-67; комментарий: интеграция с CRM обязательна.
Агент:  Подтверждает готовность брифа и возвращает crm.deal.add.
```

Фактическую формулировку ответов показывает `demo.ps1`; она является частью детерминированной логики, а не сгенерированным LLM текстом.

Пример итоговой команды:

```json
{
  "method": "crm.deal.add",
  "params": {
    "fields": {
      "TITLE": "Заявка: лендинг для онлайн-школы — Анна",
      "OPPORTUNITY": 120000,
      "CURRENCY_ID": "RUB",
      "CATEGORY_ID": 0,
      "COMMENTS": "Клиент: Анна\nЗадача: лендинг для онлайн-школы\nСрок: до 15 августа\nКонтакт: +7 999 123-45-67\nКомментарий: интеграция с CRM обязательна"
    }
  }
}
```

Команда возвращается платформе как намерение. Сам сервис не хранит Bitrix credentials и напрямую не вызывает Bitrix24; исполнение разрешённого action выполняет мост платформы.

## CLI

Общая справка:

```powershell
python -m vibe_lead_qualifier.cli --help
```

Доступные команды:

```powershell
# Проверить Bearer-токен через GET /api/agent/me
python -m vibe_lead_qualifier.cli check-token

# Попросить платформу прислать бесплатное тестовое подписанное событие
python -m vibe_lead_qualifier.cli webhook-self-test https://public.example/webhooks/vibe

# Включить или отключить push webhook для agent.message
python -m vibe_lead_qualifier.cli register-webhook https://public.example/webhooks/vibe
python -m vibe_lead_qualifier.cli register-webhook --disable

# Бесплатный pre-flight; генерацию не запускает
python -m vibe_lead_qualifier.cli estimate --type video --model veo3_fast --prompt "demo" --strict

# Сообщение в локальный demo-endpoint
python -m vibe_lead_qualifier.cli demo-message `
    --base-url http://127.0.0.1:8000 `
    --dialog-id cli-demo `
    --message-id cli-msg-1 `
    --text "Меня зовут Иван, нужен сайт"
```

CLI не печатает credentials. `estimate` использует документированный `POST /api/agent/generate/estimate`, который валидирует запрос и оценивает стоимость без запуска генерации и списания. В проекте нет команды платной генерации.

## Границы идемпотентности

SQLite обеспечивает **local processing idempotency**: событие с тем же ключом не обрабатывается повторно и не меняет локальное состояние. Сохранённый HTTP-ответ намеренно возвращается целиком, включая исходный action, чтобы команда не потерялась, если первый HTTP-ответ не дошёл до платформы.

Это не гарантирует **external side-effect idempotency**. Внешний Vibe/Bitrix-мост может повторно увидеть тот же `crm.deal.add`, а сервис не получает результат его исполнения. Exactly-once создание сделки требует поддержки моста: стабильного `action_id` или idempotency key, дедупликации при исполнении и доступного результата выполнения.

## Постоянный HTTPS webhook

Основной listener работает непосредственно на VPS:

```text
https://vibe-lead-qualifier.88-218-169-93.sslip.io/webhooks/vibe
```

Nginx принимает публичный HTTPS, а приложение остаётся на localhost. На VPS
хранится только `VIBE_WEBHOOK_SECRET`, необходимый для входящей HMAC-проверки.
`VIBE_API_TOKEN` там отсутствует: он используется на доверенной локальной
машине для исходящих CLI-команд. Подробная конфигурация Docker, Nginx, Basic
Auth и Certbot приведена в [DEPLOYMENT.md](DEPLOYMENT.md).

### Резервный временный туннель

Quick Tunnel использовался только как промежуточный smoke test и после проверки
постоянного адреса остановлен. Для отдельной локальной проверки перед запуском
любого публичного туннеля установите `ENABLE_DEMO_ENDPOINTS=false` в `.env`,
перезапустите приложение и убедитесь, что `/demo/*` возвращает `404`. После
этого оставьте приложение на `127.0.0.1:8000` и запустите туннель отдельным
процессом.

Вариант с ngrok:

```powershell
ngrok http 8000
```

Вариант с Cloudflare Quick Tunnel:

```powershell
cloudflared tunnel --url http://127.0.0.1:8000
```

Добавьте к выданному временному HTTPS-домену путь `/webhooks/vibe`. Туннель
открывает локальный сервис интернету; после проверки остановите его. Он не
заменяет постоянный VPS deployment.

### Регистрация и официальный self-test

Сначала поместите `VIBE_API_TOKEN` и `VIBE_WEBHOOK_SECRET` в локальный `.env`, затем:

```powershell
$CallbackUrl = "https://vibe-lead-qualifier.88-218-169-93.sslip.io/webhooks/vibe"

python -m vibe_lead_qualifier.cli register-webhook $CallbackUrl
python -m vibe_lead_qualifier.cli webhook-self-test $CallbackUrl
```

Self-test вызывает официальный `POST /api/agent/webhook-test` и не запускает генерацию. По документации он требует право `read`. Для `POST /api/agent/webhook-url` официальный OpenAPI указывает право `write`, поэтому его нужно явно выдать ключу. Успешное тело ответа этого метода в OpenAPI не типизировано, и клиент намеренно принимает его расширяемо.

Для отключения push-режима:

```powershell
python -m vibe_lead_qualifier.cli register-webhook --disable
```

## Контракт webhook и подпись

Новая документированная схема:

```text
expected = hex(HMAC-SHA256(raw_request_body, VIBE_WEBHOOK_SECRET))
received = X-Vibe-Signature
```

Сравнение выполняется constant-time функцией `hmac.compare_digest`, причём до JSON-парсинга. В официальной документации также описана legacy-схема для старых токенов, где секрет производился из raw API token. Этот MVP сознательно поддерживает только более безопасный выделенный `webhook_secret`; для старого ключа следует перевыпустить ключ, а не передавать API token в проверяющий контур.

Документация перечисляет поля входящего сообщения (`id`/`message_id`, `text`, `channel`, `context.dialog_id`, вложения), но на дату проверки не показывает точную JSON-оболочку push-события `agent.message`. Поэтому адаптер проекта принимает документированные поля как на верхнем уровне, так и внутри объекта `message`. Это совместимость с наблюдаемым пробелом документации, а не утверждение о канонической оболочке Vibe.

Примеры находятся в каталоге `examples/`:

- `webhook-agent-message.json` — поддерживаемая проектом иллюстрация `agent.message`, не каноническая схема платформы;
- `webhook-test.json` — минимальное документированное тестовое событие;
- `generation-complete.json` — документированный callback завершения генерации;
- `crm-deal-add.json` — ответ с разрешённым Bitrix24 action.

## Тесты и качество

```powershell
python -m pytest
python -m ruff check .
python -m ruff format --check .
```

Тесты работают локально, используют временную SQLite и mock-ответы для Vibe API. Реальные токены, сетевые вызовы и списания им не нужны.

Те же проверки автоматически запускает
[GitHub Actions workflow](.github/workflows/ci.yml) на каждый push и pull
request.

## Безопасность

- HMAC вычисляется по неизменённым raw bytes, а не по повторно сериализованному JSON;
- Bearer-токен и webhook secret берутся только из окружения и не попадают в журнал;
- принимается только выделенный webhook secret, legacy-вывод секрета из токена отключён;
- action ограничен точным методом `crm.deal.add`; произвольный метод из текста лида не исполняется;
- повторное событие не меняет локальное состояние и не пересчитывает сохранённый ответ;
- платный `POST /generate` проектом автоматически не вызывается;
- ошибки внешнего API нормализуются без раскрытия credentials.

Demo-маршруты по умолчанию отключены. Сами FastAPI-маршруты не реализуют
авторизацию или rate limiting; в live VPS deployment эти ограничения применяет
Nginx, а прямой обход proxy исключён localhost binding. Для production также
нужны политика хранения персональных данных, шифрование/секрет-хранилище,
наблюдаемость и распределённое хранилище локальной идемпотентности.

## Ограничения MVP

- правила извлечения рассчитаны на ограниченный набор русскоязычных формулировок и не понимают смысл так глубоко, как LLM;
- SQLite подходит для одного небольшого экземпляра, но не для горизонтального масштабирования;
- в текущей документации нет универсального signed delivery timestamp, поэтому сервис не может реализовать полноценное временное окно anti-replay;
- fallback fingerprint при отсутствии стабильного message ID — компромисс и теоретически может дать коллизию бизнес-событий;
- при обходе поставляемого reverse proxy включённые demo-endpoints не имеют
  собственной application-level авторизации и rate limiting;
- персональные данные хранятся в локальном файле без шифрования на уровне приложения;
- сервис формирует CRM-action, но не получает результат его исполнения и не гарантирует exactly-once внешний side effect в Bitrix24;
- нет очереди фоновых задач, метрик/SLA, миграционного инструмента и multi-tenant изоляции;
- поддерживается только новая схема с отдельным webhook secret.

## Развитие

1. PostgreSQL и атомарный outbox для нескольких экземпляров сервиса.
2. LLM-extractor за существующим интерфейсом: structured output, confidence, deterministic fallback и набор golden-тестов.
3. Стабильный `action_id`/idempotency key, дедупликация мостом, receipt/status с внешним ID сделки и аудит результата исполнения CRM-action.
4. Распределённый/application-level rate limiting, OpenTelemetry, метрики задержки/дублей и алерты.
5. Шифрование PII, TTL/удаление диалогов и tenant-aware доступ.
6. Поддержка официального delivery ID и timestamp, если они появятся в контракте Agent API.
