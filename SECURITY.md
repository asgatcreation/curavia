# Security policy

Curavia handles patient records, so security is a feature of the design rather
than a layer on top of it. This document covers how to report a problem, and
what the system actually does.

## Reporting a vulnerability

Please report security issues **privately**, not as a public GitHub issue.

- Open a [private security advisory](https://github.com/asgatcreation/curavia/security/advisories/new), or
- contact the author through [@asgatcreation](https://github.com/asgatcreation).

Please include what you found, how to reproduce it, and what it lets an
attacker do. You will get an acknowledgement within 72 hours and an assessment
within seven days. Please give a reasonable period for a fix before disclosing
publicly.

**Please do not** run automated scanners against the live demonstration, test
denial of service, or attempt to access any account that is not one of the
clearly-labelled demo logins. The demo contains synthetic data only, so there
is nothing there worth taking.

## Scope

In scope: the application code in this repository, and the live demonstration.

Out of scope: the hosting platform, Neon, Upstash, Cloudflare and other
third-party services (report those to them); missing security headers with no
demonstrable impact; and anything that requires a compromised device.

## What the system does

Each item below corresponds to code, and to a test that fails if it stops being
true. Fuller detail is in
[docs/02-tenancy-and-permissions.md](docs/02-tenancy-and-permissions.md).

### Keeping hospitals apart

Every row of hospital data carries its organisation, and the model layer
**refuses to build a query** without a hospital in scope — so forgetting a
filter raises `TenantScopeMissing` instead of returning every hospital's rows.
The scope is derived from the signed-in user's own membership and never from
anything the client sends; a forged `X-Curavia-Organisation` header is a 403.

Underneath, PostgreSQL row-level security is available as a second barrier (it
needs a dedicated non-owner database role — see
[docs/adr/0002-row-level-security.md](docs/adr/0002-row-level-security.md)).
Above it, `backend/tests/isolation` is a suite whose only purpose is to attempt
cross-hospital access through the ORM, the HTTP API, forged headers, background
jobs, the audit log and WebSocket group names.

`scripts/check-cross-tenant-allowlist.sh` fails CI if the escape hatch is used
in a file not listed, with a reason, in
[docs/cross-tenant-allowlist.txt](docs/cross-tenant-allowlist.txt).

### Authorisation

Four independent checks must all pass: the hospital's plan, the modules it has
switched on, the staff member's role, and an object-level rule about the
specific record. Roles are per-hospital, editable records rather than
hard-coded classes.

[docs/permissions-matrix.yml](docs/permissions-matrix.yml) states every
(permission × role) expectation **by hand, from intent** — never derived from
the role definitions, which would make the tests tautological. It generates the
test suite, the README table and the in-app role editor, so widening a role
fails the build.

Field-level masking is enforced server-side: a cashier's API response does not
contain a diagnosis at all, rather than hiding one in the browser.

### Sensitive fields

HIV status, psychiatric notes and anything flagged sensitive require a separate
`clinical.sensitive.view` permission, are encrypted at application level with a
per-organisation key held outside the database, and every read is audited.

### Audit

Every **view** of a patient record is logged, not just every change — the
common real-world abuse is reading a record, not altering one. Each entry
carries the previous entry's SHA-256 hash, so a deleted or edited row breaks
the chain and `manage.py verify_audit_chain` reports the exact sequence number.
The application's database role holds no `UPDATE` or `DELETE` on the table, and
the chain tip is anchored nightly.

### Authentication

Email-based login with mandatory TOTP two-factor for staff: a password alone
never yields a usable session. Rate limits and lockout with backoff on failed
attempts. Deactivating a staff member **ends their sessions and closes their
open WebSockets immediately**, rather than waiting for a cookie to expire.

### Emergency access

Break-the-glass access requires a written reason, lasts a limited time,
notifies the medical director immediately, and is permanently flagged in the
audit log. Expiry is enforced by the permission check itself, so it does not
depend on a cleanup job having run.

### Platform access

Curavia's own staff cannot read any hospital's patient data by default. Access
requires a `SupportAccessGrant` that the hospital creates, naming a person,
with a reason and an expiry. Everything done under it is written into the
hospital's own audit log.

### Files

Medical files live in a private bucket with no public URL. They are reached
only through signed links valid for minutes, and generating one is audited.

### Transport and headers

HTTPS only, HSTS with preload, secure and HTTP-only cookies, Content Security
Policy via Django 6's native support, `X-Frame-Options: DENY`, a strict
referrer policy and a restrictive permissions policy.

## Compliance

Under the **Nigeria Data Protection Act 2023** the hospital is the data
controller and Curavia is the data processor. The repository includes a Data
Processing Agreement template, consent records, configurable medical-record
retention, patient access/export/deletion requests with deadlines, and a
72-hour breach-notification process covering both the hospital and the Nigeria
Data Protection Commission.

A hospital can export everything it holds at any time. A hospital that stops
paying becomes read-only; its data is never deleted without notice and an
export.

## Known limitations

Stated plainly, because a security document that claims no gaps is not credible:

- **Row-level security is off by default.** It needs a non-owner database role,
  because a table's owner bypasses RLS in PostgreSQL. The ORM-level guard is
  the active protection today.
- **The live demonstration runs on free hosting** with no availability
  commitment, and is not suitable for real patient data.
- **Not independently audited.** No third-party penetration test has been
  carried out.
- **Not a certified medical device.** See [LICENSE](LICENSE).
