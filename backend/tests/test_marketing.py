"""
The public marketing site.

Anonymous pages, so they are cheap to test and there is no excuse for one of
them being broken. The template-comment test is the reason this file exists.
"""

from __future__ import annotations

import re

import pytest
from django.urls import reverse

pytestmark = pytest.mark.django_db

# Reversed inside each test, not here: at import time the URL configuration
# has not been loaded yet, and reverse() raises NoReverseMatch during
# collection rather than failing one test.
PAGES = [
    ("marketing:home", {}),
    ("marketing:legal", {"page": "terms"}),
    ("marketing:legal", {"page": "privacy"}),
    ("marketing:legal", {"page": "dpa"}),
    ("marketing:legal", {"page": "security"}),
]


@pytest.mark.parametrize(("name", "kwargs"), PAGES)
def test_the_page_renders_for_an_anonymous_visitor(client, name: str, kwargs: dict) -> None:
    """No login, no hospital in scope, no database row required."""
    response = client.get(reverse(name, kwargs=kwargs))
    assert response.status_code == 200


@pytest.mark.parametrize(("name", "kwargs"), PAGES)
def test_no_template_comment_leaks_into_the_page(client, name: str, kwargs: dict) -> None:
    """
    Django's ``{# ... #}`` comment is SINGLE-LINE ONLY.

    Spread over two lines it is not a comment at all, and every word of it
    renders as visible text. This happened twice: once at the top of
    base.html, where a two-line note about the stylesheet appeared above the
    Curavia logo on every page of the site, and again a few minutes after it
    was fixed, in a different two-line note.

    It is an easy mistake precisely because the syntax looks like a block
    comment in every other template language, so it gets a test rather than a
    resolution to be careful. ``{% comment %}`` is the block form.
    """
    url = reverse(name, kwargs=kwargs)
    body = client.get(url).content.decode()
    leaked = re.findall(r"\{#.{0,80}", body, flags=re.DOTALL)
    assert not leaked, (
        f"A template comment rendered as visible text on {url}. "
        f"Django's {{# #}} is single-line only — use {{% comment %}} for more "
        f"than one line. Found: {leaked}"
    )
    assert "{%" not in body, f"An unrendered template tag reached the browser on {url}."


def test_the_home_page_lists_every_module(client) -> None:
    """
    The module grid is generated from the registry, not typed into the
    template, so a module added at a later milestone appears here on its own.
    """
    from django.utils.html import escape

    from apps.core.modules import MODULES

    body = client.get(reverse("marketing:home")).content.decode()
    for module in MODULES:
        # escape(), because "Emergency / A&E" reaches the browser as
        # "Emergency / A&amp;E" and a raw comparison would fail on the
        # ampersand rather than on anything being missing.
        assert escape(module.label) in body, f"{module.code} is missing from the home page"


def test_the_home_page_says_nothing_about_a_real_patient(client) -> None:
    """
    A marketing page is the easiest place for a screenshot of real data to end
    up. There is none here, and this asserts the footer keeps saying so.
    """
    body = client.get(reverse("marketing:home")).content.decode()
    assert "synthetic" in body.lower()
