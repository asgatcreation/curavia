# Tenancy, permissions and audit

This is the part that has to be *provable*, not claimed. Three questions to answer:

1. How do we guarantee Hospital A can never see Hospital B's data?
2. How does each of 17 roles see exactly the right rows, columns and menus?
3. How do we prove afterwards who looked at what?

---

## 1. Tenancy — keeping hospitals apart

### The choice: shared database, shared schema, row-scoped

Three options exist. Here is why I picked the third.

| Approach | How | Why not / why yes |
|---|---|---|
| Database per hospital | one Postgres DB each | Neon free tier won't do it, connection limits break, and migrating 200 databases takes hours. No. |
| **Schema** per hospital (`django-tenants`) | one Postgres schema each | Tempting, and genuinely strong isolation. But: every migration runs once *per schema* (slow and risky at scale), Django Channels and Celery both need custom schema-switching plumbing, the group roll-up dashboard needs cross-schema queries which Postgres makes painful, and connection pooling fights it. No. |
| **Shared schema, `organisation_id` on every row** ← chosen | one database, one schema, a tenant column | What real multi-tenant SaaS does. Simple migrations, works naturally with Channels and Celery, group reporting is a plain query. The risk is a developer forgetting a filter — so we remove the ability to forget. |

### Four layers so that "forgetting the filter" is impossible

**Layer 1 — the model refuses to be queried unscoped.**

```python
class TenantManager(models.Manager):
    def get_queryset(self):
        org = current_organisation()          # a contextvar
        if org is None:
            raise TenantScopeMissing(
                f"{self.model.__name__} queried with no tenant scope active"
            )
        return super().get_queryset().filter(organisation_id=org.id)
```

Every tenant-scoped model uses this as its **default** manager. There is a deliberate `Model.unscoped` manager for the handful of legitimate cross-tenant jobs (platform console aggregates, migrations, the nightly demo reset). Every use of `.unscoped` is grep-able, and CI fails if a new one appears outside an allow-list file. So the normal path is safe and the dangerous path is visible.

**Layer 2 — scope is set once, at the edge.**

A `contextvar` (not thread-local — `contextvar` works correctly with async views and Channels consumers) is set by:

- `TenantMiddleware` for HTTP, from the authenticated user's active `StaffMembership`
- a Channels middleware for WebSockets, same source
- a Celery task base class, from an `organisation_id` that **every** task signature must carry
- later, from the subdomain for white-label

The **client never sends the tenant id.** It is derived from the session. A forged header changes nothing.

**Layer 3 — PostgreSQL Row Level Security as the safety net.**

On the tenant tables we enable RLS with a policy of `organisation_id = current_setting('app.current_org')::uuid`, and the request sets `app.current_org` with `SET LOCAL` inside the request's transaction. Then even a hand-written `raw()` query, or an ORM bug, or a developer in a shell, cannot cross the line.

Honest caveat: this needs care with connection pooling (`SET LOCAL` is transaction-scoped, so it must be set inside `ATOMIC_REQUESTS`), and the migration role needs `BYPASSRLS`. I will do this in milestone 2 and write an ADR documenting the pitfalls, rather than pretend it is free.

**Layer 4 — tests that try to break it.**

A test module whose entire job is cross-tenant attacks: fetch Hospital B's patient by UUID as a Hospital A doctor, subscribe to Hospital B's WebSocket group, run a report with a tampered filter, call every list endpoint and assert the row count matches only your own org, queue a Celery task with another org's id. These run on every push. **A tenancy leak is a failing build, not a bug report.**

### Branch scoping

Branch is a *second*, softer scope on top. Operational data (`BranchScoped`) is filtered to the user's active branch unless they hold `branch.view_all`. Patients are Organisation-scoped and so visible across branches **only** if the hospital enabled `share_patient_records_across_branches` — and that read writes an audit event marked `cross_branch`.

---

## 2. Permissions — four layers

A staff member may do something only if **all four** agree:

```
      plan allows the module           (billing)
  AND the Organisation enabled it      (hospital size mode / module switches)
  AND their role grants the action     (coarse: "may verify lab results")
  AND an object rule says yes          (fine: "...but only in your own branch")
```

### Layer A — plan and module gating

`Plan.modules` and `Organisation.enabled_modules`. A solo clinic never sees the ward or theatre menus — not greyed out, *absent*. Enforced in one DRF permission class and mirrored in the API response that builds the navigation, so the menu and the API can never disagree.

### Layer B — roles → permission codes

`Role` is a **per-organisation, editable record**, seeded from templates so a hospital can rename "Front desk" to "Health records" or make a role that can do two jobs. It holds a set of string codes:

```
patients.view  patients.create  patients.merge
clinical.note.write  clinical.note.sign  clinical.sensitive.view
lab.order.view  lab.result.enter  lab.result.verify
pharmacy.dispense  pharmacy.override_warning  pharmacy.controlled.record
billing.invoice.view  billing.payment.take  billing.waiver.approve
inventory.receive  inventory.adjust  hr.staff.manage
audit.view  branch.view_all  …
```

A user's effective permissions are the union of the roles on their active `StaffMembership`. ~17 seeded role templates, matching your list.

### Layer C — object-level rules: `django-rules`, **not** `django-guardian`

You offered either. I am choosing `django-rules`, and this is a decision worth defending in the README:

| | `django-guardian` | `django-rules` |
|---|---|---|
| How | stores a permission **row per (user, object)** | evaluates a **predicate** live |
| "Doctors see their own patients" | needs a row per doctor per patient — millions of rows, and a row must be written the instant a patient is referred | `is_treating_doctor(user, patient)` — one function |
| Keeping it correct | every clinical event must remember to grant/revoke rows; miss one and access silently leaks or breaks | nothing to keep in sync; the rule reads the current state |
| Cost | table grows with patients × staff | a query per check, cached per request |

For a hospital, where the question is almost always *relational* ("is this patient on my ward right now?"), predicates are the right tool. Guardian's row model suits "share this one document with this one user", which is not our problem.

Our predicates:

```python
is_same_organisation        is_same_branch           has_branch_view_all
is_treating_doctor          was_referred_to_me       is_in_my_ward
is_on_my_shift              patient_in_my_queue      order_is_for_my_department
is_own_record               encounter_is_open        has_live_emergency_grant
is_platform_support_with_grant
```

Composed with `&` / `|`, e.g.:

```python
rules.add_perm("clinical.note.read",
    is_same_organisation & (
        is_treating_doctor | was_referred_to_me | is_in_my_ward
        | has_perm("clinical.view_all") | has_live_emergency_grant
    )
)
```

Worked examples of what this produces:

- **Cashier** — sees bills, payments and patient name/MRN. **No diagnosis, no note, no result.** Enforced at field level, so even the raw API response does not contain them.
- **Accountant** — money only. Aggregates and payer names; never a patient chart.
- **Nurse** — patients currently assigned to her ward, plus the queue of her department. When a patient is discharged they leave her view (the old encounter stays readable to her for her documented shift, which is a real clinical requirement).
- **Lab scientist** — lab orders for his branch, and the demographics needed to interpret them (age, sex, relevant diagnosis), nothing else.
- **Doctor** — his own patients, those referred to him, those in his clinic's queue today.
- **Auditor** — read-only, everything, and **his own reads are audited too.**

### Layer D — field-level masking

A serializer mixin drops or masks fields the caller may not see, based on permission codes — not on the front end. Phone numbers partly masked for roles that don't need them; `clinical.sensitive.view` required for HIV status and psychiatric notes; NIN masked except for the front desk and the HMO officer.

### Break the glass

`EmergencyAccessGrant(user, patient, reason, expires_at)`. A doctor in A&E who needs a record they have no relationship to types a **mandatory reason**, gets immediate access for a short window, and:

- the medical director is notified instantly (WebSocket + email),
- the event is logged at high severity and shown at the top of the audit screen,
- the grant expires on its own.

No one is ever locked out of a dying patient's record, and no one uses it casually.

### Platform staff and your own access

By default **you cannot see any patient data.** Platform roles get aggregate counts and system health only. Opening a hospital's data needs a live `SupportAccessGrant` that *the hospital* creates, naming you, with a reason and an expiry. While it is live every action you take is audited into **the hospital's** audit log, visible to them. That is the difference between claiming privacy and building it.

---

## 3. The permissions matrix test — one file, three uses

A single `docs/permissions-matrix.yml` lists every **(role × action × relationship)** with the expected result:

```yaml
- action: clinical.note.read
  object: ClinicalNote
  expect:
    doctor:        { treating: allow, referred: allow, other_branch_patient: deny, other_org: deny }
    nurse:         { own_ward: allow, other_ward: deny }
    cashier:       { any: deny }
    accountant:    { any: deny }
    lab_scientist: { any: deny }
    auditor:       { any: allow_readonly }
    platform_admin:{ any: deny, with_support_grant: allow }
```

That one file is:

1. **the test** — `pytest` parametrises over every cell and asserts the real API returns 200 or 403. Hundreds of cases, generated, not hand-written.
2. **the README table** — generated from the same file, so the documentation cannot go stale.
3. **the in-app help** — "what can this role do?" on the role editor screen reads the same source.

One source of truth for permissions documentation, tests and UI. This is the kind of thing that makes a reviewer trust the rest of the codebase.

---

## 4. Audit — append-only and hash-chained

```python
class AuditEvent(models.Model):
    organisation, actor, actor_role, action        # "patient.view", "note.sign", "stock.adjust"
    object_type, object_id, patient_id            # patient_id denormalised: "show me every access to THIS patient"
    changes          = JSONField()                # before/after for writes; field list for reads
    reason           = TextField(blank=True)      # required for break-the-glass, waivers, overrides
    ip, user_agent, session_id, at
    flags            = ArrayField()               # cross_branch, emergency_access, support_grant, after_hours
    prev_hash, hash                               # sha256(prev_hash + canonical_json(this row))
```

- **Views are logged, not just changes.** Opening a patient chart writes an event. This is the requirement people skip because reads are the hard part; it is also the one that matters, because the real-world abuse is a staff member reading a celebrity's file.
- Logged at the **service layer**, not with model signals — signals cannot see reads, cannot see the reason, and fire during migrations.
- **Hash-chained per organisation**: each row carries the previous row's hash, so a deleted or altered row breaks the chain and a verification command reports exactly where. A nightly job writes an `AuditAnchor` (chain head + row count) so tampering with the tail is detectable too.
- `INSERT`-only at the database level: the app's database role has no `UPDATE`/`DELETE` grant on this table.
- Visible to the **auditor** and **medical director** roles, filterable by patient, staff member, date and flag.
- Retention follows the hospital's configured medical-record retention policy; audit rows outlive the records they describe.

---

## 5. The rest of the security posture

| | |
|---|---|
| In transit | HTTPS only, HSTS, secure cookies |
| At rest | Neon's encryption + application-level envelope encryption on the sensitive field list (`01-data-model.md` §5) |
| Staff auth | django-allauth, **MFA (TOTP) required** for every staff role, recovery codes |
| Sessions | idle timeout, device list, revoke-all; **deactivating a staff member kills their live sessions and WebSocket connections immediately** |
| Brute force | per-IP and per-account rate limits, lockout with backoff |
| Headers | CSP via Django 6's native support, `X-Frame-Options`, `Referrer-Policy`, `Permissions-Policy` |
| Files | private bucket, **no public URL exists**; access only via a signed URL valid for minutes, and generating one writes an audit event |
| Patients | portal login by one-time code to a verified phone, plus password |
| Backups | Neon point-in-time recovery + a weekly logical dump to R2; **a documented and actually-tested restore procedure**, not just "we have backups" |
| Drafts | every long form autosaves locally and server-side, so a power cut does not lose a consultation note |
| Compliance docs | NDPA 2023 roles (hospital = controller, Curavia = processor), a DPA template, consent records, retention settings, patient access/export/deletion requests with deadlines, a 72-hour breach-response runbook, `SECURITY.md` |
| Disclaimers | synthetic demo data; not a certified medical device; decision support is informational — on the site, in the app and in the README |
