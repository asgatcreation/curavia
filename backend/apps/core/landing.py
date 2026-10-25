"""
Where each role lands when they sign in.

A cashier should open Curavia and be looking at the cashier's screen, not at a
dashboard with a cashier's tile on it. In a hospital, staff sign in once at the
start of a shift and then work one screen all day; making every one of them
navigate from a generic home page is a tax paid by the busiest people in the
building.

Decided on the server rather than in the client for two reasons. The role
templates live here, so the mapping cannot drift from them. And the fallback
has to be a *permission* question — a hospital that has renamed its roles, or
built its own, still gets somebody sent somewhere they can actually work.

The path is the staff app's own route. That is a small coupling between the
API and the client, and it is deliberate: the alternative is an abstract
"landing kind" that the client immediately maps to a path anyway, with the
mapping then in two places.
"""

from __future__ import annotations

from collections.abc import Iterable

#: Role slug to route. Only the roles whose work IS one screen; everybody else
#: falls through to the permission rules below.
ROLE_LANDING: dict[str, str] = {
    "owner": "/group",
    "facility_admin": "/group",
    "front_desk": "/",
    "cashier": "/billing",
    "doctor": "/consultation",
    "consultant": "/consultation",
    "nurse": "/triage",
    "nurse_in_charge": "/triage",
    "pharmacist": "/pharmacy",
    "pharmacy_technician": "/pharmacy",
    "lab_scientist": "/laboratory",
    "lab_supervisor": "/laboratory",
    "radiographer": "/radiology",
    "store_keeper": "/inventory",
    "accountant": "/accounts",
    "hr_officer": "/staff",
    "auditor": "/audit",
    "hmo_officer": "/billing",
}

#: Checked in order: the first permission a user holds decides where they go.
#: Ordered by how specific the screen is, so somebody who holds both pharmacy
#: and billing permissions lands on the narrower of the two.
PERMISSION_LANDING: tuple[tuple[str, str], ...] = (
    ("pharmacy.dispense", "/pharmacy"),
    ("lab.result.enter", "/laboratory"),
    ("radiology.report.write", "/radiology"),
    ("clinical.note.write", "/consultation"),
    ("triage.vitals.record", "/triage"),
    ("billing.payment.take", "/billing"),
    ("inventory.adjust", "/inventory"),
    ("patients.create", "/"),
    ("reporting.view", "/group"),
    ("audit.view", "/audit"),
)

#: Somebody with a membership but no useful permission at all. Not an error —
#: a hospital can create a role that grants nothing yet — but they need a page
#: that explains why everything is empty rather than a blank screen.
FALLBACK = "/no-access"


def landing_for(role_slugs: Iterable[str], permissions: Iterable[str]) -> str:
    """
    The route this person should open on.

    Roles win over permissions because a role is what the hospital *called*
    this person, and it is the more specific statement of intent. The
    permission rules exist for custom roles, renamed roles and people holding
    several roles at once.
    """
    slugs = list(role_slugs)

    # One role: unambiguous.
    if len(slugs) == 1 and slugs[0] in ROLE_LANDING:
        return ROLE_LANDING[slugs[0]]

    # Several roles, one of which is the owner: the group view, because that is
    # the screen that leads to all the others.
    if "owner" in slugs:
        return ROLE_LANDING["owner"]

    held = set(permissions)
    for code, path in PERMISSION_LANDING:
        if code in held:
            return path

    # Several roles, none of them recognised, and no permission we route on.
    for slug in slugs:
        if slug in ROLE_LANDING:
            return ROLE_LANDING[slug]

    return FALLBACK
