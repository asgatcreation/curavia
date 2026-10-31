"""
The encounter, the live board, and the television in the waiting room.

Every name here is synthetic.

Three things carry most of the weight:

* **The board is never the only path.** Every live update has an HTTP
  equivalent, because a socket that drops silently leaves a screen showing
  patients who left an hour ago — and people believe a board.
* **A television never learns a name.** The display payload is built by one
  function, and these tests assert that no patient name reaches it however
  the data is arranged.
* **WebSocket authentication actually works for this app**, which it did not
  before this milestone: Channels reads a Django session and sign-in issues
  JWTs, so every socket the real staff app opened would have closed with
  4401.
"""

from __future__ import annotations

import datetime as dt

import pytest
from channels.layers import get_channel_layer
from channels.testing import WebsocketCommunicator
from django.core.cache import cache
from django.urls import reverse
from django.utils import timezone

from apps.core.audit import AuditEvent
from apps.core.models import Department
from apps.core.tenancy import tenant_scope, unscoped
from apps.core.ws_tickets import TICKET_MAX_AGE, issue_ticket, redeem_ticket
from apps.journey import service
from apps.journey.models import DisplayScreen, Encounter, JourneyEvent, Stage
from apps.patients.models import Patient, Sex
from apps.patients.registration import RegistrationInput, register_patient
from apps.scheduling import booking
from apps.scheduling.models import Clinic, QueueTicket

pytestmark = [pytest.mark.django_db]


# --------------------------------------------------------------------------
# Scaffolding
# --------------------------------------------------------------------------


def make_patient(hospital, **overrides) -> Patient:
    defaults = {
        "first_name": "Ngozi",
        "last_name": "Balogun",
        "sex": Sex.FEMALE,
        "phone": "08031234567",
        "date_of_birth": dt.date(1988, 7, 11),
    }
    defaults.update(overrides)
    with hospital.scope():
        return register_patient(
            organisation=hospital.organisation,
            branch=hospital.main_branch,
            data=RegistrationInput(**defaults),
        )


@pytest.fixture
def department(hospital) -> Department:
    with hospital.scope():
        found = Department.objects.filter(kind=Department.Kind.CLINIC).first()
        assert found is not None
        return found


@pytest.fixture
def api_as_nurse(api, hospital):
    """A nurse holds journey.view and queue.manage."""
    user = hospital.staff("nurse")
    api.force_authenticate(user=user)
    api.credentials(
        HTTP_X_CURAVIA_ORGANISATION=str(hospital.organisation.pk),
        HTTP_X_CURAVIA_BRANCH=str(hospital.main_branch.pk),
    )
    return api


def arrive(hospital, department, **overrides) -> Encounter:
    patient = make_patient(hospital, **overrides)
    with hospital.scope():
        return service.start_encounter(
            patient=patient, branch=hospital.main_branch, department=department
        )


# --------------------------------------------------------------------------
# The encounter
# --------------------------------------------------------------------------


def test_a_visit_starts_at_arrived_with_an_event(hospital, department) -> None:
    encounter = arrive(hospital, department)
    with hospital.scope():
        events = list(encounter.events.all())

    assert encounter.stage == Stage.ARRIVED
    assert encounter.status == Encounter.Status.OPEN
    assert [event.to_stage for event in events] == [Stage.ARRIVED]
    assert events[0].from_stage == ""


def test_checking_the_same_patient_in_twice_is_one_visit(hospital, department) -> None:
    """
    Two clerks checking the same person in is a normal Tuesday. Two rows on
    the board is how a patient gets seen twice and billed twice.
    """
    patient = make_patient(hospital)
    with hospital.scope():
        first = service.start_encounter(
            patient=patient, branch=hospital.main_branch, department=department
        )
        second = service.start_encounter(
            patient=patient, branch=hospital.main_branch, department=department
        )
        assert first.pk == second.pk
        assert Encounter.objects.filter(patient=patient, status="open").count() == 1


def test_moving_records_how_long_the_previous_stage_took(hospital, department) -> None:
    """
    Written at the moment of the move, not recomputed later. It is what
    "the patient waited two hours before anybody saw them" is built from.
    """
    encounter = arrive(hospital, department)
    with hospital.scope():
        Encounter.objects.filter(pk=encounter.pk).update(
            stage_since=timezone.now() - dt.timedelta(minutes=47)
        )
        encounter.refresh_from_db()
        service.move_to(encounter, Stage.IN_TRIAGE)
        event = encounter.events.order_by("-at").first()

    assert event is not None
    assert event.from_stage == Stage.ARRIVED
    assert event.to_stage == Stage.IN_TRIAGE
    assert event.minutes_in_previous_stage == 47
    assert event.previous_was_waiting is True


def test_time_being_seen_is_not_counted_as_waiting(hospital, department) -> None:
    """
    Twenty minutes with the doctor is not twenty minutes of waiting, and a
    board that adds them together tells management the opposite of the truth.
    """
    encounter = arrive(hospital, department)
    with hospital.scope():
        service.move_to(encounter, Stage.WITH_DOCTOR)
        Encounter.objects.filter(pk=encounter.pk).update(
            stage_since=timezone.now() - dt.timedelta(minutes=20)
        )
        encounter.refresh_from_db()
        service.move_to(encounter, Stage.PHARMACY)
        event = encounter.events.order_by("-at").first()

    assert event is not None
    assert event.minutes_in_previous_stage == 20
    assert event.previous_was_waiting is False, "being seen was counted as waiting"


def test_pressing_the_same_button_twice_does_not_add_an_event(hospital, department) -> None:
    encounter = arrive(hospital, department)
    with hospital.scope():
        service.move_to(encounter, Stage.TRIAGE)
        service.move_to(encounter, Stage.TRIAGE)
        assert encounter.events.filter(to_stage=Stage.TRIAGE).count() == 1


def test_finishing_closes_the_visit(hospital, department) -> None:
    encounter = arrive(hospital, department)
    with hospital.scope():
        service.close_encounter(encounter)
        encounter.refresh_from_db()

    assert encounter.status == Encounter.Status.CLOSED
    assert encounter.ended_at is not None
    assert encounter.stage == Stage.DONE


def test_leaving_without_being_seen_is_its_own_ending(hospital, department) -> None:
    """
    "Finished" and "gave up and went home" mean opposite things about a
    hospital, and a report that cannot tell them apart is worse than none.
    """
    encounter = arrive(hospital, department)
    with hospital.scope():
        service.abandon_encounter(encounter)
        encounter.refresh_from_db()

    assert encounter.stage == Stage.LEFT
    assert encounter.status == Encounter.Status.CLOSED


def test_a_closed_visit_cannot_be_moved(hospital, department) -> None:
    encounter = arrive(hospital, department)
    with hospital.scope():
        service.close_encounter(encounter)
        with pytest.raises(service.JourneyError, match="closed"):
            service.move_to(encounter, Stage.PHARMACY)


def test_an_invented_stage_is_refused(hospital, department) -> None:
    encounter = arrive(hospital, department)
    with hospital.scope(), pytest.raises(service.JourneyError, match="no stage called"):
        service.move_to(encounter, "teleported")


def test_a_merged_away_record_cannot_start_a_visit(hospital, department) -> None:
    from apps.patients.merge import merge_patients

    keeper = make_patient(hospital)
    duplicate = make_patient(hospital, last_name="Balogun-Smith")
    with hospital.scope():
        merge_patients(winner=keeper, loser=duplicate, reason="same person")
        duplicate.refresh_from_db()
        with pytest.raises(service.JourneyError, match="merged into another"):
            service.start_encounter(patient=duplicate, branch=hospital.main_branch)


def test_checking_in_at_the_desk_opens_a_visit(hospital, department) -> None:
    """
    Arriving is the start of a visit, so the encounter opens there rather
    than being a separate thing somebody has to remember to press.
    """
    patient = make_patient(hospital)
    with hospital.scope():
        ticket = booking.check_in(
            patient=patient, department=department, branch=hospital.main_branch
        )
        encounter = Encounter.objects.filter(patient=patient, status="open").first()

    assert encounter is not None
    assert encounter.ticket_id == ticket.pk
    assert encounter.stage == Stage.ARRIVED


# --------------------------------------------------------------------------
# The board
# --------------------------------------------------------------------------


def test_the_board_lists_the_longest_wait_first(hospital, department) -> None:
    """
    The board exists to answer "who has been standing there longest". A list
    that has to be scanned to answer that is a list nobody uses.
    """
    first = arrive(hospital, department, last_name="Early")
    second = arrive(hospital, department, last_name="Later")
    with hospital.scope():
        Encounter.objects.filter(pk=second.pk).update(
            stage_since=timezone.now() - dt.timedelta(hours=2)
        )
        rows = service.board_rows(hospital.main_branch.pk)

    assert [row["id"] for row in rows] == [str(second.pk), str(first.pk)]
    assert rows[0]["minutes_in_stage"] >= 119


def test_a_finished_visit_leaves_the_board(hospital, department) -> None:
    encounter = arrive(hospital, department)
    with hospital.scope():
        assert len(service.board_rows(hospital.main_branch.pk)) == 1
        service.close_encounter(encounter)
        assert service.board_rows(hospital.main_branch.pk) == []


def test_the_board_endpoint_matches_the_service(api_as_nurse, hospital, department) -> None:
    arrive(hospital, department)
    response = api_as_nurse.get(reverse("api:v1:journey-board"))

    assert response.status_code == 200, response.data
    assert len(response.data["rows"]) == 1
    assert response.data["rows"][0]["stage"] == Stage.ARRIVED


def test_reading_the_board_is_audited_as_a_bulk_read(api_as_nurse, hospital, department) -> None:
    """It is every patient in the building, by name."""
    arrive(hospital, department)
    api_as_nurse.get(reverse("api:v1:journey-board"))

    event = AuditEvent.objects.filter(action="journey.board.view").first()
    assert event is not None
    assert "bulk" in event.flags


def test_a_store_keeper_cannot_see_the_board(api, hospital, department) -> None:
    """They never meet a patient."""
    arrive(hospital, department)
    api.force_authenticate(user=hospital.staff("store_keeper"))
    api.credentials(HTTP_X_CURAVIA_ORGANISATION=str(hospital.organisation.pk))
    assert api.get(reverse("api:v1:journey-board")).status_code == 403


def test_moving_through_the_api_records_the_journey(api_as_nurse, hospital, department) -> None:
    encounter = arrive(hospital, department)

    moved = api_as_nurse.post(
        reverse("api:v1:journey-move", args=[encounter.pk]),
        {"stage": Stage.WITH_DOCTOR, "note": "Seen by Dr Ilesanmi"},
        format="json",
    )
    assert moved.status_code == 200, moved.data
    assert moved.data["stage"] == Stage.WITH_DOCTOR

    events = api_as_nurse.get(reverse("api:v1:journey-events", args=[encounter.pk]))
    assert events.status_code == 200
    assert [row["to_stage"] for row in events.data["events"]] == [
        Stage.ARRIVED,
        Stage.WITH_DOCTOR,
    ]
    assert AuditEvent.objects.filter(action="journey.move").exists()


def test_the_journey_of_one_visit_adds_up_the_waiting(api_as_nurse, hospital, department) -> None:
    encounter = arrive(hospital, department)
    with hospital.scope():
        Encounter.objects.filter(pk=encounter.pk).update(
            stage_since=timezone.now() - dt.timedelta(minutes=30)
        )
        encounter.refresh_from_db()
        service.move_to(encounter, Stage.WITH_DOCTOR)
        Encounter.objects.filter(pk=encounter.pk).update(
            stage_since=timezone.now() - dt.timedelta(minutes=10)
        )
        encounter.refresh_from_db()
        service.move_to(encounter, Stage.PHARMACY)

    response = api_as_nurse.get(reverse("api:v1:journey-events", args=[encounter.pk]))
    # 30 minutes waiting to be seen; the 10 with the doctor do not count.
    assert response.data["minutes_waiting"] == 30


def test_the_board_is_scoped_to_one_hospital(hospital, other_hospital, department) -> None:
    arrive(hospital, department)
    with other_hospital.scope():
        assert Encounter.objects.count() == 0
        assert service.board_rows(other_hospital.main_branch.pk) == []


# --------------------------------------------------------------------------
# WebSocket tickets
# --------------------------------------------------------------------------


def test_a_ticket_can_be_spent_once(hospital) -> None:
    """
    Single use, so a ticket that leaks into an access log is already gone.
    """
    cache.clear()
    user = hospital.staff("nurse")
    ticket, seconds = issue_ticket(user)

    assert seconds == TICKET_MAX_AGE
    assert redeem_ticket(ticket) == str(user.pk)
    assert redeem_ticket(ticket) is None, "a ticket was spent twice"


def test_a_tampered_ticket_is_refused(hospital) -> None:
    cache.clear()
    ticket, _ = issue_ticket(hospital.staff("nurse"))
    assert redeem_ticket(ticket + "x") is None
    assert redeem_ticket("") is None
    assert redeem_ticket("nonsense") is None


def test_a_ticket_whose_nonce_is_gone_is_refused(hospital) -> None:
    """
    Fails closed. An unreplayable ticket that occasionally makes somebody
    reconnect beats a replayable one that never does.
    """
    cache.clear()
    ticket, _ = issue_ticket(hospital.staff("nurse"))
    cache.clear()
    assert redeem_ticket(ticket) is None


def test_the_ticket_endpoint_needs_a_signed_in_user(api, hospital) -> None:
    assert api.post(reverse("api:v1:ws-ticket")).status_code in (401, 403)

    api.force_authenticate(user=hospital.staff("nurse"))
    api.credentials(HTTP_X_CURAVIA_ORGANISATION=str(hospital.organisation.pk))
    response = api.post(reverse("api:v1:ws-ticket"))
    assert response.status_code == 200
    assert response.data["expires_in"] == TICKET_MAX_AGE
    assert response.data["ticket"]


# --------------------------------------------------------------------------
# The sockets
# --------------------------------------------------------------------------


#: Every socket here is opened with an Origin header, because the real stack
#: is wrapped in ``AllowedHostsOriginValidator`` and that refuses a
#: connection that sends none. That is the cross-site-hijacking protection
#: doing its job — a browser always sends Origin, and a script pointed at
#: somebody else's hospital sends the wrong one — so a test that omitted it
#: would be testing a path no browser takes.
BROWSER_HEADERS = [(b"origin", b"http://testserver")]


async def connect_board(hospital, user):
    """Open the board socket the way the browser does: with a ticket."""
    from channels.db import database_sync_to_async

    from config.asgi import application

    ticket, _ = await database_sync_to_async(issue_ticket)(user)
    communicator = WebsocketCommunicator(
        application, f"/ws/journey/?ticket={ticket}", headers=BROWSER_HEADERS
    )
    connected, _ = await communicator.connect()
    return communicator, connected


@pytest.mark.asyncio
@pytest.mark.django_db(transaction=True)
async def test_the_board_socket_authenticates_with_a_ticket(hospital, department) -> None:
    """
    The bug this milestone had to fix first: Channels resolves the user from
    a Django session, and Curavia's sign-in issues JWTs and makes no
    session, so every socket the staff app opened closed with 4401.
    """
    from channels.db import database_sync_to_async

    cache.clear()
    user = await database_sync_to_async(hospital.staff)("nurse")
    communicator, connected = await connect_board(hospital, user)

    assert connected, "the board socket refused a valid ticket"
    ready = await communicator.receive_json_from(timeout=5)
    assert ready["type"] == "ready"
    snapshot = await communicator.receive_json_from(timeout=5)
    assert snapshot["type"] == "snapshot"
    await communicator.disconnect()


@pytest.mark.asyncio
@pytest.mark.django_db(transaction=True)
async def test_the_board_socket_refuses_a_connection_with_no_ticket(hospital) -> None:
    from config.asgi import application

    communicator = WebsocketCommunicator(application, "/ws/journey/", headers=BROWSER_HEADERS)
    connected, code = await communicator.connect()
    assert not connected
    assert code == 4401
    await communicator.disconnect()


@pytest.mark.asyncio
@pytest.mark.django_db(transaction=True)
async def test_a_role_without_journey_view_cannot_open_the_board(hospital) -> None:
    from channels.db import database_sync_to_async

    cache.clear()
    user = await database_sync_to_async(hospital.staff)("store_keeper")
    communicator, connected = await connect_board(hospital, user)
    assert not connected
    await communicator.disconnect()


@pytest.mark.asyncio
@pytest.mark.django_db(transaction=True)
async def test_the_snapshot_carries_everybody_in_the_building(hospital, department) -> None:
    """
    A full snapshot on connect, not only deltas. A client that missed a
    message while reconnecting would otherwise be wrong until the next
    change — which in a quiet pharmacy could be twenty minutes.
    """
    from channels.db import database_sync_to_async

    cache.clear()
    user = await database_sync_to_async(hospital.staff)("nurse")
    await database_sync_to_async(arrive)(hospital, department)

    communicator, connected = await connect_board(hospital, user)
    assert connected
    await communicator.receive_json_from(timeout=5)  # ready
    snapshot = await communicator.receive_json_from(timeout=5)

    assert len(snapshot["rows"]) == 1
    assert snapshot["rows"][0]["stage"] == Stage.ARRIVED
    await communicator.disconnect()


@pytest.mark.asyncio
@pytest.mark.django_db(transaction=True)
async def test_a_move_reaches_an_open_board(hospital, department) -> None:
    """The showpiece: somebody presses a button and every board changes."""
    from channels.db import database_sync_to_async

    cache.clear()
    user = await database_sync_to_async(hospital.staff)("nurse")
    encounter = await database_sync_to_async(arrive)(hospital, department)

    communicator, connected = await connect_board(hospital, user)
    assert connected
    await communicator.receive_json_from(timeout=5)  # ready
    await communicator.receive_json_from(timeout=5)  # snapshot

    def move():
        with tenant_scope(hospital.organisation.pk, hospital.main_branch.pk):
            service.move_to(encounter, Stage.WITH_DOCTOR)

    await database_sync_to_async(move)()

    update = await communicator.receive_json_from(timeout=5)
    assert update["event"] == "journey"
    assert update["row"]["stage"] == Stage.WITH_DOCTOR
    await communicator.disconnect()


@pytest.mark.asyncio
@pytest.mark.django_db(transaction=True)
async def test_a_board_can_ask_for_a_fresh_snapshot(hospital, department) -> None:
    """What the client sends after a reconnect, rather than trusting deltas."""
    from channels.db import database_sync_to_async

    cache.clear()
    user = await database_sync_to_async(hospital.staff)("nurse")
    communicator, connected = await connect_board(hospital, user)
    assert connected
    await communicator.receive_json_from(timeout=5)
    await communicator.receive_json_from(timeout=5)

    await communicator.send_json_to({"type": "refresh"})
    again = await communicator.receive_json_from(timeout=5)
    assert again["type"] == "snapshot"
    await communicator.disconnect()


# --------------------------------------------------------------------------
# The television
# --------------------------------------------------------------------------


@pytest.fixture
def screen(hospital, department) -> DisplayScreen:
    with hospital.scope():
        return DisplayScreen.objects.create(
            organisation=hospital.organisation,
            branch=hospital.main_branch,
            name="Main reception",
            footer="Please keep your number until you are called.",
        )


def test_a_display_payload_never_contains_a_patient_name(hospital, department, screen) -> None:
    """
    The rule the whole DisplayScreen design exists for. A waiting-room
    television is watched by everybody in the room, including people who do
    not work here.
    """
    patient = make_patient(hospital, first_name="Chiamaka", last_name="Nwachukwu")
    with hospital.scope():
        ticket = booking.check_in(
            patient=patient, department=department, branch=hospital.main_branch
        )
        booking.call_next(department=department, branch=hospital.main_branch, called_to="Room 3")
        payload = service.display_rows(screen)

    blob = repr(payload)
    assert "Chiamaka" not in blob
    assert "Nwachukwu" not in blob
    assert patient.mrn not in blob
    assert str(patient.pk) not in blob
    assert ticket.label in blob, "the television should show the number"
    assert "Room 3" in blob


def test_a_display_shows_only_its_own_departments(hospital, department, screen) -> None:
    """The pharmacy television should not be calling the antenatal queue."""
    with hospital.scope():
        other = Department.objects.create(
            organisation=hospital.organisation,
            branch=hospital.main_branch,
            name="Pharmacy",
            code="PHA",
            kind=Department.Kind.PHARMACY,
        )
        booking.check_in(
            patient=make_patient(hospital, last_name="One"),
            department=department,
            branch=hospital.main_branch,
        )
        booking.check_in(
            patient=make_patient(hospital, last_name="Two"),
            department=other,
            branch=hospital.main_branch,
        )

        screen.departments.set([other])
        payload = service.display_rows(screen)

    assert [row["department"] for row in payload["waiting"]] == ["Pharmacy"]


def test_a_display_with_no_departments_shows_the_whole_branch(hospital, department, screen) -> None:
    with hospital.scope():
        booking.check_in(
            patient=make_patient(hospital),
            department=department,
            branch=hospital.main_branch,
        )
        payload = service.display_rows(screen)

    assert payload["waiting"][0]["count"] == 1


def test_the_public_display_endpoint_needs_no_sign_in(api, hospital, department, screen) -> None:
    """A television has no account."""
    with hospital.scope():
        booking.check_in(
            patient=make_patient(hospital),
            department=department,
            branch=hospital.main_branch,
        )

    response = api.get(reverse("api:v1:display-public", args=[screen.token]))
    assert response.status_code == 200, response.data
    assert response.data["screen"] == "Main reception"
    assert response.data["footer"].startswith("Please keep")


def test_an_unknown_screen_token_is_refused(api) -> None:
    response = api.get(reverse("api:v1:display-public", args=["not-a-real-token"]))
    assert response.status_code == 404


def test_rotating_a_token_kills_the_old_url(api, hospital, screen) -> None:
    """Used when a screen is moved, or when somebody photographs the address bar."""
    old = screen.token
    api.force_authenticate(user=hospital.staff("facility_admin"))
    api.credentials(
        HTTP_X_CURAVIA_ORGANISATION=str(hospital.organisation.pk),
        HTTP_X_CURAVIA_BRANCH=str(hospital.main_branch.pk),
    )
    response = api.post(reverse("api:v1:display-token", args=[screen.pk, "rotate"]))

    assert response.status_code == 200, response.data
    assert api.get(reverse("api:v1:display-public", args=[old])).status_code == 404
    with hospital.scope():
        screen.refresh_from_db()
    assert api.get(reverse("api:v1:display-public", args=[screen.token])).status_code == 200


def test_the_screen_list_does_not_leak_the_tokens(api, hospital, screen) -> None:
    """
    A screen's URL is a credential that needs no password, so it is not on
    a settings page somebody might leave open on a shared machine.
    """
    api.force_authenticate(user=hospital.staff("facility_admin"))
    api.credentials(
        HTTP_X_CURAVIA_ORGANISATION=str(hospital.organisation.pk),
        HTTP_X_CURAVIA_BRANCH=str(hospital.main_branch.pk),
    )
    listed = api.get(reverse("api:v1:display-list"))
    assert listed.status_code == 200
    assert listed.data[0]["url"] == ""
    assert screen.token not in repr(listed.data)

    revealed = api.post(reverse("api:v1:display-token", args=[screen.pk, "reveal"]))
    assert screen.token in revealed.data["url"]


def test_a_nurse_cannot_create_a_television(api_as_nurse, hospital) -> None:
    response = api_as_nurse.post(
        reverse("api:v1:display-list"), {"name": "My own screen"}, format="json"
    )
    assert response.status_code == 403


@pytest.mark.asyncio
@pytest.mark.django_db(transaction=True)
async def test_the_display_socket_connects_with_only_its_token(hospital, department) -> None:
    from channels.db import database_sync_to_async

    from config.asgi import application

    def make_screen():
        with tenant_scope(hospital.organisation.pk, hospital.main_branch.pk):
            return DisplayScreen.objects.create(
                organisation=hospital.organisation,
                branch=hospital.main_branch,
                name="Reception",
            )

    made = await database_sync_to_async(make_screen)()
    communicator = WebsocketCommunicator(
        application, f"/ws/display/{made.token}/", headers=BROWSER_HEADERS
    )
    connected, _ = await communicator.connect()

    assert connected, "a television could not connect with its own token"
    payload = await communicator.receive_json_from(timeout=5)
    assert payload["type"] == "display"
    assert payload["screen"] == "Reception"
    await communicator.disconnect()


@pytest.mark.asyncio
@pytest.mark.django_db(transaction=True)
async def test_an_unknown_token_cannot_open_a_display_socket(hospital) -> None:
    from config.asgi import application

    communicator = WebsocketCommunicator(
        application, "/ws/display/guessed-token/", headers=BROWSER_HEADERS
    )
    connected, code = await communicator.connect()
    assert not connected
    assert code == 4404
    await communicator.disconnect()


# --------------------------------------------------------------------------
# Broadcasting
# --------------------------------------------------------------------------


def test_a_broadcast_failure_never_breaks_the_move(hospital, department, monkeypatch) -> None:
    """
    Redis being down stops the boards updating. It must not stop a nurse
    sending a patient to the laboratory.
    """
    encounter = arrive(hospital, department)

    def explode(*args, **kwargs):
        raise RuntimeError("redis is on fire")

    monkeypatch.setattr(service, "broadcast", explode)
    with hospital.scope():
        service.move_to(encounter, Stage.LAB)
        encounter.refresh_from_db()

    assert encounter.stage == Stage.LAB


def test_group_names_carry_the_organisation(hospital, screen) -> None:
    """
    The tenant boundary for live updates. A group name another hospital
    could collide with is a live feed of their patients.
    """
    board = service.board_group(hospital.organisation.pk, hospital.main_branch.pk)
    assert str(hospital.organisation.pk) in board
    assert str(hospital.main_branch.pk) in board

    with hospital.scope():
        assert str(screen.organisation_id) in service.display_group(screen)


def test_the_events_table_is_never_rewritten(hospital, department) -> None:
    """
    Append-only by discipline here, and by the database's own grant in
    production. A wait that can be edited afterwards cannot be shown to
    anybody who matters.
    """
    encounter = arrive(hospital, department)
    with hospital.scope():
        service.move_to(encounter, Stage.TRIAGE)
        service.move_to(encounter, Stage.WAITING_DOCTOR)
        stages = list(encounter.events.order_by("at").values_list("to_stage", flat=True))

    assert stages == [Stage.ARRIVED, Stage.TRIAGE, Stage.WAITING_DOCTOR]


def test_one_patient_cannot_hold_two_open_visits_at_one_branch(hospital, department) -> None:
    """
    Enforced by the database, not only by the service: a second row on the
    board is a patient seen twice and billed twice.
    """
    from django.db import IntegrityError

    patient = make_patient(hospital)
    with hospital.scope():
        service.start_encounter(patient=patient, branch=hospital.main_branch)
        with pytest.raises(IntegrityError), unscoped(reason="test: attack the constraint directly"):
            Encounter.unscoped.create(
                organisation=hospital.organisation,
                branch=hospital.main_branch,
                patient=patient,
                status=Encounter.Status.OPEN,
            )


def test_a_queue_ticket_and_an_encounter_point_at_each_other(hospital, department) -> None:
    patient = make_patient(hospital)
    with hospital.scope():
        ticket = booking.check_in(
            patient=patient, department=department, branch=hospital.main_branch
        )
        encounter = Encounter.objects.get(patient=patient, status="open")
        assert encounter.ticket_id == ticket.pk
        assert QueueTicket.objects.get(pk=ticket.pk).encounter.pk == encounter.pk


def test_the_board_row_carries_the_token_number(hospital, department) -> None:
    """So a clerk can call somebody from the board without looking it up."""
    patient = make_patient(hospital)
    with hospital.scope():
        ticket = booking.check_in(
            patient=patient, department=department, branch=hospital.main_branch
        )
        rows = service.board_rows(hospital.main_branch.pk)

    assert rows[0]["token"] == ticket.label


def test_clinics_and_encounters_live_side_by_side(hospital, department) -> None:
    """A regression guard: the journey app imports scheduling and vice versa."""
    with hospital.scope():
        assert Clinic.objects.count() == 0
        assert JourneyEvent.objects.count() == 0
    assert get_channel_layer() is not None


# --------------------------------------------------------------------------
# The development server has to speak WebSocket
# --------------------------------------------------------------------------


def test_the_development_server_serves_asgi() -> None:
    """
    Found by opening the board in a browser for the first time.

    Channels 4 no longer overrides ``runserver``; ``daphne`` does, and only
    when it comes before ``django.contrib.staticfiles``. Without it the dev
    server is plain WSGI and every socket is answered with a 404 from the
    HTTP URLconf — while production, which runs daphne directly, works fine.
    So nothing except actually using it locally would catch this, and
    nothing except this test keeps it fixed.
    """
    from django.conf import settings

    assert "daphne" in settings.INSTALLED_APPS, (
        "daphne must be installed or `runserver` will not serve WebSockets"
    )
    assert settings.INSTALLED_APPS.index("daphne") < settings.INSTALLED_APPS.index(
        "django.contrib.staticfiles"
    ), "daphne has to come before staticfiles to override runserver"
    assert settings.ASGI_APPLICATION == "config.asgi.application"


def test_every_websocket_route_is_reachable() -> None:
    """
    A consumer that is written and never routed is a feature that does not
    exist. This asserts the three routes by name rather than by import.
    """
    from config.routing import websocket_urlpatterns

    names = {str(route.pattern) for route in websocket_urlpatterns}
    assert "ws/journey/" in names
    assert "ws/notifications/" in names
    assert any(name.startswith("ws/display/") for name in names)
