"""Bounded archive policy tests."""
import importlib.util
from pathlib import Path
import unittest
import tempfile
from unittest import mock

ROOT = Path(__file__).resolve().parents[1] / "scripts"

def load(name, filename):
    spec = importlib.util.spec_from_file_location(name, ROOT / filename)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module

config = load("bound_config", "nagios_graphs_config.py")
collector = load("bound_import", "nagios-rrd-import.py")

class BoundTests(unittest.TestCase):
    def metric(self, **extra):
        return dict({"id": "a", "host": "host", "service": "sensor", "metric": "value", "type": "GAUGE"}, **extra)

    def validate(self, metric):
        return config.validate({"version": 1, "metrics": [metric], "graphs": []})

    def test_sensor_policy(self):
        cases = [
            ("wifi_strength", "WiFi Strength", (0, 100)),
            ("wifi_rssi_db", "WiFi RSSI", (-120, 0)),
            ("rssi_dbm", "Bluetooth signal", (-120, 0)),
            ("input_voltage", "UPS Meterkast", (0, 300)),
            ("voltage_l1_v", "Voltage Phase 1", (0, 300)),
            ("battery_pct", "Battery Critical", (0, 100)),
            ("flow_lpm", "Active Liter Per Minute", (0, None)),
            ("power_l1_w", "Power Phase 1", (None, None)),
            ("voltage", "Unrelated voltage sensor", (None, None)),
            ("/", "Disk /", (None, None)),
        ]
        for key, service, expected in cases:
            with self.subTest(key=key, service=service):
                self.assertEqual(collector.sensor_bounds({"metric": key, "service": service}), expected)

    def test_explicit_sensor_override(self):
        metric = self.metric(metric="wifi_strength", service="WiFi Strength",
                             min_value=10, max_value=90)
        self.assertEqual(collector.metric_bounds(metric, {}), (10, 90))

    def test_explicit_percent_bounds(self):
        self.validate(self.metric(min_value=0, max_value=100))
        with self.assertRaises(ValueError):
            self.validate(self.metric(min_value=101, max_value=100))

    def test_signed_temperature_bounds(self):
        self.validate(self.metric(type="GAUGE_SIGNED", min_value=-40, max_value=80))

    def test_interface_speed_bounds(self):
        metric = self.metric(type="COUNTER", metric="nic0_in_octet")
        self.assertEqual(collector.metric_bounds(metric, {"nic0_speed_bps": 1_000_000_000}),
                         (None, 131_250_000))
        self.assertEqual(collector.metric_bounds(metric, {}), (None, None))

    def test_explicit_limit_is_not_relaxed(self):
        metric = self.metric(type="COUNTER", metric="nic0_out_octet", max_value=10_000_000)
        self.assertEqual(collector.metric_bounds(metric, {"nic0_speed_bps": 1_000_000_000}),
                         (None, 10_000_000))

if __name__ == "__main__":
    unittest.main()


class ScopedTuningTests(unittest.TestCase):
    def test_fault_only_plan_excludes_traffic_and_voltage(self):
        tuner = load('fault_tuner', 'tune-rrd-bounds.py')
        metrics = [dict(id=str(i), host='switch', service='Interface port1', metric=name,
                        type='COUNTER' if i < 3 else 'GAUGE', min_value=0, max_value=100)
                   for i, name in enumerate(('port1_in_error', 'port1_out_discard',
                                              'port1_in_octet', 'voltage'))]
        catalog = config.validate(dict(version=1, metrics=metrics, graphs=[]))
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            for m in catalog["metrics"].values():
                (root / config.archive_filename(m)).touch()
            info = mock.Mock(stdout='ds[value].min = NaN\nds[value].max = NaN\n')
            with mock.patch.object(tuner.subprocess, 'run', return_value=info) as run:
                changes = tuner.plan(catalog, root, {}, interface_faults_only=True)
            self.assertEqual(len(changes), 2)
            self.assertEqual(run.call_count, 2)
            self.assertTrue(all(c[1:] == (0, 100) for c in changes))
            self.assertTrue(all(call.args[0][:2] == ['rrdtool', 'info'] for call in run.call_args_list))
