# Repository reliability pass

Scope: pytest collection, integrity portability, conservative dependency pins,
safe re-import, coverage wording, documentation and CI. No scheme CSV or
eligibility implementation was changed. Prior security/privacy implementations
were compared against the start-of-pass files and preserved byte-for-byte.

## Import safety

Existing rows are compared against desired metadata and a sorted representation
of all structured rules and their source metadata. Differences skip the whole
scheme by default and are reported without printing profile data or credentials.
This covers previously made admin edits without needing a new dirty-marker
column or a migration. The report deliberately does not guess whether a difference
originated from a source change or a manual edit. Identical records stay
idempotent; reviewed replacements require `--overwrite-existing`.

## Integrity baseline

The active manifest is `user-ux-preserved-hashes.json`. Its representation now
uses sorted forward-slash paths and SHA-256 after CRLF-to-LF normalization. No
other whitespace/content normalization occurs. Existing raw hashes were verified
before translation. The importer hash was updated for this pass's intentional
change. The existing rule-engine mismatch was **not** blessed: its previous
expected content was used for normalization, so that check still fails.
Other phase hash files are historical records and were not regenerated.

## Dependencies and Python

The supported target is Python 3.12, matching the exercised interpreter,
`.python-version`, Railpack and CI. Direct dependency pins were read from installed
package metadata using the existing `.test-deps` test overlay, without upgrading
packages. This is not a complete transitive lock or proof of a clean Linux install.
No Python 3.10 compatibility claim is made. A fresh hosted CI build remains to be run.

## Legacy classification

| Path | Classification and decision |
| --- | --- |
| `backend/server.py`, services/repositories/API modules | Active production paths |
| `backend/logic.py` | Compatibility/test layer; imported by `tests/test_logic.py` and patched by a conversation test; retained |
| `backend/agent/executor.py`, `evaluator.py`, `planner.py` | Unused by current server/tests/scripts; historical callers of compatibility code; retained and labelled here |
| `backend/tts.py` | Unused standalone legacy synthesis; active TTS is in server.py; retained as historical code |

No Java files, Maven POM or existing Java/Spring runtime were found. No conversion
or architectural refactoring was performed. The older ARCHITECTURE document is
explicitly marked historical; README is the current description.

## Coverage and known blockers

The state master and state eligibility CSVs each represent 11 unique jurisdictions;
the review CSV represents one of those jurisdictions. Their union is 11, not
complete India-wide state coverage. No source research or new facts were added.

The pre-existing rule engine returns POTENTIALLY_ELIGIBLE while the response schema
accepts only three older statuses. A response-contract test also omits the existing
potential_schemes field. Those failures and the stale rule-engine integrity guard
remain visible; this backend is not yet safe to freeze as a passing reference.
CI runs ordinary pytest without suppressing these failures. Tests apply their
own migrations in isolated databases/schemas; service containers need no manual seed.

## Verification results

- `python -m pytest tests/test_import_safety.py tests/test_database.py tests/test_admin.py -q --tb=short`: 29 passed, 1,057 subtests passed.
- `python -m pytest tests/test_import_safety.py tests/test_integrity_portability.py -q`: 4 passed.
- `python -m pytest -q`: 295 passed, 21 failed, 3 skipped, 3 warnings, 1,111 subtests passed (49.00 seconds on this local run).
- Skips: no disposable PostgreSQL/Redis test URLs configured locally; Windows did not permit the symlink fixture. CI config supplies disposable services, but hosted CI was not executed in this pass.
- The 21 failures are the unchanged status/schema and response-contract mismatch plus the existing rule-engine integrity mismatch. No failure was skipped or weakened to obtain a green report.
- The accidental helper collection warnings are gone. Python compilation and workflow YAML parsing passed. Git diff and a start-of-pass comparison showed focused implementation changes; pre-existing large formatting differences were not introduced by this pass. No CSV facts, eligibility code, or security/privacy implementation bytes changed. Credential-pattern checks of changed/new text files found no matches; local secret files were not copied or committed.
- Local tests used `PYTHONPATH=.test-deps` and `PYTHONIOENCODING=utf-8`. Fresh dependency installation and Linux CI execution remain unverified.
