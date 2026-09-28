# marketplace-seller-collector

Сбор базы продавцов маркетплейсов (Ozon, Wildberries, Яндекс.Маркет) с enrichment через DaData.

## Возможности

- выбор маркетплейса, городов и категорий через веб-интерфейс;
- фоновый сбор через RQ (web-процесс не блокируется);
- дедупликация продавцов (`marketplace + external_seller_id`, fallback по URL);
- контакты (телефоны, email, сайт) с нормализацией и provenance (marketplace / dadata);
- enrichment по ИНН через DaData (единственный внешний enrichment-источник);
- город продавца — по юридическому адресу (строковый матчинг, без GIS);
- таблица результатов с фильтрами, история запусков, прогресс job'а (HTMX polling);
- экспорт в XLSX (текущий job / все продавцы);
- Telegram-ссылка формируется технически из мобильного номера (не признак наличия аккаунта).
- discovery и detail разделены: seller references сохраняются в `CollectionJobSeller`, затем выполняются detail/enrichment;
- пагинация идёт до конца выдачи или `COLLECT_MAX_SELLERS` (0 — без программного лимита), с checkpoint/resume;
- `BLOCKED`, `RATE_LIMITED`, `TEMPORARY_ERROR`, `PARSE_ERROR` не превращаются в успешный пустой job;
- endurance-проверка: `python manage.py endurance_marketplaces --marketplace ozon --query "наушники" --target 100`.

## Запуск через Docker

```bash
cp .env.example .env          # заполните DADATA_TOKEN при необходимости
docker compose up --build     # поднимает web, worker, postgres, redis
docker compose exec web python manage.py createsuperuser
```

Приложение: http://localhost:8000

## Локальный запуск без Docker

Требуется Python 3.11+ (проверено на 3.14), Redis для фоновых задач.

```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env          # DATABASE_URL можно не задавать — тогда SQLite
export RQ_ASYNC=0             # выполнять задачи в web-процессе без Redis (только dev)
python manage.py migrate
python manage.py createsuperuser
python manage.py runserver
```

## Тесты и smoke-тесты

```bash
python manage.py test core                          # все тесты, без live-интернета
python manage.py smoke_marketplaces --limit 5       # live-проверка всех маркетплейсов
python manage.py smoke_marketplaces --limit 5 --marketplace yandex_market
python manage.py endurance_marketplaces --marketplace wildberries --query "наушники" --target 1000
python manage.py project_health
python manage.py project_health --live
```

## Автоматический повторный сбор

Сервис `scheduler` раз в сутки создаёт по одному запуску для каждого
маркетплейса со всеми активными категориями и городами. Он не дублирует
queued/running jobs. При временной ошибке запускается resume после
`retry_after`; при anti-bot блокировке resume выполняется только после
обновления файла/записи сессии, поэтому источник не получает бесконечные 403.

```env
AUTO_COLLECT_TARGET=5000
AUTO_COLLECT_INTERVAL=86400
AUTO_COLLECT_POLL=60
AUTO_COLLECT_USERNAME=admin
```

Разовый запуск планировщика для проверки:

```bash
python manage.py auto_collect
```

Планировщик работает постоянно после `docker compose up -d`. Он переживает
перезапуск контейнера, а данные и checkpoint хранятся в PostgreSQL. CAPTCHA
автоматически не обходится: blocked job ждёт новой пользовательской сессии.

Сервис `catalog-sync` ежедневно обновляет справочники: города берутся из
официального ОКТМО Росстата, категории Wildberries — из публичного меню,
категории Яндекс.Маркета — из ежедневно обновляемого XLS Яндекса. Для Ozon,
чей endpoint блокирует серверный IP, используется открытый snapshot ответов
официального category endpoint. Разовый ручной запуск:

```bash
python manage.py sync_catalogs
```

В форме запуска доступны отдельные пункты «Все города» и «Все категории».

## Категории и города

Начальные города (Уфа, Челябинск, Екатеринбург) и базовые категории засеяны миграцией.
Города и категории добавляются/редактируются через Django Admin (`/admin/`).
Для Wildberries есть синк категорий из дерева меню: `Category` можно создать из
`WildberriesAdapter().get_categories()`.

## Рабочий flow и cookies маркетплейсов

1. `docker compose up --build`
2. войдите в UI;
3. выберите marketplace, города и категории;
4. запустите сбор;
5. при блокировке job получает `paused`, причина и checkpoint видны в UI;
6. если источник заблокирован, получите пользовательскую сессию командой bootstrap ниже и нажмите `Продолжить сбор`;
7. экспортируйте XLSX конкретного job.

Bootstrap открывает обычный пользовательский Chromium один раз. Основной crawl
после этого всегда выполняется через curl_cffi, не через браузер:

Проще всего обновить сессию из UI: «Справочники → Сессии → Открыть Chromium»,
пройти проверку в защищённом noVNC-окне и нажать «Сохранить cookies». Chromium
работает отдельным постоянным контейнером; основной crawler браузер не использует.

```bash
python manage.py bootstrap_sessions --marketplace ozon
python manage.py bootstrap_sessions --marketplace wildberries
python manage.py bootstrap_sessions --marketplace yandex_market
```

В открывшемся окне пройдите captcha/verification вручную и нажмите Enter в
терминале. Cookies сохраняются в `runtime/sessions/*.json` (этот каталог не
коммитится), автоматически подхватываются соответствующим адаптером.
Рекомендуемый способ — запуск на host Python (окно браузера откроется локально,
сессия попадёт в `./runtime/sessions`, которая в Docker примонтирована и в `web`,
и в `worker` — пересборка образа не нужна). Если bootstrap всё же запускается
в контейнере, headed-режиму нужен доступный display (например, `DISPLAY=:0` либо
проброс X11/ssh -X); `--headless` предназначен только для сессий без ручной
проверки. Альтернативно можно задать Cookies в `.env` (строка Cookie-заголовка
или JSON-словарь):

```env
OZON_COOKIES=
WB_COOKIES=
YANDEX_MARKET_COOKIES=
```

Без cookies job не объявляется успешным пустым результатом: источник получает
статус `blocked`/`rate_limited`, checkpoint сохраняется, а job можно продолжить
из страницы задачи после добавления cookies. Реальные cookies и токены не коммитятся в git.

## Enrichment (DaData)

`DADATA_TOKEN` в `.env`. Если у продавца есть ИНН — данные дополняются из DaData
(название, юр. адрес, телефоны, email), без перезаписи данных маркетплейса.
Повторный enrichment пропускается в течение `DADATA_TTL_DAYS` (по умолчанию 30).

## Известные ограничения

- Яндекс.Маркет: список продавцов собирается из выдачи поиска/категорий (supplierId,
  рейтинг); страница продавца (`/seller/<id>/`) защищена и без cookies отдаёт 404 —
  имя/ИНН заполняются частично.
- Без cookies/рабочей сессии live discovery не гарантируется; protection pages не считаются пустой выдачей.
- Live target 100/1000/5000/10000 не заявляется без фактического успешного прогона. Текущие результаты — в `MASS_COLLECTION_STATUS.md`.
- Не гарантируется 100% продавцов/контактов и обход капчи.
