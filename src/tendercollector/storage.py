"""SQLite-хранилище: runs, tenders, awards, organizations, org_links, наблюдения."""
from __future__ import annotations

import json
import sqlite3
from contextlib import closing
from pathlib import Path

from .models import AwardRecord, OrgRecord, TenderRecord

SCHEMA = """
create table if not exists runs (
  id integer primary key autoincrement,
  kind text not null, spec text, source text,
  status text default 'running', stop_reason text, note text,
  requests integer default 0, notices integer default 0,
  new_tenders integer default 0, updated_tenders integer default 0,
  new_awards integer default 0, updated_awards integer default 0,
  started_at text, finished_at text);

create table if not exists run_hits (
  run_id integer, source text, source_notice_id text,
  filter_label text, page integer, rank integer,
  seen_at text);
create index if not exists idx_hits_run on run_hits(run_id);

create table if not exists tenders (
  source text not null, source_notice_id text not null,
  source_url text, title text, description_snippet text,
  cpv_codes text, naics_code text, buyer_org_key text,
  country text, place_raw text,
  value_estimated real, value_currency text,
  published_at text, deadline_at text, notice_status text,
  procedure_raw text, raw_ref text, parser_version text,
  first_seen_at text, last_seen_at text, content_hash text,
  primary key (source, source_notice_id));

create table if not exists awards (
  source text not null, source_award_id text not null,
  notice_id text, winner_org_key text, buyer_org_key text,
  value_awarded real, value_currency text, awarded_at text,
  vendor_count integer, raw_ref text,
  first_seen_at text, last_seen_at text, content_hash text,
  primary key (source, source_award_id));

create table if not exists organizations (
  source text not null, source_org_key text not null,
  name text, country text, city text, address_raw text,
  org_type text, website text, key_confidence text,
  wins_count integer default 0, buyer_contracts_count integer default 0,
  total_awarded_json text default '{}', last_award_at text,
  first_seen_at text, last_seen_at text,
  primary key (source, source_org_key));

create table if not exists org_links (
  source_a text, key_a text, source_b text, key_b text,
  basis text, created_at text,
  primary key (source_a, key_a, source_b, key_b));

create table if not exists observations (
  entity text, source text, entity_id text, observed_at text,
  event text, content_hash text, raw_ref text);
create index if not exists idx_obs on observations(entity, source, entity_id);

create table if not exists request_attempts (
  run_id integer, source text, url text, status integer,
  transport text, duration_ms integer, error text, at text);
"""

RUN_UPDATE_FIELDS = {
    "status", "stop_reason", "note", "requests", "notices",
    "new_tenders", "updated_tenders", "new_awards", "updated_awards",
    "finished_at",
}


class Store:
    def __init__(self, path: Path):
        self.path = str(Path(path))
        Path(self.path).parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(self.path)
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("pragma journal_mode=WAL")
        self.conn.execute("pragma busy_timeout=5000")
        self.conn.executescript(SCHEMA)
        self.conn.commit()

    def close(self):
        self.conn.close()

    # ------------------------------------------------------------- runs
    def start_run(self, kind: str, spec: str, source: str) -> int:
        from .models import utcnow
        with closing(self.conn.execute(
                "insert into runs(kind, spec, source, started_at) "
                "values(?,?,?,?)",
                (kind, spec, source, utcnow().isoformat()))) as cur:
            self.conn.commit()
            return cur.lastrowid

    def note_run(self, run_id: int, **fields):
        if not fields:
            return
        bad = set(fields) - RUN_UPDATE_FIELDS
        if bad:
            raise ValueError(f"недопустимые поля run: {bad}")
        assignments = ", ".join(f"{k} = ?" for k in fields)
        self.conn.execute(
            f"update runs set {assignments} where id = ?",
            (*fields.values(), run_id))
        self.conn.commit()

    def finish_run(self, run_id: int, status: str, **fields):
        from .models import utcnow
        fields.setdefault("finished_at", utcnow().isoformat())
        self.note_run(run_id, status=status, **fields)

    def log_attempt(self, run_id: int, source: str, url: str,
                    status: int | None, transport: str,
                    duration_ms: int | None, error: str | None):
        from .models import utcnow
        from .rawstore import sanitized_url
        self.conn.execute(
            "insert into request_attempts values(?,?,?,?,?,?,?,?)",
            (run_id, source, sanitized_url(url), status, transport,
             duration_ms, error, utcnow().isoformat()))
        self.conn.commit()

    def record_hit(self, run_id: int, source: str, notice_id: str,
                   filter_label: str, page: int, rank: int):
        from .models import utcnow
        self.conn.execute(
            "insert into run_hits values(?,?,?,?,?,?,?)",
            (run_id, source, notice_id, filter_label, page, rank,
             utcnow().isoformat()))

    # -------------------------------------------------------- entities
    def _observe(self, entity: str, source: str, entity_id: str,
                 event: str, content_hash: str, raw_ref: str | None):
        from .models import utcnow
        self.conn.execute(
            "insert into observations values(?,?,?,?,?,?,?)",
            (entity, source, entity_id, utcnow().isoformat(),
             event, content_hash, raw_ref))

    def upsert_tender(self, record: TenderRecord) -> str:
        row = record.row()
        existing = self.conn.execute(
            "select content_hash from tenders where source=? and "
            "source_notice_id=?", (record.source, record.source_notice_id)
        ).fetchone()
        now_fields = {"last_seen_at": row["last_seen_at"]}
        if existing is None:
            columns = ", ".join(row.keys())
            placeholders = ", ".join("?" for _ in row)
            self.conn.execute(
                f"insert into tenders({columns}) values({placeholders})",
                tuple(row.values()))
            self._observe("tender", record.source, record.source_notice_id,
                          "first_seen", record.content_hash, record.raw_ref)
            self.conn.commit()
            return "first_seen"
        if existing["content_hash"] != record.content_hash:
            updates = {k: v for k, v in row.items()
                       if k not in ("source", "source_notice_id",
                                    "first_seen_at")}
            updates.update(now_fields)
            assignments = ", ".join(f"{k} = ?" for k in updates)
            self.conn.execute(
                f"update tenders set {assignments} where source=? and "
                "source_notice_id=?",
                (*updates.values(), record.source, record.source_notice_id))
            self._observe("tender", record.source, record.source_notice_id,
                          "updated", record.content_hash, record.raw_ref)
            self.conn.commit()
            return "updated"
        self.conn.execute(
            "update tenders set last_seen_at=? where source=? and "
            "source_notice_id=?",
            (row["last_seen_at"], record.source, record.source_notice_id))
        self.conn.commit()
        return "unchanged"

    def upsert_award(self, record: AwardRecord) -> str:
        row = record.row()
        existing = self.conn.execute(
            "select content_hash from awards where source=? and "
            "source_award_id=?",
            (record.source, record.source_award_id)).fetchone()
        if existing is None:
            columns = ", ".join(row.keys())
            placeholders = ", ".join("?" for _ in row)
            self.conn.execute(
                f"insert into awards({columns}) values({placeholders})",
                tuple(row.values()))
            self._observe("award", record.source, record.source_award_id,
                          "first_seen", record.content_hash, record.raw_ref)
            self.conn.commit()
            return "first_seen"
        if existing["content_hash"] != record.content_hash:
            updates = {k: v for k, v in row.items()
                       if k not in ("source", "source_award_id",
                                    "first_seen_at")}
            assignments = ", ".join(f"{k} = ?" for k in updates)
            self.conn.execute(
                f"update awards set {assignments} where source=? and "
                "source_award_id=?",
                (*updates.values(), record.source, record.source_award_id))
            self._observe("award", record.source, record.source_award_id,
                          "updated", record.content_hash, record.raw_ref)
            self.conn.commit()
            return "updated"
        self.conn.execute(
            "update awards set last_seen_at=? where source=? and "
            "source_award_id=?",
            (row["last_seen_at"], record.source, record.source_award_id))
        self.conn.commit()
        return "unchanged"

    def upsert_org(self, org: OrgRecord) -> str:
        row = org.row()
        existing = self.conn.execute(
            "select org_type, name from organizations where source=? and "
            "source_org_key=?", (org.source, org.source_org_key)).fetchone()
        if existing is None:
            if not org.source_org_key:
                return "skipped_no_key"
            columns = ", ".join(row.keys())
            placeholders = ", ".join("?" for _ in row)
            self.conn.execute(
                f"insert into organizations({columns}) values({placeholders})",
                tuple(row.values()))
            self.conn.commit()
            return "first_seen"
        # апгрейд типа buyer/supplier → both
        if (existing["org_type"] in ("buyer", "supplier")
                and org.org_type in ("buyer", "supplier")
                and existing["org_type"] != org.org_type):
            self.conn.execute(
                "update organizations set org_type='both', last_seen_at=? "
                "where source=? and source_org_key=?",
                (row["last_seen_at"], org.source, org.source_org_key))
        else:
            self.conn.execute(
                "update organizations set name=coalesce(?, name), "
                "last_seen_at=? where source=? and source_org_key=?",
                (org.name, row["last_seen_at"], org.source,
                 org.source_org_key))
        self.conn.commit()
        return "updated"

    def link_orgs(self, links: list[tuple[str, str, str, str, str]]) -> int:
        from .models import utcnow
        for source_a, key_a, source_b, key_b, basis in links:
            if source_a == source_b and key_a == key_b:
                continue
            lo, hi = sorted([(source_a, key_a), (source_b, key_b)])
            self.conn.execute(
                "insert or ignore into org_links values(?,?,?,?,?,?)",
                (lo[0], lo[1], hi[0], hi[1], basis,
                 utcnow().isoformat()))
        self.conn.commit()
        row = self.conn.execute(
            "select count(*) from org_links").fetchone()
        return row[0]

    def recompute_org_stats(self) -> int:
        """wins_count / last_award_at / total по валютам из awards+orgs."""
        self.conn.execute(
            """update organizations set
                 wins_count = (select count(*) from awards a
                    where a.source = organizations.source
                      and a.winner_org_key = organizations.source_org_key),
                 last_award_at = (select max(a.awarded_at) from awards a
                    where a.source = organizations.source
                      and a.winner_org_key = organizations.source_org_key),
                 buyer_contracts_count = (select count(*) from tenders t
                    where t.source = organizations.source
                      and t.buyer_org_key = organizations.source_org_key)""")
        rows = self.conn.execute(
            """select o.source, o.source_org_key, a.value_currency,
                      sum(a.value_awarded) as total
               from organizations o join awards a
                 on a.source = o.source
                and a.winner_org_key = o.source_org_key
               where a.value_awarded is not null
               group by o.source, o.source_org_key, a.value_currency"""
        ).fetchall()
        for row in rows:
            self.conn.execute(
                "update organizations set total_awarded_json = ? "
                "where source=? and source_org_key=?",
                (json.dumps({row["value_currency"]: row["total"]},
                            ensure_ascii=False),
                 row["source"], row["source_org_key"]))
        self.conn.commit()
        return len(rows)

    # ------------------------------------------------------------- reads
    def tenders(self, source: str | None = None):
        where, params = "", []
        if source:
            where, params = "where source = ?", [source]
        return self.conn.execute(
            f"select * from tenders {where} "
            "order by first_seen_at desc", params).fetchall()

    def awards(self, source: str | None = None):
        where, params = "", []
        if source:
            where, params = "where source = ?", [source]
        return self.conn.execute(
            f"select * from awards {where} "
            "order by first_seen_at desc", params).fetchall()

    def orgs(self, source: str | None = None, org_type: str | None = None):
        where, params = [], []
        if source:
            where.append("source = ?")
            params.append(source)
        if org_type in ("buyer", "supplier", "both"):
            where.append("org_type = ?")
            params.append(org_type)
        clause = ("where " + " and ".join(where)) if where else ""
        return self.conn.execute(
            f"select * from organizations {clause} "
            "order by wins_count desc, first_seen_at desc",
            params).fetchall()

    def summary(self) -> dict:
        def one(sql, params=()):
            return self.conn.execute(sql, params).fetchone()[0]

        by_source = {}
        for row in self.conn.execute(
                "select source, count(*) n from tenders group by source"):
            by_source[row["source"]] = {"tenders": row["n"]}
        for row in self.conn.execute(
                "select source, count(*) n from awards group by source"):
            by_source.setdefault(row["source"], {})["awards"] = row["n"]
        last_runs = [dict(r) for r in self.conn.execute(
            "select id, kind, source, status, stop_reason, requests, notices,"
            " new_tenders, updated_tenders, new_awards, updated_awards,"
            " started_at, finished_at from runs order by id desc limit 10")]
        return {
            "tenders_total": one("select count(*) from tenders"),
            "awards_total": one("select count(*) from awards"),
            "orgs_total": one("select count(*) from organizations"),
            "suppliers": one("select count(*) from organizations "
                             "where org_type in ('supplier','both')"),
            "org_links": one("select count(*) from org_links"),
            "by_source": by_source,
            "last_runs": last_runs,
        }
