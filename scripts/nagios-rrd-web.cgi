#!/usr/bin/env python3
# Serve read-only Nagios RRD graphs as an authenticated CGI.
# Author: Stijn Jonker (sjcjonker) | https://github.com/sjcjonker
# Usage: Deploy as graphs.cgi; request graphs.cgi?view=graph-id&period=day
# Uses /var/lib/nagios-graphs/rrd; configure authentication in the web server. No CLI help.
# Nagios exit codes: 0 OK, 1 WARNING, 2 CRITICAL, 3 UNKNOWN.
"""Read-only Nagios Core RRD graphs CGI. No shell, uploads or external assets."""
from __future__ import annotations

import base64
import hashlib
import html
import json
import math
import os
import re
from pathlib import Path
import subprocess
import time
import xml.etree.ElementTree as ET
from urllib.parse import parse_qs, urlencode


def configured_catalog():
    """Load the mandatory catalog helper from the component or installation."""
    import runpy
    global _catalog_module
    if _catalog_module is None:
        source = Path(__file__).resolve().with_name("nagios_graphs_config.py")
        if not source.is_file():
            source = Path("/usr/local/libexec/nagios_graphs_config.py")
        _catalog_module = runpy.run_path(str(source))
    _catalog_module["load"]()
    return _catalog_module


_catalog_module = None
RRD_DIR = Path(os.environ.get("NAGIOS_GRAPHS_RRD_DIR", "/var/lib/nagios-graphs/rrd"))
PERIODS = {"hour": ("1 hour", 3600), "day": ("24 hours", 86400), "week": ("7 days", 604800),
           "month": ("30 days", 2592000), "year": ("1 year", 31536000)}
WIDTH = 860
HEIGHT = 280


def all_series():
    return configured_catalog()["series"]()


def archive_id(host: str, service: str, metric: str) -> str:
    catalog = configured_catalog()
    selected = catalog["metric_for"](host, service, metric)
    if selected is None:
        raise ValueError("Unknown metric")
    return catalog["archive_id"](selected)


def parse_selection(query: str) -> tuple[str, str, str]:
    arguments = parse_qs(query, keep_blank_values=True)
    view = arguments.get("view", [next(iter(all_series()), "")])[0]
    if "service" in arguments:
        host = arguments.get("host", [""])[0]
        service = arguments["service"][0]
        matches = [key for key, entry in all_series().items()
                   if not configured_catalog()["load"]()["graphs"][key].get("hidden", False)
                   and entry[1:3] == (host, service)]
        if len(matches) != 1:
            raise ValueError("Unknown interface service")
        view = matches[0]
    period = arguments.get("period", ["day"])[0]
    fmt = arguments.get("format", ["html"])[0]
    if (view not in all_series() and not (not all_series() and "view" not in arguments and fmt == "html")) or period not in PERIODS or fmt not in ("html", "png"):
        raise ValueError("Invalid graph selection")
    return view, period, fmt


def style_and_legend(arguments: list[str], unit: str, series_units: dict | None = None) -> list[str]:
    """One consistent legend for every plotted series, in measurement units."""
    result = [arg for arg in arguments if not arg.startswith(("VDEF:", "GPRINT:"))]
    result[3:3] = [
        "--color", "BACK#FFFFFF", "--color", "CANVAS#F8FAFC",
        "--color", "SHADEA#FFFFFF", "--color", "SHADEB#FFFFFF",
        "--color", "GRID#E2E8F0", "--color", "MGRID#CBD5E1",
        "--color", "FONT#334155", "--color", "FRAME#CBD5E1",
        # Keep the vertical RRDtool tag smaller than the axis label.
        "--font", "DEFAULT:10", "--font", "TITLE:12",
        "--font", "WATERMARK:6",
        "--font", "LEGEND:9:DejaVu Sans Mono", "--border", "0",
        "--grid-dash", "1:0", "--alt-y-grid",
    ]
    output = []
    for arg in result:
        output.append(arg)
        match = re.match(r"LINE[12]:([a-zA-Z0-9_]+)#[0-9A-Fa-f]+:(.*)", arg)
        if not match:
            continue
        name, label = match.groups()
        if not label or label.startswith(":"):
            continue  # Unlabelled drawing-only lines have no statistics.
        # Fixed label width keeps statistic columns aligned across legend rows.
        output.append("COMMENT:" + " " * max(1, 24 - len(label)))
        # Fault curves are scaled for drawing only. Statistics use original rates.
        source = name.removesuffix("_scaled")
        suffix = "" if name == "sample" else "_" + name
        for statistic, operation in (("latest", "LAST"), ("minimum", "MINIMUM"),
                                     ("average", "AVERAGE"), ("maximum", "MAXIMUM")):
            output.append(f"VDEF:{statistic}{suffix}={source},{operation}")
        display_unit = (series_units or {}).get(name, "/s" if name.endswith("_scaled") else unit)
        display_unit = display_unit.replace(":", r"\:").replace("%", "%%")
        precision = 4 if display_unit == "/s" else 2
        for statistic, heading in (("latest", "Last"), ("minimum", "Min"),
                                   ("average", "Avg"), ("maximum", "Max")):
            ending = r"\l" if statistic == "maximum" else ""
            output.append(f"GPRINT:{statistic}{suffix}:{heading}\\:%8.{precision}lf {display_unit}{ending}")
    return output


def graph_command(view: str, period: str, root: Path = RRD_DIR, axis_scale=None) -> list[str]:
    catalog = configured_catalog()
    args = catalog["graph_command"](view, PERIODS[period][1], root, WIDTH, HEIGHT, axis_scale)
    graph = catalog["load"]()["graphs"][view]
    units = {}
    for i, line in enumerate(graph["lines"]):
        units[f"line{i}"] = line.get("unit", graph["unit"])
        units[f"line{i}_scaled"] = line.get("unit", graph["unit"])
    return style_and_legend(args, graph["unit"], units)


def render_graph(view: str, period: str, root: Path = RRD_DIR,
                 window: tuple[int, int] | None = None, details: bool = False):
    catalog = configured_catalog()
    graph = catalog["load"]()["graphs"][view]
    arguments = graph_command(view, period, root)
    apply_window(arguments, window)
    # Freeze one endpoint for all probes and the final image.
    end = window[1] if window else int(time.time())
    arguments[arguments.index("--end") + 1] = str(end)
    if graph.get("right_axis", {}).get("scale") == "auto":
        first = next(i for i, arg in enumerate(arguments) if arg.startswith(("DEF:", "CDEF:")))
        probe = arguments[:first]
        probe[2] = "/dev/null"
        probe += [arg for arg in arguments if arg.startswith(("DEF:", "CDEF:"))]
        for i in range(len(graph["lines"])):
            probe += [f"VDEF:max{i}=line{i},MAXIMUM", f"PRINT:max{i}:peak=%le"]
        peaks = subprocess.run(probe, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                               timeout=20, check=True, text=True)
        values = [float(line[5:]) for line in peaks.stdout.splitlines() if line.startswith("peak=")]
        if len(values) != len(graph["lines"]):
            raise RuntimeError("Graph scale calculation failed")
        left = max([v for v, line in zip(values, graph["lines"])
                    if line.get("axis", "left") == "left" and math.isfinite(v) and v > 0] or [1.0])
        right = max([v for v, line in zip(values, graph["lines"])
                     if line.get("axis", "left") == "right" and math.isfinite(v) and v > 0] or [1.0])
        scale = right / left
        if not math.isfinite(scale) or scale <= 0:
            scale = 1.0
        arguments = graph_command(view, period, root, scale)
        apply_window(arguments, window)
        arguments[arguments.index("--end") + 1] = str(end)
    if any(line.get("bridge_gaps", False) for line in graph["lines"]):
        start = window[0] if window else end - PERIODS[period][1]
        arguments = add_gap_bridges(arguments, start, end, graph)
    if details:
        arguments[1] = "graphv"
    proc = subprocess.run(arguments, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                          timeout=20, check=False)
    if details:
        if proc.returncode:
            raise RuntimeError("Graph renderer failed")
        return parse_graph_details(proc.stdout)
    if proc.returncode or not proc.stdout.startswith(b"\x89PNG\r\n\x1a\n"):
        raise RuntimeError("Graph renderer failed")
    return proc.stdout


def gap_expression(samples: list[tuple[int, float | None]]) -> str | None:
    """Interpolate only bounded missing runs; never extrapolate."""
    segments = []
    previous = None
    missing = False
    for stamp, value in samples:
        if value is None or not math.isfinite(value):
            missing = True
            continue
        if previous and missing:
            before, first = previous
            if stamp > before:
                slope = (value - first) / (stamp - before)
                segments.append(
                    f"TIME,{before},GE,TIME,{stamp},LE,*,"
                    f"TIME,{before},-,{slope:.12g},*,{first:.12g},+,UNKN,IF")
        previous = (stamp, value)
        missing = False
    return (
        segments[0] + "".join("," + part + ",ADDNAN" for part in segments[1:])
        if segments else None)


def add_gap_bridges(arguments: list[str], start: int, end: int, graph: dict) -> list[str]:
    """Overlay bounded interpolation, keeping statistics on measured values."""
    selected = [(i, line) for i, line in enumerate(graph["lines"]) if line.get("bridge_gaps", False)]
    definitions = [arg for arg in arguments if arg.startswith(("DEF:", "CDEF:"))]
    command = ["/usr/bin/rrdtool", "xport", "--showtime", "--start", str(start), "--end", str(end),
               "--maxrows", str(WIDTH), *definitions, *[f"XPORT:line{i}" for i, _ in selected]]
    proc = subprocess.run(command, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=20, check=True)
    if len(proc.stdout) > 1024 * 1024:
        raise RuntimeError("Graph samples exceed limit")
    try:
        rows = ET.fromstring(proc.stdout).findall("./data/row")
        if len(rows) > WIDTH * 2:
            raise ValueError("too many graph samples")
        samples = [[] for _ in selected]
        for row in rows:
            stamp = int(row.findtext("t"))
            values = row.findall("v")
            if len(values) != len(selected):
                raise ValueError("invalid graph sample")
            for series, element in zip(samples, values):
                value = float(element.text)
                series.append((stamp, value if math.isfinite(value) else None))
    except (ET.ParseError, TypeError, ValueError):
        raise RuntimeError("Invalid graph samples") from None
    bridges = []
    for (i, line), series in zip(selected, samples):
        expression = gap_expression(series)
        if expression:
            bridges += [f"CDEF:gap{i}=line{i},POP,{expression}",
                        f"LINE1:gap{i}{line['color']}::dashes=4,4"]
    # Keep all dependencies before gap CDEFs and all drawing after them.
    result = [arg for arg in arguments if not arg.startswith(("DEF:", "CDEF:"))]
    first_line = next(i for i, arg in enumerate(result) if arg.startswith("LINE"))
    return result[:first_line] + definitions + bridges + result[first_line:]


def parse_graph_details(output: bytes) -> tuple[bytes, dict]:
    marker = re.search(rb"(?m)^image = BLOB_SIZE:([0-9]+)\r?\n", output)
    if not marker:
        raise RuntimeError("Graph metadata missing")
    png = output[marker.end():marker.end() + int(marker[1])]
    fields = dict((key.decode(), int(value)) for key, value in re.findall(
        rb"(?m)^(graph_left|graph_top|graph_width|graph_height|graph_start|graph_end|image_width|image_height) = ([0-9]+)\r?$",
        output[:marker.start()]))
    if len(fields) != 8 or not png.startswith(b"\x89PNG\r\n\x1a\n") or len(png) != int(marker[1]):
        raise RuntimeError("Invalid graph metadata")
    if (min(fields['graph_width'], fields['graph_height']) <= 0 or
            fields['graph_left'] + fields['graph_width'] > fields['image_width'] or
            fields['graph_top'] + fields['graph_height'] > fields['image_height'] or
            fields['graph_end'] <= fields['graph_start']):
        raise RuntimeError("Invalid plot bounds")
    return png, fields


def parse_window(query: str) -> tuple[int, int] | None:
    params = parse_qs(query, keep_blank_values=True)
    if "start" not in params and "end" not in params:
        return None
    start, end = (params.get(key, [""])[0] for key in ("start", "end"))
    if not re.fullmatch(r"[0-9]{1,10}", start) or not re.fullmatch(r"[0-9]{1,10}", end):
        raise ValueError("Invalid time range")
    start, end = int(start), int(end)
    if start < 1 or not 300 <= end - start <= PERIODS["year"][1] or end > int(time.time()):
        raise ValueError("Invalid time range")
    return start, end


def apply_window(arguments: list[str], window: tuple[int, int] | None) -> None:
    if window:
        for flag, value in zip(("--start", "--end"), window):
            arguments[arguments.index(flag) + 1] = str(value)


def link(label: str, **params) -> str:
    return '<a href="?{}">{}</a>'.format(html.escape(urlencode(params), quote=True), html.escape(label))


def listed_view(key: str) -> bool:
    return not configured_catalog()["load"]()["graphs"][key].get("hidden", False)


def available_series() -> dict:
    # Reuse the renderer's archive checks, without starting RRDtool.
    result = {}
    for key, entry in all_series().items():
        if not listed_view(key):
            continue
        try:
            graph_command(key, "day", RRD_DIR)
        except FileNotFoundError:
            continue
        result[key] = entry
    return result


def navigation(view: str, period: str, window=None) -> str:
    host = all_series()[view][1]
    choices = available_series()
    choices[view] = all_series()[view]
    options = "".join('<option value="{}"{}>{}</option>'.format(
        html.escape(key, quote=True), ' selected' if key == view else '', html.escape(entry[0]))
        for key, entry in sorted(choices.items(), key=lambda item: item[1][0].casefold())
        if entry[1] == host and listed_view(key))
    if not listed_view(view):
        options = '<option value="" selected disabled>Select a graph</option>' + options
    hidden = {"period": period}
    if window:
        hidden.update(zip(("start", "end"), window))
    fields = "".join(f'<input type="hidden" name="{key}" value="{value}">' for key, value in hidden.items())
    return ('<div class="graph-toolbar"><nav aria-label="Breadcrumb">' + link("All hosts") +
            link(host, host=host, period=period) + '</nav><form class="graph-picker" method="get">' + fields +
            '<label for="graph">Graph: </label><select id="graph" name="view">' + options +
            '</select> <button id="graph-show" type="submit">Show</button></form></div>')


def zoom_links(view: str, period: str, window=None) -> str:
    end = int(time.time()) if window is None else window[1]
    start = end - PERIODS[period][1] if window is None else window[0]
    span = end - start
    links = []
    for label, size, finish in (("Zoom in", max(300, span // 2), end),
                                ("Zoom out", min(PERIODS["year"][1], span * 2), end),
                                ("Earlier", span, end - span // 2),
                                ("Later", span, min(int(time.time()), end + span // 2))):
        if finish > size:
            links.append(link(label, view=view, period=period, start=finish-size, end=finish))
    links.append(link("Reset / live", view=view, period=period))
    return '<nav class="zoom-controls" aria-label="Zoom and pan">' + " ".join(links) + '</nav>'


def catalog_page(host: str, period: str) -> bytes:
    available = available_series()
    if host:
        items = [link(entry[0], view=key, period=period)
                 for key, entry in sorted(available.items(), key=lambda item: item[1][0].casefold())
                 if entry[1] == host]
        title = host
    else:
        hosts = sorted({entry[1] for entry in available.values()}, key=str.casefold)
        items = [link(name, host=name, period=period) for name in hosts]
        title = "Hosts with graphs"
    list_class = 'host-grid'
    content = f'<ul class="{list_class}">' + ''.join('<li>' + item + '</li>' for item in items) + '</ul>' if items else '<p>No graph data is available yet.</p>'
    return document(title, '', '<nav>' + link("All hosts") + '</nav>' + content, period)


def page(view: str, period: str, message: str = "", window=None, rendered=None) -> bytes:
    opts = " ".join(
        '<a href="?{}"{}>{}</a>'.format(
            html.escape(urlencode({"view": view, "period": key}), quote=True),
            ' aria-current="page"' if period == key else "",
            html.escape(label))
        for key, (label, _) in PERIODS.items()
    )
    png_params = {"view": view, "period": period, "format": "png"}
    if window:
        png_params.update(zip(("start", "end"), window))
    png = "?" + urlencode(png_params)
    image = f'<img src="{html.escape(png, quote=True)}" width="{WIDTH}" alt="Graph {html.escape(all_series()[view][0])}">' if not message else f'<p role="alert">{html.escape(message)}</p>'
    if rendered and not message:
        png_data, bounds = rendered
        src = "data:image/png;base64," + base64.b64encode(png_data).decode()
        data = html.escape(json.dumps(bounds), quote=True)
        image = (f'<div class="plot-wrap"><img id="plot-image" src="{src}" width="{WIDTH}" '
                 f'alt="Graph {html.escape(all_series()[view][0])}" draggable="false">'
                 f'<div id="plot-select" data-bounds="{data}" data-view="{html.escape(view, quote=True)}" '
                 f'data-period="{period}" hidden><div id="plot-band" hidden></div></div></div>')
    title, host, service, _, unit = all_series()[view]
    graph = configured_catalog()["load"]()["graphs"][view]
    note = graph.get("note", "Gaps indicate unavailable measurements. Values are averaged over the selected period.")
    if any(line.get("bridge_gaps", False) for line in graph["lines"]):
        note += " Dotted lines visually bridge gaps and do not contribute to statistics."
    range_note = ""
    if window:
        range_note = '<p class="meta">' + " – ".join(
            time.strftime("%Y-%m-%d %H:%M UTC", time.gmtime(value)) for value in window) + '</p>'
    content = (navigation(view, period, window) + '<nav aria-label="Time range">' + opts + '</nav>' +
               range_note + image + '<p class="note">' + html.escape(note) + '</p>' +
               '<hr class="zoom-divider"><p id="zoom-hint" class="meta" hidden>' +
               'Drag across the plot to zoom (minimum 5 minutes). Escape cancels.</p>' +
               zoom_links(view, period, window))
    return document(title, host + " / " + service, content, period)


# The CSP permits only this fixed script, not arbitrary inline JavaScript.
SELECT_SCRIPT = """const graph = document.getElementById('graph');
if (graph) {
    graph.addEventListener('change', () => graph.form.requestSubmit());
    document.getElementById('graph-show').hidden = true;
}
const plot = document.getElementById('plot-select');
if (plot) {
    const image = document.getElementById('plot-image');
    const band = document.getElementById('plot-band');
    const bounds = JSON.parse(plot.dataset.bounds);
    let drag = null;
    function cancel() {
        const previous = drag;
        drag = null;
        band.hidden = true;
        if (previous && plot.hasPointerCapture(previous.id)) plot.releasePointerCapture(previous.id);
    }
    function layout() {
        cancel();
        plot.style.left = (100 * bounds.graph_left / bounds.image_width) + '%';
        plot.style.top = (100 * bounds.graph_top / bounds.image_height) + '%';
        plot.style.width = (100 * bounds.graph_width / bounds.image_width) + '%';
        plot.style.height = (100 * bounds.graph_height / bounds.image_height) + '%';
        plot.hidden = false;
        document.getElementById('zoom-hint').hidden = false;
    }
    function position(event) {
        const rect = plot.getBoundingClientRect();
        return Math.max(0, Math.min(1, (event.clientX - rect.left) / rect.width));
    }
    plot.addEventListener('pointerdown', event => {
        if (event.pointerType !== 'mouse' || event.button !== 0 || !event.isPrimary) return;
        event.preventDefault();
        drag = {id: event.pointerId, start: position(event)};
        plot.setPointerCapture(event.pointerId);
        band.style.left = (drag.start * 100) + '%';
        band.style.width = '0';
        band.hidden = false;
    });
    plot.addEventListener('pointermove', event => {
        if (!drag || drag.id !== event.pointerId) return;
        const end = position(event);
        band.style.left = (Math.min(drag.start, end) * 100) + '%';
        band.style.width = (Math.abs(end - drag.start) * 100) + '%';
    });
    plot.addEventListener('pointerup', event => {
        if (!drag || drag.id !== event.pointerId) return;
        const end = position(event), first = Math.min(drag.start, end), last = Math.max(drag.start, end);
        const pixels = (last - first) * plot.getBoundingClientRect().width;
        const span = bounds.graph_end - bounds.graph_start;
        const startTime = Math.round(bounds.graph_start + first * span);
        const endTime = Math.round(bounds.graph_start + last * span);
        cancel();
        if (pixels < 6 || endTime - startTime < 300) return;
        const url = new URL(location.href);
        url.search = new URLSearchParams({view: plot.dataset.view, period: plot.dataset.period,
            start: String(startTime), end: String(endTime)}).toString();
        location.assign(url);
    });
    plot.addEventListener('pointercancel', cancel);
    plot.addEventListener('lostpointercapture', cancel);
    document.addEventListener('keydown', event => { if (event.key === 'Escape') cancel(); });
    window.addEventListener('resize', cancel);
    window.addEventListener('blur', cancel);
    image.addEventListener('load', layout);
    if (image.complete && image.naturalWidth) layout();
}"""


def document(title: str, subtitle: str, content: str, period: str) -> bytes:
    catalog = configured_catalog()
    nagios_url = html.escape(catalog["load"]()["nagios_url"], quote=True)
    script_hash = base64.b64encode(hashlib.sha256(SELECT_SCRIPT.encode()).digest()).decode()
    body = f"""<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<meta http-equiv="Content-Security-Policy" content="default-src 'none'; script-src 'sha256-{script_hash}'; img-src 'self' data:; style-src 'unsafe-inline'; base-uri 'none'; form-action 'self'; frame-ancestors 'self'">
<title>Nagios RRD graphs</title><style>
body{{font:14px system-ui,-apple-system,sans-serif;margin:0;padding:24px;background:#f1f3f5;color:#263442}}
main{{max-width:1100px;margin:auto}}header{{margin-bottom:20px}}h1{{font-size:24px;margin:4px 0}}h2{{font-size:18px;margin:0}}
.eyebrow{{color:#64748b;font-size:11px;font-weight:700;letter-spacing:.12em;text-transform:uppercase}}
.panel{{background:white;border:1px solid #cbd1d7;border-radius:8px;box-shadow:0 2px 5px #00000008;margin-bottom:18px;overflow:hidden}}
.panel-heading{{background:linear-gradient(#f5f6f7,#e3e6e9);border-bottom:1px solid #cbd1d7;padding:14px 18px}}
.content{{padding:18px}}nav{{display:flex;flex-wrap:wrap;gap:8px;margin:0 0 16px}}
a{{color:#23618c;text-decoration:none}}nav a{{padding:7px 13px;border:1px solid #ccd4dd;border-radius:5px;background:#f8fafc}}
nav a:hover{{background:#e8eef4}}nav a[aria-current]{{color:white;background:#35698d;border-color:#35698d}}
.graph-toolbar{{display:flex;align-items:center;justify-content:space-between;flex-wrap:wrap;gap:12px;margin-bottom:16px}}
.graph-toolbar nav{{margin:0}}.graph-picker{{display:flex;align-items:center;gap:8px;margin:0 0 0 auto;min-width:0;max-width:100%}}
.graph-picker label{{color:#627080;font-size:12px;font-weight:400;line-height:1.6;white-space:nowrap}}
select,button{{font:inherit;color:#23618c;background:#f8fafc;border:1px solid #ccd4dd;border-radius:5px;padding:7px 10px;min-width:0;max-width:100%}}
select:hover,button:hover{{background:#e8eef4}}select:focus-visible,button:focus-visible{{outline:2px solid #35698d;outline-offset:2px}}
.graph-picker select{{flex:1;width:320px}}li{{margin:12px 0}}
.host-grid{{display:grid;grid-template-columns:1fr;gap:10px 18px;list-style:none;padding:0;margin:0}}
.host-grid li{{margin:0;min-width:0}}.host-grid a{{display:block;padding:10px 13px;border:1px solid #ccd4dd;border-radius:5px;background:#f8fafc;overflow-wrap:anywhere}}
.host-grid a:hover{{background:#e8eef4}}
@media(min-width:600px){{.host-grid{{grid-template-columns:repeat(2,minmax(0,1fr))}}}}
@media(min-width:900px){{.host-grid{{grid-template-columns:repeat(3,minmax(0,1fr))}}}}
.zoom-controls{{display:grid;grid-template-columns:repeat(5,minmax(0,1fr));width:75%;gap:8px;margin:12px auto 0}}
.zoom-controls a{{font-size:12px;text-align:center;padding:5px 14px}}
.zoom-divider{{border:0;border-top:1px solid #e5e9ed;margin:16px 0 12px}}
@media(max-width:700px){{.zoom-controls{{width:100%;grid-template-columns:repeat(auto-fit,minmax(100px,1fr))}}}}
.plot-wrap{{position:relative;width:860px;max-width:100%;margin:auto}}
#plot-select{{position:absolute;cursor:crosshair;user-select:none}}
#plot-band{{position:absolute;top:0;bottom:0;background:#35698d33;border-left:1px solid #35698d;border-right:1px solid #35698d;pointer-events:none;box-sizing:border-box}}
#zoom-hint{{text-align:center;margin-top:8px}}
img{{display:block;max-width:100%;height:auto;margin:auto}}.meta,.note{{color:#627080;font-size:12px;line-height:1.6}}
.meta{{margin:5px 0 0}}.note{{margin:12px 0 0}}
[role=alert]{{padding:18px;background:#fff5dd;border:1px solid #e6d39a;border-radius:5px}}
footer{{display:flex;flex-wrap:wrap;justify-content:space-between;gap:8px;color:#627080;font-size:12px}}
@media(max-width:600px){{body{{padding:12px}}.content{{padding:12px}}h1{{font-size:21px}}}}
</style></head><body><main><header><div class="eyebrow">Nagios · Performance monitoring</div><h1>Performance graphs</h1></header>
<section class="panel"><div class="panel-heading"><h2>{html.escape(title)}</h2>
<p class="meta">{html.escape(subtitle)}</p></div>
<div class="content">{content}</div></section>
<footer><a href="{nagios_url}" target="_top">Back to Nagios</a><span>RRD archives · {html.escape(PERIODS[period][0])}</span></footer>
</main><script>{SELECT_SCRIPT}</script></body></html>"""
    return body.encode("utf-8")


def application(environ: dict[str, str]) -> tuple[str, str, bytes]:
    try:
        query = environ.get("QUERY_STRING", "")
        view, period, fmt = parse_selection(query)
        window = parse_window(query)
        params = parse_qs(query, keep_blank_values=True)
        host = params.get("host", [""])[0]
        if "host" in params and host not in {entry[1] for entry in all_series().values()}:
            raise ValueError("Invalid host")
        if "view" not in params and "service" not in params and fmt == "html":
            return "200 OK", "text/html; charset=utf-8", catalog_page(host, period)
    except ValueError:
        return "400 Bad Request", "text/plain; charset=utf-8", b"Invalid graph selection"
    if fmt == "png":
        try:
            return "200 OK", "image/png", render_graph(view, period, window=window)
        except (FileNotFoundError, OSError, RuntimeError, subprocess.TimeoutExpired, subprocess.CalledProcessError):
            return "404 Not Found", "text/plain; charset=utf-8", b"Graph unavailable"
    rendered = None
    try:
        graph_command(view, period)
        rendered = render_graph(view, period, details=True, window=window)
        error = ""
    except FileNotFoundError:
        error = "No graph data is available yet."
    except (OSError, RuntimeError, subprocess.TimeoutExpired, subprocess.CalledProcessError):
        error = "Graph unavailable."
    return "200 OK", "text/html; charset=utf-8", page(view, period, error, window, rendered)


def main() -> None:
    try:
        status, mime, body = application(os.environ)
    except (OSError, ValueError, KeyError, TypeError):
        status, mime, body = "503 Service Unavailable", "text/plain; charset=utf-8", b"Graph configuration unavailable"
    headers = (f"Status: {status}\r\nContent-Type: {mime}\r\n"
               f"Cache-Control: no-store\r\nX-Content-Type-Options: nosniff\r\n"
               f"Content-Length: {len(body)}\r\n\r\n").encode("ascii")
    os.write(1, headers + body)


if __name__ == "__main__":
    main()





