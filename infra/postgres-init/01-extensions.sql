-- Extensions Curavia needs, created once when the database is initialised.
--
-- pg_trgm is the one that matters for daily use: a front-desk clerk who types
-- "Adebayou" for "Adebayo", or just a phone number fragment, still finds the
-- patient. Trigram similarity plus a GIN index is what makes that fast.
--
-- On Neon these are created by a migration instead, because the init scripts
-- in this directory only run for a local container.

CREATE EXTENSION IF NOT EXISTS pg_trgm;
CREATE EXTENSION IF NOT EXISTS btree_gin;
CREATE EXTENSION IF NOT EXISTS unaccent;

-- Also create the test database owner's template expectations, so
-- `pytest --reuse-db` does not have to be run as a superuser.
