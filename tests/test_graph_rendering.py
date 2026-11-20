"""Rendering contracts for generic dual axes, missing counters and gap overlays."""
import copy
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest
from unittest import mock

from test_configured_catalog import module


class RenderingTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.path = self.root / 'catalog.json'
        self.data = dict(version=1, metrics=[
            dict(id='left', host='server', service='Status', metric='value', type='GAUGE_SIGNED'),
            dict(id='right', host='server', service='Status', metric='voltage'),
            dict(id='optional', host='server', service='Status', metric='faults', type='COUNTER')],
            graphs=[dict(id='status', title='Device status', unit='%', axis_label='Usage (%)',
                         right_axis=dict(unit='V', scale=3), lines=[
                             dict(metric='left', label='Battery', color='#1682B2'),
                             dict(metric='right', label='Voltage', color='#D97706', axis='right', unit='V'),
                             dict(metric='optional', label='Errors', color='#FF0000', axis='right', unit='/s', optional=True)])])
        self.write()
        patch = mock.patch.dict(os.environ, NAGIOS_GRAPHS_CONFIG=str(self.path))
        patch.start()
        self.addCleanup(patch.stop)
        self.web = module('render_web', 'nagios-rrd-web.cgi')
        self.imp = module('render_imp', 'nagios-rrd-import.py')
        self.helper = module('render_config', 'nagios_graphs_config.py')

    def write(self):
        self.path.write_text(json.dumps(self.data))

    def archives(self, optional=False):
        for metric in self.helper.load()['metrics'].values():
            if metric['id'] != 'optional' or optional:
                (self.root / (self.helper.archive_filename(metric))).touch()

    def test_scaled_byte_units_and_percent_disable_automatic_prefixes(self):
        self.archives()
        for unit in ('MB', 'GB', 'GiB', '%'):
            self.helper._cache = None
            self.data['graphs'][0]['unit'] = unit
            self.write()
            args = self.helper.graph_command('status', 3600, self.root, 860, 280)
            self.assertEqual(args[args.index('--units-exponent') + 1], '0')
        self.helper._cache = None
        self.data['graphs'][0]['unit'] = 'B'
        self.write()
        self.assertNotIn('--units-exponent', self.helper.graph_command('status', 3600, self.root, 860, 280))
        self.helper._cache = None
        self.data['graphs'][0].update(unit='GB', units_exponent=3)
        self.write()
        args = self.helper.graph_command('status', 3600, self.root, 860, 280)
        self.assertEqual(args.count('--units-exponent'), 1)
        self.assertEqual(args[args.index('--units-exponent') + 1], '3')

    def test_shared_unit_defaults_and_explicit_axis_override(self):
        self.archives()
        for unit in ('W', 'load', 'Mbit/s', '/s', 'Errors/s', 'Discards/s'):
            self.helper._cache = None
            self.data['graphs'][0]['unit'] = unit
            self.write()
            args = self.helper.graph_command('status', 3600, self.root, 860, 280)
            self.assertEqual(args[args.index('--units-exponent') + 1], '0')
        self.helper._cache = None
        self.data['graphs'][0].update(unit='W', left_format='%.3lf', units_exponent=3)
        self.write()
        args = self.helper.graph_command('status', 3600, self.root, 860, 280)
        self.assertEqual(args[args.index('--left-axis-format') + 1], '%.3lf')
        self.assertEqual(args[args.index('--units-exponent') + 1], '3')
        for unit, expected in (('W', '%8.0lf W'), ('B', '%8.2lf %sB'),
                               ('/s', '%8.4lf /s'), ('Errors/s', '%8.4lf Errors/s'),
                               ('load', '%8.2lf load'), ('%', '%8.2lf %%')):
            self.assertEqual(self.helper.legend_format(unit), expected)
        args = self.web.style_and_legend(['rrdtool', 'graph', '-', 'LINE2:line0#1682B2:Power'], 'W')
        self.assertTrue(any(a.startswith('GPRINT:latest_line0:') and '%8.0lf W' in a for a in args))

    def test_host_navigation_does_not_probe_other_hosts_archives(self):
        for i, (host, graph_id) in enumerate((('other-host', 'other-graph'), ('server', 'same-host'))):
            self.data['metrics'].append(dict(id='extra' + str(i), host=host,
                service='Status', metric='extra', type='GAUGE'))
            self.data['graphs'].append(dict(id=graph_id, title=graph_id, unit='W',
                lines=[dict(metric='extra' + str(i), label='Power', color='#1682B2')]))
        self.write()
        with mock.patch.object(self.web, 'graph_command', return_value=[]) as render:
            visible = self.web.available_series('server')
        self.assertEqual(set(visible), {'status', 'same-host'})
        self.assertEqual([c.args[0] for c in render.call_args_list], ['status', 'same-host'])

    def test_long_legend_labels_keep_statistics_aligned(self):
        labels = ['/', '/var/unbound/usr/local/share/GeoIP']
        args = self.web.style_and_legend(['rrdtool', 'graph', '-'] +
            [f'LINE2:line{i}#1682B2:{label}' for i, label in enumerate(labels)], '%')
        widths = []
        for index, arg in enumerate(args):
            if arg.startswith('LINE2:'):
                widths.append(len(arg.split(':', 2)[2]) +
                              len(args[index + 1].removeprefix('COMMENT:')))
        self.assertEqual(widths[0], widths[1])

    def test_adaptive_voltage_range_is_explicit_and_validated(self):
        self.data['graphs'][0].update(unit='V', autoscale=True)
        self.write()
        self.archives()
        args = self.helper.graph_command('status', 3600, self.root, 860, 280)
        self.assertIn('--alt-autoscale', args)
        self.assertNotIn('--lower-limit', args)
        self.data['graphs'][0]['autoscale'] = 'yes'
        with self.assertRaisesRegex(ValueError, 'autoscale'):
            self.helper.validate(self.data)

    def test_raw_byte_legends_use_si_prefix_without_changing_statistics(self):
        args=self.web.style_and_legend(['rrdtool','graph','-','LINE2:used#1682B2:Used'],'B')
        self.assertIn('VDEF:latest_used=used,LAST',args)
        self.assertTrue(any(arg.startswith('GPRINT:latest_used:') and '%sB' in arg for arg in args))
        other=self.web.style_and_legend(['rrdtool','graph','-','LINE2:used#1682B2:Used'],'W')
        self.assertFalse(any('%s' in arg for arg in other if arg.startswith('GPRINT:')))

    def test_percent_autoscale_preserves_zero_baseline(self):
        graph=self.data['graphs'][0]
        graph.pop('right_axis')
        graph['lines']=[graph['lines'][0]]
        graph.update(unit='%',lower=0,autoscale=True)
        self.write();self.archives()
        args=self.helper.graph_command('status',3600,self.root,860,280)
        self.assertIn('--alt-autoscale-max',args)
        self.assertNotIn('--upper-limit',args)

    def test_missing_optional_is_unknown_and_stats_use_original_values(self):
        self.archives()
        args = self.web.graph_command('status', 'day', self.root)
        self.assertIn('CDEF:line2=line0_raw,POP,UNKN', args)
        self.assertIn('CDEF:line1_scaled=line1,3,/', args)
        self.assertIn('VDEF:latest_line1_scaled=line1,LAST', args)
        self.assertTrue(any(x.startswith('GPRINT:latest_line1_scaled:') and ' V' in x for x in args))
        self.assertEqual(args[args.index('--right-axis') + 1], '3:0')

    def test_required_archive_missing_hides_catalog_entry(self):
        with mock.patch.object(self.web, 'RRD_DIR', self.root):
            self.assertEqual(self.web.available_series(), {})

    def test_optional_first_line_has_defined_dependency(self):
        lines = self.data['graphs'][0]['lines']
        lines.insert(0, lines.pop())
        self.write()
        self.archives()
        args = self.web.graph_command('status', 'day', self.root)
        self.assertIn('CDEF:line0=line1_raw,POP,UNKN', args)
        self.assertLess(next(i for i, v in enumerate(args) if v.startswith('DEF:line1_raw')),
                        args.index('CDEF:line0=line1_raw,POP,UNKN'))

    def test_auto_axis_uses_same_end_and_preserves_physical_statistics(self):
        self.data['graphs'][0]['right_axis']['scale'] = 'auto'
        self.write()
        self.archives()
        calls = []
        def run(args, **kwargs):
            calls.append(args)
            if args[2] == '/dev/null':
                return mock.Mock(returncode=0, stdout='peak=100\npeak=200\npeak=nan\n')
            return mock.Mock(returncode=0, stdout=b'\x89PNG\r\n\x1a\n', stderr=b'')
        with mock.patch.object(self.web.subprocess, 'run', side_effect=run):
            self.web.render_graph('status', 'day', self.root, window=(1791828000, 1791828600))
        self.assertEqual(calls[-1][calls[-1].index('--right-axis') + 1], '2.0:0')
        self.assertTrue(all(args[args.index('--end') + 1] == '1791828600' for args in calls))
        self.assertIn('VDEF:latest_line1_scaled=line1,LAST', calls[-1])
        self.assertNotIn('--right-axis-format', calls[-1])

    def test_auto_fault_axis_numeric_labels_and_only_known_zero_is_hidden(self):
        graph = self.data['graphs'][0]
        graph['right_axis']['scale'] = 'auto'
        graph['lines'] = [graph['lines'][0], graph['lines'][2]]
        self.write()
        self.archives(optional=True)
        for peak, expected in (('0', None), ('0.24', '%.2lf'),
                               ('0.001', '%.4lf'), ('40', '%.0lf'), ('nan', '%.2lf')):
            calls = []
            def run(args, **kwargs):
                calls.append(args)
                return mock.Mock(returncode=0, stderr=b'',
                    stdout=f'peak=10\npeak={peak}\n' if args[2] == '/dev/null'
                           else b'\x89PNG\r\n\x1a\n')
            with mock.patch.object(self.web.subprocess, 'run', side_effect=run):
                self.web.render_graph('status', 'hour', self.root,
                                      window=(1791828000, 1791828600))
            final = calls[-1]
            if expected is None:
                self.assertNotIn('--right-axis', final)
                self.assertNotIn('--right-axis-label', final)
                self.assertNotIn('--right-axis-format', final)
            else:
                self.assertIn('--right-axis', final)
                self.assertEqual(final[final.index('--right-axis-format') + 1], expected)
            self.assertIn('VDEF:latest_line1_scaled=line1,LAST', final)
            self.assertTrue(any(a.startswith('LINE2:line0') for a in final))
            self.assertTrue(any(a.startswith('LINE2:line1_scaled') for a in final))

    def test_unknown_fault_series_prevents_hiding_and_explicit_format_wins(self):
        graph = self.data['graphs'][0]
        self.data['metrics'][1]['type'] = 'COUNTER'
        graph['lines'][1]['unit'] = '/s'
        graph['right_axis']['scale'] = 'auto'
        graph['right_format'] = '%.5lf'
        self.write()
        self.archives(optional=True)
        calls = []
        def run(args, **kwargs):
            calls.append(args)
            return mock.Mock(returncode=0, stderr=b'',
                stdout='peak=10\npeak=0\npeak=nan\n' if args[2] == '/dev/null'
                       else b'\x89PNG\r\n\x1a\n')
        with mock.patch.object(self.web.subprocess, 'run', side_effect=run):
            self.web.render_graph('status', 'hour', self.root,
                                  window=(1791828000, 1791828600))
        self.assertIn('--right-axis', calls[-1])
        self.assertEqual(calls[-1][calls[-1].index('--right-axis-format') + 1], '%.5lf')

    def test_polling_tail_is_dashed_bounded_and_not_in_statistics(self):
        self.data['metrics'][0]['poll_interval_seconds'] = 900
        self.write()
        self.archives()
        args = self.web.graph_command('status', 'hour', self.root)
        original_stats = [a for a in args if a.startswith(('VDEF:', 'GPRINT:'))]
        with mock.patch.object(self.web.subprocess, 'run', return_value=mock.Mock(stdout='1791828500')):
            tail = self.web.add_polling_tails(args, self.data['graphs'][0],
                                               self.helper.load()['metrics'], self.root, 1791828600)
        self.assertIn('LINE1:tail0#1682B2::dashes=3,3', tail)
        self.assertIn('CDEF:tail0=TIME,1791827700,GE,line0,UN,*,tailhold0,UNKN,IF', tail)
        self.assertEqual(original_stats, [a for a in tail if a.startswith(('VDEF:', 'GPRINT:'))])
        with mock.patch.object(self.web.subprocess, 'run', return_value=mock.Mock(stdout='1791827000')):
            stale = self.web.add_polling_tails(args, self.data['graphs'][0],
                                                self.helper.load()['metrics'], self.root, 1791828600)
        self.assertEqual(stale, args)

    def test_gap_expression_never_extrapolates(self):
        self.assertIsNone(self.web.gap_expression([(1, None), (2, 4), (3, None)]))
        expression = self.web.gap_expression([(1, 2), (2, None), (3, 6)])
        self.assertIn('TIME,1,GE,TIME,3,LE', expression)

    def test_gap_overlay_is_unlabelled_and_not_used_in_statistics(self):
        self.data['graphs'][0]['lines'][0]['bridge_gaps'] = True
        self.write()
        self.archives()
        args = self.web.graph_command('status', 'day', self.root)
        xml = b'<xport><data><row><t>1</t><v>2</v></row><row><t>2</t><v>NaN</v></row><row><t>3</t><v>6</v></row></data></xport>'
        with mock.patch.object(self.web.subprocess, 'run', return_value=mock.Mock(stdout=xml)):
            bridged = self.web.add_gap_bridges(args, 1, 3, self.data['graphs'][0])
        self.assertIn('LINE1:gap0#1682B2::dashes=4,4', bridged)
        self.assertEqual([a for a in bridged if a.startswith(('VDEF:', 'GPRINT:'))],
                         [a for a in args if a.startswith(('VDEF:', 'GPRINT:'))])

    def test_invalid_selection_and_empty_catalog(self):
        self.assertEqual(self.web.application({'QUERY_STRING': 'view=../other'})[0], '400 Bad Request')
        self.data['graphs'] = []
        self.write()
        empty = module('empty_web', 'nagios-rrd-web.cgi')
        status, _, body = empty.application({'QUERY_STRING': ''})
        self.assertEqual(status, '200 OK')
        self.assertIn(b'No graph data', body)

    def test_hidden_secondary_graphs_do_not_break_service_links(self):
        other = copy.deepcopy(self.data['graphs'][0])
        other.update(id='secondary', hidden=True)
        self.data['graphs'].append(other)
        self.write()
        self.assertEqual(self.web.parse_selection('host=server&service=Status')[0], 'status')
        self.assertFalse(self.web.listed_view('secondary'))

    @unittest.skipUnless(shutil.which('rrdtool'), 'rrdtool missing')
    def test_real_quiet_and_nonzero_fault_axes_render_without_archive_changes(self):
        graph = self.data['graphs'][0]
        graph['right_axis']['scale'] = 'auto'
        graph['lines'] = [graph['lines'][0], graph['lines'][2]]
        self.write()
        batch = self.root / 'faults.batch'
        # A quiet window followed by 0.25 events/s, retaining the counter's values.
        batch.write_text(''.join(f'{stamp}\tserver\tStatus\tvalue=10 faults={count}c\n'
            for stamp, count in ((1791828000, 0), (1791828060, 0), (1791828120, 0),
                                 (1791828180, 0), (1791828240, 15), (1791828300, 30))))
        self.assertEqual(self.imp.import_batch(batch, self.root), (12, 0))
        before = {p: p.read_bytes() for p in self.root.glob('*.rrd')}
        for window in ((1791828000, 1791828180), (1791828180, 1791828360)):
            png = self.web.render_graph('status', 'hour', self.root, window=window)
            self.assertTrue(png.startswith(b'\x89PNG\r\n\x1a\n'))
        self.assertEqual(before, {p: p.read_bytes() for p in before})

    @unittest.skipUnless(shutil.which('rrdtool'), 'rrdtool missing')
    def test_real_shared_unit_legends_render_without_archive_changes(self):
        graph = self.data['graphs'][0]
        graph.pop('right_axis')
        graph['lines'] = [graph['lines'][0]]
        self.write()
        batch = self.root / 'input.batch'
        batch.write_text(''.join(f'{stamp}\tserver\tStatus\tvalue=940490463.64\n'
                                 for stamp in (1791828000, 1791828060, 1791828120)))
        self.assertEqual(self.imp.import_batch(batch, self.root), (3, 0))
        before = {p: p.read_bytes() for p in self.root.glob('*.rrd')}
        for unit in ('W', 'B', '%', 'load', 'Errors/s', 'Discards/s'):
            graph['unit'] = unit
            self.write()
            self.web._catalog_module = None
            png = self.web.render_graph('status', 'hour', self.root,
                                        window=(1791828000, 1791828180))
            self.assertTrue(png.startswith(b'\x89PNG\r\n\x1a\n'))
        self.assertEqual(before, {p: p.read_bytes() for p in before})

    @unittest.skipUnless(shutil.which('rrdtool'), 'rrdtool missing')
    def test_real_signed_dual_axis_gap_and_immutable_archives(self):
        self.data['graphs'][0]['right_axis']['scale'] = 'auto'
        self.data['graphs'][0]['lines'][0]['bridge_gaps'] = True
        self.write()
        batch = self.root / 'input.batch'
        batch.write_text(''.join(f'{stamp}\tserver\tStatus\tvalue={value} voltage=230\n'
                                 for stamp, value in ((1791828000, -5), (1791828060, -5),
                                                      (1791828120, 'U'), (1791828180, 'U'),
                                                      (1791828240, 5), (1791828300, 5))))
        self.assertEqual(self.imp.import_batch(batch, self.root), (12, 0))
        before = {p: p.read_bytes() for p in self.root.glob('*.rrd')}
        png, bounds = self.web.render_graph('status', 'hour', self.root,
                                             window=(1791828000, 1791828360), details=True)
        self.assertTrue(png.startswith(b'\x89PNG\r\n\x1a\n'))
        self.assertGreater(bounds['graph_width'], 0)
        self.assertEqual(before, {p: p.read_bytes() for p in before})


if __name__ == '__main__':
    unittest.main()
