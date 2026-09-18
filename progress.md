# tender-collector — прогресс

Цель: сборщик международных госзакупок по ТЗ
`tender_contracts_collector_GLM53_spec.md` (корень workspace).

## Состояние: COMPLETE — все три источника LIVE_VERIFIED

- [x] TED: 36 тендеров + 20 awards live; идемпотентность +0.
- [x] UK CF: 3+3 live; rate limit 12/120с закрыт задержкой 12с.
- [x] SAM.gov: ключ в .env; 3/3 записей сечения NAICS 541613 сверены
      вручную против raw; buyer_org_key-баг найден сверкой и исправлен.
- [x] Тесты: 27/27 OK. Повторные прогоны: +0/~0 по всем источникам.
- [x] README + acceptance.md обновлены.

## Финальные данные

- БД: data/tenders.db — 42 тендера, 23 awards, 83 организации.
- Экспорт: data/export/{tenders,awards,orgs}.csv.
- Raw: data/raw/<source>/<date>/ (gzip JSON, ключи вырезаны).

## Известные ограничения (README)

- countries_ted не применяется (синтаксис не проверен).
- keywords — локальный фильтр по title (SAM: 1 из 3, UK: ~6 из 1000).
- TED fields v3 без deadline/procedure; SAM — один naics за запрос,
  только федеральные США.

## Обновление 2026-09-18 (вечер): без NAICS + GDPR

- naics_codes убран из config: поток SAM без фильтра = **25 957 записей
  за 30 дней** (в т.ч. Award Notice с победителями и ueiSAM).
- Суточная квота SAM-ключа исчерпана (~5 запросов/день): 429 с
  nextAccessTime 2026-09-19 00:00 UTC; завтра collect продолжит.
  delay_override=6с добавлен.
- Добавлен `prune-raw --days N` — retention raw (ТЗ п.8, GDPR).
- 27/27 тестов; TED/UK идемпотентность +0/~0 подтверждена снова.
- README: раздел GDPR (юрлица vs контактные лица, минимизация, retention).

## Возможные следующие шаги (не требуются для ТЗ)

- Завтра: повторный collect без naics — набрать US-поток в пределах квоты
  (по 4-5 запросов/день, ~400-500 записей/день ceiling 100 на прогон —
  поднять max_notices_per_source до 100 не проблема).
- Кросс-источниковые org_links (сейчас candidate-логика есть, связей 0).
