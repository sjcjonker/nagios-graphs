#!/usr/bin/env python3
"""End-to-end RRD pipeline smoke test in an isolated temporary directory.

Requires rrdtool. No Nagios config or service changes. Exercises:
  source file -> seal -> spool drain -> RRD -> replay/cleanup.
"""
from __future__ import annotations

import importlib.util
import os
from unittest import mock
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parent.parent


def load(name: str):
    source = ROOT / "scripts" / f"nagios-rrd-{name}.py"
    spec = importlib.util.spec_from_file_location("nagios_rrd_" + name, source)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@unittest.skipUnless(shutil.which("rrdtool"), "rrdtool missing")
class RRDSpoolEndToEnd(unittest.TestCase):
    def test_seal_drain_rrd_and_repeat(self):
        seal = load("seal")
        drain = load("drain")
        with tempfile.TemporaryDirectory(prefix="nagios-graphs-e2e-") as directory:
            base = Path(directory)
            spool = base / "spool"
            spool.mkdir()
            source = spool / "service-perfdata"
            source.write_text(
                "1791828000\tserver01\tTemperature\ttemperature_c=1.1\n"
                "1791828060\tserver01\tTemperature\ttemperature_c=1.3\n",
                encoding="utf-8",
            )
            archived = seal.seal(source, spool)
            self.assertTrue(archived.exists())
            self.assertFalse(source.exists())
            importer = ROOT / "scripts/nagios-rrd-import.py"
            rrd_dir = base / "rrd"
            with mock.patch.dict(os.environ, NAGIOS_GRAPHS_CONFIG=str(ROOT / "examples/graphs.json")):
                self.assertEqual(drain.drain(spool, importer, rrd_dir, "rrdtool"), (1, 0))
            self.assertFalse(archived.exists())
            self.assertEqual(len(list(rrd_dir.glob("*.rrd"))), 1)
            self.assertEqual(drain.drain(spool, importer, rrd_dir, "rrdtool"), (0, 0))


if __name__ == "__main__":
    unittest.main()

