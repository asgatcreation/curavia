# Curavia — production image.
#
# One image runs both processes. That is not a shortcut, it is the hosting
# decision: Render's free tier has no background worker at all, and its 750
# free instance-hours per month only stretch to ONE always-on service. So
# Daphne and the Celery worker run side by side under honcho. See
# docs/00-decisions.md §3 and infra/Procfile.
#
# Build from the repository root:
#     docker build -f infra/Dockerfile -t curavia .

# --------------------------------------------------------------------------
# Stage 1 — build the frontend
# --------------------------------------------------------------------------
FROM node:24-alpine AS frontend

WORKDIR /build
COPY package.json package-lock.json* ./
COPY frontend/staff/package.json ./frontend/staff/
COPY frontend/portal/package.json ./frontend/portal/
COPY frontend/shared/package.json ./frontend/shared/

# NOT --omit=dev: vite, typescript and the Tailwind plugin are devDependencies
# and are exactly what builds the bundle. Nothing from this stage reaches the
# runtime image, so there is nothing to slim down here.
RUN npm ci --no-audit --fund=false || npm install --no-audit --fund=false

# tsconfig.base.json lives at the repository root and every app's tsconfig
# extends ../../tsconfig.base.json, so the build cannot type-check without it.
COPY tsconfig.base.json ./
COPY frontend ./frontend
RUN npm run build --workspaces --if-present

# --------------------------------------------------------------------------
# Stage 2 — python dependencies
# --------------------------------------------------------------------------
FROM python:3.13-slim AS python-deps

ENV PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

# build-essential and libpq-dev are needed to compile anything without a
# wheel; they do not travel to the final image.
RUN apt-get update && apt-get install --no-install-recommends -y \
        build-essential libpq-dev \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY backend/requirements.txt ./
RUN python -m venv /opt/venv \
    && /opt/venv/bin/pip install --upgrade pip \
    && /opt/venv/bin/pip install -r requirements.txt \
    && /opt/venv/bin/pip install honcho

# --------------------------------------------------------------------------
# Stage 3 — the runtime image
# --------------------------------------------------------------------------
FROM python:3.13-slim AS runtime

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PATH="/opt/venv/bin:$PATH" \
    DJANGO_SETTINGS_MODULE=config.settings.prod \
    TZ=Africa/Lagos

# libpq5 only — the runtime library, not the headers.
RUN apt-get update && apt-get install --no-install-recommends -y \
        libpq5 curl tzdata \
    && rm -rf /var/lib/apt/lists/*

# Never run as root: a container escape should not start as the host's
# superuser.
RUN useradd --create-home --shell /usr/sbin/nologin --uid 10001 curavia

COPY --from=python-deps /opt/venv /opt/venv

WORKDIR /app
COPY --chown=curavia:curavia backend/ ./
# settings.REPO_ROOT is BASE_DIR.parent, which is / in this image, so the
# permissions matrix has to land at /docs. Spelled out rather than written
# as /app/../docs, which is the same path but reads like a mistake.
COPY --chown=curavia:curavia docs/permissions-matrix.yml /docs/permissions-matrix.yml
COPY --chown=curavia:curavia infra/Procfile /app/Procfile
COPY --from=frontend --chown=curavia:curavia /build/frontend/staff/dist ./static/staff
COPY --from=frontend --chown=curavia:curavia /build/frontend/portal/dist ./static/portal

# Collect static with a throwaway key: collectstatic imports settings, and
# production settings refuse to load without one. Nothing secret is baked in.
RUN DJANGO_SECRET_KEY=build-only \
    DJANGO_ALLOWED_HOSTS=localhost \
    USE_OBJECT_STORAGE=false \
    python manage.py collectstatic --noinput --clear

USER curavia

EXPOSE 8000

# The keep-alive and the platform both watch this. It must stay cheap — no
# database call — and it must NOT be /robots.txt, which Render answers itself
# without waking a sleeping service.
HEALTHCHECK --interval=60s --timeout=5s --start-period=30s --retries=3 \
    CMD curl -fsS http://localhost:8000/healthz || exit 1

CMD ["honcho", "start"]
