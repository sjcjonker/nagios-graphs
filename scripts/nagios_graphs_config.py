"""Validated, explicit graph catalog. Configuration is administrator-owned data."""
import hashlib
import json
import math
import os
from pathlib import Path
import re

DEFAULT_CONFIG = Path('/etc/nagios-graphs/graphs.json')
_cache = None


def text(value, label):
    if not isinstance(value, str) or not 0 < len(value) <= 256 or not value.isprintable():
        raise ValueError('Invalid ' + label)
    return value


def number(value):
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        raise ValueError('Expected finite number')
    return value


def validate(data):
    if not isinstance(data, dict) or type(data.get('version')) is not int or data.get('version') != 1:
        raise ValueError('Expected configuration version 1')
    if set(data) - {'version', 'metrics', 'graphs', 'nagios_url'}:
        raise ValueError('Unknown configuration field')
    if not isinstance(data.get('metrics'), list) or not isinstance(data.get('graphs'), list):
        raise ValueError('metrics and graphs must be arrays')
    metrics, graphs, identities, archives = {}, {}, set(), {}
    for item in data.get('metrics', []):
        if not isinstance(item, dict):
            raise ValueError('Metric must be an object')
        if set(item) - {'id', 'host', 'service', 'metric', 'type', 'heartbeat', 'archive_service'}:
            raise ValueError('Unknown metric field')
        key = text(item.get('id'), 'metric id')
        identity = tuple(text(item.get(k), k) for k in ('host', 'service', 'metric'))
        archive_service = text(item.get('archive_service', identity[1]), 'archive service')
        kind = item.get('type', 'GAUGE')
        heartbeat = item.get('heartbeat', 1800)
        if kind not in ('GAUGE', 'GAUGE_SIGNED', 'COUNTER'):
            raise ValueError('Invalid metric type')
        if type(heartbeat) is not int or not 60 <= heartbeat <= 604800:
            raise ValueError('Invalid heartbeat')
        if key in metrics or identity in identities:
            raise ValueError('Duplicate metric id or identity')
        archive = (identity[0], archive_service, identity[2])
        contract = (kind, heartbeat)
        if archive in archives and archives[archive] != contract:
            raise ValueError('Conflicting archive types or heartbeat')
        archives[archive] = contract
        identities.add(identity)
        metrics[key] = dict(item, type=kind, heartbeat=heartbeat, archive_service=archive_service)
    for item in data.get('graphs', []):
        if not isinstance(item, dict):
            raise ValueError('Graph must be an object')
        if set(item) - {'id', 'title', 'unit', 'lines', 'lower', 'upper', 'hidden', 'right_axis', 'note', 'units_exponent', 'left_format', 'right_format', 'axis_label'}:
            raise ValueError('Unknown graph field')
        key = text(item.get('id'), 'graph id')
        if not re.fullmatch(r'[A-Za-z0-9_.-]{1,120}', key) or key in graphs:
            raise ValueError('Invalid or duplicate graph id')
        text(item.get('title'), 'graph title')
        text(item.get('unit'), 'graph unit')
        if 'axis_label' in item:
            text(item['axis_label'], 'axis label')
        lines = item.get('lines')
        if not isinstance(lines, list) or not 1 <= len(lines) <= 16:
            raise ValueError('Expected 1 to 16 graph lines')
        hosts = set()
        for line in lines:
            if not isinstance(line, dict):
                raise ValueError('Graph line must be an object')
            if set(line) - {'metric', 'label', 'color', 'scale', 'subtract', 'axis', 'unit', 'optional', 'bridge_gaps', 'width'}:
                raise ValueError('Unknown line field')
            if line.get('metric') not in metrics or ('subtract' in line and line['subtract'] not in metrics):
                raise ValueError('Unknown metric reference')
            hosts.add(metrics[line['metric']]['host'])
            if 'subtract' in line:
                hosts.add(metrics[line['subtract']]['host'])
            text(line.get('label'), 'line label')
            if not re.fullmatch(r'#[0-9A-Fa-f]{6}', line.get('color', '')):
                raise ValueError('Invalid line color')
            number(line.get('scale', 1))
            if line.get('axis', 'left') not in ('left', 'right'):
                raise ValueError('Invalid line axis')
            if line.get('axis') == 'right' and 'right_axis' not in item:
                raise ValueError('Right-axis line requires right_axis')
            for flag in ('optional', 'bridge_gaps'):
                if type(line.get(flag, False)) is not bool:
                    raise ValueError('Invalid line flag')
            if line.get('bridge_gaps') and line.get('axis') == 'right':
                raise ValueError('Gap bridges are supported on the left axis only')
            if type(line.get('width', 2)) is not int or line.get('width', 2) not in (1, 2):
                raise ValueError('Invalid line width')
            if 'unit' in line:
                text(line['unit'], 'line unit')
        if all(line.get('optional', False) for line in lines):
            raise ValueError('At least one required line is needed')
        if 'note' in item:
            text(item['note'], 'note')
        if 'units_exponent' in item and (type(item['units_exponent']) is not int or not -24 <= item['units_exponent'] <= 24):
            raise ValueError('Invalid units exponent')
        for fmt in ('left_format', 'right_format'):
            if fmt in item and not re.fullmatch(r'%\.[0-6]lf', item[fmt]):
                raise ValueError('Invalid axis format')
        if 'right_axis' in item:
            axis = item['right_axis']
            if not isinstance(axis, dict) or set(axis) != {'unit', 'scale'}:
                raise ValueError('right_axis needs unit and scale')
            text(axis['unit'], 'right axis unit')
            if axis['scale'] != 'auto' and number(axis['scale']) <= 0:
                raise ValueError('Right axis scale must be positive or auto')
            if not any(line.get('axis') == 'right' for line in lines) or not any(line.get('axis', 'left') == 'left' for line in lines):
                raise ValueError('Dual-axis graphs require left and right lines')
        if len(hosts) != 1:
            raise ValueError('Graph lines must belong to one host')
        for limit in ('lower', 'upper'):
            if limit in item:
                number(item[limit])
        if 'lower' in item and 'upper' in item and item['upper'] <= item['lower']:
            raise ValueError('Upper limit must exceed lower limit')
        if type(item.get('hidden', False)) is not bool:
            raise ValueError('Invalid hidden flag')
        graphs[key] = item
    url = data.get('nagios_url', '/nagios4/')
    if not isinstance(url, str) or not url.startswith('/') or url.startswith('//') or '\\' in url or any(ord(c) < 32 for c in url):
        raise ValueError('nagios_url must be a local absolute URL path')
    return {'metrics': metrics, 'graphs': graphs, 'nagios_url': url}


def load():
    global _cache
    if _cache is not None:
        return _cache
    path = Path(os.environ.get('NAGIOS_GRAPHS_CONFIG', str(DEFAULT_CONFIG)))
    if path.stat().st_size > 1024 * 1024:
        raise ValueError('Configuration exceeds 1 MiB')
    _cache = validate(json.loads(path.read_text(encoding='utf-8')))
    _cache['identity_index'] = {(m['host'], m['service'], m['metric']): m for m in _cache['metrics'].values()}
    return _cache


def metric_for(host, service, metric):
    config = load()
    return config['identity_index'].get((host, service, metric))


def archive_id(metric):
    identity = [metric['host'], metric['archive_service'], metric['metric']]
    return hashlib.sha256(json.dumps(identity).encode()).hexdigest()[:32]


def series():
    config = load()
    result = {}
    for key, graph in config['graphs'].items():
        metric = config['metrics'][graph['lines'][0]['metric']]
        result[key] = (graph['title'], metric['host'], metric['service'], metric['metric'], graph['unit'])
    return result


def escape_label(value):
    return value.replace('\\', '\\\\').replace(':', r'\:')


def graph_command(view, period_seconds, root, width, height, axis_scale=None):
    config = load()
    graph = config['graphs'][view]
    args = ['/usr/bin/rrdtool', 'graph', '-', '--imgformat', 'PNG',
            '--start', f'end-{period_seconds}', '--end', 'now',
            '--width', str(width), '--height', str(height), '--slope-mode',
            '--title', graph['title'], '--vertical-label', graph.get('axis_label', graph['unit'])]
    for limit in ('lower', 'upper'):
        if limit in graph:
            args += [f'--{limit}-limit', str(graph[limit])]
    if 'units_exponent' in graph:
        args += ['--units-exponent', str(graph['units_exponent']), '--units-length', '8']
    for fmt in ('left_format', 'right_format'):
        if fmt in graph:
            args += ['--' + fmt.replace('_', '-').replace('format', 'axis-format'), graph[fmt]]
    right = graph.get('right_axis')
    ratio = 1.0
    if right:
        ratio = axis_scale if axis_scale is not None else (1.0 if right['scale'] == 'auto' else right['scale'])
        if number(ratio) <= 0:
            raise ValueError('Axis scale must be positive')
        args += ['--right-axis', f'{ratio}:0', '--right-axis-label', right['unit']]
    definitions, curves = [], []
    # Resolve all required archives first, including subtract operands.
    resolved = []
    for index, line in enumerate(graph['lines']):
        paths = {key: root / (archive_id(config['metrics'][line[key]]) + '.rrd')
                 for key in ('metric', 'subtract') if key in line}
        exists = all(path.is_file() for path in paths.values())
        if not exists and not line.get('optional', False):
            raise FileNotFoundError('Graph archive unavailable')
        resolved.append((index, line, paths, exists))
    anchor = next(i for i, line, paths, exists in resolved if exists)
    for index, line, paths, exists in resolved:
        name = f'line{index}'
        if exists:
            for key, path in paths.items():
                suffix = 'raw' if key == 'metric' else 'sub'
                definitions.append(f'DEF:{name}_{suffix}={path}:value:AVERAGE')
            expression = f'{name}_raw' + (f',{name}_sub,-' if 'subtract' in line else '')
            definitions.append(f'CDEF:{name}={expression},{line.get("scale", 1)},*')
        else:
            definitions.append(f'CDEF:{name}=line{anchor}_raw,POP,UNKN')
        plotted = name
        if line.get('axis') == 'right':
            plotted += '_scaled'
            definitions.append(f'CDEF:{plotted}={name},{ratio},/')
        curves.append(f'LINE{line.get("width", 2)}:{plotted}{line["color"]}:{escape_label(line["label"])}')
    # DEF dependencies precede CDEFs, even for missing optional lines at index 0.
    return args + [x for x in definitions if x.startswith('DEF:')] + [x for x in definitions if x.startswith('CDEF:')] + curves


if __name__ == '__main__':
    import argparse
    import sys
    parser = argparse.ArgumentParser(description='Validate an explicit graph catalog without writing files')
    parser.add_argument('config', type=Path)
    args = parser.parse_args()
    try:
        if args.config.stat().st_size > 1024 * 1024:
            raise ValueError('Configuration exceeds 1 MiB')
        config = validate(json.loads(args.config.read_text(encoding='utf-8')))
    except (OSError, ValueError, TypeError, KeyError) as error:
        print(f'Invalid graph configuration: {error}', file=sys.stderr)
        sys.exit(2)
    print(f'Valid catalog: {len(config["metrics"])} metrics, {len(config["graphs"])} graphs')
