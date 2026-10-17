"""Verify the audit hash chain for one hospital, or for all of them."""

from __future__ import annotations

from typing import Any

from django.core.management.base import BaseCommand, CommandError

from apps.core.audit import AuditChainHead, ChainVerificationError, verify_chain
from apps.core.models import Organisation
from apps.core.tenancy import unscoped


class Command(BaseCommand):
    help = (
        "Recompute every audit event's hash and report whether the chain holds. "
        "Exits non-zero if any chain is broken, so it can be run from cron."
    )

    def add_arguments(self, parser: Any) -> None:
        parser.add_argument(
            "--organisation",
            help="Slug or UUID of one hospital. Omit to check every chain.",
        )

    def handle(self, *args: Any, **options: Any) -> None:
        targets: list[tuple[str, Any]] = []
        requested = options["organisation"]

        with unscoped(reason="management command: verify audit chains platform-wide"):
            if requested:
                organisation = Organisation.objects.filter(slug=requested).first()
                if organisation is None:
                    organisation = Organisation.objects.filter(pk=requested).first()
                if organisation is None:
                    raise CommandError(f"No hospital matches {requested!r}.")
                targets.append((organisation.name, organisation.pk))
            else:
                targets = [
                    (organisation.name, organisation.pk)
                    for organisation in Organisation.objects.all()
                ]
                if AuditChainHead.objects.filter(chain_key="platform").exists():
                    targets.append(("Curavia platform", None))

            broken = 0
            for label, organisation_id in targets:
                try:
                    count, head = verify_chain(organisation_id)
                except ChainVerificationError as exc:
                    broken += 1
                    self.stderr.write(
                        self.style.ERROR(
                            f"BROKEN  {label}: {exc.problem} (at sequence {exc.sequence})"
                        )
                    )
                    continue
                self.stdout.write(
                    self.style.SUCCESS(f"ok      {label}: {count} event(s), head {head[:16]}...")
                )

        if broken:
            raise CommandError(f"{broken} audit chain(s) failed verification.")
        self.stdout.write(self.style.SUCCESS(f"All {len(targets)} chain(s) verified."))
