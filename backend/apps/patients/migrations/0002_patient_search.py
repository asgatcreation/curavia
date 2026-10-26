"""
Patient search: a trigger-maintained vector and a trigram index.

**Why a trigger and not Python.** The front desk finds patients through this
and nothing else, so it must never be stale. A Python-side update in
``save()`` is skipped by ``bulk_create``, by ``.update()``, by a data
migration and by anything written straight through SQL — and the failure is
silent: the patient exists, and simply cannot be found. The trigger runs
whichever way the row was written.

**Two indexes, because there are two kinds of search.** A clerk typing
``adebayo 0803`` wants full-text over name and phone, and the tsvector with a
GIN index answers that. A clerk typing ``Adebayou`` for ``Adebayo`` wants
fuzzy similarity, and only a trigram index does that. Django's ``GinIndex``
cannot express the ``gin_trgm_ops`` operator class, so that one is raw SQL.

``unaccent`` is not applied. Nigerian names are written in the Latin alphabet
with few diacritics, and folding them would merge distinct Yoruba names that
differ only by tone mark — which is exactly the wrong outcome at a desk that
is trying to tell two patients apart.
"""

from __future__ import annotations

from django.contrib.postgres.operations import TrigramExtension
from django.db import migrations

# One function, used by one trigger. The weights matter: a surname match
# should outrank an address match, and an exact MRN should beat both.
#
#   A  medical record number
#   B  surname
#   C  first and other names
#   D  phone numbers
#
# `search_text` is the flattened version the trigram index uses. It is kept
# in the same trigger so the two can never disagree about what a patient is
# called.
CREATE_FUNCTION = """
CREATE OR REPLACE FUNCTION curavia_patient_search_update() RETURNS trigger AS $$
BEGIN
    NEW.search_vector :=
        setweight(to_tsvector('simple', coalesce(NEW.mrn, '')), 'A') ||
        setweight(to_tsvector('simple', coalesce(NEW.last_name, '')), 'B') ||
        setweight(to_tsvector('simple', coalesce(NEW.first_name, '')), 'C') ||
        setweight(to_tsvector('simple', coalesce(NEW.other_names, '')), 'C') ||
        setweight(to_tsvector('simple', coalesce(NEW.phone, '')), 'D') ||
        setweight(to_tsvector('simple', coalesce(NEW.alternate_phone, '')), 'D');

    NEW.search_text := lower(
        coalesce(NEW.first_name, '') || ' ' ||
        coalesce(NEW.other_names, '') || ' ' ||
        coalesce(NEW.last_name, '') || ' ' ||
        coalesce(NEW.phone, '') || ' ' ||
        coalesce(NEW.alternate_phone, '') || ' ' ||
        coalesce(NEW.mrn, '')
    );
    RETURN NEW;
END
$$ LANGUAGE plpgsql;
"""

DROP_FUNCTION = "DROP FUNCTION IF EXISTS curavia_patient_search_update() CASCADE;"

CREATE_TRIGGER = """
DROP TRIGGER IF EXISTS curavia_patient_search ON patients_patient;
CREATE TRIGGER curavia_patient_search
    BEFORE INSERT OR UPDATE OF
        mrn, first_name, last_name, other_names, phone, alternate_phone
    ON patients_patient
    FOR EACH ROW EXECUTE FUNCTION curavia_patient_search_update();
"""

DROP_TRIGGER = "DROP TRIGGER IF EXISTS curavia_patient_search ON patients_patient;"

# Backfill, so the trigger does not only apply to rows written after it.
# A no-op on a fresh database and correct on one that already has patients.
BACKFILL = """
UPDATE patients_patient SET mrn = mrn;
"""

CREATE_TRIGRAM_INDEX = """
CREATE INDEX IF NOT EXISTS patient_search_text_trgm
    ON patients_patient USING gin (search_text gin_trgm_ops);
"""

DROP_TRIGRAM_INDEX = "DROP INDEX IF EXISTS patient_search_text_trgm;"


class Migration(migrations.Migration):
    dependencies = [("patients", "0001_initial")]

    operations = [
        # Idempotent, and present for Neon, where the container init script
        # in infra/postgres-init never runs.
        TrigramExtension(),
        migrations.RunSQL(CREATE_FUNCTION, DROP_FUNCTION),
        migrations.RunSQL(CREATE_TRIGGER, DROP_TRIGGER),
        migrations.RunSQL(BACKFILL, migrations.RunSQL.noop),
        migrations.RunSQL(CREATE_TRIGRAM_INDEX, DROP_TRIGRAM_INDEX),
    ]
