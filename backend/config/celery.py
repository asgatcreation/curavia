"""
Celery application.

On the current hosting (Render's free tier, which has no background worker)
this runs in the same container as Daphne, under honcho, at concurrency 1.
See docs/00-decisions.md §3 and infra/Procfile.
"""

from __future__ import annotations

import os

from celery import Celery
from celery.schedules import crontab

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings.dev")

app = Celery("curavia")
app.config_from_object("django.conf:settings", namespace="CELERY")
app.autodiscover_tasks()

# A static beat schedule rather than a database scheduler: django-celery-beat
# does not support Django 6.1 yet, and these jobs are ours, not the hospital's.
# Hospital-configurable scheduled reports arrive at milestone 11 and will carry
# their own schedule rows.
app.conf.beat_schedule = {
    "anchor-audit-chains-nightly": {
        "task": "core.anchor_audit_chains",
        "schedule": crontab(hour=2, minute=30),
    },
    "expire-emergency-grants-hourly": {
        "task": "core.expire_emergency_grants",
        "schedule": crontab(minute=5),
    },
}


@app.task(bind=True, name="core.debug_ping")
def debug_ping(self) -> str:
    """Prove the worker is alive. Used by the readiness check."""
    return "pong"
