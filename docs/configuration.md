# Explicit graph configuration

Install an administrator-owned `/etc/nagios-graphs/graphs.json`. Both the importer
and CGI use it automatically. A site may instead select a generated catalog
under `/var/lib/nagios-graphs/` using `NAGIOS_GRAPHS_CONFIG`. `NAGIOS_GRAPHS_CONFIG` selects an alternative path
for tests or a second installation. A selected missing or invalid file fails
closed; it never falls back to collecting legacy metrics. A configuration file is mandatory. There is no host-specific fallback.

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

Archive filenames remain the first 32 hexadecimal characters of SHA-256 of
Python `json.dumps([host, archive_service, metric]).encode()`. Existing IDs are
unchanged. Changing datatype or heartbeat does **not** migrate existing RRD files;
plan those changes separately. Retention remains 1-minute/1-day, 5-minute/7-day,
and hourly/1-year archives. Missing values stay unknown.

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
can set `width` to 1 or 2. Graphs can set `note`, `units_exponent`, `left_format`
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
