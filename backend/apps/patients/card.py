"""
The patient card.

A credit-card-sized card the patient keeps, with their name, their MRN and a
QR code. It replaces the single most common cause of a duplicate record: a
returning patient who cannot remember their number, and a clerk who then
cannot find them because the surname was spelled differently the first time.

**What the QR contains, and what it deliberately does not.**

The payload is ``CURAVIA:<hospital slug>:<MRN>`` and nothing else. No name,
no date of birth, no phone number, no link.

That is not an oversight, it is the whole design:

* **A card gets lost.** Someone who finds one on the floor of a danfo should
  learn nothing about its owner. Scan this with a phone camera and you get an
  opaque string.

* **A QR that was a URL would be an identifier that opens a door.** MRNs are
  sequential by design — ``MAIN/2026/000001`` is followed by ``000002`` — so
  any URL carrying one is a URL whose neighbours are other patients. This
  payload resolves only inside Curavia, only for a signed-in member of staff
  with ``patients.view``, and the read is audited like every other.

* **It names the hospital.** A group with six branches issues one card per
  patient, and a scanner at Lekki has to know the number it just read is one
  of its own and not a card from the clinic across the road that also starts
  at 000001.

The card is rendered as SVG rather than PNG: it has to print crisply on a
cheap thermal or inkjet printer at 86 mm wide, and it has to survive being
scaled by a browser's print dialogue. An SVG does both, and arrives as text
small enough to put in a JSON response alongside the patient's details, so
drawing a card is one request rather than two.
"""

from __future__ import annotations

import io
import re
from dataclasses import dataclass

import qrcode
from qrcode.constants import ERROR_CORRECT_Q
from qrcode.image.svg import SvgPathImage

from apps.patients.models import Patient

#: Prefix on every payload, so a scanner can tell a Curavia card from the
#: dozens of other QR codes a phone meets in a day and refuse the rest.
PAYLOAD_PREFIX = "CURAVIA"

#: Error correction level Q recovers from about 25% of the code being
#: unreadable. Chosen over the usual M because these cards live in wallets:
#: they get folded, they get wet in the rainy season, and a card that stops
#: scanning sends its owner back to the "find me by name" problem the card
#: exists to solve.
ERROR_CORRECTION = ERROR_CORRECT_Q

#: Printed size of the QR block. 22 mm is the smallest that a cheap phone
#: camera reads reliably at arm's length, and it leaves room on an 86 mm
#: card for a name long enough to be a real Nigerian name.
QR_MILLIMETRES = 22

_XML_DECLARATION = re.compile(r"^<\?xml[^>]*\?>\s*")


class CardError(Exception):
    """The card cannot be issued. The message is shown to the clerk."""


@dataclass(frozen=True, slots=True)
class PatientCard:
    """Everything printed on one card."""

    patient: Patient
    hospital_name: str
    branch_name: str
    payload: str
    qr_svg: str
    #: Printed small on the back, so a patient ringing the hospital can read
    #: out something that is not their MRN.
    support_phone: str = ""

    @property
    def printed_name(self) -> str:
        """
        The name as it goes on the card: surname in capitals.

        Surname first and capitalised because that is how a records clerk
        reads a card held up at a counter, and because Nigerian names
        frequently give no clue which part is the surname.
        """
        return f"{self.patient.last_name.upper()}, {self.patient.first_name}".strip(", ")


def payload_for(patient: Patient, *, slug: str) -> str:
    return f"{PAYLOAD_PREFIX}:{slug}:{patient.mrn}"


def qr_svg(payload: str, *, millimetres: int = QR_MILLIMETRES) -> str:
    """
    One QR code as an inline SVG fragment.

    The XML declaration is stripped and the width and height are set in
    millimetres with a unit-square viewBox, so the same string can be
    dropped into a web page (where CSS sizes it) and into a print
    stylesheet (where the millimetres are what matter).
    """
    code = qrcode.QRCode(
        version=None,  # smallest that fits
        error_correction=ERROR_CORRECTION,
        box_size=10,
        # No quiet zone from the library: the card's own padding provides it,
        # and a border baked into the image cannot be adjusted by the layout.
        border=0,
    )
    code.add_data(payload)
    code.make(fit=True)

    buffer = io.BytesIO()
    code.make_image(image_factory=SvgPathImage).save(buffer)
    svg = _XML_DECLARATION.sub("", buffer.getvalue().decode("utf-8")).strip()

    # The factory writes its own width and height in millimetres derived from
    # the module count, which is not the printed size we want.
    svg = re.sub(r'width="[^"]*"', f'width="{millimetres}mm"', svg, count=1)
    svg = re.sub(r'height="[^"]*"', f'height="{millimetres}mm"', svg, count=1)
    # Named for CSS, and told not to announce itself: the MRN is printed
    # beside it in text, so a screen reader reading out a QR path is noise.
    return svg.replace(
        "<svg ", '<svg class="curavia-qr" role="img" aria-label="Patient card code" ', 1
    )


def build_card(patient: Patient, *, support_phone: str = "") -> PatientCard:
    """
    Make the card for one patient.

    Refused for a record that has been merged away: issuing a card with a
    dead number on it, when the surviving record has its own, is how a
    patient ends up holding the wrong card for years. The old number still
    *resolves* — see ``find_by_mrn`` — but it is not what gets printed.

    Refused for a provisional record too. A card is an assertion that the
    hospital knows who this person is, and "Unknown Male 3" is the opposite
    of that.
    """
    if patient.merged_into_id is not None:
        raise CardError(
            "This record has been merged into another one. Print the card from that record."
        )
    if patient.is_provisional:
        raise CardError(
            "This record has no confirmed identity yet. Identify the patient first, "
            "then print their card."
        )

    organisation = patient.organisation
    branch = patient.home_branch
    return PatientCard(
        patient=patient,
        hospital_name=organisation.name,
        branch_name=branch.name if branch else "",
        payload=payload_for(patient, slug=organisation.slug),
        qr_svg=qr_svg(payload_for(patient, slug=organisation.slug)),
        support_phone=support_phone,
    )


def parse_payload(text: str) -> tuple[str, str] | None:
    """
    Read a scanned string back, or refuse it.

    Returns ``(slug, mrn)``. ``None`` for anything that is not a Curavia
    payload, which is most of what a scanner will be pointed at by mistake.

    The MRN itself contains slashes, so this splits on the first two colons
    only — ``CURAVIA:lifeline:MAIN/2026/000001``.
    """
    parts = (text or "").strip().split(":", 2)
    if len(parts) != 3 or parts[0].upper() != PAYLOAD_PREFIX:
        return None
    slug, mrn = parts[1].strip(), parts[2].strip()
    if not slug or not mrn:
        return None
    return slug, mrn


__all__ = [
    "CardError",
    "PatientCard",
    "build_card",
    "parse_payload",
    "payload_for",
    "qr_svg",
]
