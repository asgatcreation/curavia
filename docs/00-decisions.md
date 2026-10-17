# Milestone 1 — Decisions

Status: **signed off, 17 October 2026.** Nothing is installed or committed yet.

## The four decisions, settled

| | Decision |
|---|---|
| **Name** | **Curavia** — Latin *cura* (care) + *via* (way), "the way of care". Repo `curavia`. |
| **Hosting** | **Render free tier + an UptimeRobot keep-alive**, built to move to Fly.io or Render paid later without a rewrite. §3. |
| **Marketing site** | **Django templates + Tailwind**, not a third React app. §2. |
| **Scope** | Build all modules in the order in `03-build-plan.md`. |

The reasoning behind each is kept below, including the options that were rejected, so the choices can be defended later.

---

## 1. The name

### What I found

"ClinicOS" is **already used by at least three shipping products**:

| Product | What it is | Where |
|---|---|---|
| **ClinicOS by ACS** | Cloud EMR + clinic management for private practices | Philippines |
| **Clinic OS Pro** | Patient-retention software for physiotherapy clinics, from $149/mo | US / Canada |
| **Clinic OS** (by Zidbit) | Registration, scheduling, consultation, prescription, billing | listed on G2 |

None of them is a big brand and none appears to hold a Nigerian trademark, so **you would not get sued**. But three problems remain for a flagship portfolio piece:

1. **You would be the fourth.** A recruiter or hospital owner who searches "ClinicOS" finds three other products before yours.
2. **Search is poisoned.** "clinicos" is also the Spanish and Portuguese word *clínicos* ("clinical"), so the name competes with millions of unrelated pages.
3. **`clinicos.com` is taken**, and so is `clinikos.com`.

### The decision: **Curavia**

Latin *cura* (care) + *via* (way) — "the way of care". Four syllables, so it reads as enterprise software rather than a phone app; the meaning explains itself in a sales pitch without being literal; and it is regionally neutral, so it does not read as a south-western or northern product to a buyer anywhere in Nigeria.

Pronounced **cu-RAH-vee-ah**. Repo and Python package: `curavia`.

Domains free at the time of checking: `curavia.ng` · `curavia.health` · `curavia.care` · `curavia.clinic` · `curavia.africa` · `curaviahq.com`. The bare `curavia.com` is parked, like essentially every pronounceable `.com`.

**No domain is needed yet.** What the portfolio needs is the repo name and a working demo link, and `*.onrender.com` is free. `curavia.ng` is worth buying when the pitching starts, because a `.ng` address signals a Nigerian product to Nigerian hospital owners.

### Names rejected, with the reason

Checked for company and product conflicts by web search, and for domain availability against the `.com`, `.ng`, `.health`, `.care`, `.clinic` and `.africa` registries.

| Name | Why not |
|---|---|
| **ClinicOS** | Three shipping products already use it (table above); `clinicos.com` taken; "clinicos" is also Spanish *clínicos*, so search is drowned. |
| **Sanovia** | Existing pharmacy-management software brand, Media PA, acquired by Health Information Designs. Directly our market. |
| **Salveo** | Two health-tech companies: Salveo Innovations, and a Salveo Healthcare in Australian healthcare software. |
| **Curantis** | Curantis Solutions — hospice and palliative-care management software, Texas. Same market. |
| **Healthera** | UK digital-health platform, 1M+ patients and 1,700+ pharmacies. Too established. |
| **Lafiya** (Hausa for health) | Two Nigerian companies already: a Lagos health-insurance startup and LaFiya TeleHealth App Ltd. |
| **Ilera** (Yoruba for health) | Clean and available, but a Yoruba word risks reading as a regional product to a buyer in Kano or Enugu. |
| **Halewell**, **Medoria**, **Clinora** | All clean and available; kept as fallbacks. Halewell leans wellness-brand, Medoria sits in the crowded "Med-" prefix, Clinora is closest to the generic clinic-plus-suffix naming being escaped. |

**Two limits on this diligence, stated honestly:** conflicts were checked by web search, not against a formal trademark register — do a search at the Nigerian trademark registry before printing anything. And the `.ng` registry returned intermittent errors on a re-check, so confirm availability at a registrar before paying.

---

## 2. Stack — confirmed versions

I checked every one of these against PyPI and npm this morning, so they are today's actual latest stable releases, not what I remember.

### Your machine (already checked)

| | |
|---|---|
| Python | 3.13.5 ✅ |
| Node | 24.16.0 ✅ |
| npm | 11.13.0 ✅ |
| git | 2.55.0 ✅ |
| Docker | 29.8.1 ✅ |

Everything we need is already installed. Nothing for you to install locally.

### Backend

| Package | Version | What for |
|---|---|---|
| Django | **6.1.2** | the framework |
| djangorestframework | 3.18.3 | the JSON API |
| drf-spectacular | 0.30.0 | OpenAPI docs + TypeScript type generation |
| channels | 4.3.2 | WebSockets (live queues, journey board, notifications) |
| channels-redis | 4.3.0 | the WebSocket message bus |
| daphne | 4.2.3 | the ASGI server |
| celery | 5.6.3 | background jobs (SMS, reminders, imports, nightly demo reset) |
| redis | 8.1.0 | Celery broker + Channels layer + cache |
| psycopg | 3.3.6 | PostgreSQL driver |
| django-allauth | 65.20.0 | sign-up, email verification, MFA |
| rules | 3.5 | object-level permissions (see `02-tenancy-and-permissions.md`) |
| django-storages + boto3 | 1.14.6 / 1.43.114 | private S3/R2 file storage |
| weasyprint | 70.0 | PDFs (results, receipts, discharge summaries) |
| qrcode / python-barcode | 8.2 / 0.16.1 | QR verification, sample labels, wristbands |
| pandas + openpyxl | 3.0.6 / 3.1.5 | Excel/CSV import and export |
| sentry-sdk | 2.72.0 | error monitoring |
| pytest-django + factory_boy | 4.14.0 / 3.3.3 | tests |
| ruff / mypy | 0.17.1 / 2.4.0 | lint and type-check |

**One change worth knowing about:** Django 6.0 introduced a **built-in background Tasks framework** and **native Content-Security-Policy support**. I am still recommending Celery, because we need Redis for WebSockets anyway and Celery's retries, rate limits and scheduled beat jobs matter a lot for SMS and claims. But if hosting cost becomes the deciding factor, Django's built-in tasks with a database backend would let us drop both Redis and the second worker process. So all background work goes behind one thin `enqueue()` helper and switching later is a one-file change. The native CSP support we use from day one.

### Frontend

| Package | Version |
|---|---|
| react / react-dom | **19.3.1** |
| typescript | **7.0.2** |
| vite | **8.3.4** |
| @tanstack/react-query | 5.104.3 |
| @tanstack/react-router | 1.170.45 |
| tailwindcss | **4.3.3** |
| react-hook-form | 7.90.0 |
| zod | **4.6.7** |
| i18next | 26.4.7 |
| vitest | 5.0.5 |
| playwright | 1.64.1 |
| eslint | 10.13.0 |

Plus **shadcn/ui** (components copied into our repo, not a dependency) and **openapi-typescript** to generate our API types from the backend's OpenAPI schema.

> Note: TypeScript 7, Tailwind 4, Vite 8, zod 4 and ESLint 10 are all major versions that changed their config format from what most tutorials show. I will write the configs correctly from scratch rather than copy old boilerplate.

### Do we need Next.js or HTMX? My answer: no, but with one change to your plan

You asked me to explain first if either is clearly better for any part. Here is the honest breakdown of the **three** front ends:

| Part | What I recommend | Why |
|---|---|---|
| **Staff app** (front desk, doctor, pharmacy, lab, wards…) | **React + Vite SPA** | It is behind a login, so SEO is irrelevant. It is extremely interactive, holds long-lived WebSocket connections, and staff keep it open all day. Next.js server rendering would buy nothing and would cost us a Node server to host. Your choice was right. |
| **Patient portal** (PWA) | **React + Vite SPA** | Same reasons, and a Vite PWA installs cleanly on phones. Your choice was right. |
| **Public marketing site** (home, modules, pricing, FAQ, book-a-demo, sign-up) | **Django templates + Tailwind** — *the one change to the original plan, now agreed* | This page is what sells the product, so it needs perfect SEO and a fast first paint, which an SPA is bad at. Next.js would fix that but would add a **third toolchain and a second Node server to deploy and pay for**. Django templates give server-rendered HTML with ideal SEO on the backend we are already running — and the pricing page, the sign-up form and the book-a-demo form all need backend data anyway. It is also exactly how CarHub works, so it is a pattern you already know. |

So: **no Next.js, no HTMX, no third Node app.** Two React apps and a set of Django templates. If a marketing page ever needs real interactivity we drop a single small React island into the template.

---

## 3. Hosting — the honest comparison

You want free. I have to be straight with you: **a genuinely free, always-on WebSocket deployment does not exist in 2026.** The current state of the four platforms you named:

| Platform | Free tier today | WebSockets | Verdict for us |
|---|---|---|---|
| **Render** | Still a real free tier, but free web services **sleep after 15 minutes idle** and take 30–50 s to wake. No free background worker. | Fine on paid; idle connections drop on free | Free tier is unusable for a flagship demo — a client clicking your link waits 50 seconds and the live queue is dead. Paid is about $7/mo per always-on service. |
| **Railway** | **No permanent free tier.** A one-off $5 trial credit, valid 30 days. Hobby $5/mo minimum. | Good, stays warm on paid | Fine, but no free path. |
| **Fly.io** | Sources conflict on whether new accounts still get free VMs; a card is required either way. | **Excellent** — the best of the four for long-lived connections | Cheapest real compute, roughly $2–3 per machine. Needs a little more ops knowledge. |
| **Koyeb** | Effectively gone — new users can no longer get the Starter tier since the Mistral acquisition. | — | Rule out. |

### What a working deployment actually needs

1. **Web process** — Django + Daphne, serving HTTP *and* WebSockets. Must be always on.
2. **Worker process** — Celery, for SMS, reminders, imports, the nightly demo reset. Must be always on.
3. **PostgreSQL** → **Neon** free tier, from day one. Render's free disk is wiped on every deploy, so you were right that this is non-negotiable.
4. **Redis** → **Upstash** free tier for the Channels layer and the Celery broker.
5. **Private file storage** → **Cloudflare R2** free tier (10 GB, no egress fees). Never a public image CDN — medical files leave only through short-lived signed URLs.
6. **Static marketing assets and SPA builds** → Cloudflare Pages or Render static sites, free.

### The decision: Render free tier, kept awake by UptimeRobot

Spending nothing now, and moving to paid hosting when there is a hospital to pitch. The keep-alive trick does work — but it has hard limits, so here are the numbers, taken from Render's own free-tier documentation rather than from memory.

| Fact, from Render's docs | What it means for us |
|---|---|
| "Render grants **750 Free instance hours** to each workspace per calendar month." Unused hours do not roll over. | A 31-day month is **744 hours**. So keeping **one** service awake around the clock fits — with about 6 hours spare. A **second** always-on free service does not fit. Do not spin up a second free web service, not even a preview, or the demo goes dark mid-month. |
| Render "spins down a Free web service that goes **15 minutes** without receiving any inbound traffic", counting HTTP requests **and WebSocket messages on existing connections**. | An UptimeRobot check every 5 minutes resets the timer and the service never sleeps. Active users on the journey board also keep it awake by themselves. |
| Spin-up "takes about **one minute**". | If a ping is ever missed, the next visitor waits a minute. |
| While spun down, requests to **`/robots.txt` get an automatic response and do not trigger a spin-up**. | Point UptimeRobot at **`/healthz`**, never at `/robots.txt` or the site root's robots file. This is the detail that silently breaks the trick. |
| "Other service types don't support Free instances." Free types are web services, Render Postgres, Render Key Value and static sites. | **There is no free background worker.** Celery has to run inside the one web service. |

### So the deployment shape is

- **One** Render free **web service** running Daphne *and* a Celery worker side by side under a small supervisor (`honcho`), from our Dockerfile.
- **Neon** Postgres (free) — Render's own free disk is wiped on every deploy, so this was never optional.
- **Upstash** Redis (free) for the Channels layer and the Celery broker.
- **Cloudflare R2** (free) for private medical files, signed URLs only.
- **UptimeRobot** hitting `/healthz` every 5 minutes.

Two consequences to plan around:

1. **Memory.** Everything shares one small free instance. Celery runs at `--concurrency=1`, and if it turns out too tight we drop Redis entirely and switch to **Django 6's built-in database-backed tasks** — which is exactly why all background work goes behind one `enqueue()` helper. Decided by measurement at milestone 9, not now.
2. **It is explicitly not a production tier.** Render can change the terms, and a real hospital customer cannot run on it. The Dockerfile, `render.yaml` and `fly.toml` are all written at milestone 2, so moving to Fly.io (~$5/mo) or Render paid (~$14/mo) is a config change and a deploy, not a rewrite.

Honest summary: **this is the right call for a portfolio demo and the wrong call for a paying hospital** — and the README will say so, which is itself a point in your favour with a technical reviewer.

---

## 4. Video for the online clinic

| | LiveKit Cloud | Daily | Jitsi |
|---|---|---|---|
| Free tier | 5,000 WebRTC minutes/mo | about 10,000 participant-minutes/mo | self-host free; managed (8x8 JaaS) pricing unclear |
| Self-hostable | **Yes — Apache 2.0** | No | Yes |
| Embed in our own UI | Yes, full control | Yes, full control | Yes, but heavier |
| Who may join a room | **We mint the tokens in Django** | We mint tokens | Token or JWT |
| HIPAA BAA | paid tier only, around $500/mo | paid add-on, around $500/mo | via 8x8 JaaS |

**Recommendation: LiveKit Cloud, behind an adapter.**

Why: the free tier covers a demo comfortably; we mint join tokens inside Django so *our own* permission rules decide who may enter a consultation room; and because LiveKit is Apache-2.0, if you ever outgrow the free tier you can self-host the identical server and **our code does not change**. Daily is just as good technically but you can never leave it. Jitsi means running another server from day one.

**An honest note on compliance:** the HIPAA/BAA tiers of all three cost roughly $500/month. You are in Nigeria under the **NDPA 2023**, not HIPAA, and this build runs on **synthetic data only**, so we do not need a BAA. A real hospital carrying real patients would, and the README will say that plainly rather than implying the demo is certified. Everything video-related sits behind a `VideoProvider` adapter, so swapping provider later is one file.

---

## 5. What happens next

Milestone 1 is closed. Milestone 2 (Foundations) is next: the monorepo scaffold, tenancy, auth with MFA, the audit log, the permission engine, the permissions-matrix and tenant-isolation test suites, and CI — then the first commit and push.

Two things are needed from you during milestone 2, and I will give click-by-click instructions for both:

1. An **empty public repository** at github.com/new named **`curavia`** — no README, no licence, no `.gitignore`. Then tell me, and I add the remote and push to `main`.
2. A **Neon** account and its connection string, which you paste into `.env` yourself.

Everything else (Brevo, Termii, Paystack test keys, Render, UptimeRobot, Upstash, R2, Sentry, LiveKit) comes later, in the order set out at the end of `03-build-plan.md`.

I will never create an account, and I will never see or print your keys.
