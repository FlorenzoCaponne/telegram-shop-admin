# Premium Telegram Shop — FastAPI + aiogram 3 + полная CMS-админка

Асинхронный магазин цифровых товаров в Telegram с веб-админкой, в которой
настраивается **всё без кода**: тексты, кнопки, emoji, изображения, структура
экранов, каталог, склад, заказы, платежи, промокоды, рассылки, роли.

## Стек

| Компонент | Технология |
|-----------|------------|
| Бэкенд | FastAPI (async), Uvicorn |
| Бот | aiogram 3 (polling локально / webhook на VPS) |
| Админка | Jinja2 + HTMX + Tailwind (тёмная тема, адаптив) |
| База | PostgreSQL + SQLAlchemy 2 (async) + Alembic |
| Кеш | Redis (+ L1 in-process, автоинвалидация по namespace) |
| Платежи | Platega (СБП QR — код 2, Криптовалюта — код 13) + stub-провайдер |
| Языки | RU / EN (переключаемые тексты в админке) |

Нигде нет `time.sleep()`, `requests` и синхронных запросов — всё асинхронно
(`httpx.AsyncClient`, `AsyncSession`, `asyncio`). Колбеки бота отвечают мгновенно.

## Путь покупателя

КАТЕГОРИЯ → ТОВАР → ЦЕНА → 💳 ОПЛАТИТЬ → ПЛАТЕЖ → АВТОВЫДАЧА

Баланса и корзины нет — один товар = один заказ = один платёж.

## Структура

```
app/
  main.py            точка входа: lifespan, /health, /api, вебхуки, воркеры
  models.py          все таблицы и enum'ы
  seed.py            первичное наполнение (тексты, кнопки, блоки, админ)
  core/              config, logging, cache (Redis), security (Argon2, Fernet, CSRF, роли)
  db/                база и сессии
  bot/               aiogram: роутеры, middlewares, рендерер экранов из БД
  payments/          абстракция провайдера + Platega + stub
  services/          бизнес-логика: catalog, orders, payments, promo, users, cms,
                     broadcast, stats, audit, defaults
  admin/             веб-админка (routes + templates + static)
migrations/          Alembic
deploy/              nginx.conf, tgshop.service, backup.sh, update.sh, run_local.sh
Dockerfile, docker-compose.yml
```

## Запуск на локальном ПК

Требуется Python 3.11+, PostgreSQL 14+, Redis.

```bash
cp .env.example .env       # впишите BOT_TOKEN, DATABASE_URL, SECRET_KEY,
                           # ADMIN_BOOTSTRAP_PASSWORD, ключи Platega
bash deploy/run_local.sh   # venv + зависимости + миграции + uvicorn --reload
```

Или вручную:

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
alembic upgrade head
uvicorn app.main:app --reload --port 8000
```

Проверка:

- Админка: http://localhost:8000/admin (логин из `ADMIN_BOOTSTRAP_LOGIN`)
- Health: http://localhost:8000/health
- API: http://localhost:8000/api

Локально бот работает в `BOT_MODE=polling` — домен и HTTPS не нужны.
Для проверки оплаты без боевых ключей поставьте `PAYMENT_PROVIDER=stub`:
счёт создаётся локально, страница `/payments/sandbox/{transaction_id}` позволяет
«оплатить» или отменить транзакцию и проверить автовыдачу.

## Запуск на VPS (Docker, рекомендуется)

```bash
# 1. Подготовка
git clone <ваш-репо> /opt/tgshop && cd /opt/tgshop
cp .env.example .env && nano .env
#    APP_ENV=production
#    BASE_URL=https://ваш-домен
#    BOT_MODE=webhook
#    DATABASE_URL=postgresql+asyncpg://tgshop:пароль@postgres:5432/tgshop
#    REDIS_URL=redis://redis:6379/0

# 2. Домен в nginx
sed -i 's/example.com/ваш-домен/g' deploy/nginx.conf

# 3. Сертификат Let's Encrypt (первый раз)
mkdir -p deploy/certbot/www deploy/certbot/conf
docker compose up -d nginx
docker compose run --rm certbot certonly --webroot -w /var/www/certbot \
  -d ваш-домен --email ваш@email --agree-tos --no-eff-email

# 4. Старт
docker compose up -d --build
docker compose logs -f app
```

Вебхук Telegram устанавливается автоматически на старте при `BOT_MODE=webhook`.

Вариант без Docker: `deploy/tgshop.service` (systemd) + системные PostgreSQL/Redis/nginx.

### Обновление и бэкапы

```bash
bash deploy/update.sh    # бэкап → сборка → миграции → рестарт → health-check
bash deploy/backup.sh    # дамп БД + медиа в ./backups (ротация 14 дней)
```

Cron для ежедневного бэкапа:

```
0 4 * * * /opt/tgshop/deploy/backup.sh >> /var/log/tgshop-backup.log 2>&1
```

## Админ-панель

| Раздел | Что делает |
|--------|-------------|
| Сводка | выручка, заказы, конверсия, новые пользователи (HTMX-автообновление) |
| Категории / Товары | дерево категорий, цены, описания по языкам, тип выдачи |
| Склад | массовая загрузка позиций, остатки, выданное |
| Заказы | фильтры, карточка, ручная выдача, отмена |
| Платежи | транзакции, сверка статуса в Platega, журнал вебхуков, выгрузка Excel |
| Промокоды | процент/фикс, лимиты, сроки, привязка к товарам |
| Пользователи | поиск, блокировка, история заказов |
| Рассылки | сегменты, подсчёт аудитории, кнопки, старт/пауза/отмена, прогресс |
| Конструктор экранов | блоки (заголовок/текст/картинка/кнопки/разделитель), порядок, предпросмотр |
| Дизайн | колонки кнопок, валюта, формат цены, разметка, удаление предыдущих сообщений |
| Тексты / Кнопки / Emoji / Изображения | любая надпись бота меняется без кода, для RU и EN |
| Настройки | магазин, Telegram, платежи (методы, ключи, TTL), лимиты бота |
| Администраторы | роли super_admin / admin / manager, смена пароля, блокировка |
| Логи действий | кто, когда, что изменил, с IP |

Защита админки: Argon2id-пароли, серверные сессии, CSRF на всех POST-формах,
блокировка после неудачных попыток входа, шифрование секретов в БД (Fernet),
права по ролям на каждый раздел, аудит-лог.

## Платежи Platega

- Создание: `POST /transaction/process` (`paymentMethod`, `paymentDetails`, `description`,
  `return`, `failedUrl`, `payload`, `metadata.userId/userName`).
- Статус: `GET /transaction/{id}` — источник истины. Фоновый воркер опрашивает
  незавершённые платежи каждые `PAYMENT_POLL_INTERVAL` секунд.
- Вебхук: `POST /webhook/platega` — всегда отвечает `200 OK`, идемпотентен
  (дедупликация по `event_key`), события видны в админке.
- Выгрузка: `POST /transaction/export/excel` → ссылка на файл (кнопка в разделе Платежи).
- Статусы: `PENDING`, `CONFIRMED`, `CANCELED`, `CHARGEBACKED`; TTL счёта 15 минут,
  после истечения заказ автоматически отменяется, резерв со склада снимается.

В кабинете Platega укажите Callback URL: `https://ваш-домен/webhook/platega`.

### ⚠️ Безопасность ключей

MerchantId и API-ключ, переданные в переписке, считайте скомпрометированными —
перевыпустите их в личном кабинете и вносите только в `.env` или в раздел
«Настройки → Платежи» (там они шифруются). `.env` в гит не коммитить.

## Фоновые процессы

- `payment_poll_worker` — сверка статусов платежей и автовыдача.
- `expire_orders_worker` — отмена просроченных заказов.
- возобновление незавершённых рассылок после рестарта.

## Частые проблемы

| Симптом | Причина / решение |
|---------|-------------------|
| Бот не отвечает локально | проверьте `BOT_TOKEN` и `BOT_MODE=polling` |
| Бот не отвечает на VPS | `BASE_URL` с https, сертификат валиден, `BOT_MODE=webhook` |
| Ошибка входа в админку | задайте `ADMIN_BOOTSTRAP_PASSWORD` и перезапустите (seed создаст админа) |
| Платежи в stub-режиме | не заданы MerchantId/секрет — внесите их в Настройках → Платежи |
| Изменения в админке не видны в боте | кеш сбрасывается автоматически; проверьте доступность Redis |
