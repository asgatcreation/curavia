from __future__ import annotations

from django.apps import AppConfig


class CoreConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "apps.core"
    label = "core"
    verbose_name = "Curavia core"

    def ready(self) -> None:
        # Importing this module registers an object rule for every permission
        # code, so a view can never check a code with no rule behind it.
        from . import rules  # noqa: F401
        from .middleware import validate_module_settings

        validate_module_settings()
