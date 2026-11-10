"""The copied-layout test runs from a separate working directory without RRDTool."""
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / 'tools/test-staged-install.py'


class StagedInstallTests(unittest.TestCase):
    def test_layout_validates_copied_helpers_and_records_skipped_runtime(self):
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory) / 'installation'
            result = subprocess.run([sys.executable, str(SCRIPT), '--layout-only', '--output', str(base)],
                                    cwd=directory, capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stderr)
            report = json.loads((base / 'report.json').read_text())
            self.assertEqual(report['rrd_and_cgi_test'], 'skipped')
            self.assertEqual(report['mode'], 'layout-only')
            self.assertTrue(report['layout_valid'])
            config = json.loads((base / 'etc/nagios-graphs/graphs.json').read_text())
            self.assertEqual((len(config['metrics']), len(config['graphs'])), (9, 5))
            wrapper = (base / 'usr/lib/cgi-bin/nagios4/graphs.cgi').read_text()
            self.assertIn(str(base / 'usr/local/libexec/nagios-rrd-web.cgi'), wrapper)
            self.assertFalse(list((base / 'var/lib/nagios-graphs/rrd').glob('*.rrd')))

    def test_existing_directory_is_not_reused_or_overwritten(self):
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            marker = base / 'keep'; marker.write_text('keep this file')
            result = subprocess.run([sys.executable, str(SCRIPT), '--layout-only', '--output', str(base)],
                                    capture_output=True, text=True)
            self.assertNotEqual(result.returncode, 0)
            self.assertEqual(marker.read_text(), 'keep this file')
            self.assertFalse((base / 'usr').exists())


if __name__ == '__main__':
    unittest.main()
