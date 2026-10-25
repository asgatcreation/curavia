"""
Spreadsheet import.

Two halves. The reader is tested against the files hospitals actually send —
Excel-saved CSVs in cp1252, semicolon delimiters, blank rows in the middle,
numbers that arrive as 30.0 — and against files that are trying it on.

The importers are tested on the thing that matters most: **nothing is written
until commit**, and what commit writes is exactly what the preview showed.
"""

from __future__ import annotations

import io

import pytest
from django.urls import reverse

from apps.accounts.models import Invitation
from apps.catalogue.models import Payer, Price, Service
from apps.onboarding.importing import reader
from apps.onboarding.importing.base import suggest_mapping
from apps.onboarding.importing.reader import SpreadsheetError, read_upload
from apps.onboarding.importing.specs import (
    IMPORTS,
    SERVICES_IMPORT,
    guess_category,
    parse_money_to_kobo,
)
from integrations.email.fake import FakeEmailProvider

pytestmark = [pytest.mark.django_db]


@pytest.fixture(autouse=True)
def _empty_outbox() -> None:
    FakeEmailProvider.clear()


@pytest.fixture
def api_as_owner(api, hospital):
    user = hospital.staff("owner")
    api.force_authenticate(user=user)
    api.credentials(
        HTTP_X_CURAVIA_ORGANISATION=str(hospital.organisation.pk),
        HTTP_X_CURAVIA_BRANCH=str(hospital.main_branch.pk),
    )
    return api


def upload(content: bytes | str, name: str = "prices.csv"):
    data = content.encode("utf-8") if isinstance(content, str) else content
    handle = io.BytesIO(data)
    handle.name = name
    return handle


PRICE_CSV = (
    "Service Code,Description,Unit Price (N),Category\n"
    'FBC,Full blood count,"6,500",Laboratory\n'
    "GOPD-NEW,Consultation - new patient,5000,Consultation\n"
    "CXR,Chest X-ray,₦18000,\n"
)


# --------------------------------------------------------------------------
# Reading
# --------------------------------------------------------------------------


def test_a_plain_csv_is_read() -> None:
    sheet = read_upload(name="prices.csv", data=PRICE_CSV.encode("utf-8"))

    assert sheet.headers == ["Service Code", "Description", "Unit Price (N)", "Category"]
    assert len(sheet.rows) == 3
    assert sheet.rows[0][2] == "6,500"


def test_a_csv_saved_by_excel_on_windows_is_read() -> None:
    """
    The commonest file this will ever see.

    Excel on a Nigerian Windows machine saves cp1252 with a BOM. Decoding it
    as UTF-8 turns the naira sign into a replacement character and a surname
    into nonsense, so the order the encodings are tried in is load-bearing.
    """
    text = "Name,Price\nConsultation,₦5000\n"
    data = text.encode("utf-8-sig")
    sheet = read_upload(name="prices.csv", data=data)
    assert sheet.rows[0][1] == "₦5000"

    latin = "Name,Price\nCafé service,5000\n".encode("cp1252")
    sheet = read_upload(name="prices.csv", data=latin)
    assert sheet.rows[0][0] == "Café service"


def test_a_semicolon_delimited_csv_is_read() -> None:
    """A machine set to a European locale saves these, still called .csv."""
    sheet = read_upload(name="prices.csv", data=b"Name;Price\nConsultation;5000\n")
    assert sheet.headers == ["Name", "Price"]
    assert sheet.rows[0] == ["Consultation", "5000"]


def test_blank_rows_in_the_middle_are_dropped() -> None:
    sheet = read_upload(name="x.csv", data=b"Name,Price\nA,1\n,\nB,2\n")
    assert len(sheet.rows) == 2


def test_duplicate_column_names_are_made_unique() -> None:
    """Two columns called Price is common and is not an error."""
    sheet = read_upload(name="x.csv", data=b"Name,Price,Price\nA,1,2\n")
    assert sheet.headers == ["Name", "Price", "Price (2)"]


def test_a_nameless_column_still_gets_a_name() -> None:
    sheet = read_upload(name="x.csv", data=b"Name,,Price\nA,x,1\n")
    assert sheet.headers[1] == "Column 2"


def test_an_empty_file_is_refused() -> None:
    with pytest.raises(SpreadsheetError, match="empty"):
        read_upload(name="x.csv", data=b"")


def test_a_file_with_only_a_header_imports_nothing_without_failing() -> None:
    sheet = read_upload(name="x.csv", data=b"Name,Price\n")
    assert sheet.headers == ["Name", "Price"]
    assert sheet.rows == []


def test_the_old_excel_format_is_refused_with_instructions() -> None:
    with pytest.raises(SpreadsheetError, match="Save As"):
        read_upload(name="prices.xls", data=b"\xd0\xcf\x11\xe0")


def test_an_unknown_extension_is_refused() -> None:
    with pytest.raises(SpreadsheetError, match=r"\.csv or \.xlsx"):
        read_upload(name="prices.pdf", data=b"%PDF-1.7")


def test_a_file_that_is_not_really_a_spreadsheet_is_refused() -> None:
    """Renaming a PDF to .xlsx must fail clearly, not crash."""
    with pytest.raises(SpreadsheetError, match="could not be opened"):
        read_upload(name="prices.xlsx", data=b"%PDF-1.7 not a zip at all")


def test_an_oversized_file_is_refused_before_it_is_parsed() -> None:
    too_big = b"a" * (reader.MAX_UPLOAD_BYTES + 1)
    with pytest.raises(SpreadsheetError, match="limit is"):
        read_upload(name="x.csv", data=too_big)


def test_a_file_with_too_many_rows_is_truncated_and_says_so(monkeypatch) -> None:
    """
    The zip-bomb defence, and the honesty requirement beside it.

    A 2 KB XLSX can expand to gigabytes of XML, so the only real protection
    is to stop reading. Stopping silently would be worse than the bomb: a
    hospital would believe its whole price list had imported.
    """
    monkeypatch.setattr(reader, "MAX_ROWS", 3)
    body = "Name,Price\n" + "".join(f"Service {n},{n}00\n" for n in range(10))
    sheet = read_upload(name="x.csv", data=body.encode("utf-8"))

    assert len(sheet.rows) == 3
    assert sheet.truncated is True


def test_an_xlsx_is_read_and_whole_numbers_do_not_arrive_as_floats() -> None:
    """Excel stores every number as a float, so 30 arrives as 30.0."""
    openpyxl = pytest.importorskip("openpyxl")

    workbook = openpyxl.Workbook()
    sheet = workbook.active
    sheet.append(["Name", "Price", "Quantity"])
    sheet.append(["Full blood count", 6500, 30])
    buffer = io.BytesIO()
    workbook.save(buffer)

    parsed = read_upload(name="prices.xlsx", data=buffer.getvalue())
    assert parsed.headers == ["Name", "Price", "Quantity"]
    assert parsed.rows[0] == ["Full blood count", "6500", "30"]


# --------------------------------------------------------------------------
# Mapping
# --------------------------------------------------------------------------


def test_the_usual_column_names_are_matched_without_help() -> None:
    headers = ["Service Code", "Description", "Unit Price (N)", "Category"]
    mapping = suggest_mapping(SERVICES_IMPORT, headers)

    assert mapping["code"] == "Service Code"
    assert mapping["name"] == "Description"
    assert mapping["price"] == "Unit Price (N)"
    assert mapping["category"] == "Category"


def test_one_header_is_never_matched_to_two_columns() -> None:
    """
    "Price" could serve as both code and price under a loose match. It must
    be claimed once, or a mapping silently imports the price as the code.
    """
    mapping = suggest_mapping(SERVICES_IMPORT, ["Name", "Price"])
    used = list(mapping.values())
    assert len(used) == len(set(used))


def test_a_file_with_nothing_recognisable_suggests_nothing() -> None:
    """A wrong guess the person does not notice is worse than no guess."""
    mapping = suggest_mapping(SERVICES_IMPORT, ["A", "B", "C"])
    assert mapping == {}


# --------------------------------------------------------------------------
# Money
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("raw", "kobo"),
    [
        ("6500", 650_000),
        ("6,500", 650_000),
        ("6,500.50", 650_050),
        ("₦6,500", 650_000),
        ("N6500", 650_000),
        ("NGN 6500", 650_000),
        ("6\u00a0500", 650_000),
        ("0", 0),
    ],
)
def test_prices_are_read_the_way_people_write_them(raw: str, kobo: int) -> None:
    assert parse_money_to_kobo(raw, column="Price") == kobo


@pytest.mark.parametrize("raw", ["", "N/A", "free", "-500", "(500)", "abc"])
def test_a_price_that_cannot_be_read_fails_loudly(raw: str) -> None:
    """A price read as zero is a service given away."""
    with pytest.raises(ValueError):
        parse_money_to_kobo(raw, column="Price")


def test_a_category_is_guessed_from_the_name_when_the_file_has_none() -> None:
    assert guess_category("Chest X-ray", "") == "radiology"
    assert guess_category("Malaria parasite test", "") == "laboratory"
    assert guess_category("Consultation - new patient", "") == "consultation"
    assert guess_category("Something unusual", "") == "other"


def test_a_stated_category_beats_the_guess() -> None:
    assert guess_category("Chest X-ray", "laboratory") == "laboratory"


# --------------------------------------------------------------------------
# The three steps, through the API
# --------------------------------------------------------------------------


def test_analyse_says_what_is_in_the_file(api_as_owner) -> None:
    response = api_as_owner.post(
        reverse("api:v1:import-analyse", kwargs={"kind": "services"}),
        {"file": upload(PRICE_CSV)},
        format="multipart",
    )

    assert response.status_code == 200
    assert response.data["row_count"] == 3
    assert response.data["suggested_mapping"]["name"] == "Description"
    assert len(response.data["sample"]) == 3


def test_preview_writes_nothing(api_as_owner, hospital) -> None:
    """The guarantee the whole three-step shape exists for."""
    import json

    response = api_as_owner.post(
        reverse("api:v1:import-preview", kwargs={"kind": "services"}),
        {
            "file": upload(PRICE_CSV),
            "mapping": json.dumps(
                {
                    "code": "Service Code",
                    "name": "Description",
                    "price": "Unit Price (N)",
                    "category": "Category",
                }
            ),
        },
        format="multipart",
    )

    assert response.status_code == 200
    assert response.data["ok"] == 3
    assert response.data["failed"] == 0
    with hospital.scope():
        assert Service.objects.count() == 0
        assert Price.objects.count() == 0


def test_commit_writes_exactly_what_the_preview_showed(api_as_owner, hospital) -> None:
    import json

    mapping = json.dumps({"code": "Service Code", "name": "Description", "price": "Unit Price (N)"})
    preview = api_as_owner.post(
        reverse("api:v1:import-preview", kwargs={"kind": "services"}),
        {"file": upload(PRICE_CSV), "mapping": mapping},
        format="multipart",
    )
    commit = api_as_owner.post(
        reverse("api:v1:import-commit", kwargs={"kind": "services"}),
        {"file": upload(PRICE_CSV), "mapping": mapping},
        format="multipart",
    )

    assert commit.status_code == 200
    assert commit.data["created"] == preview.data["ok"] == 3

    with hospital.scope():
        fbc = Service.objects.get(code="FBC")
        self_pay = Payer.objects.get(kind=Payer.Kind.SELF)
        assert Price.objects.get(service=fbc, payer=self_pay).amount_kobo == 650_000
        # ₦18000 with the naira sign, and no category column for that row.
        assert Service.objects.get(code="CXR").category == "radiology"


def test_a_bad_row_is_named_and_the_rest_still_import(api_as_owner, hospital) -> None:
    """
    A 900-line price list with three bad rows needs to know which three.
    """
    import json

    body = (
        "Code,Name,Price\n"
        "FBC,Full blood count,6500\n"
        "BAD,Something,not a price\n"
        "CXR,Chest X-ray,18000\n"
    )
    mapping = json.dumps({"code": "Code", "name": "Name", "price": "Price"})

    response = api_as_owner.post(
        reverse("api:v1:import-commit", kwargs={"kind": "services"}),
        {"file": upload(body), "mapping": mapping},
        format="multipart",
    )

    assert response.status_code == 200
    assert response.data["created"] == 2
    assert response.data["skipped"] == 1
    error = response.data["errors"][0]
    # Row 3 as Excel numbers it: the header is row 1.
    assert error["row_number"] == 3
    assert error["column"] == "Price"

    with hospital.scope():
        assert Service.objects.count() == 2


def test_running_an_import_twice_updates_rather_than_duplicating(api_as_owner, hospital) -> None:
    import json

    mapping = json.dumps({"code": "Service Code", "name": "Description", "price": "Unit Price (N)"})
    url = reverse("api:v1:import-commit", kwargs={"kind": "services"})
    api_as_owner.post(url, {"file": upload(PRICE_CSV), "mapping": mapping}, format="multipart")
    second = api_as_owner.post(
        url, {"file": upload(PRICE_CSV), "mapping": mapping}, format="multipart"
    )

    assert second.data["created"] == 0
    assert second.data["updated"] == 3
    with hospital.scope():
        assert Service.objects.count() == 3


def test_a_missing_required_column_stops_the_import(api_as_owner) -> None:
    import json

    response = api_as_owner.post(
        reverse("api:v1:import-preview", kwargs={"kind": "services"}),
        {"file": upload(PRICE_CSV), "mapping": json.dumps({"name": "Description"})},
        format="multipart",
    )

    assert response.status_code == 200
    assert response.data["ok"] == 0
    assert "Price" in response.data["errors"][0]["message"]


def test_a_file_with_nothing_importable_refuses_rather_than_reporting_success(
    api_as_owner,
) -> None:
    import json

    response = api_as_owner.post(
        reverse("api:v1:import-commit", kwargs={"kind": "services"}),
        {
            "file": upload("Code,Name,Price\nA,Thing,rubbish\n"),
            "mapping": json.dumps({"code": "Code", "name": "Name", "price": "Price"}),
        },
        format="multipart",
    )

    assert response.status_code == 400
    assert response.data["code"] == "nothing_to_import"


def test_uploading_no_file_is_a_clear_error(api_as_owner) -> None:
    response = api_as_owner.post(
        reverse("api:v1:import-analyse", kwargs={"kind": "services"}), {}, format="multipart"
    )
    assert response.status_code == 400
    assert response.data["code"] == "no_file"


def test_an_unknown_import_kind_is_refused(api_as_owner) -> None:
    response = api_as_owner.post(
        reverse("api:v1:import-analyse", kwargs={"kind": "patients"}),
        {"file": upload(PRICE_CSV)},
        format="multipart",
    )
    assert response.status_code == 400
    assert response.data["code"] == "unknown_import"


# --------------------------------------------------------------------------
# Permissions
# --------------------------------------------------------------------------


def test_a_cashier_cannot_import_a_price_list(api, hospital) -> None:
    api.force_authenticate(user=hospital.staff("cashier"))
    api.credentials(
        HTTP_X_CURAVIA_ORGANISATION=str(hospital.organisation.pk),
        HTTP_X_CURAVIA_BRANCH=str(hospital.main_branch.pk),
    )
    response = api.post(
        reverse("api:v1:import-analyse", kwargs={"kind": "services"}),
        {"file": upload(PRICE_CSV)},
        format="multipart",
    )
    assert response.status_code == 403


def test_importing_a_price_list_does_not_let_you_import_staff(api, hospital) -> None:
    """
    An import is not a separate kind of power.

    Somebody who may manage prices has no business inviting forty people
    because the file happened to arrive as a spreadsheet.
    """
    import json

    user = hospital.staff("accountant")
    api.force_authenticate(user=user)
    api.credentials(
        HTTP_X_CURAVIA_ORGANISATION=str(hospital.organisation.pk),
        HTTP_X_CURAVIA_BRANCH=str(hospital.main_branch.pk),
    )

    staff_csv = "Email,Role\nnew@example.test,Nurse\n"
    response = api.post(
        reverse("api:v1:import-commit", kwargs={"kind": "staff"}),
        {
            "file": upload(staff_csv),
            "mapping": json.dumps({"email": "Email", "role": "Role"}),
        },
        format="multipart",
    )
    assert response.status_code == 403
    assert not Invitation.unscoped.filter(email="new@example.test").exists()


def test_the_catalogue_only_lists_imports_you_may_run(api, hospital) -> None:
    api.force_authenticate(user=hospital.staff("hr_officer"))
    api.credentials(
        HTTP_X_CURAVIA_ORGANISATION=str(hospital.organisation.pk),
        HTTP_X_CURAVIA_BRANCH=str(hospital.main_branch.pk),
    )
    kinds = {row["kind"] for row in api.get(reverse("api:v1:import-catalogue")).data}
    assert "services" not in kinds


# --------------------------------------------------------------------------
# Staff import
# --------------------------------------------------------------------------


def test_staff_are_imported_as_invitations(api_as_owner, hospital) -> None:
    import json

    body = (
        "Email,First name,Surname,Role,Job title\n"
        "amarachi@example.test,Amarachi,Eze,Nurse,Staff nurse\n"
        "yusuf@example.test,Yusuf,Abubakar,Doctor,Medical officer\n"
    )
    response = api_as_owner.post(
        reverse("api:v1:import-commit", kwargs={"kind": "staff"}),
        {
            "file": upload(body),
            "mapping": json.dumps(
                {
                    "email": "Email",
                    "first_name": "First name",
                    "last_name": "Surname",
                    "role": "Role",
                    "job_title": "Job title",
                }
            ),
        },
        format="multipart",
    )

    assert response.status_code == 200
    assert response.data["created"] == 2
    assert len(FakeEmailProvider.sent) == 2
    with hospital.scope():
        assert Invitation.objects.filter(email="amarachi@example.test").exists()


def test_owner_access_cannot_be_granted_by_spreadsheet(api_as_owner, hospital) -> None:
    """
    The single worst thing this feature could do.

    A file that silently makes forty people owners is not recoverable by the
    hospital. One at a time, from the staff screen, deliberately.
    """
    import json

    body = "Email,Role\nsneaky@example.test,Owner / Medical director\n"
    response = api_as_owner.post(
        reverse("api:v1:import-commit", kwargs={"kind": "staff"}),
        {"file": upload(body), "mapping": json.dumps({"email": "Email", "role": "Role"})},
        format="multipart",
    )

    assert response.status_code == 400
    assert "owner access cannot be granted by import" in str(response.data).lower()
    with hospital.scope():
        assert not Invitation.objects.filter(email="sneaky@example.test").exists()


def test_a_role_that_does_not_exist_is_named_in_the_error(api_as_owner) -> None:
    import json

    body = "Email,Role\nx@example.test,Chief Wizard\n"
    response = api_as_owner.post(
        reverse("api:v1:import-preview", kwargs={"kind": "staff"}),
        {"file": upload(body), "mapping": json.dumps({"email": "Email", "role": "Role"})},
        format="multipart",
    )

    assert response.data["failed"] == 1
    assert "Chief Wizard" in response.data["errors"][0]["message"]


def test_a_role_from_another_hospital_is_not_found(api_as_owner, other_hospital) -> None:
    """
    Roles are looked up by NAME inside this hospital's own scope, so another
    hospital's role is simply not there — which is the behaviour, not a
    special case.
    """
    import json

    with other_hospital.scope():
        from apps.accounts.models import Role

        Role.objects.filter(slug="nurse").update(name="Their Special Role")

    body = "Email,Role\nx@example.test,Their Special Role\n"
    response = api_as_owner.post(
        reverse("api:v1:import-preview", kwargs={"kind": "staff"}),
        {"file": upload(body), "mapping": json.dumps({"email": "Email", "role": "Role"})},
        format="multipart",
    )
    assert response.data["failed"] == 1


def test_somebody_already_on_staff_is_skipped_with_a_reason(api_as_owner, hospital) -> None:
    import json

    existing = hospital.staff("nurse")
    body = f"Email,Role\n{existing.email},Nurse\n"
    response = api_as_owner.post(
        reverse("api:v1:import-preview", kwargs={"kind": "staff"}),
        {"file": upload(body), "mapping": json.dumps({"email": "Email", "role": "Role"})},
        format="multipart",
    )

    assert response.data["failed"] == 1
    assert "already on your staff list" in response.data["errors"][0]["message"]


# --------------------------------------------------------------------------
# Payers
# --------------------------------------------------------------------------


def test_payers_are_imported_without_tariffs(api_as_owner, hospital) -> None:
    import json

    body = "Name,Code,Type\nHygeia HMO,HYGEIA,HMO\nDangote Group,DANG,Corporate\n"
    response = api_as_owner.post(
        reverse("api:v1:import-commit", kwargs={"kind": "payers"}),
        {
            "file": upload(body),
            "mapping": json.dumps({"name": "Name", "code": "Code", "kind": "Type"}),
        },
        format="multipart",
    )

    assert response.status_code == 200
    assert response.data["created"] == 2
    with hospital.scope():
        assert Payer.objects.get(code="DANG").kind == Payer.Kind.CORPORATE
        assert not Price.objects.filter(payer__code="HYGEIA").exists()


def test_a_second_self_pay_payer_cannot_be_imported(api_as_owner) -> None:
    import json

    body = "Name,Type\nCash patients,self\n"
    response = api_as_owner.post(
        reverse("api:v1:import-preview", kwargs={"kind": "payers"}),
        {"file": upload(body), "mapping": json.dumps({"name": "Name", "kind": "Type"})},
        format="multipart",
    )
    assert response.data["failed"] == 1


# --------------------------------------------------------------------------
# Tenancy
# --------------------------------------------------------------------------


def test_an_import_writes_only_into_the_callers_hospital(
    api_as_owner, hospital, other_hospital
) -> None:
    import json

    api_as_owner.post(
        reverse("api:v1:import-commit", kwargs={"kind": "services"}),
        {
            "file": upload(PRICE_CSV),
            "mapping": json.dumps(
                {"code": "Service Code", "name": "Description", "price": "Unit Price (N)"}
            ),
        },
        format="multipart",
    )

    with hospital.scope():
        assert Service.objects.count() == 3
    with other_hospital.scope():
        assert Service.objects.count() == 0


def test_every_import_declares_a_permission() -> None:
    """
    A new importer with no entry in REQUIRED_PERMISSION would fall through
    the gate and be runnable by anybody signed in.
    """
    from apps.onboarding.api.import_views import REQUIRED_PERMISSION

    missing = sorted(set(IMPORTS) - set(REQUIRED_PERMISSION))
    assert not missing, (
        f"These imports have no required permission and would be open to any "
        f"signed-in user: {missing}"
    )

    from apps.core.permissions import ALL_PERMISSION_CODES

    unknown = sorted(set(REQUIRED_PERMISSION.values()) - ALL_PERMISSION_CODES)
    assert not unknown, f"REQUIRED_PERMISSION names codes that do not exist: {unknown}"
