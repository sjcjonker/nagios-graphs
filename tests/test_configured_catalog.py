"""Explicit-mode contracts: exact collection, identity, rendering and failures."""
import copy
import hashlib
import importlib.util
from importlib.machinery import SourceFileLoader
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]


def module(name, filename):
    loader = SourceFileLoader(name, str(ROOT / 'scripts' / filename))
    result = importlib.util.module_from_spec(importlib.util.spec_from_loader(name, loader))
    loader.exec_module(result)
    return result


class ConfiguredCatalogTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.config = json.loads((ROOT / 'examples/graphs.json').read_text())
        self.path = self.root / 'graphs.json'
        self.write()
        patch = mock.patch.dict(os.environ, NAGIOS_GRAPHS_CONFIG=str(self.path))
        patch.start()
        self.addCleanup(patch.stop)
        self.importer = module('explicit_import', 'nagios-rrd-import.py')
        self.web = module('explicit_web', 'nagios-rrd-web.cgi')
        self.catalog = module('explicit_config', 'nagios_graphs_config.py')

    def write(self):
        self.path.write_text(json.dumps(self.config))

    def test_exact_allowlist_no_legacy_fallback(self):
        self.assertEqual(self.importer.classify('server01', 'Temperature', 'temperature_c'), 'GAUGE_SIGNED')
        for identity in [('other', 'Temperature', 'temperature_c'),
                         ('server01', 'Other', 'temperature_c'),
                         ('server01', 'Temperature', 'other'),
                         ('unconfigured', 'CPU', 'cpu_pct')]:
            self.assertIsNone(self.importer.classify(*identity))
        self.assertEqual(set(self.web.all_series()), {'server-temperature', 'uplink'})

    def test_archive_alias_matches_original_algorithm(self):
        self.config['metrics'][0]['archive_service'] = 'Old temperature'
        self.write()
        identity = ['server01', 'Old temperature', 'temperature_c']
        expected = hashlib.sha256(json.dumps(identity).encode()).hexdigest()[:32]
        self.assertEqual(self.importer.identifier('server01', 'Temperature', 'temperature_c'), expected)
        self.assertEqual(self.web.archive_id('server01', 'Temperature', 'temperature_c'), expected)

    def test_invalid_or_missing_selected_config_fails_closed(self):
        self.path.unlink()
        with self.assertRaises(OSError):
            self.importer.classify('unconfigured', 'CPU', 'cpu_pct')
        self.path.write_text('{bad json')
        with self.assertRaises(ValueError):
            self.web.all_series()

    def test_invalid_catalog_contracts(self):
        mutations = [
            lambda c: c.update(version=2),
            lambda c: c['metrics'].append(copy.deepcopy(c['metrics'][0])),
            lambda c: c['metrics'][0].update(type='EXEC'),
            lambda c: c['metrics'][0].update(heartbeat=0),
            lambda c: c['graphs'][0]['lines'][0].update(metric='missing'),
            lambda c: c['graphs'][0]['lines'][0].update(color='red:extra'),
            lambda c: c['graphs'][0]['lines'][0].update(scale=float('nan')),
            lambda c: c['graphs'][0]['lines'][0].update(subtract='rx'),
            lambda c: c.update(nagios_url='//external.example'),
            lambda c: c['graphs'][0].update(id='../bad'),
        ]
        for mutate in mutations:
            config = copy.deepcopy(self.config)
            mutate(config)
            with self.subTest(config=config), self.assertRaises(ValueError):
                self.catalog.validate(config)

    def test_generic_commands_require_archives_and_scale_counter_rates(self):
        with self.assertRaises(FileNotFoundError):
            self.web.graph_command('uplink', 'day', self.root)
        for metric in self.catalog.load()['metrics'].values():
            (self.root / (self.catalog.archive_id(metric) + '.rrd')).touch()
        args = self.web.graph_command('uplink', 'day', self.root)
        self.assertIn('CDEF:line0=line0_raw,8e-06,*', args)
        self.assertIn('LINE2:line1#D97706:Outbound', args)
        self.assertEqual(args[:3], ['/usr/bin/rrdtool', 'graph', '-'])
        self.assertNotIn('--lower-limit', self.web.graph_command('server-temperature', 'day', self.root))
        self.assertEqual(self.web.parse_selection('view=uplink')[0], 'uplink')
        self.assertEqual(self.web.parse_selection('host=switch01&service=Uplink')[0], 'uplink')
        with self.assertRaises(ValueError):
            self.web.parse_selection('view=home')

    def test_configured_heartbeat_and_signed_archive(self):
        self.config['metrics'][0]['heartbeat'] = 7200
        self.write()
        batch = self.root / 'samples.batch'
        batch.write_text('1791828000\tserver01\tTemperature\ttemperature_c=-5\n')
        with mock.patch.object(self.importer.subprocess, 'run', return_value=mock.Mock(returncode=0)) as run:
            self.assertEqual(self.importer.import_batch(batch, self.root / 'rrd'), (1, 0))
        self.assertIn('DS:value:GAUGE:7200:U:U', run.call_args_list[0].args[0])

    def test_missing_config_never_creates_archive_directory(self):
        self.path.unlink()
        with self.assertRaises(OSError):
            self.importer.import_batch(self.root / 'input', self.root / 'rrd')
        self.assertFalse((self.root / 'rrd').exists())

    def test_perfdata_unknown_nonfinite_and_semicolon_separated_load(self):
        parsed = self.importer.parse_perfdata("load1=0.5;1;2;load5=1.5;2;3 battery=U value=nan huge=1e999")
        self.assertEqual(parsed, {'load1': .5, 'load5': 1.5, 'battery': None})

    def test_malformed_rows_are_rejected_and_unconfigured_metrics_not_written(self):
        batch = self.root / 'input.batch'
        batch.write_text('broken\nnot-a-timestamp\tserver01\tTemperature\ttemperature_c=5\n'
                         '1791828000\tunconfigured\tStatus\tother=5\n')
        with mock.patch.object(self.importer.subprocess, 'run') as run:
            self.assertEqual(self.importer.import_batch(batch, self.root / 'rrd'), (0, 2))
        run.assert_not_called()

    def test_replay_is_ignored_only_after_rrd_confirms_timestamp(self):
        batch = self.root / 'input.batch'
        batch.write_text('1791828000\tserver01\tTemperature\ttemperature_c=-5\n')
        destination = self.root / 'rrd'
        destination.mkdir()
        archive = self.importer.identifier('server01', 'Temperature', 'temperature_c')
        (destination / (archive + '.rrd')).touch()
        for last, expected in (('1791828000', (0, 0)), ('1791827999', (0, 1))):
            responses = [mock.Mock(returncode=1, stderr='illegal attempt to update', stdout=''),
                         mock.Mock(returncode=0, stdout=last)]
            with mock.patch.object(self.importer.subprocess, 'run', side_effect=responses):
                self.assertEqual(self.importer.import_batch(batch, destination), expected)

    def test_large_counter_keeps_integer_rrd_update_format(self):
        batch = self.root / 'input.batch'
        batch.write_text('1791828000\tswitch01\tUplink\trx_bytes=1000000000000c\n')
        with mock.patch.object(self.importer.subprocess, 'run', return_value=mock.Mock(returncode=0)) as run:
            self.assertEqual(self.importer.import_batch(batch, self.root / 'rrd'), (1, 0))
        self.assertEqual(run.call_args_list[-1].args[0][-1], '1791828000:1000000000000')

    @unittest.skipUnless(shutil.which('rrdtool'), 'rrdtool missing')
    def test_real_explicit_import_and_render(self):
        batch = self.root / 'samples.batch'
        batch.write_text(''.join(f'{stamp}\tserver01\tTemperature\ttemperature_c=-5\n'
                                 for stamp in (1791828000, 1791828060, 1791828120)))
        self.assertEqual(self.importer.import_batch(batch, self.root / 'rrd'), (3, 0))
        args = self.web.graph_command('server-temperature', 'hour', self.root / 'rrd')
        args[args.index('--end') + 1] = '1791828180'
        self.assertTrue(subprocess.check_output(args).startswith(b'\x89PNG\r\n\x1a\n'))


if __name__ == '__main__':
    unittest.main()
