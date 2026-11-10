#!/usr/bin/env python3
from __future__ import annotations
import importlib.util
from pathlib import Path
import tempfile
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent

def load(name, script):
    spec = importlib.util.spec_from_file_location(name, ROOT / "scripts" / script)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module

seal = load("nagios_rrd_seal", "nagios-rrd-seal.py")
drain = load("nagios_rrd_drain", "nagios-rrd-drain.py")


class SpoolTests(unittest.TestCase):
    def test_seal_is_atomic_and_keeps_original_on_backlog_failure(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            spool = root / "spool"
            spool.mkdir()
            source = root / "service-perfdata"
            source.write_text("one\n")
            result = seal.seal(source, spool)
            self.assertFalse(source.exists())
            self.assertEqual(result.read_text(), "one\n")
            source.write_text("two\n")
            with mock.patch.object(seal, "MAX_SPOOL_FILES", 1):
                with self.assertRaisesRegex(ValueError, "backlog"):
                    seal.seal(source, spool)
            self.assertEqual(source.read_text(), "two\n")

    def test_failed_import_keeps_batch_and_order(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "a.batch").write_text("first\n")
            (root / "b.batch").write_text("second\n")
            with mock.patch.object(drain.subprocess, "run", return_value=mock.Mock(returncode=2)) as run:
                result = drain.drain(root, Path("/tmp/importer"), root / "rrd", "rrdtool")
            self.assertEqual(result, (0, 1))
            self.assertEqual(run.call_count, 1)
            self.assertTrue((root / "a.batch").exists())
            self.assertTrue((root / "b.batch").exists())

    def test_success_deletes_only_processed_batch(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "a.batch").write_text("first\n")
            with mock.patch.object(drain.subprocess, "run", return_value=mock.Mock(returncode=0)):
                self.assertEqual(drain.drain(root, Path("/tmp/importer"), root / "rrd", "rrdtool"), (1, 0))
            self.assertFalse((root / "a.batch").exists())

    def test_oversized_batch_remains_in_spool(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "a.batch").write_text("more than limit")
            with mock.patch.object(drain, "MAX_BATCH_BYTES", 3):
                self.assertEqual(drain.drain(root, Path("/tmp/importer"), root / "rrd", "rrdtool"), (0, 1))
            self.assertTrue((root / "a.batch").exists())
