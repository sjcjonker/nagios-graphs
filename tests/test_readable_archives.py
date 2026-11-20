"""Verify collector and renderer resolve the same migrated archive names."""
import importlib.util
from pathlib import Path
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1] / "scripts"
spec = importlib.util.spec_from_file_location("graphs_config", ROOT / "nagios_graphs_config.py")
config = importlib.util.module_from_spec(spec)
spec.loader.exec_module(config)
spec = importlib.util.spec_from_file_location("rrd_import", ROOT / "nagios-rrd-import.py")
collector = importlib.util.module_from_spec(spec)
spec.loader.exec_module(collector)


class ReadableArchiveTests(unittest.TestCase):
    def setUp(self):
        self.metric = {"id": "a", "host": "node", "service": "CPU",
                       "archive_service": "CPU", "metric": "usage"}
        self.graph = {"id": "cpu", "title": "CPU", "unit": "%",
                      "lines": [{"metric": "a", "label": "Usage", "color": "#abcdef"}]}
        self.catalog = config.validate({"version": 1, "metrics": [self.metric],
                                        "graphs": [self.graph]})
        self.original_load = config.load
        self.original_configured = collector.configured_catalog
        config.load = lambda: self.catalog
        collector.configured_catalog = lambda: {
            "metric_for": lambda h, s, m: self.catalog["metrics"]["a"]
            if (h, s, m) == ("node", "CPU", "usage") else None,
            "archive_filename": config.archive_filename,
        }
        self.addCleanup(self.restore)

    def restore(self):
        config.load = self.original_load
        collector.configured_catalog = self.original_configured

    def test_collector_and_graph_share_filename(self):
        name = config.archive_filename(self.metric)
        self.assertEqual(collector.identifier("node", "CPU", "usage"), name)
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / name).write_bytes(b"placeholder")
            args = config.graph_command("cpu", 3600, root, 600, 200)
            self.assertTrue(any(str(root / name) in arg for arg in args if arg.startswith("DEF:")))

    def test_missing_archive_fails_closed(self):
        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaises(FileNotFoundError):
                config.graph_command("cpu", 3600, Path(directory), 600, 200)


if __name__ == "__main__":
    unittest.main()
