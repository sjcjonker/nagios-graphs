# Explicit graph configuration

Install an administrator-owned `/etc/nagios-graphs/graphs.json`. Both the importer
and CGI use it automatically. A site may instead select a generated catalog
under `/var/lib/nagios-graphs/` using `NAGIOS_GRAPHS_CONFIG`. The same variable
selects an isolated test configuration. A selected missing or invalid file fails
closed; there is no built-in host catalog or legacy fallback.

The version 1 format has `metrics`, `graphs` and an optional `nagios_url`
(local URL path, default `/nagios4/`). See [the complete example](../examples/graphs.json).
Unknown fields, duplicate identities, invalid types and unknown references are
rejected. Configuration is limited to 1 MiB. Reloading the oneshot importer and
CGI processes naturally picks up edits; write changes atomically.

## Metrics

Each metric has an arbitrary unique `id` and exact `host`, `service`, `metric`
strings matching Nagios perfdata. No wildcard collection is enabled.

| Field | Default | Meaning |
|---|---|---|
| `type` | `GAUGE` | `GAUGE` for nonnegative values, `GAUGE_SIGNED` for values including negatives, `COUNTER` for monotonic counters |
| `heartbeat` | 1800 | Maximum seconds between measurements, 60–604800 |
| `archive_service` | `service` | Original service description to retain an existing archive after a rename |
| `poll_interval_seconds` | unset | Expected polling interval, 60–604800 seconds; permits a bounded dashed tail for gauges, without changing stored data or statistics |
| `min_value`, `max_value` | sensor-specific | Optional explicit bounds in native gauge units or counter rates per second; see [bounds](install.md#metric-bounds-new-and-existing-archives) |

Archive filenames use the readable policy in `scripts/rrd_names.py`:
`<host>__<archive-service>__<metric>__<12-hex-digest>.rrd`. Each name part is
ASCII-normalized, sanitized and limited to 64 characters. The digest uses the
original, NUL-separated identity strings, so similarly sanitized names remain
distinct. `archive_service` preserves identity when a service is renamed.

The earlier 32-character SHA-256 filenames are legacy archive IDs retained for
migration/audit helpers. The current importer and renderer use readable filenames;
[coordinate their installation with archive migration](install.md#upgrade-existing-rrd-archives-to-readable-names).
Changing datatype, heartbeat or bounds does not migrate existing RRD metadata.
Retention remains 1-minute/1-day, 5-minute/7-day and hourly/1-year archives.
Missing values stay unknown.

## Graphs

Each graph has a unique URL-safe `id`, `title`, `unit`, and 1–16 `lines`.
Each line references a metric id and supplies a `label` and six-digit hex `color`.
Optional `scale` multiplies values; optional `subtract` references another metric
to subtract before scaling, for example total minus free memory. Counter values
are rates calculated by RRDTool before scaling. Octets/s to Mbit/s uses 0.000008.
All referenced metrics must belong to the graph's host.

Optional `lower` and `upper` set axis limits; omit `lower` for signed values.
`hidden: true` keeps a graph out of the catalog but permits direct links.
The CGI validates view ids against the configured catalog; it accepts no arbitrary
paths or RRD expressions from requests or configuration.

## Dual axes, optional counters and gap bridges

A graph can define `right_axis: {"unit": "Input voltage (V)", "scale": 3}`.
Lines with `axis: "right"` are drawn divided by this ratio, and the right axis
maps back to real units. Set a per-line `unit` for legend statistics; those always
use measured/scaled values before the axis drawing conversion.

Use `scale: "auto"` in `right_axis` for peak-based alignment between left and right
lines, recalculated for the exact selected window. All-zero or unknown peaks use
a stable fallback. `optional: true` on a line renders missing archives as unknown,
never fabricated zero; every graph must contain at least one required line.

`bridge_gaps: true` overlays dotted interpolation between bounded measurements
on a left-axis line. It never extrapolates or changes archives/statistics. Lines
can set `width` to 1 or 2. Graphs can set `axis_label` (defaults to `unit`),
`note`, `units_exponent`, `left_format`
and `right_format` (format strings limited to `%.0lf` through `%.6lf`).

## Interface generation

`generate-interface-graphs.py --config-dir ... --stdout` emits an interface manifest.
Add `--catalog-base ...` to merge interfaces with an explicit base catalog.
Use `--seed ...` for private historic mappings and repeat `--exclude-host ...`
for site exclusions. Generated interface graphs preserve `host`/`service` action
links; hidden error/discard graphs are excluded from ambiguous link resolution.

The seed is a JSON array of rows containing `key`, `host`, `service`, `metric_in`
and optionally `archive_service`. Newly generated rows also contain `metric_out`
and `title`. `metric_in` ends in `_in_octet`. Archive identities are independent
of current service labels. Removing a service removes it from the active catalog
without deleting any RRD file. No host exclusions are built into the generator.

The generator supports explicit hosts and literal interface selectors for
`check_int`, `check_int_32`, `check_int_noregex`, `check_int_down_noregex`,
`check_int_admin_noregex` and `check_local_interface`. It deliberately rejects
hostgroup expansion, regex/multi-interface selection and cyclic templates.

### Plugins and Nagios commands

Install these plugins separately:
- [check_snmp_netint.pl (WL-NagiosPlugins)](https://github.com/willixix/WL-NagiosPlugins/blob/master/check_snmp_netint.pl) for remote SNMP checks; follow its dependency instructions.
- [check-local-interface](https://github.com/sjcjonker/check-local-interface) for local Linux sysfs counters; provision its writable state directory as documented.

These examples use `$USER1$` as the plugin directory. Adjust it, load the command
file through `nagios.cfg`, and avoid duplicate command definitions.

```cfg
define command {
    command_name    check_int
    command_line    $USER1$/check_snmp_netint.pl -H $HOSTADDRESS$ -C '$ARG1$' -2 -n $ARG2$ -f -e -S -q -w 0,0,10,10,0,10 -c 0,0,20,20,0,20 --label --pcount 2 -k -Z -g $ARG3$
}

define command {
    command_name    check_int_32
    command_line    $USER1$/check_snmp_netint.pl -H $HOSTADDRESS$ -C '$ARG1$' -2 -n $ARG2$ -f -e -S -q -w 0,0,10,10,0,10 -c 0,0,20,20,0,20 --label --pcount 2 -k -Z  $ARG3$
}

define command {
    command_name    check_int_noregex
    command_line    $USER1$/check_snmp_netint.pl -H $HOSTADDRESS$ -C '$ARG1$' -2 -n $ARG2$ -f -e -S -q -w 0,0,10,10,0,10 -c 0,0,20,20,0,20 --label --pcount 2 -k -Z -r -g $ARG3$
}

define command {
    command_name    check_int_down_noregex
    command_line    $USER1$/check_snmp_netint.pl -H $HOSTADDRESS$ -C '$ARG1$' -2 -n $ARG2$ -f -e -S -q -w 0,0,10,10,0,10 -c 0,0,20,20,0,20 --label --pcount 2 -k -Z -i -D -r -g $ARG3$
}

define command {
    command_name    check_int_admin_noregex
    command_line    $USER1$/check_snmp_netint.pl -H $HOSTADDRESS$ -C '$ARG1$' -2 -n $ARG2$ -f -e -S -q -w 0,0,10,10,0,10 -c 0,0,20,20,0,20 --label --pcount 2 -k -Z -r -a $ARG3$
}

define command {
    command_name    check_local_interface
    command_line    $USER1$/check_local_interface.py $ARG1$
}
```

The generator recognizes these exact command names and argument positions:
SNMP uses `$ARG1$` for the community, `$ARG2$` for the interface and optional
`$ARG3$` for trusted administrator-controlled options. The local check uses
`$ARG1$` for the interface. It does not inspect or execute command definitions.

Prefer `check_int_noregex` for exact-name operational checks with 64-bit octet
counters. `check_int_down_noregex` inverts operational status and accepts dormant;
`check_int_admin_noregex` checks administrative status with 32-bit octets.
`check_int` uses regex selection with 64-bit octets; `check_int_32` uses regex
selection with 32-bit octets. Every selector must return exactly one interface
whose perfdata labels match the literal selector. Multi-interface results and
patterns are unsuitable for generation.

Example service objects (replace hosts, interface and template):

```cfg
define service {
    use                     generic-service
    host_name               router01
    service_description     Interface eth0
    check_command           check_int_noregex!$USER10$!eth0
    process_perf_data       1
}

define service {
    use                     generic-service
    host_name               monitoring01
    service_description     Interface eth0
    check_command           check_local_interface!eth0
    process_perf_data       1
}
```

Define `$USER10$` in a protected Nagios resource file; do not commit credentials.
The examples use SNMPv2c: restrict SNMP network access and adapt authentication
to your policy while retaining the argument positions. Plugin arguments are
administrator-controlled shell input; do not populate them from untrusted data.

The local command executes on the Nagios server, so its service host must
represent that machine. Other Linux hosts need a separately configured execution
mechanism.

Keep cumulative `<interface>_in_octet` / `_out_octet` counters and, where available,
`_in_error`, `_out_error`, `_in_discard`, `_out_discard` labels.
`-f -e` enables SNMP counter perfdata; `-g` selects 64-bit octets.
Do not substitute percentage or precomputed rate perfdata: RRD derives rates.
Example error/discard thresholds are 10/20 events per minute, with inbound
discard alerting disabled. Graph error/discard rates are displayed per second.

For isolated CLI/CGI tests, `NAGIOS_GRAPHS_RRD_DIR` selects the archive directory.
These environment settings are administrator-controlled; request parameters never
select files, configuration or external commands.

### Shared numeric axis defaults

Graphs expressed in kB/KB, MB, GB, TB, KiB, MiB, GiB, TiB, percent, W, load,
Mbit/s, /s, Errors/s or Discards/s
disable RRDtool's additional SI prefixes by default. For example, a GB graph
shows `7000`, not `7 k`. Raw bytes (`B`) retain automatic prefix scaling.
An explicit `units_exponent` overrides the default. This affects only display,
not stored values, unit conversions or archive identities. Legend statistics
retain their calculation; their formatting follows the unit as described below.

Use the existing line `scale`, `unit` and `axis` fields for mixed-unit graphs.
Keep import bounds in the source's native unit: a millivolt UPS datasource needs
a maximum of `300000` where a volt datasource uses `300`. A graph line can
convert millivolts to volts with `scale: 0.001`. Changing a catalog bound does
not alter an existing RRD datasource; use the bounds tuning tool with a backup
when applying a correction. Already missing historical samples are not recovered.

## Time windows

Graph pages and hover previews default to four hours. The toolbar offers common
hourly windows plus day/week/month/year. Explicit `period=Nhour` or `Nhours`
accepts 1–24 hours; `period=hour` remains supported. Custom zoom changes the
requested display window, not archive resolution or retention.

## Adaptive ranges and numeric labels

Omit `left_format` to let RRDtool choose decimal precision from the visible range.
Generated traffic graphs use this for both quiet and busy links, while their
Mbit/s unit remains fixed. Set `autoscale: true` to derive axis bounds from the
visible data (RRDtool `--alt-autoscale`), for example phase voltage. Single-axis
percentage graphs with `lower: 0` use `--alt-autoscale-max`, keeping a zero
baseline while adapting the upper range. Omit fixed
lower/upper bounds when the range should be fully adaptive. A missing voltage
measurement stays unknown; it must not be converted to a zero-voltage sample.

### Explicit capacity for virtual interfaces

Set `_GRAPH_MAX_BPS` on an interface service when SNMP reports zero speed but
the reviewed physical capacity is known. The generator translates this into
an octet-counter maximum in bytes/second with 5% measurement tolerance. For
example, `1000000000` yields `131250000` bytes/s. Error/discard counters are
unaffected. The same catalog bounds are used by the existing RRD tuning tool;
regenerating a catalog alone does not tune existing files or repair old samples.


### Interface fault counter limits

Generated in/out error and discard metrics have minimum 0 and default maximum
`2**31 / poll_interval_seconds` events/s (about 2,386,093/s at 15 minutes).
These SNMP fault counters are Counter32 even when traffic uses Counter64.
The half-counter-space guard rejects the near-`2**32 / interval` spikes caused
by small counters resetting, without assuming that faults are limited to half
the physical packet capacity. It is a heuristic: some resets remain below the
limit, and genuine sustained high rates above it become unknown. A service can
override it with a positive integer `_GRAPH_MAX_FAULTS_PPS` when needed.
Traffic counter policy and archive identities remain unchanged.

Existing RRD limits require explicit tuning; catalog deployment alone does not
update their datasource bounds. `tune-rrd-bounds.py --interface-faults-only` previews
only these four counter kinds. Applying additionally requires `--apply` and a
new `--backup-dir`, with the importer stopped. The tool preserves historical
samples, including existing spikes.


### Legend formatting and axis precision

One shared display policy in `nagios_graphs_config.py` defines defaults for
all graphs using the same unit. Watts have integer legend statistics; raw bytes
use automatic decimal SI prefixes with two decimals (940490463.64 B becomes
940.49 MB). Rates in `/s`, `Errors/s` and `Discards/s` have four decimals; all
other legend statistics retain two decimals. Average values are rounded for
display only. Per-line units apply before right-axis drawing conversion.

Generated interfaces rely on these unit defaults. Their traffic, right error/
discard axis, and hidden fault graphs let RRDtool choose tick precision instead
of imposing three or four decimals. Explicit `left_format`, `right_format` and
`units_exponent` in administrator-owned catalogs still take precedence.

Both interface discovery and other metric polling metadata use the same service
template resolver: local properties override inherited ones, and earlier `use`
templates take precedence. Fractional check intervals are converted from minutes
to rounded whole seconds consistently (for example 1.5 minutes becomes 90
seconds). The supported 60–604800 second range remains unchanged.


### Quiet interface fault axes

Automatic right axes containing only nonnegative COUNTER error/discard rates
(`/s`, `Errors/s`, `Discards/s`) use ordinary numeric labels without SI prefixes.
Precision follows the measured peak: 0.24/s uses two decimals, while 0.001/s
uses four (up to six). An explicit `right_format` still overrides this default.

When every right-hand series has a measured maximum of zero in the selected
window, hide the right axis and its label. Keep the curves and their legend
statistics. An unknown/missing series prevents hiding: absence is not zero.
Fixed-ratio axes such as UPS voltage retain their existing behavior. Formatting
uses the viewer's existing decimal separator; no locale change is introduced.
