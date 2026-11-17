#!/usr/bin/env python3
"""
Unit tests for Command Output Distiller.
"""

import sys
import os
import unittest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "../zeromcp-py")))
from command_distiller import (
    strip_ansi,
    distill_pytest,
    distill_cargo_test,
    distill_cargo_build,
    distill_git_status,
    distill_generic,
    distill_output
)

class TestCommandDistiller(unittest.TestCase):
    def test_strip_ansi(self):
        colored = "\x1b[31mRed Error\x1b[0m and \x1b[32mGreen\x1b[0m"
        self.assertEqual(strip_ansi(colored), "Red Error and Green")

    def test_distill_pytest_failures(self):
        raw = """
============================= test session starts ==============================
platform darwin -- Python 3.12.0, pytest-8.0.0
rootdir: /tmp
collected 10 items

test_mod.py ........F.                                                   [100%]

=================================== FAILURES ===================================
__________________________________ test_error __________________________________

    def test_error():
>       assert "expected" == "actual"
E       AssertionError: assert 'expected' == 'actual'
E         - actual
E         + expected

test_mod.py:20: AssertionError
=========================== short test summary info ============================
FAILED test_mod.py::test_error - AssertionError: assert 'expected' == 'actual'
========================= 1 failed, 9 passed in 0.45s ==========================
"""
        distilled, telem = distill_output("pytest tests/", raw)
        self.assertIn("AssertionError", distilled)
        self.assertIn("test_error", distilled)
        self.assertNotIn("test session starts", distilled)
        self.assertGreater(telem["savings_percent"], 25.0)

    def test_distill_pytest_all_passed(self):
        raw = """
============================= test session starts ==============================
collected 5 items

test_mod.py .....                                                        [100%]

============================== 5 passed in 0.05s ===============================
"""
        distilled, telem = distill_output("pytest", raw)
        self.assertIn("5 passed", distilled)

    def test_distill_cargo_test(self):
        raw = """
   Compiling crate v0.1.0 (/tmp/crate)
    Finished `test` profile [unoptimized + debuginfo] target(s) in 0.31s
     Running unittests src/lib.rs (target/debug/deps/crate-1234)

running 3 tests
test tests::test_ok_1 ... ok
test tests::test_fail ... FAILED
test tests::test_ok_2 ... ok

failures:

---- tests::test_fail stdout ----
thread 'tests::test_fail' panicked at src/lib.rs:15:9:
assertion `left == right` failed
  left: 10
 right: 20

failures:
    tests::test_fail

test result: FAILED. 1 failed; 2 passed; 0 ignored; 0 measured; 0 filtered out; finished in 0.00s
"""
        distilled, telem = distill_output("cargo test", raw)
        self.assertIn("tests::test_fail", distilled)
        self.assertIn("assertion `left == right` failed", distilled)
        self.assertNotIn("test tests::test_ok_1 ... ok", distilled)
        self.assertGreater(telem["savings_percent"], 30.0)

    def test_distill_cargo_build(self):
        raw = """
   Compiling dep v1.0.0
   Compiling target v0.1.0 (/tmp/target)
error[E0425]: cannot find value `foo` in this scope
  --> src/main.rs:10:5
   |
10 |     foo += 1;
   |     ^^^ not found in this scope

error: could not compile `target` (bin "target") due to 1 previous error
"""
        distilled, telem = distill_output("cargo build", raw)
        self.assertIn("error[E0425]: cannot find value `foo`", distilled)
        self.assertIn("src/main.rs:10:5", distilled)
        self.assertNotIn("Compiling dep", distilled)

    def test_distill_git_status(self):
        raw = """
On branch main
Your branch is up to date with 'origin/main'.

Changes not staged for commit:
  (use "git add <file>..." to update what will be committed)
  (use "git restore <file>..." to discard changes in working directory)
	modified:   src/lib.rs

Untracked files:
  (use "git add <file>..." to include in what will be committed)
	scratch.txt

no changes added to commit (use "git add" to track)
"""
        distilled, telem = distill_output("git status", raw)
        self.assertIn("On branch main", distilled)
        self.assertIn("modified:   src/lib.rs", distilled)
        self.assertIn("scratch.txt", distilled)
        self.assertNotIn('use "git add', distilled)

    def test_distill_generic(self):
        raw_lines = ["normal line"] * 10 + ["CRITICAL_ERROR: socket closed"] + ["another normal line"] * 100
        raw = "\n".join(raw_lines)
        distilled = distill_generic(raw, max_lines=40)
        self.assertIn("CRITICAL_ERROR: socket closed", distilled)
        self.assertIn("lines truncated", distilled)

if __name__ == "__main__":
    unittest.main()
