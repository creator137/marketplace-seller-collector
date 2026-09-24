# Инструкция по развёртыванию marketplace-seller-collector

## Состав системы

| Компонент | Назначение |
|---|---|
| web | Django + Gunicorn (порт 8000) |
| worker | RQ-воркер фоновых задач сбора |
| postgres | БД PostgreSQL 16 |
| redis | Очередь задач RQ |

## Требования

- Docker 24+ и Docker Compose v2 — для серверного/контейнерного запуска;
- либо Python 3.11+ (проверено на 3.14), Redis и PostgreSQL — для локального запуска без Docker.

---

## 1. Развёртывание через Docker (рекомендуется)

### 1.1 Подготовка конфигурации

```bash
cd marketplace-seller-collector
cp .env.example .env
```

Заполните в `.env`:

```env
SECRET_KEY=<случайная строка 50+ символов>   # python -c "import secrets; print(secrets.token_urlsafe(50))"
DEBUG=0
ALLOWED_HOSTS=ваш-домен,IP-сервера
DADATA_TOKEN=<API-ключ DaData>
DADATA_SECRET=<секретный ключ DaData>
OZON_COOKIES=            # необязательно, для сбора с Ozon
WB_COOKIES=              # необязательно, для сбора с Wildberries
YANDEX_MARKET_COOKIES=   # необязательно, для Яндекс.Маркета
```

Значения `DATABASE_URL` и `REDIS_URL` в `.env` для Docker не нужны —
compose подставляет их сам.

### 1.2 Запуск

```bash
docker compose up -d --build
docker compose exec web python manage.py createsuperuser
```

Проверка: http://localhost:8000 — страница логина.

### 1.3 Полезные команды

```bash
docker compose logs -f web        # логи web
docker compose logs -f worker     # логи фонового сбора
docker compose restart worker     # перезапуск воркера
docker compose down               # остановка (данные Postgres остаются в volume)
docker compose exec web python manage.py test core   # тесты
```

---

## 2. Локальный запуск без Docker

### 2.1 Зависимости

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

Нужен Redis (очередь). Без PostgreSQL используется SQLite автоматически.

### 2.2 Конфигурация

```bash
cp .env.example .env
```

Минимум для локальной разработки: `DADATA_TOKEN`/`DADATA_SECRET` для обогащения.
`DATABASE_URL` можно оставить пустым — тогда SQLite.

### 2.3 Инициализация и запуск

```bash
python manage.py migrate
python manage.py createsuperuser
python manage.py runserver
```

### 2.4 Фоновые задачи

Вариант А — без Redis (только разработка): в `.env` задайте

```env
RQ_ASYNC=0
```

Задачи выполняются в web-процессе, redis не нужен.

Вариант Б — с Redis (как в проде): `RQ_ASYNC=1` и отдельным терминалом:

```bash
python manage.py rqworker default
```

---

## 3. Развертывание на сервере (VPS)

1. Установите Docker и Compose плагин.
2. Скопируйте проект на сервер (`git clone` или rsync).
3. Выполните шаги 1.1–1.2.
4. Пробросьте порт 8000 или поставьте reverse-proxy (nginx/caddy) перед ним.
   Сromium-проксирование не обязательно: достаточно `proxy_pass http://127.0.0.1:8000;`
   с заголовками `Host`, `X-Forwarded-Proto`.
5. В `ALLOWED_HOSTS` добавьте домен сервера.

Обновление версии:

```bash
git pull
docker compose up -d --build
docker compose exec web python manage.py migrate
```

---

## 4. Проверка работоспособности

```bash
# тесты (без live-интернета)
python manage.py test core

# live-проверка адаптеров маркетплейсов
python manage.py smoke_marketplaces --limit 5
python manage.py smoke_marketplaces --limit 5 --marketplace yandex_market
```

E2E-сценарий в UI:

1. Войдите под superuser.
2. «Новый сбор» → маркетплейс → города → категории → «Начать сбор».
3. На странице задачи видно статус/прогресс (обновляется сам).
4. «Продавцы» — таблица результатов, «Export all sellers» — XLSX.

---

## 5. Администрирование

- `/admin/` — города (можно добавлять без правки кода), категории, продавцы, пользователи.
- Начальные города (Уфа, Челябинск, Екатеринбург) и базовые категории сидируются миграцией.
- Синк категорий Wildberries из дерева меню — через shell:

```bash
python manage.py shell -c "
from core.adapters import get_adapter
from core.models import Category, Marketplace
for c in get_adapter('wildberries').get_categories():
    Category.objects.get_or_create(marketplace=Marketplace.WB, external_id=c['external_id'], defaults={'title': c['title']})
"
```

## 6. Безопасность

- `.env` не коммитится (в `.gitignore`); реальные cookies/токены хранить только там.
- В проде: `DEBUG=0`, случайный `SECRET_KEY`, корректный `ALLOWED_HOSTS`.
- Открывайте наружу только порт web (8000); postgres/redis оставьте во внутренней сети compose.
