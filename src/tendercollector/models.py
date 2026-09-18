"""Модели данных: спецификация прогона, сырой документ, распарсенные записи."""
from __future__ import annotations

import hashlib
import json
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def request_id() -> str:
    return uuid.uuid4().hex[:12]


class SourceBlocked(Exception):
    """Источник недоступен: политика/отсутствие ключа/блокировка."""

    def __init__(self, source: str, reason: str):
        super().__init__(f"{source}: {reason}")
        self.source, self.reason = source, reason


class TransportError(Exception):
    def __init__(self, message: str, status: int | None = None,
                 blocked: bool = False):
        super().__init__(message)
        self.status, self.blocked = status, blocked


@dataclass
class TransportConfig:
    request_delay_seconds: float = 1.0
    timeout_seconds: int = 30
    max_retries: int = 3

    @staticmethod
    def from_dict(data: dict | None) -> "TransportConfig":
        data = data or {}
        return TransportConfig(
            request_delay_seconds=float(data.get("request_delay_seconds", 1.0)),
            timeout_seconds=int(data.get("timeout_seconds", 30)),
            max_retries=int(data.get("max_retries", 3)))


@dataclass
class TenderSpec:
    sources: list[str] = field(default_factory=lambda: ["ted"])
    mode: str = "both"            # tenders | awards | both
    countries_ted: list[str] = field(default_factory=list)
    cpv_codes: list[str] = field(default_factory=list)
    naics_codes: list[str] = field(default_factory=list)
    keywords: list[str] = field(default_factory=list)
    posted_within_days: int = 30
    min_value_eur: float = 0.0
    max_notices_per_source: int = 100
    max_pages_per_search: int = 10
    transport: TransportConfig = field(default_factory=TransportConfig)

    def validate(self) -> list[str]:
        errors = []
        known = {"ted", "sam", "ukcf"}
        if not self.sources:
            errors.append("sources пуст")
        for source in self.sources:
            if source not in known:
                errors.append(f"неизвестный источник: {source}")
        if self.mode not in ("tenders", "awards", "both"):
            errors.append(f"mode должен быть tenders|awards|both, получен {self.mode}")
        if self.posted_within_days <= 0 or self.posted_within_days > 3650:
            errors.append("posted_within_days должен быть 1..3650")
        if self.max_notices_per_source <= 0:
            errors.append("max_notices_per_source должен быть > 0")
        if self.max_pages_per_search <= 0:
            errors.append("max_pages_per_search должен быть > 0")
        if self.transport.request_delay_seconds < 0:
            errors.append("request_delay_seconds не может быть отрицательным")
        if self.transport.timeout_seconds < 5:
            errors.append("timeout_seconds должен быть >= 5")
        return errors


@dataclass
class RawDocument:
    source: str
    url: str
    status: int
    content: str
    mime: str = "application/json"
    fetched_at: datetime = field(default_factory=utcnow)
    req_id: str = field(default_factory=request_id)
    error: str | None = None

    @property
    def ok(self) -> bool:
        return 200 <= self.status < 300 and self.error is None

    def meta(self) -> dict:
        return {"source": self.source, "url": self.url, "status": self.status,
                "mime": self.mime, "fetched_at": self.fetched_at.isoformat(),
                "request_id": self.req_id, "error": self.error}


@dataclass
class ParsedNotice:
    """Источник-независимое представление одной записи до нормализации."""
    source: str
    source_notice_id: str
    is_award: bool = False
    title: str | None = None
    title_lang_used: str | None = None
    buyer_name: str | None = None
    buyer_country: str | None = None
    buyer_org_key: str | None = None      # ueiSAM / ted-org-id / ocid-party id
    winner_name: str | None = None
    winner_country: str | None = None
    winner_org_key: str | None = None
    cpv_codes: list[str] = field(default_factory=list)
    naics_code: str | None = None
    value: float | None = None
    currency: str | None = None
    awarded_value: float | None = None
    awarded_currency: str | None = None
    published_at_raw: str | None = None
    deadline_at_raw: str | None = None
    awarded_at_raw: str | None = None
    vendor_count: int | None = None
    url: str | None = None
    form_type: str | None = None
    place_raw: str | None = None
    extra: dict = field(default_factory=dict)


@dataclass
class SearchPage:
    records: list[ParsedNotice] = field(default_factory=list)
    next_cursor: str | None = None
    status: str = "success"        # success/partial/empty/blocked/failed
    stop_reason: str | None = None
    reported_total: int | None = None
    raw_ref: str | None = None


@dataclass
class OrgRecord:
    source: str
    source_org_key: str
    name: str
    country: str | None = None
    city: str | None = None
    address_raw: str | None = None
    org_type: str = "buyer"        # buyer | supplier | both
    website: str | None = None
    key_confidence: str = "exact"  # exact | name_country
    first_seen_at: datetime = field(default_factory=utcnow)
    last_seen_at: datetime = field(default_factory=utcnow)

    def row(self) -> dict:
        return {
            "source": self.source, "source_org_key": self.source_org_key,
            "name": self.name, "country": self.country, "city": self.city,
            "address_raw": self.address_raw, "org_type": self.org_type,
            "website": self.website, "key_confidence": self.key_confidence,
            "first_seen_at": self.first_seen_at.isoformat(),
            "last_seen_at": self.last_seen_at.isoformat(),
        }


@dataclass
class TenderRecord:
    source: str
    source_notice_id: str
    source_url: str | None = None
    title: str | None = None
    description_snippet: str | None = None
    cpv_codes: str = "[]"          # JSON list
    naics_code: str | None = None
    buyer_org_key: str | None = None
    country: str | None = None
    place_raw: str | None = None
    value_estimated: float | None = None
    value_currency: str | None = None
    published_at: str | None = None
    deadline_at: str | None = None
    notice_status: str = "unknown"
    procedure_raw: str | None = None
    raw_ref: str | None = None
    parser_version: str = "0.1.0"
    first_seen_at: datetime = field(default_factory=utcnow)
    last_seen_at: datetime = field(default_factory=utcnow)
    content_hash: str = ""

    def basis(self) -> str:
        return "|".join(str(x) for x in (
            self.title, self.buyer_org_key, self.country, self.value_estimated,
            self.value_currency, self.deadline_at, self.cpv_codes))

    def rehash(self) -> "TenderRecord":
        self.content_hash = hashlib.sha256(
            self.basis().encode("utf-8")).hexdigest()[:16]
        return self

    def row(self) -> dict:
        return {
            "source": self.source,
            "source_notice_id": self.source_notice_id,
            "source_url": self.source_url, "title": self.title,
            "description_snippet": self.description_snippet,
            "cpv_codes": self.cpv_codes, "naics_code": self.naics_code,
            "buyer_org_key": self.buyer_org_key, "country": self.country,
            "place_raw": self.place_raw,
            "value_estimated": self.value_estimated,
            "value_currency": self.value_currency,
            "published_at": self.published_at, "deadline_at": self.deadline_at,
            "notice_status": self.notice_status,
            "procedure_raw": self.procedure_raw,
            "raw_ref": self.raw_ref, "parser_version": self.parser_version,
            "first_seen_at": self.first_seen_at.isoformat(),
            "last_seen_at": self.last_seen_at.isoformat(),
            "content_hash": self.content_hash,
        }


@dataclass
class AwardRecord:
    source: str
    source_award_id: str
    notice_id: str                 # (source, source_notice_id) ссылка
    winner_org_key: str | None = None
    buyer_org_key: str | None = None
    value_awarded: float | None = None
    value_currency: str | None = None
    awarded_at: str | None = None
    vendor_count: int | None = None
    raw_ref: str | None = None
    first_seen_at: datetime = field(default_factory=utcnow)
    last_seen_at: datetime = field(default_factory=utcnow)
    content_hash: str = ""

    def rehash(self) -> "AwardRecord":
        basis = "|".join(str(x) for x in (
            self.winner_org_key, self.buyer_org_key, self.value_awarded,
            self.value_currency, self.awarded_at))
        self.content_hash = hashlib.sha256(
            basis.encode("utf-8")).hexdigest()[:16]
        return self

    def row(self) -> dict:
        return {
            "source": self.source,
            "source_award_id": self.source_award_id,
            "notice_id": self.notice_id,
            "winner_org_key": self.winner_org_key,
            "buyer_org_key": self.buyer_org_key,
            "value_awarded": self.value_awarded,
            "value_currency": self.value_currency,
            "awarded_at": self.awarded_at, "vendor_count": self.vendor_count,
            "raw_ref": self.raw_ref,
            "first_seen_at": self.first_seen_at.isoformat(),
            "last_seen_at": self.last_seen_at.isoformat(),
            "content_hash": self.content_hash,
        }


def dumps_compact(value) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))
