# Standalone installation (explicit mode)

This project grew out of one internal Nagios installation. The integration steps
are intentionally manual and may be rough around the edges; adapt paths, users,
permissions and plugin metric labels rather than assuming a turnkey setup.
Before activating anything, you can [test a copied installation layout](staged-test.md)
in a temporary directory without building another Nagios server.

Target: Debian/Ubuntu, Nagios Core, Apache CGI and systemd. Requires Python 3.9+,
RRDTool, Node only for tests, and an existing `nagios` user and group. Commands
below run from the repository root. Review paths for other distributions.

```sh
sudo apt-get install python3 rrdtool fonts-dejavu-core
sudo install -d -o root -g root -m 0755 /usr/local/libexec /etc/nagios-graphs
sudo install -d -o nagios -g nagios -m 0750 /var/spool/nagios-graphs /var/lib/nagios-graphs/rrd
sudo install -m 0644 scripts/nagios-rrd-{seal,drain,import}.py scripts/nagios_graphs_config.py scripts/rrd_names.py scripts/generate-interface-graphs.py scripts/nagios-rrd-web.cgi /usr/local/libexec/
sudo install -m 0755 scripts/check-nagios-graphs-spool.py /usr/local/libexec/
# New installation only: do not overwrite an existing configuration.
sudo install -o root -g nagios -m 0640 examples/graphs.json /etc/nagios-graphs/graphs.json.example
sudo cp -pn /etc/nagios-graphs/graphs.json.example /etc/nagios-graphs/graphs.json
```

Edit the example to match real plugin output. Validate before enabling collection:

```sh
sudo -u nagios python3 /usr/local/libexec/nagios_graphs_config.py /etc/nagios-graphs/graphs.json
```

The live perfdata file and batches share the spool filesystem. A dedicated volume
limits disk-exhaustion impact and is recommended, but is not required by the
runtime. The monitoring plugin defaults to requiring ext4 on a dedicated mount;
use `--filesystem any` for other types, or explicitly `--allow-shared` for a
shared filesystem. That last option checks free space only and loses mount-loss
detection. Default warning/critical free space is 20%/10%.

## Web server and access

Install the CGI in the existing authenticated Nagios CGI directory:

```sh
sudo install -m 0755 examples/graphs.cgi /usr/lib/cgi-bin/nagios4/graphs.cgi
sudo install -m 0644 scripts/nagios-rrd-hover.js /usr/share/nagios4/htdocs/rrd-hover.js
```

The CGI entrypoint runs the renderer in `/usr/local/libexec`, beside its shared helper. Grant the Apache user
read/traverse access to `/etc/nagios-graphs/graphs.json` and RRD directories/files,
for example using a dedicated read-only group and directory ACLs. Do not make
archives writable by Apache. Do not expose archive or spool directories as HTTP
aliases. Verify actual filesystem access with `sudo -u www-data test -r ...`.

Protect `graphs.cgi` with the **same authentication policy** as Nagios, including
PNG requests. The CGI does not implement Nagios contact/host/service authorization:
every authenticated viewer can read every configured graph. Use it only for an
audience allowed to see the entire catalog, or restrict access to a dedicated
operator account/group. Test unauthenticated HTML and PNG requests are denied.

Load `rrd-hover.js` from the Nagios page template. Existing installations keep
the default endpoint. For arbitrary configured graph ids, define the following
in an administrator-controlled, same-origin JavaScript asset loaded first:

```js
window.NagiosGraphs = {path: '/nagios4/cgi-bin/graphs.cgi'};
```

The hover script only requests that exact same-origin path. Adapt CSP to allow
these local assets. Graph links use `?view=<graph-id>`; `host`/`service` links work
only where they identify one unambiguous graph.

## Collection and activation

Merge [the Nagios example](../examples/nagios.cfg) into the appropriate main/object
configuration files, preserving any existing perfdata consumer. Explicitly enable
`process_perf_data` on chosen services. Validate Nagios configuration before reload.

```sh
sudo install -m 0644 systemd/nagios-rrd-drain.{service,timer} /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now nagios-rrd-drain.timer
```

Confirm batches disappear, the drain service succeeds, archives appear and a PNG
renders. Monitor spool space, batch backlog and failed systemd units. A failed
batch is retained for retry; inspect it rather than deleting measurements blindly.

To stop collection, disable service perfdata processing/reload Nagios first,
then stop/disable the timer. Keep archives and failed batches for recovery.

## Validation

```sh
python3 -m unittest discover -s tests -v
node tests/test_nagios_rrd_drag.js
node tests/test_nagios_rrd_hover.js
```

Install `rrdtool` before running the suite: a skipped real-RRD test is not proof of
working rendering. The directory-based smoke test checks copied runtime files and
synthetic data; web authentication, account permissions and systemd activation
must still be verified in the target installation.



## Upgrade from v0.1.0: migrate RRD filenames

**Required for existing installations:** v0.1.0 uses hash-only filenames, while
this release uses readable filenames. Copying the new importer/viewer without
migrating existing archives can split new and historical measurements. Test the
migration against a copy first and preserve an independent backup.

### Upgrade existing RRD archives to readable names

The importer and CGI must be upgraded together with the archives. Run these
commands from the reviewed release checkout on the Nagios host. This is an
offline change; a short gap in graph collection is expected. The commands below
use the default `/etc/nagios-graphs/graphs.json`; if your deployment selects a
generated catalog with `NAGIOS_GRAPHS_CONFIG`, pass that actual path instead.
Keep the spool
batches, and do not restart the drain before the migration completes.

```sh
# Prevent the next scheduled drain while migrating.
sudo systemctl stop nagios-rrd-drain.timer
sudo systemctl stop nagios-rrd-drain.service
# Verify no importer is still running before proceeding.
pgrep -af 'nagios-rrd-(import|drain)' || true

# Review the plan, then make a verified, independent backup and rename.
sudo python3 scripts/migrate-rrd-names.py --catalog /etc/nagios-graphs/graphs.json
sudo python3 scripts/migrate-rrd-names.py --apply \
  --catalog /etc/nagios-graphs/graphs.json \
  --backup-dir /var/backups/nagios-rrd-readable-$(date +%Y%m%d-%H%M%S)

# Deploy the collector, graph renderer and shared helpers together.
sudo install -m 0644 scripts/nagios-rrd-import.py scripts/nagios-rrd-web.cgi \
  scripts/nagios_graphs_config.py scripts/rrd_names.py /usr/local/libexec/
sudo systemctl start nagios-rrd-drain.timer
```

After applying the filename migration, deploy all runtime scripts as described
in the installation section, not just the importer and viewer. Regenerate any
interface catalog with the new generator and validate it using the new helper
before restarting collection.

The migration script never deletes unmapped RRDs. Do not run --apply while
another collector can update the archives. Save the exact backup directory
printed by the shell for recovery. A rollback requires the drain to be stopped
again and refuses to overwrite any archive modified after migration. If samples
have been added, preserve them and perform a manual recovery instead of forcing
a rollback.

Verify the timer, journal, new archive timestamps, and authenticated CGI after
the upgrade. The old hash naming helper remains for audit and rollback only.

## Metric bounds (new and existing archives)

The catalog accepts optional `min_value` and `max_value` for each metric.
For GAUGE these are native sensor units; for COUNTER they are **rates per
second**, not the cumulative raw counter. Example metrics:

```json
{"id":"wifi-percent","host":"ap","service":"WiFi","metric":"signal_percent","type":"GAUGE","min_value":0,"max_value":100}
{"id":"mains-voltage","host":"meter","service":"Voltage","metric":"voltage_v","type":"GAUGE","min_value":0,"max_value":300}
```

Choose bounds from verified sensor semantics and installation capacity, not
metric-name guesses. dBm signal strength is not a percentage. Flow-rate units,
power and voltage ranges vary. Unknown or unreliable capacity means leave the
bound unspecified until verified. Values outside RRD bounds become UNKNOWN.

For new interface octet COUNTER archives the importer automatically uses the
same perfdata record's `<interface>_speed_bps` value, divided by 8 and with
5% tolerance. No speed value means no inferred upper bound. This protects
newly created RRDs, but does not modify existing RRDs or adapt an existing
archive automatically when link speed changes.

To preview and then tune existing archives using explicit catalog limits and
the latest Nagios status perfdata (including interface speeds):

```sh
sudo python3 scripts/tune-rrd-bounds.py --catalog /etc/nagios-graphs/graphs.json
# Stop collection before applying, and inspect the dry-run first:
sudo systemctl stop nagios-rrd-drain.timer
sudo systemctl stop nagios-rrd-drain.service
sudo python3 scripts/tune-rrd-bounds.py --catalog /etc/nagios-graphs/graphs.json --apply \
  --backup-dir /var/backups/nagios-rrd-bounds-$(date +%Y%m%d-%H%M%S)
sudo systemctl start nagios-rrd-drain.timer
```

The tuner backs up every affected archive before changing RRD metadata and
leaves historic samples unchanged. Re-run the dry-run after a link-speed change
or catalog update. Generated catalogs must inherit explicit bounds from their
administrator-owned base configuration; do not edit generated catalog state
directly. Generated interface error/discard counters use an interval-based Counter32 reset guard;
see [interface fault limits](configuration.md#interface-fault-counter-limits).
This is not a physical packet-rate limit and does not repair historical spikes.

### Conservative sensor defaults

The importer and bounds tuner share a unit-aware policy for known sensor
metrics: percent gauges (0–100), WiFi/Bluetooth RSSI (-120–0 dBm), UPS
and per-phase mains voltage (0–300, assuming volt-valued perfdata), humidity (0–100%), and temperature
(-40–150 °C). Known nonnegative rates, load, current, storage capacity,
and latency receive a zero lower bound only. Signed per-phase power remains
unrestricted in both directions. Ambiguous disk usage and unfamiliar metrics
are not automatically bounded.

The defaults apply to existing archives via the tuner and to future archives
via the importer. Explicit catalog `min_value`/`max_value` take precedence.
UPS plugins emitting millivolts need explicit native-unit bounds, for example
`max_value: 300000`; graph display scaling does not change datasource units.
Review the dry-run before applying. RRD maximums are data quality guards,
not safety thresholds or alerting limits.
