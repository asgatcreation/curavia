from __future__ import annotations

from django.apps import AppConfig


class PlatformAdminConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "apps.platformadmin"
    label = "platformadmin"
    verbose_name = "Platform administration"
