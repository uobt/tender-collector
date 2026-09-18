"""CLI: doctor / collect / status / export."""
from __future__ import annotations

import argparse
import csv
import json
import os
import sys
from pathlib import Path

from . import __version__
from .config import load_spec
from .export import (AWARD_COLUMNS, ORG_COLUMNS, TENDER_COLUMNS, export_csv,
                     export_jsonl)
from .models import TenderSpec
from .rawstore import save_raw
from .scheduler import CONNECTORS, SOURCE_LIVE_STATUS, plan, run_source
from .storage import Store

ROOT = Path(__file__).resolve().parent.parent.parent
DEFAULT_DB = ROOT / "data" / "tenders.db"


def _load_env() -> None:
    """Простой stdlib-загрузчик .env из корня проекта (не перезаписывает уже
    установленные переменные окружения)."""
    env_path = ROOT / ".env"
    if not env_path.exists():
        return
    for line in env_path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key, value = key.strip(), value.strip().strip('"').strip("'")
        if key and key not in os.environ:
            os.environ[key] = value


def _store(path: str | None) -> Store:
    return Store(Path(path) if path else DEFAULT_DB)


def _utf8():
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):
            pass


def cmd_doctor(args) -> int:
    print(f"tender-collector {__version__}")
    print(f"python: {sys.version.split()[0]}")
    sam_key = bool(os.environ.get("SAM_GOV_API_KEY"))
    for code, (status, reason) in SOURCE_LIVE_STATUS.items():
        if code == "sam":
            status = "READY" if sam_key else status
        print(f"{code:5s}: {status} — {reason}")
    if args.config:
        spec = load_spec(Path(args.config))
        errors = spec.validate()
        print(f"config: {args.config} — "
              f"{'OK' if not errors else 'ОШИБКИ: ' + '; '.join(errors)}")
        for task in plan(spec):
            print(f"  план {task['source']:5s}: ≤{task['pages_max']} стр. × "
                  f"{task['page_size']}/стр., потолок "
                  f"{task['notices_max']} записей")
    print(f"база: {DEFAULT_DB}")
    return 0


def cmd_collect(args) -> int:
    spec = load_spec(Path(args.config))
    errors = spec.validate()
    if errors:
        print("Ошибки конфигурации:", "; ".join(errors))
        return 2
    if args.dry_run:
        print("DRY-RUN: сетевых запросов не будет")
        for task in plan(spec):
            print(f"  {task['source']:5s} {task['label']}: до "
                  f"{task['pages_max']} страниц, потолок "
                  f"{task['notices_max']} записей")
        return 0

    store = _store(args.db)
    any_live = False
    try:
        for code in spec.sources:
            run_id = store.start_run(
                "collect", json.dumps(
                    {"config": str(args.config), "mode": spec.mode,
                     "cpv": spec.cpv_codes}, ensure_ascii=False), code)
            connector = CONNECTORS[code](
                raw_saver=lambda doc, root=ROOT / "data": save_raw(root, doc))
            report = run_source(spec, code, connector, store, run_id, ROOT)
            print(f"{code}: status={report.status} "
                  f"stop={report.stop_reason} requests={report.requests} "
                  f"notices={report.notices} "
                  f"tenders(+{report.new_tenders}/~{report.updated_tenders}) "
                  f"awards(+{report.new_awards}/~{report.updated_awards})")
            if report.note:
                print(f"  примечание: {report.note}")
            if report.status == "completed":
                any_live = True
    finally:
        store.close()
    return 0 if any_live else 2


def cmd_status(args) -> int:
    store = _store(args.db)
    try:
        info = store.summary()
        print(f"тендеров: {info['tenders_total']}  присуждений: "
              f"{info['awards_total']}  организаций: {info['orgs_total']} "
              f"(поставщиков: {info['suppliers']})  связей: "
              f"{info['org_links']}")
        for source, counts in info["by_source"].items():
            print(f"  {source}: {counts}")
        for run in info["last_runs"]:
            print(f"  run#{run['id']} {run['kind']:8s} "
                  f"{run['source'] or '-':5s} {run['status']:10s} "
                  f"stop={run['stop_reason']} "
                  f"t+{run['new_tenders']}/~{run['updated_tenders']} "
                  f"a+{run['new_awards']}/~{run['updated_awards']}")
    finally:
        store.close()
    return 0


def cmd_export(args) -> int:
    store = _store(args.db)
    try:
        if args.what == "tenders":
            rows, columns = store.tenders(), TENDER_COLUMNS
        elif args.what == "awards":
            rows, columns = store.awards(), AWARD_COLUMNS
        else:
            rows, columns = store.orgs(), ORG_COLUMNS
        out = Path(args.out)
        if args.format == "jsonl":
            written = export_jsonl(rows, out)
        else:
            written = export_csv(rows, out, columns)
        print(f"экспортировано {written} строк → {out}")
    finally:
        store.close()
    return 0


def cmd_prune_raw(args) -> int:
    """Retention: удалить raw старше N дней (GDPR-минимизация хранения)."""
    import time
    root = ROOT / "data" / "raw"
    if not root.exists():
        print("raw-архива нет")
        return 0
    cutoff = time.time() - args.days * 86400
    removed = kept = 0
    for folder in root.glob("*/*"):
        if not folder.is_dir():
            continue
        for file in folder.glob("*.json.gz"):
            if file.stat().st_mtime < cutoff:
                file.unlink()
                removed += 1
            else:
                kept += 1
    print(f"retention {args.days} дн.: удалено {removed}, оставлено {kept}")
    return 0


def main(argv: list[str] | None = None) -> int:
    _utf8()
    _load_env()
    parser = argparse.ArgumentParser(prog="tendercollector")
    parser.add_argument("--db", default=str(DEFAULT_DB))
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("doctor")
    p.add_argument("--config")
    p.set_defaults(func=cmd_doctor)

    p = sub.add_parser("collect")
    p.add_argument("--config", required=True)
    p.add_argument("--dry-run", action="store_true")
    p.set_defaults(func=cmd_collect)

    p = sub.add_parser("status")
    p.set_defaults(func=cmd_status)

    p = sub.add_parser("export")
    p.add_argument("--what", default="orgs",
                   choices=["tenders", "awards", "orgs"])
    p.add_argument("--format", default="csv", choices=["csv", "jsonl"])
    p.add_argument("--out", required=True)
    p.set_defaults(func=cmd_export)

    p = sub.add_parser("prune-raw")
    p.add_argument("--days", type=int, default=90)
    p.set_defaults(func=cmd_prune_raw)

    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
