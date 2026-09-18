"""Загрузка спецификации из JSON (YAML при установленном pyyaml)."""
from __future__ import annotations

import json
from pathlib import Path

from .models import TenderSpec, TransportConfig


def _spec_from_dict(data: dict) -> TenderSpec:
    data = data or {}
    transport_raw = data.get("transport") or {}
    return TenderSpec(
        sources=list(data.get("sources", ["ted"])),
        mode=data.get("mode", "both"),
        countries_ted=list(data.get("countries_ted", [])),
        cpv_codes=list(data.get("cpv_codes", [])),
        naics_codes=list(data.get("naics_codes", [])),
        keywords=list(data.get("keywords", [])),
        posted_within_days=int(data.get("posted_within_days", 30)),
        min_value_eur=float(data.get("min_value_eur", 0) or 0),
        max_notices_per_source=int(data.get("max_notices_per_source", 100)),
        max_pages_per_search=int(data.get("max_pages_per_search", 10)),
        transport=TransportConfig.from_dict(transport_raw),
    )


def load_spec(path: Path) -> TenderSpec:
    path = Path(path)
    text = path.read_text(encoding="utf-8")
    if path.suffix.lower() in (".yaml", ".yml"):
        try:
            import yaml  # optional dependency
        except ImportError as exc:
            raise RuntimeError(
                "pyyaml не установлен: используйте JSON-конфиг или "
                "uv sync --extra yaml") from exc
        data = yaml.safe_load(text)
    else:
        data = json.loads(text)
    if not isinstance(data, dict):
        raise ValueError(f"конфиг должен быть объектом: {path}")
    return _spec_from_dict(data)
