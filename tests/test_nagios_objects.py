import importlib.util
from pathlib import Path
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1] / "scripts"


def module(name):
    spec = importlib.util.spec_from_file_location(name, ROOT / (name + ".py"))
    result = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(result)
    return result


objects = module("nagios_objects")
names = module("rrd_names")


class DiscoveryTests(unittest.TestCase):
    def test_effective_perfdata_filter(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "objects.cache"
            path.write_text("""define service {
host_name gateway
service_description Interface eth0
process_perf_data 0
}
define service {
host_name printer
service_description Interface eth0
process_perf_data 1
}
""")
            self.assertEqual([r["host_name"] for r in objects.perfdata_services(path)], ["printer"])

    def test_unterminated_cache(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "objects.cache"
            path.write_text("define service {\nhost_name test\n")
            with self.assertRaises(ValueError):
                objects.read_objects(path)

    def test_names_stable_and_collision_resistant(self):
        first = names.filename("Gateway", "Interface eth0", "eth0_in_octet")
        self.assertTrue(first.startswith("gateway__interface-eth0__eth0_in_octet__"))
        self.assertTrue(first.endswith(".rrd"))
        self.assertNotEqual(first, names.filename("Gateway", "Interface eth1", "eth0_in_octet"))
        self.assertEqual(first, names.filename("Gateway", "Interface eth0", "eth0_in_octet"))


if __name__ == "__main__":
    unittest.main()
