"""SAM.gov (US federal): официальный opportunities/v2/search за key-gate.

Документация: open.gsa.gov/api/get-opportunities-public-api.
- обязательные postedFrom/postedTo MM/dd/yyyy, диапазон ≤ 1 года;
- limit ≤ 1000, пагинация offset; api_key в query (вырезается из raw);
- ptype=a — Award Notice; award.amount приходит числом и строкой;
- ueiSAM — стабильный ID поставщика; fullParentPathCode — ключ покупателя;
- 404 = «No Data found» (пустая выдача), не транспортная ошибка;
- uiLink без роли контрактного офицера ведёт на 404 — в export не включаем.
"""
from __future__ import annotations

import json
import os
import urllib.parse
from datetime import datetime, timedelta, timezone

from ..models import (ParsedNotice, RawDocument, SearchPage, SourceBlocked,
                      TenderSpec)
from .base import Connector

SEARCH_URL = "https://api.sam.gov/opportunities/v2/search"
MAX_RANGE_DAYS = 364


def _mmdd(date) -> str:
    return date.strftime("%m/%d/%Y")


def _to_float(value) -> float | None:
    if value is None or value == "":
        return None
    try:
        return float(str(value).replace(",", "").strip())
    except (TypeError, ValueError):
        return None


class SamConnector(Connector):
    code, label = "sam", "SAM.gov (US federal)"
    hosts = {"api.sam.gov"}
    page_size = 100
    live_allowed = True  # фактически решается наличием ключа (gate ниже)
    # live-факт 2026-09-18: суточная квота публичного ключа исчерпывается
    # (~5 запросов/день для роли) → экономим запросы консервативной паузой
    delay_override = 6.0

    def api_key(self) -> str | None:
        return os.environ.get("SAM_GOV_API_KEY") or None

    def search(self, spec: TenderSpec, cursor: str | None) -> SearchPage:
        key = self.api_key()
        if not key:
            raise SourceBlocked(
                self.code,
                "NEEDS_FREE_KEY: бесплатный ключ со страницы Account Details "
                "на sam.gov (env SAM_GOV_API_KEY)")
        transport = self.make_transport(spec)
        offset = int(cursor or 0)
        today = datetime.now(timezone.utc).date()
        since = today - timedelta(days=min(spec.posted_within_days,
                                           MAX_RANGE_DAYS))
        params = {
            "api_key": key,
            "postedFrom": _mmdd(since),
            "postedTo": _mmdd(today),
            "limit": self.page_size,
            "offset": offset,
        }
        if spec.naics_codes:
            # SAM принимает один ncode — первый; остальные помечаются в run
            params["ncode"] = spec.naics_codes[0]
        if spec.mode == "awards":
            params["ptype"] = "a"
        doc = transport.get(f"{SEARCH_URL}?{urllib.parse.urlencode(params)}")
        doc.source = self.code
        page = self.parse_search(doc)
        self.keep_raw(doc, page)
        # SAM: у каждого item ровно одна запись; полная страница → следующая
        if len(page.records) >= self.page_size:
            page.next_cursor = str(offset + self.page_size)
        else:
            page.next_cursor = None
        return page

    def parse_search(self, doc: RawDocument) -> SearchPage:
        if doc.status == 404:  # задокументировано: No Data found
            return SearchPage(status="empty", stop_reason="no_results")
        try:
            data = json.loads(doc.content)
        except json.JSONDecodeError as exc:
            return SearchPage(status="failed", stop_reason=f"bad json: {exc}")
        items = data.get("opportunitiesData") or []
        records: list[ParsedNotice] = []
        for item in items:
            notice_id = str(item.get("noticeId") or "").strip()
            if not notice_id:
                continue
            award = item.get("award") or {}
            awardee = award.get("awardee") or {}
            location = awardee.get("location") or {}
            pop = item.get("placeOfPerformance") or {}
            is_award = bool(award) or str(item.get("type", "")) == "Award Notice"
            records.append(ParsedNotice(
                source=self.code, source_notice_id=notice_id,
                is_award=is_award,
                title=item.get("title"),
                buyer_name=item.get("fullParentPathName"),
                buyer_org_key=item.get("fullParentPathCode"),
                buyer_country="US",
                winner_name=awardee.get("name"),
                winner_org_key=awardee.get("ueiSAM"),
                winner_country="US",
                cpv_codes=[],
                naics_code=str(item.get("naicsCode") or "") or None,
                value=_to_float(award.get("amount")),
                currency="USD" if award.get("amount") is not None else None,
                awarded_value=_to_float(award.get("amount")) if is_award else None,
                awarded_currency="USD" if is_award and award.get("amount") is not None else None,
                published_at_raw=item.get("postedDate"),
                awarded_at_raw=award.get("date") if is_award else None,
                place_raw=", ".join(
                    str(x) for x in [
                        (pop.get("city", {}) or {}).get("name"),
                        (pop.get("state", {}) or {}).get("code"),
                        (location.get("city", {}) or {}).get("name"),
                    ] if x) or None,
                form_type=item.get("type"),
                extra={"solicitation": item.get("solicitationNumber"),
                       "set_aside": item.get("typeOfSetAsideDescription"),
                       "winner_city": (location.get("city", {}) or {}).get("name"),
                       "winner_state": (location.get("state", {}) or {}).get("code")}))
        page = SearchPage(records=records)
        page.status = "success" if records else "empty"
        total = data.get("totalRecords")
        if isinstance(total, int):
            page.reported_total = total
        if not items:
            page.stop_reason = "no_results"
        return page
