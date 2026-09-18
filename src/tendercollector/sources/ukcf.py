"""UK Contracts Finder: анонимный OCDS Search (проверено пилотом).

Факты формата — из reports/pilot/ukcf_sample.json:
- releases[]: ocid, id, date, tag[tender|award|awardUpdate];
- tender: {id, title, description, status, classification(CPV), value, tenderPeriod.endDate};
- parties[]: {id (GB-CFS-…), name, address, roles[buyer|supplier]} — стабильные org key;
- awards[]: {id, date, value, suppliers[{id, name}], documents[].url};
- пагинация: links.next — полный URL (проходит host-allowlist).
"""
from __future__ import annotations

import json
import urllib.parse
from datetime import datetime, timedelta, timezone

from ..models import ParsedNotice, RawDocument, SearchPage, TenderSpec
from .base import Connector

SEARCH_URL = "https://www.contractsfinder.service.gov.uk/Published/Notices/OCDS/Search"


def _iso(value) -> str | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value))
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed.astimezone(timezone.utc).isoformat()
    except ValueError:
        return None


def _party(parties: list, role: str) -> dict | None:
    for party in parties or []:
        if role in (party.get("roles") or []):
            return party
    return None


class UkCfConnector(Connector):
    code, label = "ukcf", "UK Contracts Finder"
    hosts = {"www.contractsfinder.service.gov.uk"}
    page_size = 100
    # live-факт 2026-09-18: «Rate limit of 12 exceeded. Please retry after
    # 120 seconds» → безопаснее 1 запрос / 12 сек
    delay_override = 12.0

    def build_url(self, spec: TenderSpec, cursor: str | None) -> str:
        if cursor:  # полный links.next URL с сервера
            return cursor
        today = datetime.now(timezone.utc).date()
        since = today - timedelta(days=spec.posted_within_days)
        stage = {"tenders": "tender", "awards": "award"}.get(
            spec.mode, "tender,award")
        query = urllib.parse.urlencode({
            "publishedFrom": since.isoformat(),
            "publishedTo": today.isoformat(),
            "stages": stage,
            "limit": self.page_size,
        })
        return f"{SEARCH_URL}?{query}"

    def search(self, spec: TenderSpec, cursor: str | None) -> SearchPage:
        transport = self.make_transport(spec)
        doc = transport.get(self.build_url(spec, cursor))
        doc.source = self.code
        page = self.parse_search(doc)
        self.keep_raw(doc, page)
        return page

    def parse_search(self, doc: RawDocument) -> SearchPage:
        try:
            data = json.loads(doc.content)
        except json.JSONDecodeError as exc:
            return SearchPage(status="failed", stop_reason=f"bad json: {exc}")
        releases = data.get("releases") or []
        records: list[ParsedNotice] = []
        for release in releases:
            base_id = str(release.get("id") or release.get("ocid") or "")
            if not base_id:
                continue
            tender = release.get("tender") or {}
            parties = release.get("parties") or []
            buyer = _party(parties, "buyer")
            buyer_addr = (buyer or {}).get("address") or {}
            classification = tender.get("classification") or {}
            value_obj = tender.get("value") or {}
            period = tender.get("tenderPeriod") or {}
            notice_url = None
            for award in release.get("awards") or []:
                for document in award.get("documents") or []:
                    if document.get("url"):
                        notice_url = document.get("url")
                        break

            common = dict(
                title=tender.get("title"),
                buyer_name=(buyer or {}).get("name") or
                (release.get("buyer") or {}).get("name"),
                buyer_org_key=(buyer or {}).get("id"),
                buyer_country="GB",
                place_raw=(buyer_addr.get("locality")
                           or buyer_addr.get("countryName")),
                cpv_codes=[classification["id"]] if classification.get("id")
                else [],
                value=value_obj.get("amount"),
                currency=value_obj.get("currency"),
                published_at_raw=release.get("date"),
                deadline_at_raw=period.get("endDate"),
                url=notice_url,
                form_type=",".join(release.get("tag") or []),
                extra={"tender_id": tender.get("id"),
                       "tender_status": tender.get("status"),
                       "procedure": tender.get("procurementMethodDetails"),
                       "description": tender.get("description"),
                       "base_notice_id": base_id},
            )

            if not release.get("awards"):
                # чистый tender-release: одна запись
                records.append(ParsedNotice(
                    source=self.code, source_notice_id=base_id, **common))
                continue

            # award-релиз: tender-часть + по записи на каждый award
            records.append(ParsedNotice(
                source=self.code, source_notice_id=base_id, **common))
            for award in release.get("awards") or []:
                award_id = str(award.get("id") or "")
                if not award_id:
                    continue
                value_obj = award.get("value") or {}
                supplier = None
                for sup in award.get("suppliers") or []:
                    supplier = sup
                    break
                records.append(ParsedNotice(
                    source=self.code,
                    source_notice_id=f"{base_id}#{award_id}",
                    is_award=True,
                    winner_name=(supplier or {}).get("name"),
                    winner_org_key=(supplier or {}).get("id"),
                    winner_country="GB",
                    awarded_value=value_obj.get("amount"),
                    awarded_currency=value_obj.get("currency"),
                    awarded_at_raw=award.get("date"),
                    vendor_count=len(award.get("suppliers") or []),
                    extra={"base_notice_id": base_id,
                           "award_id": award_id,
                           "procedure": common["extra"]["procedure"],
                           "description": common["extra"]["description"]},
                    **{k: v for k, v in common.items()
                       if k not in ("extra",)}))

        page = SearchPage(records=records)
        page.status = "success" if records else "empty"
        links = data.get("links") or {}
        page.next_cursor = links.get("next")
        if not releases:
            page.stop_reason = "no_results"
        return page
