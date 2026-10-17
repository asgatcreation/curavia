"""
Curavia backend.

Importing the Celery app here is what makes ``@shared_task`` work everywhere,
including inside Django apps that never import Celery themselves.
"""

from .celery import app as celery_app

__all__ = ("celery_app",)
