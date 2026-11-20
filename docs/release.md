# Release checklist

## Candidate review

- [ ] Approve the proposed version number and release notes.
- [ ] Review the complete diff against the previous public release.
- [ ] Review filenames, documentation, fixtures and binary metadata for
      deployment-specific information, credentials and private paths.
- [ ] Preserve the existing public Git history; import reviewed file changes
      only, without merging development repository history.
- [ ] Run the complete Python and JavaScript suites with real RRDtool.
- [ ] Run the full [staged installation test](staged-test.md) on the exact
      approved candidate, not only its layout-only mode.
- [ ] Exercise the v0.1.0 filename migration and rollback on synthetic archives
      or an independently backed-up copy; verify samples are unchanged.
- [ ] Confirm the coordinated-upgrade instructions and documented limitations.

## Publication

- [ ] Obtain approval before creating a public branch or pull request.
- [ ] Run public CI on the reviewed candidate.
- [ ] Merge only the reviewed patch and prepare the version tag and release.
- [ ] Publish after final approval, with upgrade requirements prominent.
- [ ] Submit or update the Nagios Exchange entry if desired.

No candidate checklist item implies broader platform certification. The staged
smoke test checks copied runtime files with synthetic data; it does not validate
web authentication, account separation, systemd activation or a fresh server.
Consumer deployments must install matching importer, viewer and helper versions.
