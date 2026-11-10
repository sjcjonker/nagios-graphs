#!/usr/bin/env python3
"""Copy and exercise an installation layout inside a new temporary directory.

No root privileges, Nagios instance, network access or service activation needed.
The full test requires the host's rrdtool. --layout-only explicitly skips RRD
creation and CGI rendering; it is not the full release smoke test.
"""
from __future__ import annotations
import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import time
from urllib.parse import urlencode

ROOT = Path(__file__).resolve().parents[1]


def copy(source, destination, mode=0o644):
    destination.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(source, destination)
    destination.chmod(mode)


def run(args, base, env):
    result = subprocess.run(args, cwd=base, env=env, stdout=subprocess.PIPE,
                            stderr=subprocess.PIPE, timeout=120, check=False)
    if result.returncode:
        raise RuntimeError(f'{Path(args[0]).name} failed: {result.stderr.decode(errors="replace").strip()}')
    return result.stdout


def stage(base):
    lib = base / 'usr/local/libexec'
    config = base / 'etc/nagios-graphs'
    spool = base / 'var/spool/nagios-graphs'
    rrd = base / 'var/lib/nagios-graphs/rrd'
    for directory in (lib, config, spool, rrd):
        directory.mkdir(parents=True, exist_ok=True)
    for name in ('nagios-rrd-seal.py', 'nagios-rrd-drain.py', 'nagios-rrd-import.py',
                 'nagios_graphs_config.py', 'generate-interface-graphs.py', 'nagios-rrd-web.cgi',
                 'check-nagios-graphs-spool.py'):
        copy(ROOT / 'scripts' / name, lib / name, 0o755 if name == 'check-nagios-graphs-spool.py' else 0o644)
    copy(ROOT / 'examples/graphs.json', config / 'base.json')
    copy(ROOT / 'examples/nagios.cfg', base / 'etc/nagios4/perfdata.cfg.example')
    copy(ROOT / 'scripts/nagios-rrd-hover.js', base / 'usr/share/nagios4/htdocs/rrd-hover.js')
    for name in ('nagios-rrd-drain.service', 'nagios-rrd-drain.timer'):
        copy(ROOT / 'systemd' / name, base / 'etc/systemd/system' / name)
    # Use the documented CGI wrapper, relocated to this staging prefix. The
    # renderer resolves its helper beside the copied runtime, never on the host.
    wrapper = (ROOT / 'examples/graphs.cgi').read_text()
    target = "'/usr/local/libexec/nagios-rrd-web.cgi'"
    if wrapper.count(target) != 1:
        raise ValueError('CGI wrapper layout changed; review the staging test')
    cgi = base / 'usr/lib/cgi-bin/nagios4/graphs.cgi'
    cgi.parent.mkdir(parents=True)
    cgi.write_text(wrapper.replace(target, repr(str(lib / 'nagios-rrd-web.cgi'))))
    cgi.chmod(0o755)
    fixture = base / 'etc/nagios4'
    (fixture / 'nagios.cfg').write_text('cfg_file=interfaces.cfg\n')
    (fixture / 'interfaces.cfg').write_text(
        'define service {\n host_name switch01\n service_description Management interface\n'
        " check_command check_int_32!unused!'eth0'\n process_perf_data 1\n}\n")
    env = dict(os.environ, PYTHONPATH='', XDG_CACHE_HOME=str(base / 'cache'), NAGIOS_GRAPHS_CONFIG=str(config / 'graphs.json'),
               NAGIOS_GRAPHS_RRD_DIR=str(rrd))
    run([sys.executable, str(lib / 'generate-interface-graphs.py'), '--config-dir', str(fixture),
         '--catalog-base', str(config / 'base.json'), '--output', str(config / 'graphs.json')], base, env)
    run([sys.executable, str(lib / 'nagios_graphs_config.py'), str(config / 'graphs.json')], base, env)
    return lib, config, spool, rrd, cgi, env


def exercise(base, lib, config, spool, rrd, cgi, env):
    data = json.loads((config / 'graphs.json').read_text())
    start = int(time.time()) // 60 * 60 - 600
    source = spool / 'service-perfdata'
    rows = []
    for i in range(6):
        stamp = start + 60 * i
        rows.extend([
            f'{stamp}\tserver01\tTemperature\ttemperature_c={-5 + i}\n',
            f'{stamp}\tswitch01\tUplink\trx_bytes={1000000 + i * 60000}c tx_bytes={2000000 + i * 30000}c\n',
            f'{stamp}\tswitch01\tManagement interface\teth0_in_octet={3000000 + i * 60000}c '
            f'eth0_out_octet={4000000 + i * 30000}c eth0_in_error={i}c eth0_out_error=0c '
            f'eth0_in_discard=0c eth0_out_discard=0c\n'])
    source.write_text(''.join(rows))
    run([sys.executable, str(lib / 'nagios-rrd-seal.py'), str(source), '--spool', str(spool)], base, env)
    drain = [sys.executable, str(lib / 'nagios-rrd-drain.py'), '--spool', str(spool),
             '--importer', str(lib / 'nagios-rrd-import.py'), '--rrd-dir', str(rrd)]
    run(drain, base, env)
    if source.exists() or list(spool.glob('*.batch')):
        raise RuntimeError('Spool did not drain completely')
    archives = list(rrd.glob('*.rrd'))
    if len(archives) != len(data['metrics']):
        raise RuntimeError('Unexpected archive count')
    before = {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in archives}
    if b'processed=0 failures=0' not in run(drain, base, env):
        raise RuntimeError('Repeat drain was not empty')
    rendered = []
    for graph in data['graphs']:
        view = graph['id']
        output = run([sys.executable, str(cgi)], base,
                     dict(env, QUERY_STRING=urlencode(dict(view=view, period='hour', format='png'))))
        headers, png = output.split(b'\r\n\r\n', 1)
        if not headers.startswith(b'Status: 200 OK') or not png.startswith(b'\x89PNG\r\n\x1a\n'):
            raise RuntimeError('Staged PNG failed: ' + view)
        (base / (view + '.png')).write_bytes(png)
        html = run([sys.executable, str(cgi)], base,
                   dict(env, QUERY_STRING=urlencode(dict(view=view, period='hour'))))
        if not html.startswith(b'Status: 200 OK') or b'id="plot-select"' not in html:
            raise RuntimeError('Staged HTML/zoom metadata failed: ' + view)
        rendered.append(view)
    after = {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in archives}
    if before != after:
        raise RuntimeError('Rendering changed archive data')
    return dict(archives=len(archives), rendered=rendered, archive_data_unchanged=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, help='new directory only; defaults to a private temporary directory')
    parser.add_argument('--layout-only', action='store_true', help='skip actual RRD creation and CGI rendering')
    args = parser.parse_args()
    try:
        if not args.layout_only and shutil.which('rrdtool') is None:
            raise ValueError('rrdtool is required for the full test; --layout-only is a partial check')
        if args.output:
            base = args.output.resolve()
            base.mkdir(mode=0o700, exist_ok=False)
        else:
            base = Path(tempfile.mkdtemp(prefix='nagios-graphs-staged-'))
        print('Staged installation:', base, flush=True)
        layout = stage(base)
        result = dict(mode='layout-only' if args.layout_only else 'full', layout_valid=True,
                      rrd_and_cgi_test='skipped' if args.layout_only else 'passed')
        if not args.layout_only:
            result.update(exercise(base, *layout))
        (base / 'report.json').write_text(json.dumps(result, indent=2) + '\n')
        print(json.dumps(result, indent=2))
        return 0
    except (OSError, ValueError, RuntimeError, subprocess.SubprocessError) as error:
        print(f'Staged installation test failed: {error}', file=sys.stderr)
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
