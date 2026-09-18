"""Тесты нормализации, хранения, идемпотентности и экспорта."""
from __future__ import annotations

import csv
import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from tendercollector.export import ORG_COLUMNS, TENDER_COLUMNS, export_csv
from tendercollector.models import ParsedNotice
from tendercollector.normalize import normalize
from tendercollector.sources.ted import normalize_date_iso
from tendercollector.storage import Store


def ted_award_notice() -> ParsedNotice:
    return ParsedNotice(
        source="ted", source_notice_id="644140-2026", is_award=True,
        title="Ireland – Mystery Passenger Survey",
        buyer_name="National Transport Authority",
        buyer_org_key="ted-org:1149",
        winner_name="Ipsos MORI UK Limited t/a Ipsos",
        cpv_codes=["79342100"], value=3523239.0, currency="EUR",
        awarded_value=3523239.0, awarded_currency="EUR",
        published_at_raw="2026-09-18+02:00",
        awarded_at_raw="2026-09-18+02:00",
        url="https://ted.europa.eu/en/notice/-/detail/644140-2026",
        form_type="result", extra={})


def ted_tender_notice() -> ParsedNotice:
    return ParsedNotice(
        source="ted", source_notice_id="645194-2026",
        title="Polska – Kampania informacyjna",
        buyer_name="Województwo Śląskie", buyer_country=None,
        cpv_codes=["79342000"], value=450000.0, currency="PLN",
        published_at_raw="2026-09-17+01:00", form_type="competition",
        extra={})


class TestNormalize(unittest.TestCase):
    def test_date_offsets_to_utc(self):
        self.assertTrue(normalize_date_iso("2026-09-18+02:00")
                        .startswith("2026-09-18T"))
        # +02:00 → UTC минус 2 часа (18-е 00:30 локальное = 17-е 22:30 UTC)
        self.assertEqual(
            normalize_date_iso("2026-09-18T00:30:00+02:00"),
            "2026-09-17T22:30:00+00:00")

    def test_compact_and_iso_dates(self):
        self.assertTrue(normalize_date_iso("20260918").startswith("2026-09-18"))
        self.assertIsNone(normalize_date_iso(None))
        self.assertIsNone(normalize_date_iso("garbage"))

    def test_award_normalize_produces_award_and_supplier(self):
        tender, award, orgs = normalize(ted_award_notice(), "raw/ref")
        self.assertIsNone(tender)                       # award-запись
        self.assertIsNotNone(award)
        self.assertEqual(award.source_award_id, "644140-2026")
        self.assertIsNotNone(award.awarded_at)
        types = {o.org_type: o for o in orgs}
        self.assertIn("supplier", types)
        self.assertEqual(types["supplier"].name,
                         "Ipsos MORI UK Limited t/a Ipsos")
        # у TED-победителя нет явного org-id → name-country ключ
        self.assertEqual(types["supplier"].key_confidence, "name_country")

    def test_tender_normalize_produces_tender_and_buyer(self):
        tender, award, orgs = normalize(ted_tender_notice(), None)
        self.assertIsNone(award)
        self.assertIsNotNone(tender)
        self.assertEqual(tender.cpv_codes, '["79342000"]')
        self.assertEqual(tender.notice_status, "unknown")  # нет дедлайна
        # buyer без явного ключа → name-country ключ с confidence=name_country
        self.assertEqual(tender.buyer_org_key,
                         "nm:województwo śląskie|-")
        buyer = next(o for o in orgs if o.org_type == "buyer")
        self.assertEqual(buyer.key_confidence, "name_country")
        self.assertEqual(buyer.source_org_key, tender.buyer_org_key)

    def test_tender_keeps_explicit_buyer_key(self):
        notice = ParsedNotice(
            source="sam", source_notice_id="x1",
            buyer_name="PBS R5", buyer_org_key="047.4732.47QTCA",
            buyer_country="US", title="T")
        tender, _, orgs = normalize(notice, None)
        self.assertEqual(tender.buyer_org_key, "047.4732.47QTCA")
        self.assertEqual(orgs[0].key_confidence, "exact")


class TestStore(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.store = Store(Path(self.tmp.name) / "t.db")

    def tearDown(self):
        self.store.close()
        self.tmp.cleanup()

    def test_tender_idempotent(self):
        tender, _, _ = normalize(ted_tender_notice(), None)
        self.assertEqual(self.store.upsert_tender(tender), "first_seen")
        self.assertEqual(self.store.upsert_tender(tender), "unchanged")

    def test_award_idempotent_and_update(self):
        _, award, orgs = normalize(ted_award_notice(), None)
        for org in orgs:
            self.store.upsert_org(org)
        self.assertEqual(self.store.upsert_award(award), "first_seen")
        self.assertEqual(self.store.upsert_award(award), "unchanged")
        award.value_awarded = 4000000.0
        award.rehash()
        self.assertEqual(self.store.upsert_award(award), "updated")

    def test_org_upgrade_to_both(self):
        _, _, orgs = normalize(ted_award_notice(), None)
        supplier = next(o for o in orgs if o.org_type == "supplier")
        self.store.upsert_org(supplier)
        from tendercollector.models import OrgRecord
        same_key_as_buyer = OrgRecord(
            source=supplier.source, source_org_key=supplier.source_org_key,
            name=supplier.name, country=supplier.country,
            org_type="buyer")
        self.store.upsert_org(same_key_as_buyer)
        rows = self.store.orgs()
        self.assertEqual(rows[0]["org_type"], "both")

    def test_recompute_stats(self):
        _, award, orgs = normalize(ted_award_notice(), None)
        for org in orgs:
            self.store.upsert_org(org)
        self.store.upsert_award(award)
        self.store.recompute_org_stats()
        supplier = self.store.orgs(org_type="supplier")[0]
        self.assertEqual(supplier["wins_count"], 1)
        self.assertEqual(json.loads(supplier["total_awarded_json"]),
                         {"EUR": 3523239.0})
        self.assertIsNotNone(supplier["last_award_at"])

    def test_summary(self):
        tender, _, _ = normalize(ted_tender_notice(), None)
        self.store.upsert_tender(tender)
        info = self.store.summary()
        self.assertEqual(info["tenders_total"], 1)
        self.assertEqual(info["by_source"].get("ted"), {"tenders": 1})


class TestExport(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()

    def tearDown(self):
        self.tmp.cleanup()

    def test_csv_bom_and_formula_guard(self):
        tender, _, _ = normalize(ted_tender_notice(), None)
        tender.title = "=SUM(A1)"
        out = Path(self.tmp.name) / "t.csv"
        written = export_csv([tender.row()], out, TENDER_COLUMNS)
        self.assertEqual(written, 1)
        raw = out.read_bytes()
        self.assertTrue(raw.startswith(b"\xef\xbb\xbf"))
        with out.open("r", encoding="utf-8-sig", newline="") as handle:
            rows = list(csv.reader(handle))
        title_cell = rows[1][rows[0].index("title")]
        self.assertTrue(title_cell.startswith("'="))


if __name__ == "__main__":
    unittest.main()
