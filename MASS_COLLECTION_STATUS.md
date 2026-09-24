# Mass collection status

Фактическая live-проверка выполнена 2026-09-24 из текущей среды; endurance smoke
запущен с target=100 на каждой площадке. Тесты на 1 000/5 000/10 000 продавцов
не запускались как успешные: источники
в этой среде заблокированы или сетевой доступ к ним нестабилен. Mock/fixture-тесты
не считаются live-доказательством.

## Ozon

- query: `наушники`
- discovered: 0; endurance: 2 requests, 2×403, 0.59 req/s
- blocked: да, оба JSON endpoint'а вернули HTTP 403
- fallback: реализован `entrypoint-api.bx` → `composer-api.bx`
- limiting factor: доступность сети/сессии Ozon

## Wildberries

- query: `наушники`
- discovered: 0; endurance: 1 request, network timeout (в предыдущем smoke также был HTTP 403)
- blocked: подтверждён предыдущим live smoke ответом HTTP 403
- cookies: нужны валидные browser cookies/session
- limiting factor: защита endpoint без cookies

## Yandex Market

- query: `наушники`
- discovered: 0; endurance: 1×HTTP 200, содержимое protection page без `productSnippet`
- blocked: да, распознано по содержимому страницы
- cookies: вероятно нужны для текущей сессии; detail endpoint отдельно деградирует в partial
- limiting factor: protection page

Unit/fixture tests проходили; production-scale live target не заявляется без фактического
успешного запуска.
