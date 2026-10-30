"""
Keeping every hospital's owner role complete.

Roles are **copies** of the templates, stored per hospital, so a hospital can
rename "Front desk" to "Health Records" without renaming it for everybody
else. That is the right design, and it has one consequence that is easy to
miss until a milestone ships:

**a permission added after a hospital was created does not reach it.**

For an ordinary role that is correct — a hospital that narrowed its nurse
role should not have it widened again by a deployment. For the **owner** role
it is a broken promise. ``role_templates.py`` generates the owner's codes
rather than listing them, precisely so that "there is exactly one role that
can do anything" stays true; and ``is_protected`` exists so a hospital cannot
strip it. If adding ``appointment.book`` left every existing owner unable to
book an appointment in their own hospital, that invariant would hold only on
the day each hospital signed up.

So the owner role is topped up on every deploy, from ``post_migrate``:

* **Only protected owner roles.** Nothing else is touched, ever.
* **Only additions.** A code is never removed, so a hospital that has
  genuinely edited its owner role keeps whatever it put there.
* **Idempotent**, so running it changes nothing when there is nothing to add
  and the next milestone needs no migration of its own.

Platform permissions are excluded, because running a hospital is not the same
as running Curavia — which is the same line ``all_hospital_codes()`` draws.
"""

from __future__ import annotations

import logging

logger = logging.getLogger("curavia.accounts")


def sync_owner_roles() -> int:
    """
    Give every protected owner role any hospital permission it is missing.

    Returns how many roles changed, so a deployment log says whether this
    did anything.

    This is a deliberate cross-hospital write — it reaches every
    organisation by design — so it goes through the ordinary escape hatch
    with a reason, which is what keeps it visible to
    scripts/check-cross-tenant-allowlist.sh and to whoever reads the
    allowlist. It is confined to one field on one kind of row, and it only
    ever adds.
    """
    from apps.accounts.models import Role
    from apps.accounts.role_templates import all_hospital_codes
    from apps.core.tenancy import unscoped

    expected = set(all_hospital_codes())
    changed = 0

    with unscoped(reason="deploy: top up every hospital's owner role with new permissions"):
        for role in Role.unscoped.filter(is_protected=True, slug="owner").iterator():
            held = set(role.permissions or [])
            missing = expected - held
            if not missing:
                continue
            role.permissions = sorted(held | missing)
            role.save(update_fields=["permissions"])
            changed += 1
            logger.info(
                "owner role %s topped up with %d new permission(s): %s",
                role.pk,
                len(missing),
                ", ".join(sorted(missing)),
            )
    return changed


def on_post_migrate(sender, **kwargs) -> None:
    """
    Run after migrations, once, for the accounts app only.

    Guarded on the sender so a project with twelve apps does not run this
    twelve times per deploy.
    """
    if getattr(sender, "label", "") != "accounts":
        return
    try:
        changed = sync_owner_roles()
    except Exception:  # a deploy must not die here
        # Worth a loud log and not worth a failed deployment: the hospital
        # still works, the owner is simply missing a new permission, and the
        # next deploy tries again.
        logger.exception("could not top up owner roles")
        return
    if changed:
        logger.info("topped up %d owner role(s) with newly added permissions", changed)


__all__ = ["on_post_migrate", "sync_owner_roles"]
