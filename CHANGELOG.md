# Changelog

## v0.2.1 (2026-11-21)

### Security and maintenance

- Start the drain service and its importer directly through their shebangs.
- Restrict the drain service's writable paths to the spool and the RRD directory.
- Install executable scripts with mode `0755` and exercise direct execution in the staged installation test.

### Upgrade from v0.2.0

Stop the drain timer and let the active run finish before replacing files. Install
the sealer, drainer and importer with mode `0755`, then install the updated drain
unit and reload systemd. The update keeps the existing catalog, spool and RRD
archives. Follow the [upgrade guide](docs/install.md#upgrade-from-v020).

## v0.2.0 (2026-11-20)

### Upgrade requirement

Existing v0.1.0 installations must migrate hash-only RRD filenames to readable
names and deploy the importer, viewer and helpers together. This is a coordinated
offline upgrade with backups, not a drop-in runtime replacement. Unmapped RRDs
are retained. Rollback refuses to overwrite archives updated after migration.
See [the upgrade guide](docs/install.md#upgrade-from-v010-migrate-rrd-filenames).

### Added

- Readable, deterministic archive names shared by collector and renderer, plus
  migration preview, backup, rollback and audit tools.
- Explicit native-unit metric bounds and a preview-first bounds tuner. New
  interface traffic archives can use the reported speed as a quality guard.
- Polling metadata and bounded dashed last-known-value tails for gauges. Tails
  do not create measurements or contribute to statistics; counters are excluded.
- Adaptive graph ranges, including percentage charts with a zero baseline.
- Shared numeric unit defaults and readable byte legend statistics.
- Interface capacity and fault-rate overrides with documented limitations.
- Full runtime and real-RRD tests on each code pull request.

### Changed

- Default graph and hover window is four hours; explicit hourly windows from
  one through 24 hours are accepted, alongside day/week/month/year.
- Power legends show whole watts. Pre-scaled units, load and rate graphs retain
  their declared numeric units instead of acquiring another SI prefix.
- Interface error/discard axes use ordinary decimal labels and disappear when
  every right-hand series has a measured maximum of zero in the selected window.
  Missing or unknown series prevent hiding. Legend statistics remain visible.
- Interface error/discard counters use an interval-based reset guard. This is a
  heuristic: some reset artifacts may remain and exceptional genuine rates above
  the bound become unknown. Existing RRD bounds require explicit tuning.
- Shared service-template resolution accepts fractional check intervals
  consistently. Host navigation avoids probing unrelated archive files.

### Compatibility and limitations

- Graph IDs and URL parameters remain supported. The JSON catalog version is
  still 1, with optional additional fields; older runtime helpers reject them.
- No historical spike repair is performed automatically. Sensor bounds operate
  in native units; millivolt sources need millivolt limits even when displayed
  in volts. Tune limits only after reviewing the proposed changes and backups.
- Installation remains manual. Every authorized CGI viewer can access the
  complete configured catalog; Nagios contact authorization is not implemented.
- Existing anonymized screenshots are retained as illustrations; they have not
  been regenerated to demonstrate every new rendering option.

## v0.1.0

Initial public release with explicit graph configuration, local RRD collection,
authenticated read-only CGI graphs, interface generation, dual axes, hover
previews and drag-to-zoom.
