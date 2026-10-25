# Build plan

Each milestone is a **vertical slice that works end to end** — you can click through it in a browser when it is done. Every milestone ends with: tests green, CI green, checked at phone / tablet / desktop widths, checked in dark mode, browser console clean, committed and pushed. I will not tell you a milestone is done without doing those checks.

---

## Repository layout

A monorepo, one git repo, branch `main`.

```
curavia/
├── backend/
│   ├── config/                  settings (base/dev/prod), asgi, urls, celery
│   ├── apps/
│   │   ├── core/                tenancy, base models, audit, permission engine, money, search
│   │   ├── accounts/            users, memberships, roles, MFA, invitations, sessions
│   │   ├── platformadmin/       plans, subscriptions, verification, tickets, support grants
│   │   ├── patients/            records, identifiers, duplicates, merge, consents
│   │   ├── scheduling/          appointments, queues, journey board, display screen
│   │   ├── clinical/            encounters, triage, notes, diagnoses, orders, referrals
│   │   ├── lab/  radiology/  pharmacy/  inventory/
│   │   ├── inpatient/  maternity/  theatre/
│   │   ├── billing/  payers/  accounting/
│   │   ├── hr/  ops/  messaging/
│   │   ├── telemedicine/  portal/
│   │   ├── reporting/  fhir/
│   │   ├── marketing/           public site (Django templates)
│   │   └── demo/                synthetic seed + nightly reset
│   ├── integrations/            sms/ email/ payments/ video/ storage/  ← adapters, one interface each
│   └── tests/                   unit, permissions matrix, tenant isolation
├── frontend/
│   ├── staff/                   React + Vite SPA
│   ├── portal/                  React + Vite PWA
│   └── shared/                  UI kit, API client, types generated from OpenAPI
├── infra/                       Dockerfile, docker-compose.yml, render.yaml, fly.toml (ready for the later move)
├── docs/                        these documents, ADRs, permissions-matrix.yml, diagrams
├── .github/workflows/ci.yml
├── .env.example                 empty placeholders only
├── LICENSE                      All rights reserved (CarHub style)
├── SECURITY.md
└── README.md
```

Every integration is an **adapter with one interface** (`SmsProvider`, `EmailProvider`, `PaymentProvider`, `VideoProvider`, `FileStorage`). Termii with a Twilio fallback, Brevo, Paystack, LiveKit, R2 — each swappable in one file, each with a fake implementation used by the tests so no test ever touches a real provider.

Frontend types are **generated** from the backend's OpenAPI schema (`drf-spectacular` → `openapi-typescript`), and CI fails if they are out of date. The front end therefore cannot drift from the API.

---

# Phase A — the core hospital (first live version)

### Milestone 1 — Design and sign-off ✓ done
`00-decisions.md`, `01-data-model.md`, `02-tenancy-and-permissions.md`, this file.
**Deliverable: your approval.** No code.

### Milestone 2 — Foundations ✓ done
The skeleton, built to be safe from the first commit.

- Monorepo scaffold, Docker + compose (Postgres + Redis locally), `.env.example`, Neon connection
- Django 6.1 + DRF + Channels + Celery wired and running; health-check endpoint; Sentry
- `core`: `TenantScoped` base, the tenant contextvar and middleware (HTTP + WebSocket + Celery), **Postgres RLS**, money-as-kobo helpers, UUIDv7 keys
- `accounts`: email login, django-allauth, **MFA**, memberships, the 17 role templates, invitations, session/device list
- The **audit log** with hash chaining and a `verify_audit_chain` management command
- The **permission engine**: plan gating, role codes, `django-rules` predicates, field masking
- `docs/permissions-matrix.yml` + the generated test suite + the **tenant-isolation attack suite**
- CI: ruff, mypy, pytest, eslint, typecheck, build — with a badge
- `LICENSE` (All rights reserved), `SECURITY.md`, first README
- **First commit and push to `main`** (you create the empty repo; I add the remote)

**Done when:** CI is green, the permissions matrix and isolation tests pass, and `docker compose up` gives a running app with a login screen.

### Milestone 3 — Visual direction ✓ done *(signed off)*
Before I build 40 screens, I show you six, built as real working pages with real components:

marketing home · front desk · doctor consultation · pharmacy queue · patient journey board · group dashboard

Light and dark, at phone / tablet / desktop. The design language: calm premium clinical software — a patient banner always visible (name, age, sex, MRN, **allergies in red**, alerts), colour-blind-safe status colours, big touch targets for nurses on tablets, keyboard shortcuts for heavy users, WCAG AA. **I stop and wait for your reaction here.**

### Milestone 4 — Onboarding and the hospital shell ✓ done
Owner sign-up → email verification → **setup wizard** ("4 of 7") creating organisation, branches, departments, clinics, wards/beds, service prices, HMO tariffs — from editable templates (common departments, lab catalogue, essential drugs list, price list). Hospital size modes and module switches. Staff invitations and acceptance. **Role-based landing** — every role lands on its own dashboard. Branch switcher. **Excel/CSV import** with column mapping, preview and an error report. Group dashboard with live branch cards and drill-down.

### Milestone 5 — Patients, appointments and the live journey ← **you are here**
Registration (front desk and self-service) with **duplicate detection and merge**. MRN, patient card with QR, lifetime timeline. Emergency quick-registration. Trigram search. Booking (portal + front desk), doctor schedules and leave, walk-ins, token numbers, the **TV "now serving" screen**, SMS/email reminders, no-show tracking. The **live journey board over WebSockets** — this is the showpiece: every patient, every stage, every wait time, updating as it happens.

### Milestone 6 — Triage, consultation and orders
Vitals with BMI and **NEWS2 scoring with alerts**, nursing notes, nurse task lists, shift handover. SOAP notes with specialty templates (including dental chart, eye exam, physiotherapy), ICD coding, problem list, **sign and lock with versioned amendments**, e-signature and stamp. Orders of every type. Internal and external referrals. On-screen consent forms. Autosave drafts. Medical reports, sick notes, fitness certificates as QR-verifiable PDFs.

### Milestone 7 — Lab, radiology, pharmacy, stores
Lab: live orders, barcode sample labels, reference ranges by age and sex, abnormal and **critical flags with immediate alerts to the ordering doctor**, supervisor verification, send-outs, QC log, PDF results. Radiology: orders, scheduling, reports, private file uploads. Pharmacy: the **live prescription queue** with allergy, interaction, duplicate-therapy and dose checks, stock and price visible, substitution requests, partial dispensing, FEFO batch picking, labels, counselling notes, refills, controlled-drug register, OTC POS. Inventory: NAFDAC numbers, batches, expiry, suppliers, purchase orders, goods received, transfers, counts, wastage, valuation, alerts — all on the **stock ledger**.

**This milestone contains the moment you described:** the doctor prescribes and the pharmacist's screen updates instantly, allergies and interactions and stock already checked.

### Milestone 8 — Billing and cashier
Price lists per payer, automatic charges from orders / dispenses / bed-days, packages, patient wallet and part payments, Paystack, A4 and thermal receipts, refunds, waivers with approval, cashier shifts and end-of-day reconciliation. All PDFs QR-verifiable against a public verification page.

### Milestone 9 — Demo data and first deploy
"Lifeline Specialist Hospital", 2 branches, staff in every role, **~500 synthetic patients with Nigerian names** and realistic histories, visits, results, admissions, stock, bills, corporate clients and HMO claims across 6 months, so every report has real shape. Several sample hospitals at the platform level in different plans and states. **One-click demo logins for every role**, clearly labelled. The guided **"follow a patient"** walkthrough: book → triage → consult → lab → pharmacy → pay → follow-up. Nightly reset of demo tenants only. Then: Neon + hosting + Upstash + R2 live, and **I verify the live site anonymously** at three widths, light and dark, console clean.

**End of Phase A: a working hospital system, live, that you can send to a client.**

---

# Phase B — the full hospital and the SaaS business

### Milestone 10 — Wards and specialist units
Admissions, **live bed board**, transfers, ward rounds, care plans, the **MAR** (due / given / missed), observation and fluid charts, diet orders, discharge summary PDF, barcode wristbands. Maternity: antenatal with risk flags, delivery, postnatal, **Nigeria's national immunisation schedule with reminders**, birth notifications. Theatre: scheduling, safety checklist, consent, procedure notes. Mortuary and death certification.

### Milestone 11 — The money and people side
Corporate clients with credit limits and monthly statements. HMO/NHIA: enrolees, pre-authorisation codes, capitation vs fee-for-service, claim batches and export, rejections and resubmission. Accounts: revenue by department / service / doctor / payer, expenses, payables, visiting-consultant fee sharing, daily cash report, Excel exports. HR: profiles, documents, **professional licences with expiry alerts**, rosters, attendance, leave. Equipment and maintenance. Incidents, complaints, SMS satisfaction surveys. Staff chat and the notification centre. Reports and analytics, plus the **NHMIS / DHIS2-style monthly export**.

### Milestone 12 — The platform level (your business)
Public marketing site with pricing and FAQs and book-a-demo. **Hospital verification queue** (CAC, state MoH licence, MDCN number, documents — approve / reject / request changes), with demo-data exploration while waiting. Plans, feature flags, **Paystack subscriptions**, invoices, failed-payment reminders, grace period, then read-only mode — never deletion without notice and an export. Platform console: hospitals with status / plan / usage / health, tickets, announcements, metrics, platform audit. **Time-limited support access grants.** Full data export so a hospital can leave.

---

# Phase C — the online clinic and polish

### Milestone 13 — Telemedicine and the patient portal
Choose a specialty or doctor → availability → pay → **join a secure video call in the browser** with chat and file sharing. Async consults (symptom form + photos). Doctors use the same EMR; e-prescribe to the pharmacy for pickup or delivery, or to **any** pharmacy via a QR-verifiable prescription; order labs; refer to an in-person visit. The patient portal PWA: appointments, video consults, results, prescriptions, bills, online payment, downloads, family members, refill reminders, consents, and NDPA data access / export / deletion requests. Phone verification by one-time code.

### Milestone 14 — Interoperability, help, hardening
Read-only **FHIR R4** API (Patient, Encounter, Observation, Condition, MedicationRequest, DiagnosticReport, AllergyIntolerance), documented. In-app help centre per role, first-login guided tours, keyboard shortcut reference. Security hardening pass (headers, rate limits, dependency audit, a written threat model), accessibility pass (WCAG AA, screen reader, keyboard-only), performance pass (query budgets, N+1 hunt, indexes, Lighthouse).

### Milestone 15 — Launch polish
Load test (concurrent WebSocket clients on the journey board, bulk import, report generation). README with screenshots per role at phone / tablet / desktop in light and dark, architecture diagram, permissions matrix, the patient-journey story, engineering decisions. Final deploy and anonymous verification. Then a plain-English list of **what you need to do yourself**: accounts, keys, domain, DNS.

---

# Phase D — advanced (planned now, built when you say go)

Not started until A–C are live. Listed so the architecture leaves room for them:

AI clinical scribe (Claude API; nothing saved without the doctor's signature, patient consent, clearly labelled) · AI helpers (symptom-checker triage with red flags, no-show prediction, stock demand forecasting, long-history summaries) · React Native apps for patients and doctors with push · **offline-first mode** for poor-network clinics · lab analyser integration (HL7 v2 / ASTM) and a DICOM/PACS viewer · Yoruba, Hausa, Igbo and Pidgin in the portal and SMS · a partner pharmacy network with delivery tracking · integrations (NIN verification, NHIA e-claims, WhatsApp booking bot, USSD booking, BP/glucose devices, public API and webhooks, white-label domains) · ambulance requests and home care with live map tracking.

Three of these shape decisions I am making **now**, so they stay possible later:

- **Offline-first** → every write endpoint takes a client-generated idempotency key from day one, so a replayed queue cannot double-charge or double-dispense.
- **i18n** → every user-facing string goes through i18next from the first component. Retrofitting this is weeks of work.
- **White-label** → tenant resolution already supports a subdomain source, and all branding reads from `Organisation.settings`.

---

## Testing strategy

| Kind | Tool | What it covers |
|---|---|---|
| Unit | pytest-django + factory_boy | business rules: NEWS2 scoring, kobo arithmetic, FEFO batch picking, interaction rules, stock ledger balances |
| **Permissions matrix** | generated from `permissions-matrix.yml` | every role × every action × every relationship, against the real API |
| **Tenant isolation** | a dedicated attack suite | deliberate cross-tenant and cross-branch attempts, over HTTP, WebSocket and Celery |
| API contract | drf-spectacular schema snapshot | the OpenAPI schema cannot change silently |
| Frontend unit | vitest | components, forms, zod schemas |
| **End to end** | Playwright | the full patient journey; the doctor-prescribes-to-pharmacist-sees-it live hand-off; cashier payment; an admission |
| Accessibility | axe in Playwright | WCAG AA on the main screens |
| Load | locust or k6 | concurrent journey-board WebSocket clients |

CI on every push: ruff, mypy, eslint, tsc, pytest, vitest, Playwright, frontend build. A badge in the README.

---

## What I need from you, and when

| When | What |
|---|---|
| **Now** | The four answers in `00-decisions.md` §5 (name, hosting, marketing-site change, scope priorities) |
| Before milestone 2 ends | An empty **public** repo at github.com/new, named after the chosen name. Nothing else — no README, no licence, no .gitignore. Then tell me and I add the remote and push. |
| Before milestone 2 ends | A **Neon** account and a connection string, which you put in `.env` yourself. I will give you click-by-click instructions. |
| Before milestone 5 | **Brevo** (email) and **Termii** (SMS) API keys |
| Before milestone 8 | **Paystack test** keys |
| Before milestone 9 | A **Render** account (free tier), an **UptimeRobot** account, **Upstash** Redis, **Cloudflare R2**, **Sentry** |
| Before milestone 13 | A **LiveKit Cloud** account |

For every one of these I will tell you exactly what to click and what to paste where. **I will never create an account, and I will never see or print your keys.**
