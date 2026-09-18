"""Планировщик: прогоны по источникам, локальные фильтры, честные статусы."""
from __future__ import annotations

from dataclasses import dataclass

from .models import SearchPage, TenderSpec
from .normalize import normalize
from .sources.base import Connector
from .sources.sam import SamConnector
from .sources.ted import TedConnector
from .sources.ukcf import UkCfConnector
from .storage import Store

CONNECTORS = {
    "ted": TedConnector,
    "ukcf": UkCfConnector,
    "sam": SamConnector,
}

SOURCE_LIVE_STATUS = {
    "ted": ("LIVE_VERIFIED", "анонимный POST 200 OK (пилот 2026-09-18)"),
    "ukcf": ("LIVE_VERIFIED", "анонимный OCDS GET 200 OK (пилот 2026-09-18)"),
    "sam": ("NEEDS_FREE_KEY", "бесплатный ключ sam.gov → env SAM_GOV_API_KEY"),
}


@dataclass
class RunReport:
    run_id: int
    source: str
    requests: int = 0
    notices: int = 0
    new_tenders: int = 0
    updated_tenders: int = 0
    new_awards: int = 0
    updated_awards: int = 0
    status: str = "running"
    stop_reason: str | None = None
    note: str | None = None
    skipped_unsupported: list | None = None


def plan(spec: TenderSpec) -> list[dict]:
    tasks = []
    for source in spec.sources:
        connector = CONNECTORS[source]
        pages = spec.max_pages_per_search
        tasks.append({
            "source": source, "label": connector.label,
            "pages_max": pages,
            "page_size": connector.page_size,
            "notices_max": spec.max_notices_per_source,
        })
    return tasks


def _local_filters(parsed, spec: TenderSpec) -> tuple[bool, list[str]]:
    """Локальные фильтры (не применённые серверно). Возвращает (keep, applied)."""
    applied = []
    if spec.keywords:
        title = (parsed.title or "").lower()
        if not any(word.lower() in title for word in spec.keywords):
            return False, applied
        applied.append("keywords:local")
    if spec.mode == "tenders" and parsed.is_award:
        return False, applied
    if spec.mode == "awards" and not parsed.is_award:
        return False, applied
    applied.append(f"mode:{spec.mode}:local" if spec.mode != "both" else "")
    return True, [a for a in applied if a]


def run_source(spec: TenderSpec, source_code: str, connector: Connector,
               store: Store, run_id: int, data_root, dry_run: bool = False
               ) -> RunReport:
    from .models import TransportError, utcnow
    from .rawstore import save_raw
    from .transports.http import HttpTransport

    report = RunReport(run_id=run_id, source=source_code)
    report.skipped_unsupported = []
    if source_code == "ted" and spec.countries_ted:
        report.skipped_unsupported.append(
            "countries_ted: серверный фильтр страны не проверен — "
            "страны НЕ применяются")
    if source_code in ("ted", "ukcf") and spec.naics_codes:
        report.skipped_unsupported.append(
            "naics_codes применим только к SAM")
    if source_code == "sam" and spec.cpv_codes:
        report.skipped_unsupported.append(
            "cpv_codes применим только к TED/UK CF")
    if source_code == "sam" and len(spec.naics_codes) > 1:
        report.skipped_unsupported.append(
            f"naics_codes: SAM принимает один код — применён только "
            f"{spec.naics_codes[0]}")

    if source_code == "sam" and not connector.api_key():
        report.status = "blocked"
        report.stop_reason = "needs_free_key"
        report.note = SOURCE_LIVE_STATUS["sam"][1]
        store.note_run(run_id, status="blocked", stop_reason="needs_free_key",
                       note=report.note)
        return report

    cursor: str | None = None
    pages = 0
    stop = None
    while pages < spec.max_pages_per_search and \
            report.notices < spec.max_notices_per_source and stop is None:
        try:
            page: SearchPage = connector.search(spec, cursor)
            report.requests += 1
        except TransportError as exc:
            report.status = "blocked" if exc.blocked else "failed"
            report.stop_reason = report.status
            report.note = str(exc)
            store.log_attempt(run_id, source_code, "transport-error",
                              exc.status, "http", None, str(exc)[:500])
            store.note_run(run_id, status=report.status,
                           stop_reason=report.stop_reason,
                           note=report.note[:500])
            return report

        store.log_attempt(run_id, source_code, "search", 200, "http", None,
                          None)
        if page.status == "failed":
            report.status, report.stop_reason = "failed", page.stop_reason
            store.note_run(run_id, status="failed",
                           stop_reason=page.stop_reason)
            return report

        rank = 0
        for parsed in page.records:
            if report.notices >= spec.max_notices_per_source:
                break
            keep, _applied = _local_filters(parsed, spec)
            if not keep:
                continue
            tender, award, orgs = normalize(parsed, page.raw_ref)
            if tender is not None:
                event = store.upsert_tender(tender)
                if event == "first_seen":
                    report.new_tenders += 1
                elif event == "updated":
                    report.updated_tenders += 1
            if award is not None:
                event = store.upsert_award(award)
                if event == "first_seen":
                    report.new_awards += 1
                elif event == "updated":
                    report.updated_awards += 1
            for org in orgs:
                store.upsert_org(org)
            store.record_hit(run_id, source_code,
                             parsed.source_notice_id, source_code,
                             pages, rank)
            rank += 1
            report.notices += 1

        pages += 1
        cursor = page.next_cursor
        if page.stop_reason == "no_results" or cursor is None:
            stop = "no_results" if page.stop_reason == "no_results" \
                else "exhausted"

    store.recompute_org_stats()
    report.status = "completed"
    report.stop_reason = report.stop_reason or (
        "capped_notices" if report.notices >= spec.max_notices_per_source
        else (stop or "capped_pages"))
    if report.skipped_unsupported:
        report.note = "; ".join(report.skipped_unsupported)
    store.note_run(run_id, status="completed", stop_reason=report.stop_reason,
                   requests=report.requests, notices=report.notices,
                   new_tenders=report.new_tenders,
                   updated_tenders=report.updated_tenders,
                   new_awards=report.new_awards,
                   updated_awards=report.updated_awards,
                   note=report.note)
    return report
