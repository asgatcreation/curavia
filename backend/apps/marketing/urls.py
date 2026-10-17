from __future__ import annotations

from django.urls import path, register_converter

from . import views


class LegalPageConverter:
    """Only the legal pages that exist, so a typo is a 404 not a template error."""

    regex = "terms|privacy|dpa|security"

    def to_python(self, value: str) -> str:
        return value

    def to_url(self, value: str) -> str:
        return value


register_converter(LegalPageConverter, "legalpage")

app_name = "marketing"

urlpatterns = [
    path("", views.home, name="home"),
    path("legal/<legalpage:page>/", views.legal, name="legal"),
]
