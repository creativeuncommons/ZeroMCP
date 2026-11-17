#!/usr/bin/env python3
"""
Unit tests for ZeroMCP Content-Addressed Storage (CAS) Engine.
"""

import sys
import os
import unittest
import tempfile
import shutil
import json

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "../zeromcp-py")))
from zeromcp_cas import ZeroMCPCAS

class TestZeroMCPCAS(unittest.TestCase):
    def setUp(self):
        self.test_dir = tempfile.mkdtemp(prefix="zeromcp_cas_test_")
        self.cas = ZeroMCPCAS(storage_dir=self.test_dir, threshold_bytes=256, enabled=True)

    def tearDown(self):
        if os.path.exists(self.test_dir):
            shutil.rmtree(self.test_dir)

    def test_threshold_skipping(self):
        small_text = "This is a small message below threshold."
        h, path, count, stored = self.cas.store(small_text)
        self.assertFalse(stored)
        self.assertEqual(count, len(small_text.encode("utf-8")))

    def test_store_and_retrieve_bytes(self):
        payload = "HEADER\n" + ("DATA ROW 1234567890\n" * 20) + "FOOTER\n"
        h, path, count, stored = self.cas.store(payload)
        self.assertTrue(stored)
        self.assertTrue(os.path.exists(path))

        # Retrieve full
        ret = self.cas.retrieve(h, offset=0, limit=len(payload))
        self.assertTrue(ret["found"])
        self.assertEqual(ret["content"], payload)

        # Retrieve slice
        slice_ret = self.cas.retrieve(h, offset=0, limit=6)
        self.assertTrue(slice_ret["found"])
        self.assertEqual(slice_ret["content"], "HEADER")

    def test_retrieve_lines_mode(self):
        lines = [f"Line {i}\n" for i in range(50)]
        payload = "".join(lines)
        h, path, count, stored = self.cas.store(payload)
        self.assertTrue(stored)

        # Retrieve first 5 lines
        ret = self.cas.retrieve(h, offset=0, limit=5, mode="lines")
        self.assertTrue(ret["found"])
        self.assertEqual(ret["returned_lines"], 5)
        self.assertEqual(ret["content"], "".join(lines[:5]))

        # Retrieve lines 10-15
        ret_mid = self.cas.retrieve(h, offset=10, limit=5, mode="lines")
        self.assertEqual(ret_mid["returned_lines"], 5)
        self.assertEqual(ret_mid["content"], "".join(lines[10:15]))

    def test_search(self):
        payload = "intro line\n" + ("filler content data line\n" * 25) + "SECRET_KEY=9999\n" + ("filler content data line\n" * 15)
        h, path, count, stored = self.cas.store(payload)
        self.assertTrue(stored)

        search_res = self.cas.search(h, query="SECRET_KEY")
        self.assertTrue(search_res["found"])
        self.assertEqual(search_res["total_matches_found"], 1)
        match = search_res["matches"][0]
        self.assertEqual(match["match_line"], 27)
        self.assertEqual(match["matched_text"], "SECRET_KEY=9999")
        self.assertTrue(any(c["is_match"] for c in match["context"]))

    def test_format_pointer_summary(self):
        payload = "START" + ("X" * 1000) + "END"
        h, path, count, stored = self.cas.store(payload)
        summary = self.cas.format_pointer_summary(h, path, payload)
        self.assertIn("cas://sha256/", summary)
        self.assertIn("[ZeroMCP-CAS:", summary)
        self.assertIn("cas_fetch", summary)
        self.assertIn("cas_search", summary)

    def test_stats_and_gc(self):
        payload = "SAMPLE DATA TO PRUNE" * 20
        h, path, count, stored = self.cas.store(payload)
        self.assertTrue(stored)

        stats = self.cas.stats()
        self.assertEqual(stats["total_blobs"], 1)
        self.assertGreater(stats["total_bytes"], 0)

        # GC with 0 days should remove it
        gc_res = self.cas.gc(max_age_days=0)
        self.assertEqual(gc_res["removed_count"], 1)
        self.assertEqual(self.cas.stats()["total_blobs"], 0)

if __name__ == "__main__":
    unittest.main()
