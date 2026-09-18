"""Экспорт CSV (UTF-8 BOM, formula-guard) и JSONL: tenders/awards/orgs."""
from __future__ import annotations

import csv
import json
from pathlib import Path
from typing import Any, Iterable, Sequence

FORMULA_PREFIXES = ("=", "+", "-", "@", "\t", "\r")

TENDER_COLUMNS = [
    "source", "source_notice_id", "source_url", "title",
    "country", "place_raw", "cpv_codes", "naics_code",
    "buyer_org_key", "value_estimated", "value_currency",
    "published_at", "deadline_at", "notice_status", "procedure_raw",
    "description_snippet", "first_seen_at", "last_seen_at",
]
AWARD_COLUMNS = [
    "source", "source_award_id", "notice_id", "winner_org_key",
    "buyer_org_key", "value_awarded", "value_currency", "awarded_at",
    "vendor_count", "first_seen_at", "last_seen_at",
]
ORG_COLUMNS = [
    "source", "source_org_key", "name", "country", "city", "address_raw",
    "org_type", "key_confidence", "wins_count", "buyer_contracts_count",
    "total_awarded_json", "last_award_at", "first_seen_at", "last_seen_at",
]


def _guard_cell(value: Any) -> Any:
    if isinstance(value, str) and value.startswith(FORMULA_PREFIXES):
        return "'" + value
    return value


def _as_dict(row: Any) -> dict:
    return row if isinstance(row, dict) else dict(row)


def export_csv(rows: Iterable[Any], out_path: Path,
               columns: Sequence[str]) -> int:
    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    written = 0
    with out_path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(list(columns))
        for row in rows:
            record = _as_dict(row)
            writer.writerow([_guard_cell(record.get(col)) for col in columns])
            written += 1
    return written


def export_jsonl(rows: Iterable[Any], out_path: Path) -> int:
    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    written = 0
    with out_path.open("w", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(_as_dict(row), ensure_ascii=False,
                                    default=str) + "\n")
            written += 1
    return written
