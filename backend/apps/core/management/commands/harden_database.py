"""
Apply the database-level protections the ORM cannot enforce on its own.

Two of them:

* **The audit log becomes insert-only.** The application's role loses UPDATE
  and DELETE on it, so even a fully compromised application cannot rewrite
  history — only append to it.
* **Row-level security**, optionally. A table's owner bypasses RLS in
  PostgreSQL unless the table is FORCEd, and Django's migration role is that
  owner. So this genuinely needs a separate, non-owner application role, and
  the command says so rather than pretending a settings flag is enough.

Run once per environment after migrating, as a role with sufficient rights.
See docs/adr/0002-row-level-security.md.
"""

from __future__ import annotations

from typing import Any

from django.apps import apps
from django.conf import settings
from django.core.management.base import BaseCommand
from django.db import connection

from apps.core.models import TenantScoped

AUDIT_TABLES = ("core_auditevent",)


class Command(BaseCommand):
    help = "Make the audit log insert-only and, optionally, enable row-level security."

    def add_arguments(self, parser: Any) -> None:
        parser.add_argument(
            "--app-role",
            default=None,
            help="The non-owner database role the application connects as.",
        )
        parser.add_argument(
            "--rls",
            action="store_true",
            help="Also create row-level security policies on tenant tables.",
        )
        parser.add_argument(
            "--dry-run", action="store_true", help="Print the SQL without running it."
        )

    def handle(self, *args: Any, **options: Any) -> None:
        statements: list[str] = []
        app_role = options["app_role"]

        if app_role:
            for table in AUDIT_TABLES:
                statements.append(f"REVOKE UPDATE, DELETE, TRUNCATE ON {table} FROM {app_role};")
                statements.append(f"GRANT SELECT, INSERT ON {table} TO {app_role};")
        else:
            self.stdout.write(
                self.style.WARNING(
                    "No --app-role given, so the audit log's grants are untouched. "
                    "Without a separate application role the owner can still delete "
                    "audit rows; the hash chain is what makes that detectable."
                )
            )

        if options["rls"]:
            for model in apps.get_models():
                if not issubclass(model, TenantScoped) or model._meta.abstract:
                    continue
                table = model._meta.db_table
                policy = f"{table}_tenant_isolation"
                statements += [
                    f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY;",
                    # FORCE is the one that matters: without it the table owner,
                    # which is the migration role, ignores the policy entirely.
                    f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY;",
                    f"DROP POLICY IF EXISTS {policy} ON {table};",
                    f"CREATE POLICY {policy} ON {table} USING ("
                    "organisation_id = NULLIF("
                    "current_setting('app.current_org', true), '')::uuid);",
                ]

        if not statements:
            self.stdout.write("Nothing to do.")
            return

        if options["dry_run"]:
            self.stdout.write("\n".join(statements))
            return

        with connection.cursor() as cursor:
            for statement in statements:
                cursor.execute(statement)

        self.stdout.write(self.style.SUCCESS(f"Applied {len(statements)} statement(s)."))

        if options["rls"] and not settings.CURAVIA["ENABLE_RLS"]:
            self.stdout.write(
                self.style.WARNING(
                    "Policies are in place, but CURAVIA_ENABLE_RLS is false, so the "
                    "application never sets app.current_org and every tenant query "
                    "would return nothing. Set CURAVIA_ENABLE_RLS=true to finish."
                )
            )
