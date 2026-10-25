"""
Test settings.

Deliberately strict: the permission and tenant-isolation suites must run
against the same guards production uses.
"""

from .base import *
from .base import CURAVIA, MIDDLEWARE, REST_FRAMEWORK

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

# Throttling off.
#
# DRF's throttle counters live in the cache, which is shared by every test in
# the process, so a file that exercises a public endpoint thirty times leaves
# the next file's first request throttled. That made failures depend on test
# ORDER - the onboarding tests passed alone and 429'd when the invitation
# tests ran before them.
#
# Throttling is a real behaviour and deserves its own test, which can switch
# a rate back on for itself. It should not be a background constraint on
# everything else.
REST_FRAMEWORK = {
    **REST_FRAMEWORK,
    "DEFAULT_THROTTLE_RATES": {"auth": None, "search": None, "default": None},
}

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
