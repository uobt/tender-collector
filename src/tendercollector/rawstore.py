"""Raw-store: gzip JSON с метаданными; api_key вырезается из URL."""
from __future__ import annotations

import gzip
import json
import re
from pathlib import Path

from .models import RawDocument

SECRET_QS_RX = re.compile(r"([?&])(api_key|apikey|token|access_token)=[^&]*",
                          re.IGNORECASE)


def sanitized_url(url: str) -> str:
    return SECRET_QS_RX.sub(r"\1\2=REDACTED", url)


def save_raw(root: Path, doc: RawDocument) -> str:
    """Сохраняет raw до нормализации; возвращает относительный raw_ref."""
    day = doc.fetched_at.strftime("%Y-%m-%d")
    folder = Path(root) / "raw" / doc.source / day
    folder.mkdir(parents=True, exist_ok=True)
    name = f"{doc.fetched_at.strftime('%H%M%S')}_{doc.req_id}.json.gz"
    payload = {
        "meta": {**doc.meta(), "url": sanitized_url(doc.url)},
        "body": doc.content,
    }
    with gzip.open(folder / name, "wt", encoding="utf-8") as handle:
        json.dump(payload, handle, ensure_ascii=False)
    return str(Path("raw") / doc.source / day / name).replace("\\", "/")


def read_raw(root: Path, raw_ref: str) -> RawDocument:
    with gzip.open(Path(root) / raw_ref, "rt", encoding="utf-8") as handle:
        payload = json.load(handle)
    meta, body = payload["meta"], payload["body"]
    from datetime import datetime
    fetched = datetime.fromisoformat(meta["fetched_at"])
    return RawDocument(
        source=meta["source"], url=meta["url"], status=meta["status"],
        content=body, mime=meta.get("mime", ""),
        fetched_at=fetched, req_id=meta.get("request_id", ""),
        error=meta.get("error"))
