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
sudo install -m 0644 scripts/nagios-rrd-{seal,drain,import}.py scripts/nagios_graphs_config.py scripts/generate-interface-graphs.py scripts/nagios-rrd-web.cgi /usr/local/libexec/
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

