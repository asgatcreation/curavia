"""
Test settings.

Deliberately strict: the permission and tenant-isolation suites must run
against the same guards production uses.
"""

from .base import *
from .base import CURAVIA, MIDDLEWARE

DEBUG = False
ALLOWED_HOSTS = ["testserver", "localhost"]

# Fast, deterministic hashing — tests create a lot of users.
PASSWORD_HASHERS = ["django.contrib.auth.hashers.MD5PasswordHasher"]

EMAIL_BACKEND = "django.core.mail.backends.locmem.EmailBackend"

# Run Celery tasks inline so a test can assert on their effects.
CELERY_TASK_ALWAYS_EAGER = True
CELERY_TASK_EAGER_PROPAGATES = True

# In-memory channel layer: no Redis needed to test consumers.
CHANNEL_LAYERS = {"default": {"BACKEND": "channels.layers.InMemoryChannelLayer"}}

# Lockout would make the permission matrix flaky.
MIDDLEWARE = [m for m in MIDDLEWARE if "axes" not in m]
AXES_ENABLED = False

# Real providers are never contacted from a test.
INTEGRATION_BACKENDS = {
    "sms": "integrations.sms.fake.FakeSmsProvider",
    "email": "integrations.email.fake.FakeEmailProvider",
    "payments": "integrations.payments.fake.FakePaymentProvider",
    "video": "integrations.video.fake.FakeVideoProvider",
    "storage": "integrations.storage.fake.FakeFileStorage",
}

CURAVIA = {**CURAVIA, "ENABLE_RLS": False}
