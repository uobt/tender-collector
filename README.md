# tender-collector 0.1.0

Сборщик международных госзакупок и присуждённых контрактов из официальных
открытых источников для B2B-аутрича. ТЗ: `../tender_contracts_collector_GLM53_spec.md`.

## Источники и статусы

| Источник | Охват | Статус | Доступ |
|---|---|---|---|
| TED | ЕС/ЕЭЗ | **LIVE_VERIFIED** | анонимный POST, ключи не нужны |
| UK Contracts Finder | Великобритания | **LIVE_VERIFIED** | анонимный OCDS GET; rate limit ≈ 12 запросов/120 сек — задержка 12 с встроена |
| SAM.gov | США (федеральные) | **LIVE_VERIFIED** | официальный API; ключ пользователя в `.env` (`SAM_GOV_API_KEY`), автозагрузка CLI, вырезается из raw/логов. **Суточная квота ключа** (~5 запросов/день, live-факт 2026-09-18: 429 с nextAccessTime на след. день 00:00 UTC); поток без NAICS = 25 957 записей/30 дн. |

Условия использования: TED — публичный сервис ЕС; UK CF — Open Government
Licence v3 (указана в каждом OCDS-ответе); SAM.gov — данные правительства США.
При использовании данных в аутриче сохраняйте ссылки на первоисточники
(в выгрузках есть `source_url` для TED/UK).

## Команды (Windows)

```text
cd tender-collector
set PYTHONPATH=src
%USERPROFILE%\.local\bin\uv.exe run --python 3.12 python -X utf8 -m tendercollector doctor --config config/searches.example.json
... -m tendercollector collect --config config/searches.example.json --dry-run
... -m tendercollector collect --config config/searches.example.json
... -m tendercollector status
... -m tendercollector export --what tenders --out data/export/tenders.csv
... -m tendercollector export --what awards --out data/export/awards.csv
... -m tendercollector export --what orgs   --out data/export/orgs.csv
... -m tendercollector export --what orgs   --format jsonl --out data/export/orgs.jsonl
```

Тесты: `uv run --python 3.12 python -m unittest discover -s tests` (27 тестов, без сети).
Retention raw: `... -m tendercollector prune-raw --days 90` (GDPR-минимизация).

## Конфигурация (`config/searches.example.json`)

- `sources`: ted / ukcf / sam;
- `mode`: tenders | awards | both;
- `cpv_codes` — серверный фильтр TED; `naics_codes` — SAM;
- `keywords` — **локальный** фильтр по заголовку (см. ограничения);
- `posted_within_days`, `max_notices_per_source`, `max_pages_per_search`;
- `transport`: задержка/таймаут/ретраи.

## Данные

SQLite `data/tenders.db`: `tenders`, `awards`, `organizations`
(с `wins_count`, `total_awarded_json` по валютам, `last_award_at`),
`org_links`, `observations`, `runs`, `run_hits`, `request_attempts`.
Raw-ответы (gzip JSON) — `data/raw/<source>/<date>/`, api_key вырезается.

Дедупликация: `(source, source_notice_id)`, `(source, source_award_id)`,
организации — `(source, source_org_key)`; без явного ключа организации —
ключ `nm:<имя>|<страна>` с `key_confidence=name_country`.

## GDPR и персональные данные

- Нормализованная модель и экспорты содержат только данные **юридических
  лиц** (названия, суммы, даты, города, классификации) — GDPR регулирует
  данные физических лиц и на них не распространяется напрямую.
- Контактные лица (UK `contactPoint`, SAM `pointOfContact`) в raw-ответах
  присутствуют, но **не извлекаются** в модель и экспорты (минимизация).
  Raw — технический аудит-архив; retention: `prune-raw --days N`.
- Данные закупок ЕС публикуются **обязательно по закону** (Directive
  2014/24/EU, прозрачность рынка) — обработка соответствует цели публикации.
- Прямые письма конкретным должностным лицам из контактов закупок — уже
  персональные данные: нужны законное основание (legitimate interest),
  информирование, opt-out. Роль-based адреса компаний (info@, office@)
  мягче, но требуют opt-out по ePrivacy/национальным правилам.

## Честные ограничения

1. `countries_ted` НЕ применяется: серверный синтаксис фильтра страны не
   проверен; поля страны в ответе нет. Указано в каждом отчёте прогона.
2. `keywords` — локальный фильтр по title; на UK CF за 30 дней из ~1000
   релизов через фильтр «marketing» прошло 6. Уберите keywords для полного сбора.
3. TED: deadline и процедура НЕ запрашиваются — поля не входят в
   поддерживаемые `fields` v3 (проверено HTTP 400; см. ted.py).
4. TED total/estimated значения могут быть дробными или в разных валютах —
   суммирование между валютами не выполняется (JSON по валютам).
5. SAM.gov: live-приёмка выполнена (3/3 записей сечения NAICS 541613 за
   30 дней сверены вручную против raw — большего API за период не отдаёт);
   один naics-код за запрос; только федеральные США (не штаты).
6. Пропажа записи из выдачи не закрывает тендер; повторный прогон без
   изменений не создаёт событий (проверено live: +0/~0).

## Структура

```text
src/tendercollector/  cli.py config.py models.py scheduler.py storage.py
                      rawstore.py normalize.py export.py
       sources/       base.py ted.py ukcf.py sam.py
       transports/    http.py (GET/POST, allowlist, retry, budget)
tests/                fixtures (формат с реальных ответов) + 26 тестов
reports/              access-feasibility.md, acceptance.md, pilot/
data/                 tenders.db, raw/, export/
```
