# Test a copied installation layout

You do not need another Nagios server. This smoke test copies the runtime, shared
helper, CGI entrypoint, examples, JavaScript and systemd templates into a new
private temporary directory. It uses synthetic hosts and measurements, not your
live configuration or RRD history. Run it as a normal user without sudo.

Requires Python 3.9+, RRDTool and its normal rendering/font dependencies:

```sh
python3 tools/test-staged-install.py
```

Run this from a checkout of the intended release commit. The script prints the
temporary directory and saves `report.json` plus example PNGs there. You can use
`--output /path/to/a/new-directory`; existing directories are refused. The test
uses only this new directory for its files and does not install packages, invoke
Nagios, contact devices, start a web server or run `systemctl`.

## What the full test checks

- Runtime/helper/CGI files copied into an installation-like directory tree.
- A generated catalog from the shipped JSON example and a tiny Nagios object fixture.
- Validation by the copied helper, executed outside the source checkout.
- Synthetic signed gauges and counter samples, sealing and queued import into RRDs.
- Complete spool drainage and an empty repeat drain.
- HTML with zoom metadata and PNG rendering through the copied CGI entrypoint for
  all five example/generated graph views, including automatic dual-axis rendering.
- Rendering leaves all nine synthetic archives unchanged.

The CGI entrypoint's runtime path and the config/RRD environment variables point
into the staging directory. Unit files and the perfdata example are copied for
layout review; they are not activated or submitted to a running Nagios instance.

## Partial check without RRDTool

```sh
python3 tools/test-staged-install.py --layout-only
```

This checks copying, generation and config validation. Its report explicitly says
RRD creation and CGI rendering were skipped; it is not the full release test.
Manual GitHub Actions dispatch runs the full smoke test after installing RRDTool.

## Limits

This is a packaging and runtime smoke test on the host's Python/RRDTool versions.
It does not prove compatibility with another operating system or Nagios setup.
Apache routing/authentication, user/group access, ACLs, real plugin perfdata and
systemd hardening/timer activation remain checks for the target installation.
The project has primarily been exercised in the author's own internal setup and
installation can still require manual adjustment.

No background process remains running. After reviewing the report and images,
you may remove the printed test directory; it contains only synthetic test files.
