"""
The sticker that goes on the tube.

A sample label is the smallest document in the hospital and the one with the
worst consequences when it is wrong. It is 50 mm by 25 mm, which is the
standard roll a cheap thermal printer takes, and everything on it is there
because somebody holding a rack of tubes needs it.

**Why Code 128 and not a QR code.** The handheld scanners a Nigerian
laboratory already owns are 1D laser scanners: they read bars, not squares.
A QR-only label would mean every bench needing a new scanner, so the barcode
is Code 128 — the symbology that encodes letters and digits in the least
width — and a small QR goes beside it for a phone camera, which is what a
scientist reaches for when the laser scanner has failed.

**Why the bars are drawn here rather than by the library.** ``python-barcode``
produces a complete SVG file, with an XML declaration and a DOCTYPE, sized
in millimetres of its own choosing. What is needed is a fragment that sits
inside a label at an exact width. So the library is used for the part that
must not be got wrong — turning the accession number into Code 128's bit
pattern, including its check digit — and the bars themselves are drawn into
this module's own SVG. That keeps the symbology in tested library code and
the geometry under our control.

**Why it is a PDF.** A label printed from a browser's print dialogue comes
out at whatever scale the browser decided, and a barcode printed at 92% is a
barcode that does not scan. WeasyPrint renders at exact physical size, so
the page the ward phone sends to the printer is the same page the laboratory
desk sends.

**What is on it, and what is not.** Accession number, barcode, patient name,
MRN, date of birth, the tube type, the tests, and when it was drawn. No
diagnosis and no reason for the test: a tube travels through a corridor in a
rack, and the label is readable by everyone it passes.
"""

from __future__ import annotations

import io
from dataclasses import dataclass

import qrcode
import qrcode.image.svg
from barcode import Code128
from django.template.loader import render_to_string
from django.utils import timezone

from apps.laboratory.models import Sample
from apps.laboratory.ranges import age_days

#: The roll. 50 mm by 25 mm is the common thermal label in this part of the
#: market; the ones sold for freezer racks are the same width.
LABEL_WIDTH_MM = 50.0
LABEL_HEIGHT_MM = 25.0

#: Code 128 geometry. A module is the narrowest bar, and wider is easier to
#: read: 0.23 mm spreads a ten-character accession and its quiet zones across
#: the 33 mm left beside the QR code, which is comfortably above the 0.17 mm
#: at which cheap scanners start to miss. Anything narrower would waste the
#: label's width on the one thing a machine has to read.
MODULE_WIDTH_MM = 0.23
#: The height of the bars, which is also what the stylesheet draws them at:
#: the template is given this value rather than repeating it, because a
#: barcode whose SVG and whose CSS disagree is a barcode scaled by the
#: renderer, and a scaled barcode is the one that will not scan.
BAR_HEIGHT_MM = 5.5
#: Blank space either side of the bars, without which a scanner cannot find
#: the start of the code. Ten modules is the minimum the standard allows.
QUIET_MODULES = 10

#: The square beside the bars. A phone camera reads it; the bench scanner
#: reads the bars. Both encode the same accession number, so losing one of
#: them to a smudge still leaves the tube identifiable by machine.
QR_SIZE_MM = 8.0


@dataclass(frozen=True)
class Barcode:
    """A drawn Code 128, ready to drop into a label."""

    svg: str
    width_mm: float
    height_mm: float


def code128_svg(
    value: str,
    *,
    module_width_mm: float = MODULE_WIDTH_MM,
    height_mm: float = BAR_HEIGHT_MM,
) -> Barcode:
    """
    Draw ``value`` as Code 128 bars.

    Returns an SVG fragment, not a document: it is placed inside a label, so
    it carries its own ``width`` and ``height`` in millimetres and no XML
    declaration.

    Consecutive dark modules are drawn as one rectangle rather than several
    touching ones. Two rectangles that share an edge can leave a hairline of
    white between them once a renderer has rounded the coordinates, and a
    hairline inside a bar is a misread.
    """
    bits = "".join(Code128(value).build())
    total_modules = len(bits) + 2 * QUIET_MODULES
    width_mm = total_modules * module_width_mm

    rects: list[str] = []
    index = 0
    while index < len(bits):
        if bits[index] == "0":
            index += 1
            continue
        run = 1
        while index + run < len(bits) and bits[index + run] == "1":
            run += 1
        x = (QUIET_MODULES + index) * module_width_mm
        rects.append(
            f'<rect x="{x:.3f}" y="0" width="{run * module_width_mm:.3f}" '
            f'height="{height_mm:.3f}" />'
        )
        index += run

    svg = (
        f'<svg xmlns="http://www.w3.org/2000/svg" '
        f'width="{width_mm:.2f}mm" height="{height_mm:.2f}mm" '
        f'viewBox="0 0 {width_mm:.3f} {height_mm:.3f}" '
        f'shape-rendering="crispEdges" role="img" '
        f'aria-label="Barcode {value}">'
        f'<rect width="{width_mm:.3f}" height="{height_mm:.3f}" fill="#fff" />'
        f'<g fill="#000">{"".join(rects)}</g>'
        f"</svg>"
    )
    return Barcode(svg=svg, width_mm=width_mm, height_mm=height_mm)


def qr_svg(value: str) -> str:
    """
    A small QR of the same accession number, for a phone camera.

    Error correction L rather than the M used on a patient card: a label
    lives for hours in a rack, not years in a wallet, and the lower level
    buys back the modules that let it stay legible at 12 mm square.
    """
    code = qrcode.QRCode(
        version=None,
        error_correction=qrcode.constants.ERROR_CORRECT_L,
        box_size=8,
        border=1,
    )
    code.add_data(value)
    code.make(fit=True)
    buffer = io.BytesIO()
    code.make_image(image_factory=qrcode.image.svg.SvgPathImage).save(buffer)
    return buffer.getvalue().decode("utf-8")


def short_age(patient) -> str:
    """
    "47 y", "8 m", "12 d" - age in the space a label has for it.

    Written short rather than spelled out because the whole of this string
    has about 14 mm beside the date of birth, and because "47 y" is how a
    nurse writes an age on a request form anyway. Empty when the date of
    birth is not known, which the label then simply does not claim.
    """
    days = age_days(patient)
    if days is None:
        return ""
    if days < 31:
        return f"{days} d"
    if days < 365:
        return f"{round(days / 30.4375)} m"
    return f"{int(days // 365.25)} y"


def label_context(sample: Sample) -> dict[str, object]:
    """
    Everything one label prints. Built here so the template holds no logic.

    The patient's age is printed as well as the date of birth, because the
    person checking a tube against a request form is checking a human being
    against a label and "47 y" is what they can verify by looking.
    """
    patient = sample.patient
    collected = timezone.localtime(sample.collected_at)
    return {
        "accession": sample.accession,
        "barcode": code128_svg(sample.accession),
        "qr": qr_svg(sample.accession),
        "patient_name": patient.full_name,
        "mrn": patient.mrn,
        "date_of_birth": patient.date_of_birth,
        "age": short_age(patient),
        "sex": str(patient.get_sex_display()) if patient.sex else "",
        "specimen": str(sample.get_specimen_display()),
        "container": sample.container,
        "panels": sample.panel_names,
        "collected_at": collected,
        "is_recollection": sample.recollection_of_id is not None,
    }


def render_html(samples: list[Sample], *, hospital: str = "") -> str:
    """One page per label, in order. The HTML WeasyPrint then renders."""
    return render_to_string(
        "laboratory/labels.html",
        {
            "labels": [label_context(sample) for sample in samples],
            "hospital": hospital,
            "label_width_mm": LABEL_WIDTH_MM,
            "label_height_mm": LABEL_HEIGHT_MM,
            "bar_height_mm": BAR_HEIGHT_MM,
            "qr_size_mm": QR_SIZE_MM,
        },
    )


def render_pdf(samples: list[Sample], *, hospital: str = "") -> bytes:
    """
    The labels as a PDF, one label per page at exactly 50 x 25 mm.

    One label per page rather than a grid on A4: a thermal label printer is
    fed a roll and treats each page as one sticker. A hospital printing on
    A4 sheets of labels is a case for 7c, when the result report arrives and
    there is a reason to offer a choice of stationery.

    WeasyPrint is imported inside the function. It loads Pango and cairo
    through cffi at import time, which costs about a second and happens in
    every management command, every test run and every worker boot — and
    almost none of those will ever print a label.
    """
    from weasyprint import HTML

    html = render_html(samples, hospital=hospital)
    return HTML(string=html).write_pdf()


def mark_printed(samples: list[Sample], *, actor=None) -> None:
    """
    Record that labels were produced. See ``Sample.label_print_count``.

    Counted rather than simply flagged: a reprint is ordinary, and the number
    is only interesting when it is large.
    """
    now = timezone.now()
    for sample in samples:
        sample.label_printed_at = now
        sample.label_print_count += 1
        sample.updated_by = actor
        sample.save(
            update_fields=["label_printed_at", "label_print_count", "updated_by", "updated_at"]
        )
