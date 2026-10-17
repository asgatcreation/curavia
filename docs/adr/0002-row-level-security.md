# ADR 0002 — PostgreSQL row-level security as a second barrier

**Status:** accepted, implemented, off by default
**Date:** 17 October 2026

## Context

Curavia keeps every hospital in one database and one schema, with an
`organisation_id` on every row (see ADR 0001). The primary protection is the
manager in `apps/core/tenancy.py`, which raises `TenantScopeMissing` rather
than returning unfiltered rows.

That guard is strong but it lives in Python. It does not cover a hand-written
`raw()` query, a `cursor.execute` in a data migration, an ORM bug, or a
developer in a shell. For patient data, one layer is not enough.

PostgreSQL row-level security puts the same rule in the database: a policy on
each tenant table restricts visible rows to those whose `organisation_id`
matches a per-transaction setting.

## Decision

Implement RLS, and ship it switched off until the deployment has the database
role it requires.

- `TenantScopeMiddleware._apply_database_scope` issues
  `SELECT set_config('app.current_org', %s, true)` on each request. The `true`
  makes it transaction-local, which is why `ATOMIC_REQUESTS` is on: the setting
  is discarded when the request's transaction ends, so it cannot leak to the
  next request that reuses a pooled connection.
- `manage.py harden_database --rls` creates one policy per tenant table.
- `CURAVIA_ENABLE_RLS` gates the middleware's half.

## The pitfall that makes this not free

**A table's owner bypasses RLS.** In PostgreSQL, policies do not apply to the
role that owns the table unless the table is also set to `FORCE ROW LEVEL
SECURITY`. Django's migration role *is* the owner. So:

- Enabling policies without `FORCE` protects nothing when the application
  connects as the owner — the most dangerous possible outcome, because the
  dashboard says RLS is on.
- Adding `FORCE` without a separate role breaks migrations and the Django
  admin, because the owner then also needs `app.current_org` set, and
  `makemigrations`/`migrate` have no tenant.

The correct arrangement is two roles:

| Role | Owns tables | RLS applies | Used by |
|---|---|---|---|
| `curavia_migrate` | yes | no (owner, not forced for it) | `manage.py migrate` |
| `curavia_app` | no | **yes** | the running application |

Creating those roles is a deployment step on Neon, not something the
application can do for itself, so RLS stays off until it is done.

## Consequences

**Good.** A second, independent barrier. `raw()` queries and shell sessions are
covered. A reviewer can verify the rule in the database rather than trusting
the ORM. It is the kind of defence in depth a hospital's IT staff will ask
about.

**Bad.** Two database roles to manage, and the migration role must be used for
migrations only. A misconfiguration produces either "no rows anywhere" (loud,
fine) or "RLS appears on but is bypassed" (quiet, dangerous) — so
`harden_database` prints a warning when the policies exist but the setting is
off, and `/readyz` reports `rls_enabled` so the live state is visible.

**Also.** RLS is not a substitute for the ORM guard. Without the Python layer,
a missing scope would silently return *zero* rows instead of raising — a
hospital seeing an empty patient list is a worse failure to debug than a
crash. Both layers stay.

## Status of the work

- [x] Middleware sets `app.current_org`
- [x] `harden_database --rls` creates the policies, with `FORCE`
- [x] `/readyz` reports whether it is enabled
- [ ] `curavia_migrate` / `curavia_app` roles created on Neon — milestone 9
- [ ] A test that runs the suite with `CURAVIA_ENABLE_RLS=true` — milestone 9,
      once CI has the two roles
