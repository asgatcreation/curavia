"""
The two server-rendered pages: the public check, and the printable document.

Django templates rather than a React route, for the same reason the
marketing site is — these are pages reached from outside the application.
The verification page is opened by scanning a QR code on a phone that has
never heard of Curavia, and it has to render on a cheap Android browser over
a bad connection, with no JavaScript, no bundle and no login. The printable
page is opened to be printed, and a print stylesheet on a server-rendered
page is the oldest and most reliable way to get a document onto A4.

**On PDFs.** ``backend/requirements.txt`` defers WeasyPrint to milestone 7
on purpose: it needs the GTK runtime on Windows, which would break a
developer's setup for a feature that is not yet due. So the printable page
is the deliverable for now — Ctrl-P, Save as PDF, which is what a clinic
with a printer does anyway — and milestone 7 renders this same template
through WeasyPrint to produce the file server-side. The template is written
with that swap in mind: no JavaScript, absolute units, and a QR code that is
an inline SVG rather than a canvas.
"""

from __future__ import annotations

import io

import qrcode
import qrcode.image.svg
from django.http import Http404, HttpRequest, HttpResponse
from django.shortcuts import render
from django.views.decorators.cache import cache_control

from apps.documents import service as documents


def qr_svg(data: str, *, box_size: int = 10) -> str:
    """
    The QR code, as inline SVG.

    SVG rather than PNG so it prints at the printer's resolution instead of
    the screen's, and inline so the printed page needs no second request —
    a document rendered from a cached HTML file on a laptop with no network
    must still carry its code.
    """
    code = qrcode.QRCode(
        version=None,
        error_correction=qrcode.constants.ERROR_CORRECT_M,
        box_size=box_size,
        border=2,
    )
    code.add_data(data)
    code.make(fit=True)
    image = code.make_image(image_factory=qrcode.image.svg.SvgPathImage)
    buffer = io.BytesIO()
    image.save(buffer)
    return buffer.getvalue().decode("utf-8")


@cache_control(no_store=True)
def verify(request: HttpRequest, token: str) -> HttpResponse:
    """
    What a stranger sees when they scan the code.

    ``no_store`` because this is somebody's medical paperwork being checked
    on a phone that may be shared, and a cached page in a browser's back
    stack is the sort of leak nobody thinks about until it happens.
    """
    found = documents.verify(token)
    if found is None:
        return render(
            request,
            "documents/verify.html",
            {"found": None, "page_title": "Document not found"},
            status=404,
        )
    documents.record_verification(token)
    return render(
        request,
        "documents/verify.html",
        {
            "found": found,
            "page_title": f"{found['kind']} {found['reference']}",
        },
    )


@cache_control(no_store=True, private=True)
def printable(request: HttpRequest, token: str) -> HttpResponse:
    """
    The document itself, laid out for A4.

    Reached by its token, like the verification page, because the person
    printing it may be the patient on the portal rather than a member of
    staff — and because a printable page that needed a session could not be
    the same page WeasyPrint renders in a background job at milestone 7.

    Unlike the verification page, this shows the whole document, so it is
    only ever handed out with the token, which is the thing the patient
    holds.
    """
    from apps.core.tenancy import unscoped
    from apps.documents.models import Document

    with unscoped(reason="printing a document: reached by its own token, not a session"):
        document = Document.unscoped.filter(token=token).select_related("patient").first()
    if document is None or not document.is_issued:
        raise Http404("No such document.")

    verify_url = _verify_url(request, token)
    fields = []
    for field in document.wording.get("fields", []):
        value = document.content.get(field["key"], "")
        if value:
            fields.append({"label": field["label"], "value": value})

    return render(
        request,
        "documents/printable.html",
        {
            "document": document,
            "patient": document.patient,
            "fields": fields,
            "fixed_text": document.wording.get("fixed_text", []),
            "signature": document.signature_block(),
            "letterhead": document.letterhead,
            "verify_url": verify_url,
            "qr": qr_svg(verify_url),
            "page_title": f"{document.name} {document.reference}",
        },
    )


def _verify_url(request: HttpRequest, token: str) -> str:
    from django.conf import settings
    from django.urls import reverse

    base = str(settings.CURAVIA.get("PUBLIC_URL", "")).rstrip("/")
    path = reverse("documents:verify", args=[token])
    return f"{base}{path}" if base else request.build_absolute_uri(path)


__all__ = ["printable", "qr_svg", "verify"]
