"""Offline migration tests using synthetic archives, never production data."""
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1] / "scripts"
spec = importlib.util.spec_from_file_location("migration", ROOT / "migrate-rrd-names.py")
migration = importlib.util.module_from_spec(spec)
spec.loader.exec_module(migration)
spec2 = importlib.util.spec_from_file_location("config", ROOT / "nagios_graphs_config.py")
config = importlib.util.module_from_spec(spec2)
spec2.loader.exec_module(config)


class MigrationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.rrd = self.root / "rrd"
        self.rrd.mkdir()
        self.backup = self.root / "backup"
        self.metric = {"id": "first", "host": "node", "service": "CPU",
                       "archive_service": "CPU", "metric": "usage"}
        self.catalog = self.root / "catalog.json"
        self.write_catalog([self.metric])
        self.old = self.rrd / (config.archive_id(self.metric) + ".rrd")
        self.old.write_bytes(b"fake RRD content")

    def write_catalog(self, metrics):
        self.catalog.write_text(json.dumps({"version": 1, "metrics": metrics, "graphs": []}))

    def test_dry_run_is_read_only(self):
        moves = migration.plan(self.catalog, self.rrd)
        self.assertEqual(len(moves), 1)
        self.assertTrue(self.old.exists())
        self.assertFalse(self.backup.exists())

    def test_round_trip_and_unmapped_preserved(self):
        orphan = self.rrd / "orphan.rrd"
        orphan.write_bytes(b"history")
        moves = migration.plan(self.catalog, self.rrd)
        migration.backup_and_move(self.rrd, self.backup, moves)
        self.assertFalse(self.old.exists())
        self.assertTrue((self.rrd / moves[0]["new"]).exists())
        self.assertEqual(orphan.read_bytes(), b"history")
        migration.rollback(self.rrd, self.backup)
        self.assertEqual(self.old.read_bytes(), b"fake RRD content")
        self.assertEqual(orphan.read_bytes(), b"history")

    def test_rollback_refuses_new_samples(self):
        moves = migration.plan(self.catalog, self.rrd)
        migration.backup_and_move(self.rrd, self.backup, moves)
        (self.rrd / moves[0]["new"]).write_bytes(b"new samples")
        with self.assertRaisesRegex(ValueError, "changed since migration"):
            migration.rollback(self.rrd, self.backup)

    def test_shared_identity_migrates_once(self):
        second = dict(self.metric, id="second", service="CPU combined")
        self.write_catalog([self.metric, second])
        self.assertEqual(len(migration.plan(self.catalog, self.rrd)), 1)

    def test_conflicting_destination_refused(self):
        moves = migration.plan(self.catalog, self.rrd)
        (self.rrd / moves[0]["new"]).write_bytes(b"existing")
        with self.assertRaisesRegex(ValueError, "Destination already exists"):
            migration.plan(self.catalog, self.rrd)


if __name__ == "__main__":
    unittest.main()
