"""TED (EU/EEA): анонимный POST-поиск v3/notices/search.

Факты формата — из reports/pilot/ted_sample.json:
- notice-title: {lang: str}; buyer-name/winner-name: {lang: [str]};
- publication-date: ISO с offset ("2026-09-18+02:00");
- estimated-value-proc: строка; total-value: число;
- form-type: "competition" | "result" | "dir-awa-pre" | ...;
- links.html.ENG — готовый URL карточки;
- buyer-name может нести суффикс "_<org-id>" — извлекаем как org key.
"""
from __future__ import annotations

import re
from datetime import datetime, timedelta, timezone

from ..models import (ParsedNotice, RawDocument, SearchPage, TenderSpec)
from .base import Connector

ORG_SUFFIX_RX = re.compile(r"^(?P<name>.+)_(?P<orgid>\d+)$")

FIELDS = [
    "publication-number", "notice-title", "buyer-name", "winner-name",
    "publication-date", "form-type", "estimated-value-proc",
    "estimated-value-cur-proc", "total-value", "total-value-cur",
]
# Проверено 400-й ошибкой live: deadline-date и procedure-type не входят
# в поддерживаемые значения fields TED v3 — не запрашивать.


def pick_lang(value, lang_used: list | None = None) -> str | None:
    """{lang: str|[str]} → eng-строка или первый доступный язык."""
    if value is None:
        return None
    if isinstance(value, dict):
        chosen = value.get("eng")
        used = "eng"
        if chosen is None and value:
            used = next(iter(value))
            chosen = value[used]
        if lang_used is not None:
            lang_used.append(used)
        return pick_lang(chosen, lang_used)
    if isinstance(value, list):
        return "; ".join(str(x) for x in value if x) or None
    return str(value) or None


def to_float(value) -> float | None:
    if value is None or value == "":
        return None
    try:
        return float(str(value).replace(",", "").replace(" ", ""))
    except (TypeError, ValueError):
        return None


def normalize_date_iso(raw: str | None) -> str | None:
    """'2026-09-18+02:00' | '20260918' → UTC ISO; None → None."""
    if not raw:
        return None
    text = str(raw).strip()
    try:
        if len(text) == 8 and text.isdigit():
            return datetime.strptime(text, "%Y%m%d").replace(
                tzinfo=timezone.utc).isoformat()
        parsed = datetime.fromisoformat(text)
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed.astimezone(timezone.utc).isoformat()
    except ValueError:
        return None


def split_buyer(raw_name: str | None) -> tuple[str | None, str | None]:
    """'National Transport Authority_1149' → (name, 'ted-org:1149')."""
    if raw_name is None:
        return None, None
    match = ORG_SUFFIX_RX.match(raw_name.strip())
    if match and match.group("name").strip():
        return match.group("name").strip(), f"ted-org:{match.group('orgid')}"
    return raw_name.strip(), None


class TedConnector(Connector):
    code, label = "ted", "TED (EU/EEA)"
    hosts = {"api.ted.europa.eu"}
    page_size = 20

    def build_payload(self, spec: TenderSpec, page: int) -> dict:
        since = (datetime.now(timezone.utc)
                 - timedelta(days=spec.posted_within_days)).strftime("%Y%m%d")
        parts = []
        if spec.cpv_codes:
            cpv = " OR ".join(f"classification-cpv = {c}"
                              for c in spec.cpv_codes)
            parts.append(f"({cpv})")
        parts.append(f"publication-date >= {since}")
        query = " AND ".join(parts) + " SORT BY publication-date DESC"
        return {"query": query, "fields": FIELDS, "limit": self.page_size,
                "page": page, "scope": "ALL", "onlyLatestVersions": True}

    def search(self, spec: TenderSpec, cursor: str | None) -> SearchPage:
        transport = self.make_transport(spec)
        page_no = int(cursor or 1)
        doc = transport.post_json(
            "https://api.ted.europa.eu/v3/notices/search",
            self.build_payload(spec, page_no))
        doc.source = self.code
        page = self.parse_search(doc)
        self.keep_raw(doc, page)
        if page.next_cursor is not None:
            page.next_cursor = str(page_no + 1)
        return page

    def parse_search(self, doc: RawDocument) -> SearchPage:
        import json
        try:
            data = json.loads(doc.content)
        except json.JSONDecodeError as exc:
            return SearchPage(status="failed", stop_reason=f"bad json: {exc}")
        notices = data.get("notices") or []
        records: list[ParsedNotice] = []
        for notice in notices:
            pub_num = str(notice.get("publication-number") or "").strip()
            if not pub_num:
                continue
            title_lang: list = []
            title = pick_lang(notice.get("notice-title"), title_lang)
            buyer_raw = pick_lang(notice.get("buyer-name"))
            buyer_name, buyer_key = split_buyer(buyer_raw)
            winner = pick_lang(notice.get("winner-name"))
            form_type = notice.get("form-type")
            is_award = bool(winner) or form_type == "result"
            links = notice.get("links") or {}
            url = None
            html_links = links.get("html") if isinstance(links, dict) else None
            if isinstance(html_links, dict):
                url = html_links.get("ENG") or next(
                    iter(html_links.values()), None)
            value = to_float(notice.get("estimated-value-proc"))
            currency = notice.get("estimated-value-cur-proc")
            if value is None:
                value = to_float(notice.get("total-value"))
                currency = pick_lang(
                    notice.get("total-value-cur")) or currency
            cpv = notice.get("classification-cpv") or []
            if isinstance(cpv, dict):
                cpv = list(cpv)
            records.append(ParsedNotice(
                source=self.code, source_notice_id=pub_num,
                is_award=is_award, title=title,
                title_lang_used=title_lang[0] if title_lang else None,
                buyer_name=buyer_name, buyer_org_key=buyer_key,
                winner_name=winner,
                cpv_codes=[str(c) for c in cpv],
                value=value, currency=currency,
                awarded_value=value if is_award else None,
                awarded_currency=currency if is_award else None,
                published_at_raw=str(notice.get("publication-date") or "")
                or None,
                deadline_at_raw=str(notice.get("deadline-date") or "") or None,
                awarded_at_raw=(str(notice.get("publication-date") or "")
                                if is_award else None) or None,
                url=url, form_type=form_type,
                extra={"procedure": notice.get("procedure-type")}))
        page = SearchPage(records=records)
        page.status = "success" if records else "empty"
        if not notices:
            page.stop_reason = "no_results"
        elif len(notices) >= self.page_size:
            page.next_cursor = "more"   # search() заменит на page_no + 1
        return page
