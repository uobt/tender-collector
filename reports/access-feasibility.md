# Access feasibility — tender-collector (пилот 2026-09-18)

Метод: `reports/pilot/pilot_access.py`, по одному минимальному запросу на
источник, сырые ответы в `reports/pilot/*.json`.

## TED (EU/EEA) — ДОСТУПЕН анонимно

- `POST https://api.ted.europa.eu/v3/notices/search` → HTTP 200 без ключа;
- запрос: CPV 79342100/79342000/79413000, publication-date ≥ 30 дней, limit 5;
- вернулось 5 notice, из них 1 с winner-name (award-формы реально содержат
  победителей, но не все записи);
- заголовки — мультиязычные словари (`{"hun": ...}`): нормализация обязана
  выбирать `eng` либо первый язык (паттерн уже отработан в ted_outreach_parser);
- `total`/`took` в ответе пилота не заполнены — reported_total=unknown,
  не обещать полноту выдачи.

## UK Contracts Finder — ДОСТУПЕН анонимно (OCDS)

- `GET /Published/Notices/OCDS/Search?publishedFrom&publishedTo&stages=tender,award&limit=5`
  → HTTP 200 без аутентификации;
- вернулось 5 OCDS-releases с tender.title; `links.next` присутствует —
  курсорная пагинация работает;
- OAuth нужен только для публикации notices, не для чтения — подтверждено
  фактическим анонимным запросом.

## SAM.gov (US) — NEEDS_FREE_KEY

- Официальная документация (open.gsa.gov) изучена: GET opportunities/v2/search,
  бесплатный ключ со страницы Account Details на sam.gov;
- без ключа любой запрос → 401 «No api_key was supplied» — проверок не делали,
  чтобы не тратить попытки; коннектор реализуется за key-gate и активируется
  после выдачи ключа пользователем.

## Выводы для архитектуры

1. TED и UK CF реализуются как live-коннекторы немедленно.
2. SAM — full-коннектор с gate: doctor показывает NEEDS_FREE_KEY.
3. HTML-интерфейсы площадок не трогаем: оба API покрывают потребность.
4. Rate limit анонимного TED уточняется при первом полноценном прогоне
   (задержка по умолчанию 1 rps консервативна).
