"""Live-пилот доступности TED и UK Contracts Finder (ТЗ п.13 шаг 2).

Сохраняет сырые ответы в reports/pilot/ и печатает краткий вердикт.
Запросов мало (по одному на источник), сетевые задержки соблюдены.
"""
from __future__ import annotations

import datetime as dt
import json
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent  # tender-collector/
PILOT = ROOT / "reports" / "pilot"
PILOT.mkdir(parents=True, exist_ok=True)

UA = "tender-collector-pilot/0.1 (feasibility check)"
TODAY = dt.date.today()
SINCE_30D = (TODAY - dt.timedelta(days=30)).strftime("%Y%m%d")

TED_ENDPOINT = "https://api.ted.europa.eu/v3/notices/search"
TED_FIELDS = [
    "publication-number", "notice-title", "buyer-name", "winner-name",
    "publication-date", "form-type", "estimated-value-proc",
    "estimated-value-cur-proc", "total-value", "total-value-cur",
]
TED_CPV = ["79342100", "79342000", "79413000"]

UKCF_ENDPOINT = ("https://www.contractsfinder.service.gov.uk"
                 "/Published/Notices/OCDS/Search")


def http_json(url: str, payload: dict | None = None):
    data = json.dumps(payload).encode("utf-8") if payload is not None else None
    headers = {"User-Agent": UA, "Accept": "application/json"}
    if payload is not None:
        headers["Content-Type"] = "application/json"
    request = urllib.request.Request(url, data=data, headers=headers,
                                     method="POST" if payload else "GET")
    with urllib.request.urlopen(request, timeout=60) as response:
        return response.status, json.loads(response.read().decode("utf-8"))


def pilot_ted():
    cpv_q = " OR ".join(f"classification-cpv = {c}" for c in TED_CPV)
    payload = {
        "query": f"({cpv_q}) AND publication-date >= {SINCE_30D} "
                 f"SORT BY publication-date DESC",
        "fields": TED_FIELDS,
        "limit": 5,
        "page": 1,
        "scope": "ALL",
        "onlyLatestVersions": True,
    }
    try:
        status, data = http_json(TED_ENDPOINT, payload)
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", "replace")[:500]
        return {"ok": False, "error": f"HTTP {exc.code}: {body}"}
    notices = data.get("notices", [])
    (PILOT / "ted_sample.json").write_text(
        json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
    with_winner = [n for n in notices if n.get("winner-name")]
    return {
        "ok": True, "http": status, "took": data.get("took"),
        "total": data.get("total"), "returned": len(notices),
        "with_winner": len(with_winner),
        "sample_titles": [str(n.get("notice-title")) [:60] for n in notices[:3]],
    }


def pilot_ukcf():
    params = urllib.parse.urlencode({
        "publishedFrom": (TODAY - dt.timedelta(days=7)).isoformat(),
        "publishedTo": TODAY.isoformat(),
        "limit": 5,
        "stages": "tender,award",
    })
    try:
        status, data = http_json(f"{UKCF_ENDPOINT}?{params}")
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", "replace")[:500]
        return {"ok": False, "error": f"HTTP {exc.code}: {body}"}
    except urllib.error.URLError as exc:
        return {"ok": False, "error": f"URLError: {exc.reason}"}
    (PILOT / "ukcf_sample.json").write_text(
        json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
    releases = data.get("releases", [])
    return {
        "ok": True, "http": status,
        "uri": data.get("uri"),
        "returned_releases": len(releases),
        "links_next": bool(data.get("links", {}).get("next")),
        "sample_titles": [
            str((r.get("tender") or {}).get("title", ""))[:60]
            for r in releases[:3]],
    }


if __name__ == "__main__":
    print("== TED (EU) pilot ==")
    print(json.dumps(pilot_ted(), ensure_ascii=False, indent=1))
    print("== UK Contracts Finder OCDS pilot ==")
    print(json.dumps(pilot_ukcf(), ensure_ascii=False, indent=1))
