"""
Send the reminders that are due, and close off yesterday's no-shows.

A management command rather than only a Celery task, deliberately. The
hosting decision (docs/00-decisions.md) is Render's free tier, which has no
background worker — so this has to be runnable from a cron entry, a scheduled
task, or by hand while somebody is watching. The Celery task calls the same
code.

Safe to run as often as you like: every job is claimed under a lock before
anything is sent, so two overlapping runs produce one message each.
"""

from __future__ import annotations

from typing import Any

from django.core.management.base import BaseCommand

from apps.core.models import Branch, Organisation
from apps.core.tenancy import tenant_scope, unscoped
from apps.scheduling import reminders


class Command(BaseCommand):
    help = "Send due appointment reminders and record no-shows."

    def add_arguments(self, parser: Any) -> None:
        parser.add_argument(
            "--limit", type=int, default=200, help="How many reminders to send at most."
        )
        parser.add_argument(
            "--no-shows",
            action="store_true",
            help="Also close off appointments nobody came to.",
        )

    def handle(self, *args: Any, **options: Any) -> None:
        # Every hospital in turn, each inside its own scope. The loop is
        # unscoped; the work never is.
        with unscoped(reason="scheduled job: reach every hospital's due reminders"):
            organisations = list(Organisation.objects.all().only("pk", "name"))

        total_sent = total_failed = total_cost = 0
        for organisation in organisations:
            with tenant_scope(organisation.pk, None, all_branches=True):
                outcome = reminders.send_due(limit=options["limit"])
                total_sent += outcome.sent
                total_failed += outcome.failed
                total_cost += outcome.cost_kobo
                if outcome.sent or outcome.failed:
                    self.stdout.write(
                        f"{organisation.name}: {outcome.sent} sent, "
                        f"{outcome.failed} failed, {outcome.skipped} skipped"
                    )

                if options["no_shows"]:
                    for branch in Branch.objects.all().only("pk", "name"):
                        missed = reminders.record_no_shows(branch_id=branch.pk)
                        if missed:
                            self.stdout.write(f"{branch.name}: {missed} did not attend")

        naira = total_cost / 100
        self.stdout.write(
            self.style.SUCCESS(
                f"Done. {total_sent} sent, {total_failed} failed, "
                f"about \u20a6{naira:,.2f} in text messages."
            )
        )
