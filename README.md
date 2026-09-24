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
```

## Категории и города

Начальные города (Уфа, Челябинск, Екатеринбург) и базовые категории засеяны миграцией.
Города и категории добавляются/редактируются через Django Admin (`/admin/`).
Для Wildberries есть синк категорий из дерева меню: `Category` можно создать из
`WildberriesAdapter().get_categories()`.

## Cookies маркетплейсов

Ozon и Wildberries отдают данные только при наличии валидных browser-cookies.
Задаются в `.env` (строка Cookie-заголовка или JSON-словарь):

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
- Ozon/WB без cookies не собирают данные (антибот); парсеры покрыты fixture-тестами.
- Не гарантируется 100% продавцов/контактов маркетплейса и обход капчи.
