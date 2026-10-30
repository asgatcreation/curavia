from __future__ import annotations

from django.apps import AppConfig
from django.db.models.signals import post_migrate


class AccountsConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "apps.accounts"
    label = "accounts"
    verbose_name = "Accounts and access"

    def ready(self) -> None:
        from . import signals  # noqa: F401
        from .role_sync import on_post_migrate

        # Every milestone adds permissions, and a hospital's roles are its
        # own copies — so without this, an owner created last year cannot
        # use anything shipped since. See role_sync.py.
        post_migrate.connect(on_post_migrate, sender=self, dispatch_uid="curavia.sync_owner_roles")
