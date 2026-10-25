# Milestone 3 — visual direction

This is a sign-off point. Six screens are built as real working pages so the
look, the density and the behaviour can be argued with **before** the other
forty are built on top of them.

## Where to look

| Screen | URL | What it is for |
|---|---|---|
| Marketing home | `http://localhost:8010/` | The public page that sells Curavia |
| Front desk | `http://localhost:5173/` | The busiest screen in the hospital |
| Doctor consultation | `http://localhost:5173/consultation` | The screen the system is judged on |
| Pharmacy queue | `http://localhost:5173/pharmacy` | The handover, proved |
| Patient journey board | `http://localhost:5173/journey` | Where everybody is, right now |
| Group dashboard | `http://localhost:5173/group` | The owner's view across branches |

Run both servers:

```bash
cd backend && .venv/Scripts/python.exe manage.py runserver 8010
```

```bash
npm run dev:staff
```

The light/dark control is in the top bar of the staff app: light, dark, or
follow the operating system. The marketing site follows the operating system.

## The data is synthetic

Every patient, name, record number and amount in these screens comes from
`frontend/shared/src/demo/hospital.ts`. Nothing is real, and the file says so
at the top.

The clinical models — Patient, Encounter, Prescription, LabOrder — arrive at
milestones 4 to 7. Milestone 3 had to come first, because the point of a
sign-off is to happen before the work it governs. The fixture shapes are the
shapes the serialisers will return, so wiring these screens to the API later
replaces an import and nothing else.

The fixtures include the awkward cases on purpose, because a design that only
handles the happy path is not a design:

- a patient whose allergy history has never been taken, which is not the same
  as a patient with no allergies
- a prescription that collides with a recorded allergy
- a drug that is out of stock here and in stock at another branch
- a wait that has passed the hour
- a name long enough to break a narrow column
- self-pay, HMO, NHIA and corporate patients in the same queue

## The decisions

### 1. Clinical status never depends on colour alone

Roughly one Nigerian man in twenty cannot separate red from green. A triage
board that encodes urgency in hue alone is a safety problem, not a styling
preference.

So every status carries a word as well as a colour — "Red · Immediate", not a
red dot — and `StatusDot` takes a required `label` prop, so the component
cannot be used without one. On the journey board, progress is shown three ways
at once: a filled marker, a position in the row, and the written stage name.

The colour tokens themselves were chosen to stay separable under deuteranopia
and protanopia: critical is held far round towards red, stable sits at a
green-cyan rather than a pure green, and the middle bands differ in lightness
as well as hue.

### 2. The patient banner never moves

`PatientBanner` is pinned to the top of every screen about one patient, and it
always carries the same six things in the same order:

> name · MRN · age and sex · allergies · payer · encounter

Wrong-patient errors are the most common serious error in hospital software,
and they happen when the identifier moves between screens. Allergies sit
immediately beside the name, before anything administrative, and are never
truncated. "No known allergies" is stated explicitly, because a blank space is
ambiguous between *none* and *nobody has asked yet* — and those mean very
different things to a doctor about to prescribe.

### 3. Conflicts are attached to the thing, not thrown as a dialog

On the consultation screen the allergy warning is on the order line from the
moment the drug is added, with the reason written out in full, and the sign
button says what is blocking it and is red rather than a dimmed primary.

An alert that appears after the decision and can be dismissed by reflex *will*
be dismissed by reflex. Alert fatigue is how electronic prescribing systems
end up less safe than paper.

### 4. Touch targets are sized for a gloved finger on a ward tablet

`--size-touch` is 2.75rem and is the floor for anything tappable, including the
smallest button variant. The tablet is the width most of this system will
actually be used at, not the desktop.

### 5. Dark mode is a choice with three states, not two

Light, dark, and follow the operating system — with the OS as the default.
Wards run dim at night and screens are often the brightest object in the room,
so the dark surfaces are warm navy rather than black and text is held below
pure white to stop small type blooming.

Tailwind 4's stock `dark:` variant is media-query only, which a toggle cannot
override, and its documented class strategy ignores the OS. The custom variant
in `tokens.css` answers to both.

### 6. One token file, three applications

`frontend/shared/src/ui/tokens.css` is the single source of colour, radius and
type for the staff app and the patient portal.

The marketing site is hand-written CSS that **mirrors the same values**, rather
than running Tailwind. That is a decision, not a shortcut: the marketing site
is four Django templates, and putting Tailwind there would mean a second build
step and a generated stylesheet that either gets committed and silently drifts
or has to be produced inside the Docker image before `collectstatic` runs. It
is about a dozen numbers; if one changes, change both.

### 7. No photographs of patients anywhere in the staff UI

`Avatar` renders initials only. A face on a queue board is a patient
identifier visible to everyone walking past the screen.

### 8. The owner's dashboard shows no patient names at all

Counts, money, bed occupancy and alerts — nothing that identifies a person,
not even in a tooltip. The permission matrix enforces this separately; the
screen simply has nothing to leak.

## What is deliberately not here yet

- **Login, MFA and the account screens.** They exist in the API and are
  tested; they get their designed screens at milestone 4 with the real session.
- **The patient portal.** It shares the token file and the primitives, and is
  built at milestone 11.
- **Offline behaviour and the printed documents** (receipts, result slips,
  prescriptions) — milestone 8 and milestone 10.

## What I need from you

Look at the six screens at a phone, tablet and desktop width, in light and
dark, and tell me:

1. Is the density right? A Nigerian hospital front desk is busier than most
   software assumes, and I have erred towards fitting more on screen.
2. Is the teal right as the brand colour, against the status reds and greens?
3. Does the consultation screen look like something a consultant would use
   rather than fight?

Changing these now costs an afternoon. Changing them after forty screens
costs a fortnight.
