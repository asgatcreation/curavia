from __future__ import annotations

from django.apps import AppConfig


class NursingConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "apps.nursing"
    label = "nursing"
    verbose_name = "Nursing tasks and handover"
