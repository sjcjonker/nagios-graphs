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
                (self.root / (self.helper.archive_id(metric) + '.rrd')).touch()

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
