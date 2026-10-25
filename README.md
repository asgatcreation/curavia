<div align="center">

# Curavia

**One system for your whole hospital.**

Front desk → triage → doctor → lab → pharmacy → cashier → ward, handing patients
to each other in real time. A multi-tenant hospital operating system for Nigerian
private hospitals, clinics and hospital groups, with an online clinic and a patient portal.

[![CI](https://github.com/asgatcreation/curavia/actions/workflows/ci.yml/badge.svg)](https://github.com/asgatcreation/curavia/actions/workflows/ci.yml)
[![License: All rights reserved](https://img.shields.io/badge/license-all%20rights%20reserved-blue)](LICENSE)
[![Django 6.1](https://img.shields.io/badge/Django-6.1-0C4B33)](https://www.djangoproject.com/)
[![React 19](https://img.shields.io/badge/React-19-087EA4)](https://react.dev/)
[![Python 3.13](https://img.shields.io/badge/Python-3.13-3776AB)](https://www.python.org/)

Designed and built by **Akanji Oluwaseun Gabriel** ([@asgatcreation](https://github.com/asgatcreation))

</div>

> **🚧 In development — milestone 2 of 15.**
> The foundations are built: multi-hospital tenancy, staff accounts with mandatory
> two-factor authentication, roles with object-level permissions, and a hash-chained
> audit log, with the test suites that prove each of them. The hospital screens,
> the live demo and the screenshots arrive in later milestones — see
> [the build plan](docs/03-build-plan.md).
>
> **All data in this repository, its tests and its demo is synthetic.** No real
> patient information is used anywhere.

---

## The problem

A Nigerian private hospital typically runs on a patchwork: a paper record at the
front desk, a laboratory notebook, a pharmacy spreadsheet, a cash book, and
WhatsApp between departments. The cost is not administrative, it is clinical. A
prescription reaches the pharmacy as a photograph of handwriting, with no check
against the patient's allergies. A critical potassium result sits in a book
until somebody walks it upstairs. The same patient has four records under three
spellings of their name.

Curavia puts every department in one system and makes the hand-off immediate.
When a doctor prescribes, **the pharmacist on seat sees it instantly — with the
patient's allergies, drug interactions, dose limits and current stock already
checked.**

## Two levels

Curavia is sold as software, so it has a platform level and a hospital level,
and the boundary between them is enforced rather than promised.

| | **Platform** (the Curavia company) | **Hospital** (each customer) |
|---|---|---|
| Who | Me | The hospital's owner and staff |
| Does | Approves hospitals, bills them, supports them, watches system health | Runs the hospital |
| Sees patient data | **No.** Not by default, not ever, without the hospital granting time-limited access — and every action under that grant appears in the hospital's own audit log | Only its own |

## The three decisions that shape everything

### 1. The tenant boundary is the Organisation, not the Branch

Patients belong to the hospital; visits, queues, stock, prices, rotas and cash
belong to a branch. That is the only arrangement that gives a group shared
patient records *and* per-branch operations without one leaking into the other.
Cross-branch access is a setting the hospital turns on, and every cross-branch
read is flagged in the audit log.

### 2. A query without a tenant crashes rather than leaking

Every tenant model's default manager refuses to build a queryset when no
hospital is in scope:

```python
>>> Branch.objects.all()
TenantScopeMissing: Branch was queried with no tenant scope active.
```

Forgetting a filter is the one mistake that matters in multi-tenant software,
so the ability to forget is removed. Four layers in total: this manager, a
`contextvar` set only at the edge from the user's own membership, PostgreSQL
row-level security underneath ([ADR 0002](docs/adr/0002-row-level-security.md)),
and a suite whose only job is to attempt cross-tenant access over the ORM, the
HTTP API, forged headers, background jobs, the audit log and WebSocket group
names. A leak is a failing build.

`scripts/check-cross-tenant-allowlist.sh` fails CI if the escape hatch appears
in a file not listed, with a reason, in
[docs/cross-tenant-allowlist.txt](docs/cross-tenant-allowlist.txt).

### 3. One file is the source of truth for permissions

[`docs/permissions-matrix.yml`](docs/permissions-matrix.yml) states every
(permission × role) expectation **by hand, from intent** — never derived from
the role definitions, which would make the tests tautological. That one file
generates:

- **the test suite** — one case per pair, checked against the role definition,
  the rule engine *and* the real HTTP endpoint;
- **the README table** (`manage.py export_permissions_matrix`);
- **the in-app role editor**.

So permission documentation cannot go stale, and widening a cashier's access
fails the build. Separation of duties is asserted explicitly: a lab scientist
cannot verify their own result, the person who requests a waiver cannot approve
it, and the cashier who took the money cannot refund it.

## Access control

A staff member may do something only if **all four** agree:

```
    the plan includes the module          (what the hospital pays for)
AND the hospital switched it on           (size mode and module switches)
AND their role grants the action          (coarse:  "may verify lab results")
AND an object rule says yes               (fine:    "...but only in your own branch")
```

Object rules use [`django-rules`](https://github.com/dfunckt/django-rules)
predicates rather than `django-guardian` rows. Guardian would need a row per
(doctor, patient) — millions of them, written the instant a patient is referred
and revoked the instant they are discharged; miss one write and access leaks or
breaks silently. Hospital questions are about the present ("is this patient on
my ward *now*?"), so a predicate evaluated live is both correct and free of
state to keep in sync.

Field masking is server-side: **a cashier's API response does not contain a
diagnosis at all**, rather than hiding one in the browser.

**Break the glass** exists because nobody should be locked out of a dying
patient's record: a written reason, a short window, an immediate alert to the
medical director, and a permanent flag in the audit log.

## The audit log

Every **view** of a patient record is recorded, not just every change — the
real-world abuse is reading a record, not altering one. Each entry carries the
previous entry's SHA-256 hash:

```console
$ python manage.py verify_audit_chain
ok      Lifeline Specialist Hospital: 48,203 event(s), head 9f2c1a7b3d4e5f60…
BROKEN  Rival Clinic: row contents do not match its stored hash (at sequence 1,204)
```

A deleted or edited row breaks the chain and the command names the exact
sequence number. The application's database role holds no `UPDATE` or `DELETE`
on the table, and the chain tip is anchored nightly so tampering with recent
history is detectable too.

## Built for Nigerian practice

- **Money is integer kobo.** ₦1,500.50 is stored as `150050`. No floats, no
  rounding arguments, half-up rounding as a cashier expects.
- **Stock is an append-only ledger**, with no `quantity` column anywhere. A
  mutable number corrupts under concurrency and then cannot be explained — and
  a NAFDAC batch recall needs every unit traced from supplier invoice to
  patient.
- NAFDAC numbers, batches and expiry dates; HMO and NHIA tariffs,
  pre-authorisation codes and claim batches; the national immunisation
  schedule; CAC, state Ministry of Health licence and MDCN verification before
  a hospital goes live; `Africa/Lagos` throughout.
- Patient search uses PostgreSQL trigram similarity, so `adebayou 0803` finds
  Adebayo.

## Architecture

```
                         ┌─────────────────────────────┐
  Marketing site ───────▶│  Django templates (SEO)     │
  (public)               └─────────────────────────────┘
                                      │
  Staff app     ───────▶┌─────────────▼───────────────┐      ┌──────────────┐
  (React/Vite)          │  Django 6.1 + DRF           │─────▶│ Neon         │
                        │  ├ core      tenancy, audit │      │ PostgreSQL   │
  Patient portal ──────▶│  ├ accounts  auth, MFA, RBAC│      └──────────────┘
  (React PWA)           │  ├ clinical, lab, pharmacy… │
                        │  └ platformadmin            │      ┌──────────────┐
                        │                             │─────▶│ Upstash      │
  Live updates  ◀══════▶│  Channels (WebSockets)      │      │ Redis        │
  (journey board)       │  Celery (reminders, reports)│      └──────────────┘
                        └─────────────┬───────────────┘
                                      │                      ┌──────────────┐
                                      └─────────────────────▶│ Cloudflare R2│
                                        signed URLs only     │ private files│
                                                             └──────────────┘
```

## Tech stack

| Layer | Choice | Why this one |
|---|---|---|
| Backend | Django 6.1, DRF 3.18 | Batteries included where it counts: migrations, admin, auth, ORM |
| Realtime | Django Channels 4.3 + Redis | The live journey board is the product's most visible feature |
| Background | Celery 5.6 | Retries, rate limits and scheduling matter for SMS and claims |
| Database | PostgreSQL 18 (Neon) | Row-level security, trigram search, JSONB, real constraints |
| Permissions | django-rules 3.5 | Predicates, not permission rows — see above |
| Auth | django-allauth 65 with TOTP MFA | Mandatory two-factor for every staff role |
| API docs | drf-spectacular | Generates the frontend's TypeScript types; CI fails if they drift |
| Frontend | React 19, TypeScript 5.9, Vite 8 | Behind a login, highly interactive, long-lived sockets |
| Styling | Tailwind 4 + shadcn/ui | Components copied in, not a dependency to fight |
| Data layer | TanStack Query + Router | Cursor pagination for feeds that change while you read them |
| Marketing | Django templates | Needs SEO and fast first paint; no third Node server to pay for |
| PDFs | WeasyPrint + QR verification | Results, receipts, discharge summaries, A4 and 80 mm thermal |
| Payments | Paystack | Nigerian cards and transfers, test mode for the demo |
| Email / SMS | Brevo HTTP API / Termii | Render's free tier blocks SMTP; Termii is the Nigerian choice |
| Video | LiveKit (adapter) | Apache-2.0, so self-hosting later changes no application code |
| Hosting | Render free tier + keep-alive | Honest trade-off, [explained below](#hosting) |

Every integration sits behind an adapter with a fake implementation, so **no
test can contact a real provider** or text a real phone.

## Running locally

Requires Python 3.13, Node 24 and Docker.

```bash
git clone https://github.com/asgatcreation/curavia.git
cd curavia
cp .env.example .env     # then fill it in — see the comments in the file
```

Start Postgres and Redis (on ports 55432 and 56379, to avoid clashing with any
you already run):

```bash
docker compose -f infra/docker-compose.yml up -d
```

Backend:

```bash
cd backend
python -m venv .venv && .venv/Scripts/activate   # Windows; use bin/activate elsewhere
pip install -r requirements-dev.txt
python manage.py migrate
python manage.py seed_platform
python manage.py createsuperuser
python manage.py runserver 8010
```

Frontend:

```bash
npm install
npm run dev:staff      # http://localhost:5173
npm run dev:portal     # http://localhost:5174
```

| | |
|---|---|
| Staff app | http://localhost:5173 |
| Patient portal | http://localhost:5174 |
| Marketing site | http://localhost:8010/ |
| API docs | http://localhost:8010/api/docs/ |
| Health check | http://localhost:8010/healthz |

## Tests

```bash
cd backend
pytest                       # everything
pytest tests/isolation -v    # the cross-tenant attack suite
pytest tests/permissions -q  # the generated permissions matrix
python manage.py verify_audit_chain
bash ../scripts/check-cross-tenant-allowlist.sh
```

| Suite | What it covers |
|---|---|
| `tests/isolation` | Deliberate cross-hospital attacks: ORM, API, forged headers, Celery, audit log, WebSocket group names |
| `tests/permissions` | Generated from the matrix — role definition, rule engine and live endpoint for every (permission × role) |
| `tests/test_audit.py` | Hash chain detects edited rows, deleted rows and truncation; reads logged without logging the data |
| `tests/test_utils.py` | Kobo arithmetic, half-up rounding, UUIDv7 ordering and unguessability, module gating |

CI runs ruff, ruff format, mypy, the allowlist guard, missing-migration and
deploy checks, the isolation and permission suites first, then the full suite
with coverage, the OpenAPI schema, ESLint, tsc, the frontend build and a Docker
build.

## Hosting

<a id="hosting"></a>A genuinely free, always-on WebSocket deployment does not
exist in 2026, and the README should say so rather than imply otherwise.

Render's free tier spins a service down after 15 minutes without inbound
traffic and takes about a minute to wake, which would kill the live queue
board. The current deployment therefore runs **one** free Render web service
with Daphne and the Celery worker side by side under honcho, kept awake by an
UptimeRobot check against `/healthz` every five minutes. Render grants 750 free
instance-hours per workspace per month against a 744-hour month, so exactly one
always-on free service fits — a second would take the demo down mid-month.
Free instances also have no background worker service at all, which is why both
processes share a container.

This is right for a portfolio demonstration and **wrong for a hospital with
real patients.** The Dockerfile, `render.yaml` and `fly.toml` are all written,
so moving to Fly.io (~$5/month) or Render paid (~$14/month) is a config change
and a deploy. Reasoning and the exact figures are in
[docs/00-decisions.md §3](docs/00-decisions.md).

## Documentation

| | |
|---|---|
| [docs/00-decisions.md](docs/00-decisions.md) | Name, stack with verified versions, hosting and video comparisons |
| [docs/01-data-model.md](docs/01-data-model.md) | The five load-bearing ideas, then every entity |
| [docs/02-tenancy-and-permissions.md](docs/02-tenancy-and-permissions.md) | Tenancy layers, the four permission checks, break-the-glass, audit |
| [docs/03-build-plan.md](docs/03-build-plan.md) | 15 milestones across four phases, and the testing strategy |
| [docs/permissions-matrix.yml](docs/permissions-matrix.yml) | Who can do what, and why |
| [docs/adr/](docs/adr/) | Architecture decision records |
| [SECURITY.md](SECURITY.md) | Reporting, what the system does, and known limitations |

## Roadmap

**Phase A — core hospital.** Foundations ✅ · visual direction · onboarding and
setup wizard · patients, appointments and the live journey board · triage and
EMR · lab, radiology, pharmacy, stores · billing and cashier · demo data and
first deploy.

**Phase B — full hospital and the SaaS business.** Wards with bed board and
MAR · maternity and immunisation · theatre · mortuary · corporate clients ·
HMO/NHIA claims · accounts · HR and rosters · equipment · quality and safety ·
messaging · reports · the platform console.

**Phase C — online clinic and polish.** Telemedicine and the patient portal
PWA · FHIR R4 read API · help centre and guided tours · security, accessibility
and performance passes · load test · final deploy.

**Phase D — advanced.** AI clinical scribe · symptom-checker triage, no-show
prediction and stock forecasting · React Native apps · offline-first mode for
poor-network clinics · HL7 v2 lab analyser integration and DICOM viewer ·
Yoruba, Hausa, Igbo and Pidgin · pharmacy network · NIN verification, WhatsApp
and USSD booking, white-label · ambulance and home care with live tracking.

Three of those shape decisions already made: every write endpoint takes an
idempotency key, so an offline queue cannot double-charge or double-dispense;
every string goes through i18next from the first component; and tenant
resolution already supports a subdomain source for white-labelling.

## Licence

**All rights reserved.** The source is published to be read, not licensed for
use. See [LICENSE](LICENSE).

Not a certified medical device. Clinical decision support is informational and
does not replace clinical judgement.

---

<div align="center">
Designed and built by <strong>Akanji Oluwaseun Gabriel</strong><br>
<a href="https://github.com/asgatcreation">@asgatcreation</a>
</div>
