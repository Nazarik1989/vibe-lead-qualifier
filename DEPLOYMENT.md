# VPS deployment

Этот документ описывает воспроизводимый HTTPS-деплой Vibe Lead Qualifier на
один Ubuntu VPS. Публичный демонстрационный экземпляр доступен по адресу
`vibe-lead-qualifier.88-218-169-93.sslip.io`.

```text
Internet
   |
   v
Nginx :80/:443
   |
   v
127.0.0.1:18000
   |
   v
FastAPI container + SQLite volume
```

Приложение не публикует контейнерный порт наружу. Существующие virtual hosts
Nginx не требуется изменять: новый конфиг использует только точный
`server_name` и не содержит `default_server`.

## Файлы

- `deploy/vps/Dockerfile` — non-root Python 3.12 image;
- `deploy/vps/docker-compose.yml` — только app-сервис с localhost binding;
- `deploy/vps/nginx-vibe-lead-qualifier-public-bootstrap.conf` — временный
  HTTP server block для ACME challenge;
- `deploy/vps/nginx-vibe-lead-qualifier-public.conf` — финальный HTTP/HTTPS
  server block;
- `deploy/vps/certbot-deploy-hook.sh` — `nginx -t` и reload после успешного
  обновления сертификата.

Bootstrap и финальный Nginx-конфиг объявляют одинаковые shared zones. Их нужно
последовательно устанавливать в **один и тот же** remote-файл; одновременно
включать оба файла нельзя.

## Требования

- Ubuntu с Docker и Docker Compose;
- действующий Nginx;
- публичные TCP-порты 80 и 443;
- DNS A record выбранного hostname на IP VPS;
- email оператора для ACME-аккаунта Let's Encrypt.

Для опубликованного demo DNS предоставляет `sslip.io`. Это техническая
зависимость: при изменении IP или переходе на собственный домен нужно заменить
hostname во всех Nginx-файлах и перевыпустить сертификат.

## Секреты и runtime-файлы

Никогда не добавляйте в Git `.env`, API token, webhook secret, `.htpasswd`,
сертификаты, private keys, SQLite, логи, ZIP или backup-файлы.

На VPS API token не нужен. Он остаётся на доверенной локальной машине для
исходящих CLI-команд. Серверу нужен только отдельный webhook secret для проверки
входящей HMAC-подписи.

```bash
cd /opt/vibe-lead-qualifier
cp .env.example .env
chmod 600 .env
```

Отредактируйте `.env` локальным редактором на VPS, не печатая его содержимое в
терминал или логи. Перед первым запуском должны быть заданы следующие
нечувствительные параметры:

```dotenv
VIBE_API_TOKEN=
DATABASE_PATH=/data/vibe_leads.sqlite3
ENABLE_DEMO_ENDPOINTS=false
```

Значение `VIBE_WEBHOOK_SECRET` внесите отдельно вне Git. Demo сначала остаётся
выключенным и включается только после установки Basic Auth на Nginx.

Создайте persistent data directory с правами runtime UID контейнера:

```bash
sudo install -d -m 0700 -o 10001 -g 10001 /opt/vibe-lead-qualifier/data
```

## Запуск приложения

Из корня репозитория:

```bash
sudo docker compose -f deploy/vps/docker-compose.yml up -d --build app
sudo docker compose -f deploy/vps/docker-compose.yml ps
```

Для сервера со старым standalone Compose замените `docker compose` на
`docker-compose`. Контейнер должен быть healthy, а порт — привязан только к
loopback:

```bash
curl --fail http://127.0.0.1:18000/health
ss -lnt | grep 18000
```

## Backup и ACME bootstrap

До изменения Nginx сохраните закрытую резервную копию:

```bash
sudo install -d -m 0700 /opt/backups
sudo tar -C /etc -czf \
  /opt/backups/nginx-before-vibe-$(date -u +%Y%m%dT%H%M%SZ).tar.gz nginx
sudo chmod 600 /opt/backups/nginx-before-vibe-*.tar.gz
```

Создайте отдельный ACME webroot и установите bootstrap в новый файл:

```bash
sudo install -d -m 0755 \
  /var/www/vibe-lead-qualifier-acme/.well-known/acme-challenge
sudo install -m 0644 \
  deploy/vps/nginx-vibe-lead-qualifier-public-bootstrap.conf \
  /etc/nginx/conf.d/zz-vibe-lead-qualifier-public.conf
sudo nginx -t
sudo systemctl reload nginx
```

Перед каждым reload обязателен успешный `nginx -t`. Nginx останавливать не
нужно.

## Сертификат через webroot

Установите Certbot без Nginx installer и передайте собственный email. Не
публикуйте этот адрес в репозитории:

```bash
sudo apt-get update
sudo apt-get install -y certbot apache2-utils

sudo certbot certonly \
  --webroot \
  --webroot-path /var/www/vibe-lead-qualifier-acme \
  --domain vibe-lead-qualifier.88-218-169-93.sslip.io \
  --email '<ACME_EMAIL>' \
  --agree-tos \
  --no-eff-email
```

Команда использует только `certonly --webroot` и не редактирует Nginx.

## Basic Auth и финальный HTTPS-конфиг

Создайте отдельного demo-пользователя. `htpasswd` запросит пароль интерактивно,
поэтому он не попадёт в shell history:

```bash
sudo htpasswd -c /etc/nginx/vibe-lead-qualifier.htpasswd '<DEMO_USER>'
sudo chown root:www-data /etc/nginx/vibe-lead-qualifier.htpasswd
sudo chmod 0640 /etc/nginx/vibe-lead-qualifier.htpasswd
```

Замените bootstrap финальным конфигом по тому же пути:

```bash
sudo install -m 0644 \
  deploy/vps/nginx-vibe-lead-qualifier-public.conf \
  /etc/nginx/conf.d/zz-vibe-lead-qualifier-public.conf
sudo nginx -t
sudo systemctl reload nginx
```

Маршруты имеют следующую границу доступа:

- `/health` — публичный;
- `/webhooks/vibe` — публичный HTTP endpoint с HMAC внутри приложения;
- `/docs`, `/openapi.json`, `/demo`, `/demo/*` — Basic Auth;
- все остальные HTTPS-пути — `404`.

Конфиг ограничивает body размером 1 MiB, применяет отдельные rate limits к
webhook и demo, задаёт proxy timeouts `2s/15s/17s` и передаёт `Host`,
`X-Real-IP`, `X-Forwarded-For`, `X-Forwarded-Proto`. Basic Authorization не
передаётся приложению.

После установки защищённого proxy можно установить
`ENABLE_DEMO_ENDPOINTS=true` в `.env` и пересоздать только app-контейнер:

```bash
sudo docker compose -f deploy/vps/docker-compose.yml up -d --force-recreate app
```

Если старый `docker-compose 1.29` несовместим с текущим Docker при recreate,
данные остаются в bind mount, поэтому безопасный обход выглядит так:

```bash
sudo docker-compose -f deploy/vps/docker-compose.yml stop app
sudo docker-compose -f deploy/vps/docker-compose.yml rm -f app
sudo docker-compose -f deploy/vps/docker-compose.yml up -d app
```

## Автоматическое обновление сертификата

Установите deploy hook. Он не перезагрузит Nginx при неуспешном config test:

```bash
sudo install -m 0750 deploy/vps/certbot-deploy-hook.sh \
  /etc/letsencrypt/renewal-hooks/deploy/20-vibe-lead-qualifier-nginx
sudo systemctl enable --now certbot.timer
sudo certbot renew --dry-run --run-deploy-hooks --no-random-sleep-on-renew
```

Проверка сертификата:

```bash
openssl s_client \
  -connect vibe-lead-qualifier.88-218-169-93.sslip.io:443 \
  -servername vibe-lead-qualifier.88-218-169-93.sslip.io </dev/null 2>/dev/null |
  openssl x509 -noout -subject -ext subjectAltName -enddate
```

## Проверки после деплоя

```bash
curl -I http://vibe-lead-qualifier.88-218-169-93.sslip.io/health
curl --fail https://vibe-lead-qualifier.88-218-169-93.sslip.io/health
curl -o /dev/null -w '%{http_code}\n' \
  https://vibe-lead-qualifier.88-218-169-93.sslip.io/docs
curl -u '<DEMO_USER>' -o /dev/null -w '%{http_code}\n' \
  https://vibe-lead-qualifier.88-218-169-93.sslip.io/docs
```

Ожидаются соответственно redirect, `200`, `401` и `200`. Не указывайте Basic
Auth password прямо в командной строке: без `:<password>` curl запросит его
интерактивно.

Официальный бесплатный self-test запускайте с доверенной локальной машины, где
находятся API token и webhook secret:

```powershell
python -m vibe_lead_qualifier.cli webhook-self-test `
  https://vibe-lead-qualifier.88-218-169-93.sslip.io/webhooks/vibe
```

Команда не вызывает `/generate`. Регистрация постоянного webhook URL — отдельное
изменение настроек аккаунта и также выполняется локальным CLI.

## Обновление и rollback

Перед обновлением выполните тесты и credential scan, затем перестройте только
app-сервис. SQLite находится в `/opt/vibe-lead-qualifier/data` и не удаляется
при замене контейнера.

Для отката Nginx переместите только новый
`zz-vibe-lead-qualifier-public.conf` в закрытый backup-каталог, восстановите
предыдущую копию при необходимости, затем обязательно выполните `nginx -t` и
только после него reload. Существующие virtual hosts и firewall в этот workflow
не входят.

Cloudflare Quick Tunnel не является частью Compose-файла и не используется в
постоянном маршруте. В live-среде его резервный контейнер остановлен после
успешной внешней проверки HTTPS.
