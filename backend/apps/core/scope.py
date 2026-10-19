"""
Resolving which hospital a caller is acting for.

One function, three callers. Curavia has three entry points and they
authenticate at different moments:

* **Django views** (admin, the marketing site) — session auth, so
  ``request.user`` is populated by ``AuthenticationMiddleware``.
* **The JSON API** — JWT or session, authenticated by DRF *inside the view*,
  which is **after** all middleware has run. This is the subtlety that matters:
  a middleware that reads ``request.user`` sees ``AnonymousUser`` for every
  JWT request, so tenant resolution cannot live there.
* **WebSockets** — resolved by ``apps.core.ws`` from the session.

Three resolvers would drift apart, and the one that drifted would be a tenant
leak. So all three call ``resolve`` below, and the only difference between them
is where they get the user and the requested scope from.

The rule the function enforces: **the client never chooses its hospital.** It
may *ask* for one, by header, because a visiting consultant works at three
hospitals and a group officer covers five branches — but the request is checked
against that user's own memberships, and asking for one they do not hold is a
refusal, never a silent fall back to a different hospital.
"""

from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

logger = logging.getLogger("curavia.tenancy")

if TYPE_CHECKING:
    from apps.accounts.models import StaffMembership
    from apps.core.models import Branch, Organisation

__all__ = ["ResolvedScope", "ScopeDenied", "parse_identifier", "resolve"]


class ScopeDenied(Exception):
    """
    The caller asked for a hospital or branch they do not hold.

    Carries a message safe to return to the client: it confirms nothing about
    whether the requested hospital exists.
    """


@dataclass(frozen=True, slots=True)
class ResolvedScope:
    organisation: Organisation
    membership: StaffMembership
    branch: Branch | None
    branch_id: uuid.UUID | None
    all_branches: bool
    permission_codes: frozenset[str]
    available_modules: frozenset[str]


def parse_identifier(value: str | None, *, what: str) -> str | None:
    """
    Validate a UUID arriving from a header.

    A malformed one is a refusal rather than a silent ignore: a client sending
    rubbish is either broken or probing, and both are worth surfacing.
    """
    if not value:
        return None
    try:
        return str(uuid.UUID(value.strip()))
    except (ValueError, AttributeError) as exc:
        raise ScopeDenied(f"That {what} identifier is not valid.") from exc


def resolve(
    user: Any,
    *,
    requested_organisation: str | None = None,
    requested_branch: str | None = None,
) -> ResolvedScope | None:
    """
    Work out which hospital and branch this user is acting for.

    Returns ``None`` when the user belongs to no hospital at all — a
    patient-portal account, or one of Curavia's own staff without a support
    grant. That is not an error: the caller decides what to do with it, and
    for the API the answer is an empty scope and no hospital data.

    Raises ``ScopeDenied`` when the user asked for a specific hospital or
    branch that is not theirs.
    """
    if not (user and getattr(user, "is_authenticated", False) and user.is_active):
        return None

    organisation_id = parse_identifier(requested_organisation, what="organisation")
    branch_id = parse_identifier(requested_branch, what="branch")

    memberships = list(user.live_memberships())
    if not memberships:
        return None

    if organisation_id:
        membership = next(
            (m for m in memberships if str(m.organisation_id) == organisation_id), None
        )
        if membership is None:
            logger.warning(
                "refused organisation %s for user %s: no live membership",
                organisation_id,
                user.pk,
            )
            raise ScopeDenied("You do not have access to that hospital.")
    else:
        membership = _preferred(memberships, getattr(user, "last_organisation_id", None))

    organisation = membership.organisation
    permission_codes = user.permission_codes(organisation.pk)
    all_branches = "branch.view_all" in permission_codes

    branch = _resolve_branch(
        membership,
        requested=branch_id,
        last_branch_id=getattr(user, "last_branch_id", None),
        all_branches=all_branches,
    )

    return ResolvedScope(
        organisation=organisation,
        membership=membership,
        branch=branch,
        branch_id=branch.pk if branch else None,
        all_branches=all_branches,
        permission_codes=permission_codes,
        available_modules=organisation.available_modules(),
    )


def _preferred(memberships: list[Any], last_organisation_id: uuid.UUID | None) -> Any:
    """
    Pick a hospital when the client did not ask for one.

    The last one they worked in, so somebody who works at three hospitals
    lands where they left off rather than on a picker every morning.
    """
    if last_organisation_id:
        for membership in memberships:
            if membership.organisation_id == last_organisation_id:
                return membership
    return memberships[0]


def _resolve_branch(
    membership: Any,
    *,
    requested: str | None,
    last_branch_id: uuid.UUID | None,
    all_branches: bool,
) -> Any:
    allowed = list(membership.branches.all())
    by_id = {str(branch.pk): branch for branch in allowed}

    if requested:
        if requested in by_id:
            return by_id[requested]
        if all_branches:
            # Someone holding branch.view_all may work in any branch of their
            # own hospital, including one they are not individually assigned to.
            #
            # Read through the unscoped manager, filtered explicitly to this
            # membership's organisation. Like the role lookup in
            # User.permission_codes, this is the bootstrap: it is what decides
            # the scope, so it cannot require the scope to already be set. The
            # organisation filter is what keeps it to their own hospital.
            from apps.core.models import Branch

            branch = Branch.unscoped.filter(
                pk=requested, organisation_id=membership.organisation_id
            ).first()
            if branch is not None:
                return branch
        logger.warning(
            "refused branch %s for user %s at organisation %s",
            requested,
            membership.user_id,
            membership.organisation_id,
        )
        raise ScopeDenied("You do not have access to that branch.")

    if last_branch_id and str(last_branch_id) in by_id:
        return by_id[str(last_branch_id)]
    if membership.default_branch_id and str(membership.default_branch_id) in by_id:
        return by_id[str(membership.default_branch_id)]
    return allowed[0] if allowed else None
