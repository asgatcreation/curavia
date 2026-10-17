# Data model

The plain-English version first, then the entity list.

---

## 1. The five ideas the whole system rests on

Everything else follows from these. If you only read one section, read this one.

### Idea 1 — The hierarchy

```
Group            (optional — only for owners with separate legal companies; roll-up reporting only, NO data sharing)
  └── Organisation      ← THE TENANT. One hospital business = one customer = one subscription = one data boundary
        └── Branch      ← a physical facility: "Lifeline Ikeja", "Lifeline Lekki"
              ├── Department   (GOPD, Paediatrics, O&G, Surgery, Dental, Eye, Physiotherapy…)
              ├── Ward → Room → Bed
              ├── StockLocation (main store, pharmacy counter, theatre store, ward cupboard)
              └── Clinic session slots
```

**The single most important decision in the whole project:** the tenant boundary is the **Organisation**, not the Branch.

Why: you said a group owner's branches should share patient records while keeping staff, stock, prices and queues separate per branch. That only works cleanly if *patients belong to the Organisation* and *activity belongs to a Branch*. So:

- A **Patient** belongs to an Organisation, with a `home_branch` for convenience. One MRN per person per Organisation, never a duplicate per branch.
- An **Encounter** (a visit) belongs to a Branch. Queues, stock, staff rotas, price lists and cash are all per Branch.
- Cross-branch record access is a **setting the hospital owner turns on**, and every cross-branch read is audited and shown to the hospital.

If an owner genuinely runs two separate companies, they get two Organisations joined by a `Group` record. The group dashboard reads aggregate numbers from both; **no patient record ever crosses an Organisation line.** That keeps the legal boundary honest.

### Idea 2 — The Encounter is the spine

Every clinical and financial thing that happens hangs off one **Encounter**: notes, vitals, every order, every charge, the admission, the video session. Nothing floats loose.

This one choice gives us three features almost for free:

- **The live journey board** — "where is every patient right now?" is just the latest `JourneyEvent` on each open Encounter.
- **Wait times** — the gap between JourneyEvents.
- **Billing that cannot drift from reality** — a charge is only ever created *by* an order or a dispense on an Encounter, so the bill and the clinical record can never disagree.

### Idea 3 — Stock is a ledger, not a number

There is **no `quantity_on_hand` column anywhere.** Every receipt, dispense, transfer, count adjustment, return and wastage writes one immutable **StockLedgerEntry** (item, batch, location, signed quantity, reason, reference). Stock on hand is the sum of the ledger.

Why this matters: a mutable quantity field in a pharmacy system *will* go wrong under concurrency, and when it does you cannot tell what happened. With a ledger, every unit of every drug can be traced from the supplier invoice to the patient who received it — which is exactly what NAFDAC batch recalls and controlled-drug registers require. Current stock is kept fast with a small rolling summary table the ledger updates.

### Idea 4 — Money is integer kobo

Every amount in the database is a `BigIntegerField` of **kobo**. ₦1,500.50 is stored as `150050`. No floats, no decimals, no rounding arguments. Formatting to "₦1,500.50" happens only at the edge (PDF, screen, export). Timezone is `Africa/Lagos` everywhere.

### Idea 5 — Clinical records are append-only

A signed clinical note is never edited. An amendment creates a **new version** that points at the old one; the old one stays readable forever with who signed it and when. Same for lab results (a corrected result supersedes, never overwrites) and for the audit log. Deletion is always a soft `archived_at`, never a `DELETE`.

---

## 2. Base classes every model inherits

```python
class TenantScoped(models.Model):           # on almost everything
    organisation = FK(Organisation, PROTECT, db_index=True)   # never null
    class Meta: abstract = True

class BranchScoped(TenantScoped):           # on operational data
    branch = FK(Branch, PROTECT, db_index=True)

class Timestamped(models.Model):
    created_at, updated_at, created_by, updated_by, archived_at

class Versioned(models.Model):              # clinical notes, results, documents
    version, supersedes (self FK), signed_by, signed_at, locked
```

`TenantScoped`'s default manager **refuses to return rows unless a tenant scope is active** — details in `02-tenancy-and-permissions.md`. That is what makes a cross-tenant leak a crash instead of a silent disaster.

---

## 3. The entities, by module

Primary keys are UUIDv7 (sortable, non-guessable — an incrementing integer in a URL tells an attacker how many patients you have). `~95 tables in Phase A, ~180 in total.`

### Platform level (my console — your SaaS business)

| Model | Notes |
|---|---|
| `Group` | optional parent of several Organisations, roll-up reporting only |
| `Organisation` | **the tenant.** name, CAC number, status (trial / pending_verification / active / past_due / grace / read_only / closed), size mode (solo / clinic / hospital), settings JSON |
| `Branch` | name, address, state, LGA, phone, is_main, timezone |
| `Plan` / `PlanModule` | solo / clinic / hospital / group; price; which modules are on; per-branch and per-staff limits |
| `Subscription` | plan, period, Paystack subscription code, trial_ends_at, grace_ends_at |
| `Invoice` / `SubscriptionPayment` | kobo, status, Paystack reference |
| `VerificationApplication` + `VerificationDocument` | CAC cert, state MoH facility licence, medical director MDCN number, uploaded files, reviewer, decision, notes |
| `SupportTicket` + `TicketMessage` | |
| `Announcement` + `AnnouncementTarget` | to all, or selected Organisations |
| `SupportAccessGrant` | **the privacy guarantee.** A hospital grants a named platform staff member time-limited access, with a reason. Expires automatically. Every action under it is audited and visible to the hospital. Without a live grant, platform staff see zero patient data. |
| `PlatformAuditEvent` | separate chain from tenant audit |
| `DataExportJob` | a hospital can export everything and leave, any time |

### Identity and access

`User` (email login, no username) · `StaffMembership` (user × organisation × branch, with roles, is_active) · `Role` (per-organisation, editable, seeded from templates) · `RolePermission` (string codes like `lab.result.verify`) · `Invitation` · `MFADevice` (TOTP + recovery codes) · `LoginSession` (device list, revoke-all) · `PatientAccount` (links a `User` to a `Patient` for the portal) · `EmergencyAccessGrant` (break-the-glass) · `AuditEvent` (hash-chained, see §02).

### Patients and records

`Patient` (MRN, photo, demographics, blood group, genotype, payer type, home_branch, `search_vector`) · `PatientIdentifier` (NIN, HMO number, passport — typed, so we never add a column per ID type) · `PatientMergeRecord` (audited, reversible) · `Allergy` (substance, reaction, severity — drives the pharmacy check) · `Condition` (problem list, ICD-coded, active/resolved) · `NextOfKin` · `Dependant` · `Consent` (type, version of the text consented to, signature image, timestamp) · `PatientDocument`.

**Duplicate detection:** on registration we fuzzy-match phone + name + date of birth + NIN using PostgreSQL `pg_trgm`, and show "3 possible matches" before creating a new record. Emergency quick-registration creates `Unknown Male 3` with a flag, to be identified and merged later.

### Scheduling, queues and the journey board

`Clinic` · `DoctorSchedule` + `ScheduleException` (leave) · `AppointmentSlot` · `Appointment` (source: portal / front desk / walk-in / telemedicine) · `QueueTicket` (token number, priority) · `JourneyEvent` (**the live board** — encounter, from_stage, to_stage, at, by, branch; one row per hand-off) · `DisplayScreen` (the TV "now serving") · `ReminderJob` · `NoShowRecord`.

### Clinical core

`Encounter` (**the spine** — patient, branch, clinic, type: outpatient / emergency / inpatient / telemedicine / async, status, attending, opened_at, closed_at) · `TriageAssessment` (category) · `VitalsObservation` (BP, pulse, temp, resp, SpO2, weight, height, BMI computed, **NEWS2 score computed** with alert thresholds) · `NursingNote` · `ClinicalNote` (SOAP, specialty template, versioned, signed and locked) · `Diagnosis` (ICD-10/11 code + text, primary flag) · `Referral` (internal → lands in the target doctor's queue with full context; external → PDF letter; inbound from another facility) · `Order` (base: who, when, status, priority, Encounter) with subtypes `LabOrder`, `ImagingOrder`, `Prescription`, `ProcedureOrder`, `AdmissionOrder` · `ClinicalDocument` (medical report, sick note, fitness certificate, pre-employment medical — PDF with QR verification) · `NoteDraft` (**autosave**, so a note survives a power cut).

### Laboratory

`TestCatalogItem` (+ `TestPanel`) · `ReferenceRange` (by sex and age band — this is what makes a result "abnormal" correctly for a child vs an adult) · `LabOrderItem` · `Sample` (barcode, collected_by, collected_at, condition) · `LabResult` (value, unit, flag: normal / abnormal / **critical**, versioned) · `ResultVerification` (supervisor sign-off) · `CriticalResultAlert` (pushed to the ordering doctor immediately over WebSocket + SMS, with acknowledgement tracked) · `ExternalLabSendOut` · `QCLogEntry`.

### Radiology

`ImagingProcedure` · `ImagingOrder` · `ImagingAppointment` · `ImagingReport` (versioned, signed) · `ImagingFile` (private storage, signed URLs only).

### Pharmacy

`DrugFormularyItem` (generic name, ATC class, forms, strengths) · `DrugProduct` (brand, pack size, NAFDAC number, supplier) · `PrescriptionItem` (drug, dose, route, frequency, duration, instructions) · `DispenseRecord` + `DispenseLine` (which batch, how many, by whom — supports **partial dispensing** and refills) · `SubstitutionRequest` (pharmacist asks the doctor; doctor approves in their queue) · `CounsellingNote` · `ControlledDrugEntry` (the register) · `POSSale` + `POSSaleLine` (walk-in/OTC) · `PriceMarkupRule` · `DrugReturn`.

**Decision support rules** (a curated, versioned rule set — clearly labelled *decision support, not a substitute for clinical judgement*): `InteractionRule` (drug × drug, severity) · `AllergyCrossReaction` (e.g. penicillin → cephalosporin) · `DoseRule` (max dose by age/weight; paediatric mg/kg) · `DuplicateTherapyRule` (same ATC class already active). Every warning the pharmacist overrides records `OverrideRecord(rule, reason, by)` — so an auditor can see every time a check was bypassed and why.

### Inventory and stores

`Item` (drug, consumable or reagent; unit, pack size, reorder level) · `ItemBatch` (batch no, NAFDAC no, expiry, cost price) · `StockLocation` · **`StockLedgerEntry`** (the single source of truth, §1 idea 3) · `StockOnHand` (fast rolling summary, derived) · `Supplier` · `PurchaseOrder` + `POLine` · `GoodsReceipt` + `GRNLine` · `SupplierReturn` · `StockTransfer` (store → pharmacy → ward/theatre, with accept/reject) · `StockCount` + `CountLine` + `Variance` · `WastageRecord` · `ExpiryAlert` / `LowStockAlert`.

Batch picking is **first-expiry-first-out** by default, with an override that is recorded.

### Inpatient / wards

`Ward` → `Room` → `Bed` (status: free / occupied / cleaning / out-of-service — drives the **live bed board**) · `Admission` · `BedAssignment` (history, so transfers are traceable) · `WardRound` · `CarePlan` + `CarePlanTask` · **`MedicationAdministrationRecord`** (the MAR: scheduled dose, due_at, given_at, given_by, or missed with a reason) · `ObservationChart` · `FluidBalanceEntry` · `DietOrder` · `DischargeSummary` (versioned, signed, PDF) · `Wristband` (barcode).

### Maternity and child health

`AntenatalRecord` + `AntenatalVisit` (with risk flags) · `DeliveryRecord` · `PostnatalVisit` · `BirthNotification` · `ImmunisationSchedule` (seeded with **Nigeria's national schedule**) + `ImmunisationDose` (due, given, reminder sent).

### Theatre

`TheatreRoom` · `TheatreSlot` · `ProcedureBooking` · `SurgicalSafetyChecklist` (sign-in / time-out / sign-out) · `ProcedureNote` · `TheatreConsumableUsage` (writes to the stock ledger).

### Billing and cashier

`ServiceCatalogItem` · `Payer` (cash / an HMO / a corporate client / staff) · `PriceList` + `PriceListItem` (**one price list per payer** — the same consultation costs different amounts for cash and for each HMO tariff) · `Package` (antenatal, executive health check, delivery) · `Bill` + `BillLine` (lines are auto-created from orders, dispenses and bed-days) · `Payment` (cash / POS / transfer / Paystack) · `PatientWallet` + `WalletTransaction` (deposits, part payments) · `Refund` · `Waiver` (needs approval, both sides recorded) · `CashierShift` (open/close, expected vs counted, variance) · `Receipt` (A4 and 80 mm thermal, QR-verifiable).

### Corporate clients and HMO / NHIA

`CorporateClient` (credit limit, statement cycle) · `CorporateStatement` · `HMO` · `Enrolee` (plan, enrolee number, dependants) · `PreAuthorisation` (code, validity, approved services) · `Claim` + `ClaimLine` + `ClaimBatch` (export file, status, rejections, resubmission) · `CapitationPeriod`.

### Accounts

`ExpenseCategory` + `Expense` · `SupplierPayable` + `SupplierPayment` · `ConsultantFeeShare` (visiting-consultant commission rules and accruals) · `DailyCashReport` · `RevenueRollup` (nightly, by department / service / doctor / payer — reports read this, never the raw tables).

### HR and staff

`StaffProfile` · `StaffDocument` · `ProfessionalLicence` (MDCN, NMCN, PCN, MLSCN — with **expiry alerts**) · `Roster` + `Shift` + `ShiftAssignment` · `Attendance` · `LeaveType` + `LeaveRequest`.

### Operations, quality and safety

`Equipment` + `MaintenanceSchedule` + `MaintenanceTask` + `FaultReport` · `Incident` (falls, medication errors — with severity and follow-up) · `Complaint` · `SatisfactionSurvey` + `SurveyResponse` (sent by SMS after a visit) · `MortuaryRegisterEntry` + `DeathCertification` (lightweight, as you asked).

### Messaging and notifications

`MessageThread` + `Message` (secure staff chat, scoped to branch/department) · `Notification` + `NotificationPreference` (in-app, SMS, email, per event type) · `OutboundMessage` (every SMS and email we send, with provider, status and cost — so you can prove what was sent).

### Telemedicine and the portal

`TeleSession` (encounter, provider room id, join tokens, started/ended, duration) · `AsyncConsult` (symptom form + photos, doctor reply) · `PortalRequest` (data access / export / deletion request, with status and deadline — NDPA requires these) · `PhoneVerification` (one-time code).

### Reporting and interoperability

`ReportDefinition` + `ScheduledReport` · nightly rollups (`DailyCensus`, `RevenueRollup`, `WaitTimeRollup`, `DrugConsumption`, `StockValuation`) · `NHMISSummary` (DHIS2-style monthly export) · a read-only **FHIR R4** layer that maps our models to `Patient`, `Encounter`, `Observation`, `Condition`, `MedicationRequest`, `DiagnosticReport`, `AllergyIntolerance` — a translation layer over the models above, not new tables.

---

## 4. Search

PostgreSQL full-text + `pg_trgm` trigram similarity on a stored `search_vector` per patient, covering name, phone, MRN and NIN. A front-desk clerk typing `adebayo 0803` or a misspelled `Adebayo` → `Adebayou` still finds the record. Indexed with GIN; the vector is updated by a database trigger so it can never go stale.

---

## 5. Encryption at rest for the most sensitive fields

Most fields are protected by database-level encryption at the host (Neon) plus our permission layer. A short list gets **application-level encryption** on top, so they are unreadable even to someone holding a database dump:

- HIV status, and any condition flagged `is_sensitive`
- psychiatric and mental-health notes
- genotype where the hospital marks it sensitive
- staff disciplinary documents

Implemented as an `EncryptedTextField` using envelope encryption: a per-Organisation data key, itself encrypted by a master key held only in the environment. These fields need a dedicated `clinical.sensitive.view` permission, and **every read is audited**. Trade-off stated honestly: encrypted fields cannot be searched or sorted in the database. That is the right trade for these four.

---

## 6. What I deliberately left out

- **No `django-tenants` / schema-per-tenant.** Reasons in `02-tenancy-and-permissions.md`.
- **No generic "EAV" / custom-fields table.** Specialty templates (dental chart, eye exam) store their structured answers in a validated `JSONField` against a versioned template definition — type-safe at the edge, queryable with Postgres JSON operators, without turning the schema into a free-for-all.
- **No DICOM/PACS tables.** Phase D. For now imaging is files + a report.
- **No `quantity` column on stock.** On purpose. See idea 3.
