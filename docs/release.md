# Public release checklist

## Completed preparation

- [x] Explicit catalog; no built-in site names, graph rules or hover allowlists.
- [x] Generic interface generator with optional private archive seed and exclusions.
- [x] Generic dual axes, automatic counter scaling, optional counters and gap overlays.
- [x] Standalone configuration examples and explicit authorization boundaries.
- [x] Compare the private production catalog against live data in an isolated test.
- [x] Run the real-RRD suite and migrate the existing internal installation.
- [x] Document the personal-project scope and rough/manual installation experience.
- [x] Add sanitized graph and hover-preview illustrations with an explicit editing disclosure.
- [x] Replace the internal hostname in hover test fixtures (during private preparation).
- [x] Document interface plugin sources, all six supported command definitions,
      argument mappings and generic service examples (during private preparation).
- [x] Run the full [staged installation test](staged-test.md) on the author's Linux installation against
      commit `e691a89` on 2026-11-10: valid layout, RRD/CGI passed, nine archives,
      five rendered graph views, archive data unchanged.

## Publication progress

- [x] Review all 31 tracked text files at `e691a89` for internal hostnames,
      addresses, credentials and private paths; no additional private values found.
- [ ] Visually recheck the two shipped PNGs and their metadata before publication.
- [x] Identify the removed internal hostname in the original hover test history;
      commit author addresses use GitHub noreply addresses.
- [x] Preserve a reviewed local snapshot before deleting/recreating the repository.
- [x] Import that snapshot into the empty private repository with one new root commit;
      do not push old branches, tags or history.
- [x] Verify the repository About description and topics.
- [ ] Prepare the initial version tag and concise release notes, including requirements,
      manual setup, tested scope and known limitations.
- [x] Full CI passed for code revision `8ddd4ae` (run `42834675276`). Repeat the full staged test only
      if runtime, examples or packaging changed after `e691a89`; checklist-only edits
      do not invalidate the recorded result.
- [ ] Change repository visibility from private to public after the final review.
- [ ] Publish the versioned GitHub release.
- [ ] Submit the public repository/release to Nagios Exchange.

The agreed publication plan is to recreate the repository after preserving the
reviewed files locally. Start the replacement with fresh Git history; deletion
alone does not prevent an old checkout from reintroducing historical commits.

## Validation scope

The directory-based smoke test checks standalone packaging and runtime behavior;
a new Nagios server is not required. It does not establish fresh-server
compatibility, HTTP authentication, Unix account separation or systemd activation.
The existing installation provides operational experience; the README keeps the
limited scope explicit. Do not advertise a turnkey installer or broad platform
certification.

Consumer pin updates still require coordinated helper/catalog installation.
