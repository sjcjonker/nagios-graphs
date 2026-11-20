"""Interface edits must change the catalog, never its archive identity."""
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("interface_generator", ROOT / "scripts/generate-interface-graphs.py")
generator = importlib.util.module_from_spec(spec)
spec.loader.exec_module(generator)


class GenerationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        (self.root / "nagios.cfg").write_text("cfg_file=/etc/nagios4/services.cfg\n")
        self.config = self.root / "services.cfg"
        self.seed = self.root / "seed.json"
        self.seed.write_text(json.dumps([dict(host="switch", service="Old label", key="if-existing",
                                               metric_in="Port 1_in_octet")]))

    def services(self, *pairs):
        self.config.write_text("define service {\n name interface\n register 0\n process_perf_data 1\n}\n" +
            "".join("define service {\n use interface\n host_name switch\n service_description " + label +
                    "\n check_command check_int_noregex!$USER8$!'" + interface + "'\n}\n"
                    for interface, label in pairs))

    def test_explicit_capacity_bounds_virtual_octet_counters_only(self):
        self.services(('Port 1', 'WAN'))
        self.config.write_text(self.config.read_text().replace('check_command check_int_noregex',
            '_GRAPH_MAX_BPS 1000000000\n check_command check_int_noregex'))
        rows = generator.generate(self.root, self.seed)
        self.assertEqual(rows[0]['max_bps'], 1_000_000_000)
        catalog = generator.catalog(dict(version=1, metrics=[], graphs=[]), rows)
        for metric in catalog['metrics']:
            if metric['metric'].endswith('_octet'):
                self.assertEqual(metric['max_value'], 131_250_000)
                self.assertEqual(metric['min_value'], 0)
            else:
                self.assertAlmostEqual(metric['max_value'], 2**31 / 900)
                self.assertEqual(metric['min_value'], 0)
        self.config.write_text(self.config.read_text().replace('1000000000', '-1'))
        with self.assertRaisesRegex(ValueError, '_GRAPH_MAX_BPS'):
            generator.generate(self.root, self.seed)

    def test_fault_bounds_follow_interval_and_allow_explicit_override(self):
        self.services(('Port 1', 'Uplink'))
        rows = generator.generate(self.root, self.seed)
        for interval in (60, 300, 900):
            rows[0]['poll_interval_seconds'] = interval
            data = generator.catalog(dict(version=1, metrics=[], graphs=[]), rows)
            faults = [m for m in data['metrics'] if not m['metric'].endswith('_octet')]
            self.assertEqual(len(faults), 4)
            self.assertTrue(all(m['max_value'] == 2**31 / interval for m in faults))
            # The synthetic 32-bit reset rate is above the bound; steady discards survive.
            self.assertLess(40, faults[0]['max_value'])
        self.assertLess(faults[0]['max_value'], 4_772_185)
        self.config.write_text(self.config.read_text().replace('check_command check_int_noregex',
            '_GRAPH_MAX_FAULTS_PPS 5000000\n check_command check_int_noregex'))
        rows = generator.generate(self.root, self.seed)
        data = generator.catalog(dict(version=1, metrics=[], graphs=[]), rows)
        self.assertTrue(all(m['max_value'] == 5_000_000 for m in data['metrics']
                            if not m['metric'].endswith('_octet')))
        self.config.write_text(self.config.read_text().replace('5000000', '0'))
        with self.assertRaisesRegex(ValueError, '_GRAPH_MAX_FAULTS_PPS'):
            generator.generate(self.root, self.seed)

    def test_rename_add_delete_and_empty_catalog(self):
        self.services(("Port 1", "Old label"))
        first = generator.generate(self.root, self.seed)[0]
        self.services(("Port 1", "New uplink / Office & backup"), ("Port 2", "New interface"))
        rows = generator.generate(self.root, self.seed)
        renamed, added = rows
        self.assertEqual((renamed["key"], renamed["archive_service"]), (first["key"], "Old label"))
        self.assertEqual(renamed["service"], "New uplink / Office & backup")
        self.services(("Port 2", "Renamed new interface"))
        remaining = generator.generate(self.root, self.seed)
        self.assertEqual(len(remaining), 1)
        self.assertEqual(remaining[0]["key"], added["key"])
        self.assertEqual(remaining[0]["archive_service"], added["archive_service"])
        self.services()
        self.assertEqual(generator.generate(self.root, self.seed), [])

    def test_polling_interval_is_inherited_and_added_to_catalog(self):
        self.services(('Port 1', 'Uplink'))
        self.config.write_text(self.config.read_text().replace('process_perf_data 1',
            'process_perf_data 1\n check_interval 15'))
        rows = generator.generate(self.root, self.seed)
        self.assertEqual(rows[0]['poll_interval_seconds'], 900)
        intervals = generator.service_intervals(self.root)
        self.assertEqual(intervals[('switch', 'Uplink')], 900)
        base = dict(version=1, metrics=[dict(id='base', host='switch', service='Uplink',
                                               metric='temp')], graphs=[])
        data = generator.catalog(base, rows, intervals)
        self.assertTrue(all(m['poll_interval_seconds'] == 900 for m in data['metrics']))

    def test_32bit_command_preserves_graph_identity(self):
        self.services(('Port 1', 'Uplink'))
        before = generator.generate(self.root, self.seed)
        self.config.write_text(self.config.read_text().replace('check_int_noregex!', 'check_int_32!'))
        self.assertEqual(generator.generate(self.root, self.seed), before)

    def test_multiple_template_precedence_and_fractional_interval_are_shared(self):
        self.services(('Port 1', 'Uplink'))
        self.config.write_text(self.config.read_text().replace('use interface', 'use primary,secondary') +
            'define service {\n name primary\n register 0\n use interface\n check_interval 1.5\n}\n' +
            'define service {\n name secondary\n register 0\n check_interval 5\n}\n')
        rows = generator.generate(self.root, self.seed)
        self.assertEqual(rows[0]['poll_interval_seconds'], 90)
        self.assertEqual(generator.service_intervals(self.root)[('switch', 'Uplink')], 90)
        self.assertEqual(generator.catalog(dict(version=1, metrics=[], graphs=[]), rows)
                         ['metrics'][0]['poll_interval_seconds'], 90)
        self.config.write_text(self.config.read_text().replace('use interface\n check_interval',
                                                               'use primary\n check_interval'))
        for generate in (lambda: generator.generate(self.root, self.seed),
                         lambda: generator.service_intervals(self.root)):
            with self.assertRaisesRegex(ValueError, 'cyclic service template'):
                generate()

    def test_invalid_intervals_fail_consistently(self):
        self.services(('Port 1', 'Uplink'))
        source = self.config.read_text()
        for value in ('NaN', 'inf', 'invalid', '0', '-1', '10081'):
            self.config.write_text(source.replace('process_perf_data 1',
                                   'process_perf_data 1\n check_interval ' + value))
            for generate in (lambda: generator.generate(self.root, self.seed),
                             lambda: generator.service_intervals(self.root)):
                with self.assertRaisesRegex(ValueError, 'invalid check interval'):
                    generate()

    def test_generated_fault_axes_use_adaptive_precision(self):
        self.services(('Port 1', 'Uplink'))
        data = generator.catalog(dict(version=1, metrics=[], graphs=[]),
                                 generator.generate(self.root, self.seed))
        for graph in data['graphs']:
            self.assertNotIn('left_format', graph)
            self.assertNotIn('right_format', graph)
            self.assertNotIn('units_exponent', graph)

    def test_duplicate_interface_rejected(self):
        self.services(("Port 1", "First"), ("Port 1", "Second"))
        with self.assertRaisesRegex(ValueError, "duplicate interface"):
            generator.generate(self.root, self.seed)

    def test_invalid_description_is_rejected_before_manifest_generation(self):
        self.services(("Port 1", "Port 01 - ../../secret"))
        with self.assertRaisesRegex(ValueError, "invalid interface service description"):
            generator.generate(self.root, self.seed)

    def test_standalone_writes_atomically_without_modifying_existing_on_failure(self):
        self.services(("Port 1", "New label"))
        output = self.root / "interfaces.json"
        output.write_text("[]")
        args = ["generate", "--config-dir", str(self.root), "--seed", str(self.seed), "--output", str(output)]
        with mock.patch.object(generator.sys, "argv", args), \
             mock.patch.object(generator.os, "geteuid", return_value=0):
            self.assertEqual(generator.main(), 0)
        self.assertEqual(output.read_bytes(), generator.render(self.root, self.seed))
        self.assertEqual(output.stat().st_mode & 0o777, 0o644)

    def test_standalone_failure_leaves_active_manifest_untouched(self):
        self.services(("Port 1", "First"), ("Port 1", "Second"))
        output = self.root / "interfaces.json"
        output.write_text("original")
        args = ["generate", "--config-dir", str(self.root), "--seed", str(self.seed), "--output", str(output)]
        with mock.patch.object(generator.sys, "argv", args):
            self.assertEqual(generator.main(), 2)
        self.assertEqual(output.read_text(), "original")

    def test_disabled_perfdata_and_disabled_config_not_collected(self):
        self.services(("Port 1", "Enabled"))
        (self.root / "not-enabled.cfg").write_text(self.config.read_text())
        self.assertEqual(len(generator.generate(self.root, self.seed)), 1)
        self.config.write_text(self.config.read_text().replace("process_perf_data 1", "process_perf_data 0"))
        self.assertEqual(generator.generate(self.root, self.seed), [])

    def test_host_exclusions_are_explicit_and_not_hardcoded(self):
        self.services(('Port 1', 'Uplink'))
        self.assertEqual(len(generator.generate(self.root, self.seed)), 1)
        self.assertEqual(generator.generate(self.root, self.seed, exclude_hosts=('switch',)), [])

    def test_catalog_includes_optional_faults_and_stable_archive_alias(self):
        self.services(('Port 1', 'Renamed uplink'))
        rows = generator.generate(self.root, self.seed)
        data = generator.catalog(dict(version=1, metrics=[], graphs=[]), rows)
        self.assertEqual(len(data['metrics']), 6)
        self.assertEqual(len(data['graphs']), 3)
        self.assertTrue(all(m['archive_service'] == 'Old label' for m in data['metrics']))
        self.assertEqual(data['graphs'][0]['right_axis']['scale'], 'auto')
        self.assertEqual(sum(line.get('optional', False) for line in data['graphs'][0]['lines']), 4)
        self.assertTrue(all(g['hidden'] for g in data['graphs'][1:]))



if __name__ == "__main__":
    unittest.main()
