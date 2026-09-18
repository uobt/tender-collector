"""Тесты коннекторов на fixtures (формат снят с реальных ответов пилота)."""
from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from tendercollector.models import RawDocument, SourceBlocked, TenderSpec
from tendercollector.sources.sam import SamConnector
from tendercollector.sources.ted import (TedConnector, pick_lang,
                                         split_buyer, to_float)
from tendercollector.sources.ukcf import UkCfConnector

FIX = Path(__file__).parent / "fixtures"


def load(name: str) -> RawDocument:
    return RawDocument(source="", url="https://example.test", status=200,
                       content=(FIX / name).read_text(encoding="utf-8"))


class TestTedHelpers(unittest.TestCase):
    def test_pick_lang_dict_eng(self):
        self.assertEqual(pick_lang({"eng": ["Name_12"]}), "Name_12")

    def test_pick_lang_first_fallback(self):
        used = []
        value = pick_lang({"pol": ["Województwo"]}, used)
        self.assertEqual(value, "Województwo")
        self.assertEqual(used, ["pol"])

    def test_split_buyer_suffix(self):
        name, key = split_buyer("National Transport Authority_1149")
        self.assertEqual(name, "National Transport Authority")
        self.assertEqual(key, "ted-org:1149")

    def test_split_buyer_plain(self):
        name, key = split_buyer("Ipsos MORI UK Limited t/a Ipsos")
        self.assertEqual(name, "Ipsos MORI UK Limited t/a Ipsos")
        self.assertIsNone(key)

    def test_to_float(self):
        self.assertEqual(to_float("3523239"), 3523239.0)
        self.assertEqual(to_float(3875563), 3875563.0)
        self.assertIsNone(to_float(None))
        self.assertIsNone(to_float(""))


class TestTedParser(unittest.TestCase):
    def setUp(self):
        self.page = TedConnector().parse_search(load("ted_search.json"))

    def test_records(self):
        self.assertEqual(len(self.page.records), 2)
        ids = {r.source_notice_id for r in self.page.records}
        self.assertEqual(ids, {"644140-2026", "645194-2026"})

    def test_award_record_fields(self):
        rec = next(r for r in self.page.records
                   if r.source_notice_id == "644140-2026")
        self.assertTrue(rec.is_award)                     # form-type=result
        self.assertEqual(rec.buyer_name, "National Transport Authority")
        self.assertEqual(rec.buyer_org_key, "ted-org:1149")
        self.assertEqual(rec.winner_name,
                         "Ipsos MORI UK Limited t/a Ipsos")
        self.assertEqual(rec.value, 3523239.0)
        self.assertEqual(rec.currency, "EUR")
        self.assertEqual(
            rec.url, "https://ted.europa.eu/en/notice/-/detail/644140-2026")

    def test_competition_record(self):
        rec = next(r for r in self.page.records
                   if r.source_notice_id == "645194-2026")
        self.assertFalse(rec.is_award)
        # нет eng-заголовка — берётся первый язык, язык фиксируется
        self.assertEqual(rec.title_lang_used, "pol")
        self.assertIsNone(rec.winner_name)

    def test_non_json(self):
        page = TedConnector().parse_search(
            RawDocument(source="ted", url="u", status=200, content="{oops"))
        self.assertEqual(page.status, "failed")


class TestUkCfParser(unittest.TestCase):
    def setUp(self):
        self.page = UkCfConnector().parse_search(load("ukcf_ocds.json"))

    def test_tender_plus_award_records(self):
        # release с awards → tender-запись + award-запись; чистый tender → 1
        self.assertEqual(len(self.page.records), 3)
        awards = [r for r in self.page.records if r.is_award]
        self.assertEqual(len(awards), 1)
        self.assertIn("#", awards[0].source_notice_id)

    def test_award_fields(self):
        rec = next(r for r in self.page.records if r.is_award)
        self.assertEqual(rec.winner_name, "A Bucklers Haulage")
        self.assertEqual(rec.winner_org_key, "GB-CFS-338456")
        self.assertEqual(rec.buyer_org_key,
                         "GB-SRS-sid4gov.cabinetoffice.gov.uk/By5B2FN2")
        self.assertEqual(rec.awarded_value, 148620)
        self.assertEqual(rec.awarded_currency, "GBP")
        self.assertEqual(rec.buyer_country, "GB")
        self.assertTrue(rec.url.startswith(
            "https://www.contractsfinder.service.gov.uk/Notice/"))

    def test_tender_fields(self):
        rec = next(r for r in self.page.records
                   if r.source_notice_id.endswith("-914000"))
        self.assertFalse(rec.is_award)
        self.assertEqual(rec.title, "Marketing services framework")
        self.assertEqual(rec.cpv_codes, ["79342000"])
        self.assertEqual(rec.value, 250000)

    def test_cursor_from_links_next(self):
        self.assertTrue(self.page.next_cursor.startswith(
            "https://www.contractsfinder.service.gov.uk/"))
        self.assertIn("cursor=abc", self.page.next_cursor)


class TestSamParser(unittest.TestCase):
    def setUp(self):
        self.page = SamConnector().parse_search(load("sam_search.json"))

    def test_official_example_parsed(self):
        self.assertEqual(len(self.page.records), 1)
        rec = self.page.records[0]
        self.assertEqual(rec.source_notice_id,
                         "5b345bbb7127b91a3ad577b203fc6f68")
        self.assertTrue(rec.is_award)
        self.assertEqual(rec.winner_name, "D.G. Beyer, Inc.")
        self.assertEqual(rec.winner_org_key, "025114695AST")   # ueiSAM
        self.assertEqual(rec.winner_country, "US")
        self.assertEqual(rec.buyer_org_key, "047.4732.47QTCA")
        self.assertEqual(rec.value, 800620.0)                   # строка→float
        self.assertEqual(rec.naics_code, "236220")
        self.assertEqual(rec.extra["winner_city"], "New Berlin")

    def test_404_is_empty_not_error(self):
        page = SamConnector().parse_search(
            RawDocument(source="sam", url="u", status=404, content=""))
        self.assertEqual((page.status, page.stop_reason),
                         ("empty", "no_results"))

    def test_key_gate_blocks_without_key(self):
        connector = SamConnector()
        if connector.api_key():
            self.skipTest("SAM_GOV_API_KEY задан — gate не сработает")
        with self.assertRaises(SourceBlocked) as ctx:
            connector.search(TenderSpec(sources=["sam"]), None)
        self.assertIn("NEEDS_FREE_KEY", str(ctx.exception))


if __name__ == "__main__":
    unittest.main()
