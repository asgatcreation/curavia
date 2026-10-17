# Curavia — design documents

**Curavia** · *cura* (care) + *via* (way) — "the way of care". Repo `curavia`.

A hospital and clinic operating system sold as SaaS to Nigerian private hospitals, clinics, specialist centres and hospital groups, with an online clinic and a patient portal.

Milestone 1 output — read in this order.

| | Document | What is in it |
|---|---|---|
| 1 | [00-decisions.md](00-decisions.md) | **The four settled decisions** (name, hosting, marketing site, scope), the name check with every rejected option and why, the confirmed stack with today's real version numbers, the Render free-tier numbers taken from Render's own docs, and the video-provider comparison. |
| 2 | [01-data-model.md](01-data-model.md) | The five ideas the whole system rests on, then every entity module by module. |
| 3 | [02-tenancy-and-permissions.md](02-tenancy-and-permissions.md) | How hospitals are kept apart (four layers), how 17 roles get exactly the right access, break-the-glass, and the hash-chained audit log. |
| 4 | [03-build-plan.md](03-build-plan.md) | Repository layout, 15 milestones across Phases A–D, the testing strategy, and what is needed from you and when. |

## Status

Milestone 1 is **closed and signed off**. Nothing has been installed, scaffolded or committed — this folder is the only thing that exists.

Milestone 2 (Foundations) is next. See `00-decisions.md` §5 for the two things needed to start it.

---

Designed and built by Akanji Oluwaseun Gabriel ([@asgatcreation](https://github.com/asgatcreation)).
