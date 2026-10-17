"""
The public marketing site.

Server-rendered Django templates rather than a third React app: this is the
page that sells Curavia, so it needs real SEO and a fast first paint, and it
runs on the backend we are already paying for. Reasoning in
docs/00-decisions.md §2.

Milestone 2 ships the shell and the legal pages. The full marketing site, the
pricing page and the book-a-demo form arrive at milestone 12 with the platform
level.
"""

from __future__ import annotations

from django.http import HttpRequest, HttpResponse
from django.shortcuts import render
from django.views.decorators.cache import cache_control

from apps.core.modules import MODULES


@cache_control(max_age=300, public=True)
def home(request: HttpRequest) -> HttpResponse:
    return render(
        request,
        "marketing/home.html",
        {
            "modules": MODULES,
            "page_title": "Curavia — one system for your whole hospital",
        },
    )


@cache_control(max_age=3600, public=True)
def legal(request: HttpRequest, page: str) -> HttpResponse:
    """
    Terms, privacy policy and the Data Processing Agreement.

    These are not decoration. Under the Nigeria Data Protection Act 2023 the
    hospital is the data controller and Curavia is the processor, and a
    processor needs a written agreement in place. The templates spell out that
    split.
    """
    titles = {
        "terms": "Terms of service",
        "privacy": "Privacy policy",
        "dpa": "Data Processing Agreement",
        "security": "Security and privacy",
    }
    return render(
        request,
        f"marketing/legal/{page}.html",
        {"page_title": f"{titles.get(page, 'Legal')} — Curavia"},
    )
