# Scheme admin panel

The panel uses the existing FastAPI app, SQLAlchemy session/repositories,
PostgreSQL tables and vanilla HTML/CSS/JavaScript. It adds no framework, dependency,
user table or migration. The voice interface and deterministic eligibility engine
are unchanged. No production records are modified simply by starting the panel.

## Configure and open

1. Keep the existing `DATABASE_URL` and runtime configuration.
2. Supply `ADMIN_PASSWORD` securely in the server process environment.
3. Supply an independently generated random `ADMIN_SESSION_SECRET` of at least
   32 characters in that environment. Keep it stable across app restarts/workers.
   Never put either secret in committed files, browser code, URLs or logs.
4. For HTTPS, set `ADMIN_COOKIE_SECURE=true`; local HTTP uses `false` by default.
5. Start the existing app with `python -m uvicorn backend.server:app --reload`
   for local development and open `http://127.0.0.1:8000/admin`.

`.env.example` documents variable names with empty secret values. It is not loaded
automatically. Missing password or missing/short signing secret disables admin
access with HTTP 503; the public voice interface remains available.

## Use

The list defaults to active schemes. Choose Archived or All schemes to inspect
legacy records. Search is a literal, case-insensitive name substring. Combine
government level, state/UT, normalized category and confidence filters; results
are sorted by name and ID and paged in groups of 25. State/category options come
from stored records, including archives.

Select a scheme name for its detail view. Save metadata edits explicitly; leaving
the page discards unsaved form values. Source URLs must be absolute HTTP(S) URLs.
No source is scraped or fetched. Dates use YYYY-MM-DD. Blank nullable values are
stored as null; names must be nonblank. The UI sends changed metadata fields only.

Activate/deactivate updates `is_active` only. Deactivation requires confirmation,
excludes the scheme from existing active-list and eligibility APIs, and preserves
its metadata and rules. There is no scheme hard-delete or scheme creation endpoint.

The rule form uses the existing supported field/type registry and operator set:
`==`, `!=`, `>`, `>=`, `<`, `<=`, `IN`, `NOT_IN`. Enter JSON values: `18`, `"female"`,
`true`, or `["female", "male"]`. IN/NOT_IN require a nonempty list. Numeric ordered
comparisons, nonnegative thresholds and disability percentage bounds use the
existing `RuleDefinition` validator. Unsupported fields/operators, mismatched
types and malformed values return 422 without database writes. Existing string
case normalization and decimal precision handling remain in force.

Rules can be added, edited and deleted. Deletion requires confirmation and changes
the structured eligibility restrictions. Rule confidence/notes are stored in the
existing source metadata, preserving its other provenance keys. Both imported
`confidence` and `rule_confidence` formats are supported. No rule can be mutated
through another scheme's ID.

The manual-conditions list remains visible and read-only. Other eligibility text
can be explicitly edited, but does not clear imported manual conditions. Neither
confidence changes nor rule edits automatically resolve manual requirements or
change confidence classifications. The panel is not a manual eligibility-approval
workflow. Import identities and original raw source records are not editable here.

## Authentication boundary

This is lightweight administrative protection for a demo/student project, not
enterprise authentication. Password comparison uses `secrets.compare_digest`.
An eight-hour HMAC-signed session uses a random CSRF nonce and an HttpOnly,
SameSite=Strict cookie, with configurable Secure for HTTPS. All `/api/admin/*`
routes require that session; mutations and logout also require its CSRF header.
Login requires a custom header and JSON; cross-origin browser access is not enabled.
Do not add permissive credentialed CORS around these routes.

Panel HTML is outside the public static mount. `/admin` redirects unsigned users
to `/admin/login`; protected APIs return 401. The login page and JavaScript/CSS
are public assets containing no secrets. Successful protected responses are
marked no-store. Incorrect login responses never echo submitted passwords.
No OAuth, JWT, registration, users table or role system is added.

Sign out clears the browser cookie. Sessions are stateless: an independently
copied cookie remains valid until expiry; rotating either environment secret
invalidates all existing sessions. There is no per-session revocation, per-user
audit trail, rate limiter or multi-editor conflict resolution. Concurrent saves
to the same field follow database write order. HTTPS should be used beyond local
development.

## API and implementation

| Endpoint | Purpose |
| --- | --- |
| `POST /admin/login` | Establish admin cookie |
| `GET /api/admin/session` | CSRF token and supported rule field/operator metadata |
| `POST /api/admin/logout` | Clear cookie |
| `GET /api/admin/filters` | Stored state/UT and category options |
| `GET /api/admin/schemes` | Filtered, paginated list |
| `GET /api/admin/schemes/{id}` | Scheme metadata, provenance and rules |
| `PATCH /api/admin/schemes/{id}` | Explicit metadata/active-status edits |
| `POST /api/admin/schemes/{id}/rules` | Add validated rule |
| `PUT /api/admin/schemes/{id}/rules/{rule_id}` | Edit validated rule |
| `DELETE /api/admin/schemes/{id}/rules/{rule_id}` | Delete rule |

List parameters: `search`, `government_level`, `state_or_ut`, `status_confidence`,
`category`, `is_active`, `page`, `page_size` (maximum 100). Omitting `is_active` in
the API includes both active and archived records. Existing public read APIs retain
their previous behavior and do not permit writes.

Routes are in `backend/api/admin.py`; authentication in
`backend/services/admin_auth.py`; transaction operations in
`backend/services/admin_service.py`; request schemas in `backend/admin_schemas.py`.
Queries stay in the repository layer. Mutations commit once per request; the
existing session dependency rolls back unsuccessful requests when closing.

## Dataset refresh policy

The importer now skips and reports conflicting existing metadata/rules by default,
protecting admin edits. Review the report before using the explicit
`python -m backend.db.import_dataset --overwrite-existing` option; that option
can replace admin changes. Identical re-imports remain idempotent.

## Verification

`python -m pytest tests/test_admin.py -q` exercises authentication, cookie flags,
CSRF, expiry/tampering/secret rotation/logout, missing configuration, filters,
pagination, metadata validation, source preservation, archive behavior, rule CRUD,
cross-scheme protection and malformed-rule rejection. Temporary secrets are
generated at runtime and never committed. Tests use actual Alembic migrations on
isolated storage, including the full imported 297 active + 67 archived catalogue.

The full existing test suite remains the regression check. Live PostgreSQL and
Redis tests require their existing `TEST_DATABASE_URL`/`TEST_REDIS_URL` settings.
The final implementation run passed **248 tests and 1,106 subtests**, with two
live-service tests skipped and six existing warnings. All ten new admin tests passed.
No PostgreSQL credentials or connected browser were available in the implementation
shell, so this change does not claim fresh live PostgreSQL or rendered-browser
verification. Both admin JavaScript files passed `node --check`.

Manual browser check: log in, combine filters, paginate, open a scheme, edit one
metadata field and reload, add/edit/delete a rule, cancel then confirm deactivation,
reactivate, and sign out. Verify unauthenticated API writes fail. Use disposable
test data for rule/active-status checks.
