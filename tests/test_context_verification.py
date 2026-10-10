"""Tests for the pure context-verification module (card A2, US-1..US-8)."""

import pathlib
import sys
import unittest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "src"))

from hermes_reticulum.core import context_verify
from hermes_reticulum.core.context_verify import (
    MATCH,
    MISMATCH,
    UNAVAILABLE,
    reset_warned,
    verify_context_window,
)


def _reader(value):
    def read():
        return value

    return read


class Collector:
    def __init__(self):
        self.lines = []

    def __call__(self, msg):
        self.lines.append(msg)


class TestMismatch(unittest.TestCase):
    def setUp(self):
        reset_warned()

    def test_mismatch_warns_exactly_once(self):
        warn = Collector()
        verdict = verify_context_window(262144, _reader(8192), warn=warn)
        self.assertEqual(verdict, MISMATCH)
        self.assertEqual(len(warn.lines), 1)
        self.assertIn("262144", warn.lines[0])
        self.assertIn("8192", warn.lines[0])

    def test_mismatch_repeat_stays_silent(self):
        warn = Collector()
        for _ in range(5):
            self.assertEqual(
                verify_context_window(262144, _reader(8192), warn=warn), MISMATCH
            )
        self.assertEqual(len(warn.lines), 1)

    def test_new_pair_warns_again(self):
        warn = Collector()
        verify_context_window(262144, _reader(8192), warn=warn)
        verify_context_window(262144, _reader(32768), warn=warn)
        self.assertEqual(len(warn.lines), 2)


class TestMatch(unittest.TestCase):
    def setUp(self):
        reset_warned()

    def test_match_no_warning(self):
        warn = Collector()
        self.assertEqual(verify_context_window(8192, _reader(8192), warn=warn), MATCH)
        self.assertEqual(warn.lines, [])

    def test_match_repeat_still_silent(self):
        warn = Collector()
        for _ in range(3):
            verify_context_window(8192, _reader(8192), warn=warn)
        self.assertEqual(warn.lines, [])


class TestEndpointUnavailable(unittest.TestCase):
    def setUp(self):
        reset_warned()

    def test_none_reported_no_warning(self):
        warn = Collector()
        self.assertEqual(
            verify_context_window(262144, _reader(None), warn=warn), UNAVAILABLE
        )
        self.assertEqual(warn.lines, [])

    def test_reader_exception_no_warning(self):
        warn = Collector()

        def boom():
            raise ConnectionError("endpoint down")

        self.assertEqual(verify_context_window(262144, boom, warn=warn), UNAVAILABLE)
        self.assertEqual(warn.lines, [])

    def test_default_reader_is_unavailable(self):
        warn = Collector()
        self.assertEqual(verify_context_window(262144, warn=warn), UNAVAILABLE)
        self.assertEqual(warn.lines, [])

    def test_unreachable_endpoint_never_warns_even_on_repeat(self):
        warn = Collector()
        for _ in range(5):
            verify_context_window(262144, _reader(None), warn=warn)
        self.assertEqual(warn.lines, [])


class TestImportGuard(unittest.TestCase):
    def test_module_source_imports_no_rns_lxmf(self):
        source = pathlib.Path(context_verify.__file__).read_text(encoding="utf-8")
        for banned in ("import rns", "import lxmf", "from rns", "from lxmf"):
            self.assertNotIn(banned, source)


if __name__ == "__main__":
    unittest.main()
