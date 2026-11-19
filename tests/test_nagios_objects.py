"""Tests for effective Nagios object discovery."""
import importlib.util
from pathlib import Path
import tempfile
import unittest

MODULE = Path(__file__).resolve().parents[1] / "scripts" / "nagios_objects.py"
spec = importlib.util.spec_from_file_location("nagios_objects", MODULE)
objects = importlib.util.module_from_spec(spec)
spec.loader.exec_module(objects)


class ObjectCacheTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.path = Path(self.directory.name) / "objects.cache"

    def write(self, content):
        self.path.write_text(content, encoding="utf-8")

    def test_only_effective_perfdata_services(self):
        self.write("""define service {
 host_name FamPaJo-Gateway
 service_description Interface - VPN SJC
 process_perf_data 0
}
define service {
 host_name sjc-print
 service_description Interface eth0
 process_perf_data 1
 check_command check_int_32!$USER9$!NC-8200h
}
define service {
 host_name FamPaJo-Gateway
 service_description Memory
 process_perf_data 1
}
""")
        services = list(objects.perfdata_services(self.path))
        self.assertEqual([(s["host_name"], s["service_description"]) for s in services],
                         [("sjc-print", "Interface eth0"), ("FamPaJo-Gateway", "Memory")])

    def test_duplicate_service_fails_closed(self):
        self.write(("define service {\n host_name a\n service_description b\n"
                    " process_perf_data 1\n}\n") * 2)
        with self.assertRaisesRegex(ValueError, "Duplicate"):
            list(objects.perfdata_services(self.path))

    def test_unterminated_object_rejected(self):
        self.write("define service {\n host_name a\n")
        with self.assertRaisesRegex(ValueError, "Unterminated"):
            objects.read_objects(self.path)

    def test_symlink_rejected(self):
        target = Path(self.directory.name) / "real"
        target.write_text("")
        self.path.symlink_to(target)
        with self.assertRaisesRegex(ValueError, "symlink"):
            objects.read_objects(self.path)


if __name__ == "__main__":
    unittest.main()
