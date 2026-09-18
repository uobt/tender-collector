"""Нормализация ParsedNotice → TenderRecord/AwardRecord/OrgRecord."""
from __future__ import annotations

from datetime import datetime, timezone

from .models import (AwardRecord, OrgRecord, ParsedNotice, TenderRecord,
                     dumps_compact)

PARSER_VERSION = "0.1.0"


def normalize_country(code: str | None) -> str | None:
    if not code:
        return None
    return code.strip().upper()[:2] or None


def notice_status(parsed: ParsedNotice, deadline_iso: str | None) -> str:
    """active/expired/unknown — без выдумок: expired только по уверенной дате."""
    if deadline_iso:
        try:
            deadline = datetime.fromisoformat(deadline_iso)
            if deadline.tzinfo is None:
                deadline = deadline.replace(tzinfo=timezone.utc)
            if deadline < datetime.now(timezone.utc):
                return "expired"
            return "active"
        except ValueError:
            pass
    return "unknown"


def org_key_for(parsed: ParsedNotice, side: str) -> tuple[str | None, str]:
    """Возвращает (source_org_key, confidence). Без ключа — name-country ключ."""
    explicit = (parsed.buyer_org_key if side == "buyer"
                else parsed.winner_org_key)
    if explicit:
        return str(explicit), "exact"
    name = parsed.buyer_name if side == "buyer" else parsed.winner_name
    country = parsed.buyer_country if side == "buyer" else parsed.winner_country
    if not name:
        return None, "none"
    key = f"nm:{name.strip().lower()}|{normalize_country(country) or '-'}"
    return key, "name_country"


def normalize(parsed: ParsedNotice, raw_ref: str | None
              ) -> tuple[TenderRecord | None, AwardRecord | None,
                         list[OrgRecord]]:
    """Одна ParsedNotice → (tender, award, orgs). Award только для is_award."""
    from .sources.ted import normalize_date_iso

    tender = award = None
    orgs: list[OrgRecord] = []

    buyer_key, buyer_conf = org_key_for(parsed, "buyer")
    winner_key, winner_conf = org_key_for(parsed, "winner")

    if not parsed.is_award:
        tender = TenderRecord(
            source=parsed.source,
            source_notice_id=parsed.source_notice_id,
            source_url=parsed.url,
            title=parsed.title,
            description_snippet=(parsed.extra.get("description") or
                                 None),
            cpv_codes=dumps_compact(parsed.cpv_codes),
            naics_code=parsed.naics_code,
            buyer_org_key=buyer_key,
            country=normalize_country(parsed.buyer_country),
            place_raw=parsed.place_raw,
            value_estimated=parsed.value,
            value_currency=parsed.currency,
            published_at=normalize_date_iso(parsed.published_at_raw),
            deadline_at=normalize_date_iso(parsed.deadline_at_raw),
            procedure_raw=parsed.extra.get("procedure"),
            raw_ref=raw_ref, parser_version=PARSER_VERSION)
        tender.notice_status = notice_status(tender, tender.deadline_at)
        tender.rehash()

    buyer_key, buyer_conf = org_key_for(parsed, "buyer")
    if parsed.is_award:
        awarded_at = (normalize_date_iso(parsed.awarded_at_raw)
                      or normalize_date_iso(parsed.published_at_raw))
        award = AwardRecord(
            source=parsed.source,
            source_award_id=parsed.source_notice_id,
            notice_id=str(parsed.extra.get("base_notice_id")
                          or parsed.source_notice_id),
            winner_org_key=winner_key,
            buyer_org_key=buyer_key,
            value_awarded=parsed.awarded_value,
            value_currency=parsed.awarded_currency,
            awarded_at=awarded_at,
            vendor_count=parsed.vendor_count,
            raw_ref=raw_ref)
        award.rehash()

    # организации: buyer для tender-записи, winner для award-записи
    if parsed.buyer_name and (buyer_key or not parsed.is_award):
        orgs.append(OrgRecord(
            source=parsed.source, source_org_key=buyer_key or "",
            name=parsed.buyer_name,
            country=normalize_country(parsed.buyer_country),
            address_raw=parsed.place_raw, org_type="buyer",
            key_confidence=buyer_conf))
    if parsed.winner_name:
        orgs.append(OrgRecord(
            source=parsed.source, source_org_key=winner_key or "",
            name=parsed.winner_name,
            country=normalize_country(parsed.winner_country),
            city=parsed.extra.get("winner_city"),
            address_raw=", ".join(str(x) for x in filter(None, (
                parsed.extra.get("winner_city"),
                parsed.extra.get("winner_state")))) or None,
            org_type="supplier", key_confidence=winner_conf))
    return tender, award, orgs
