#!/usr/bin/env python3
"""Unit tests for the dedicated Nagios spool filesystem check."""
from pathlib import Path
import importlib.util
import unittest
from unittest import mock

source=Path(__file__).resolve().parents[1]/"scripts/check-nagios-graphs-spool.py"
spec=importlib.util.spec_from_file_location("check_graph_spool",source)
mod=importlib.util.module_from_spec(spec)
spec.loader.exec_module(mod)

class GraphSpoolCheckTests(unittest.TestCase):
    def test_requires_dedicated_mount(self):
        with mock.patch.object(mod.subprocess,"run",return_value=mock.Mock(returncode=0,stdout="/var ext4\n")):
            status,_=mod.assess(Path("/var/spool/nagios-graphs"))
        self.assertEqual(status,2)

    def test_free_thresholds(self):
        path=Path("/var/spool/nagios-graphs")
        for available,expected in ((30,0),(15,1),(5,2)):
            stat=mock.Mock(f_blocks=100,f_bavail=available)
            with mock.patch.object(mod.subprocess,"run",return_value=mock.Mock(returncode=0,stdout=f"{path} ext4\n")), mock.patch.object(mod.os,"statvfs",return_value=stat):
                code,_=mod.assess(path)
            self.assertEqual(code,expected)


    def test_other_dedicated_filesystem_and_shared_opt_in(self):
        path = Path('/var/spool/nagios-graphs')
        stat = mock.Mock(f_blocks=100, f_bavail=30)
        with mock.patch.object(mod.subprocess, 'run', return_value=mock.Mock(returncode=0, stdout=f'{path} xfs\n')), mock.patch.object(mod.os, 'statvfs', return_value=stat):
            self.assertEqual(mod.assess(path)[0], 2)
            self.assertEqual(mod.assess(path, filesystem='any')[0], 0)
        with mock.patch.object(mod.subprocess, 'run') as run, mock.patch.object(mod.os, 'statvfs', return_value=stat):
            self.assertEqual(mod.assess(path, allow_shared=True)[0], 0)
            run.assert_not_called()
        self.assertEqual(mod.assess(path, warning=10, critical=20)[0], 3)
