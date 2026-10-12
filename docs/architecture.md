# Telegram Task Manager

## Architecture

One Python application supplies three entry points: FastAPI, aiogram polling bot and an outbox worker. They share an asynchronous domain service layer and PostgreSQL. Redis stores conversational states and request limits. The React Mini App uses the same domain API, with Telegram initData authentication. Static assets are served by FastAPI behind the existing HTTPS Caddy proxy. No inbound Telegram webhook is required for polling.

## Directory structure

- backend/app: configuration, models, authorization, domain operations, API, bot and worker.
- backend/alembic: versioned database migrations.
- backend/tests: unit and PostgreSQL integration tests.
- frontend/src: Telegram-native React application.
- ops: backup and restore procedures.
- docs: architecture and operational instructions.

## Data model

User has a unique Telegram ID. Project has a unique uppercase key, owner and atomic task counter. ProjectMember controls access. ProjectStatus belongs to one project. Task references project, status, author, optional assignee and sprint, with an optimistic version. Checklist, comments, attachments, activity, time entries and GitHub links reference tasks. Sprint belongs to a project. Invitations are hashed, expiring and optionally restricted to one identity. NotificationSettings are per member and project. Notifications form a persistent, retryable outbox with a unique deduplication key. GitHubConnection holds encrypted access credentials; GitHubRepository holds an encrypted webhook secret. WebhookDelivery provides persistent replay protection.

Foreign keys have indexes. Task creation locks the project counter; task updates lock the task and require the current version. Timer changes lock the user, with an additional partial unique index for one open time entry. Every task mutation and notification enqueue occurs in the same transaction. Every project-scoped operation verifies membership server-side.

## API contract

All protected endpoints use Authorization: tma <Telegram initData>. The API prefix is /api within the configured deployment path. Errors have a detail field. PATCH /tasks/{id} requires version and returns HTTP 409 for stale edits. Dates use ISO 8601, deadlines are calendar dates, time tracking timestamps are UTC.

- GET /me, /dashboard, /projects, /timer, /time.
- POST /projects; GET/PATCH/DELETE /projects/{id}, with owner-only deletion and key confirmation.
- GET/POST /projects/{id}/tasks, /statuses, /sprints, /invites, /repositories.
- PATCH /projects/{id}/statuses/{status}, /sprints/{sprint}; PUT /projects/{id}/status-order.
- GET /projects/{id}/activity, /members; DELETE /projects/{id}/members/{user}.
- PUT /projects/{id}/notifications.
- GET/PATCH /tasks/{id}; POST /tasks/{id}/comments, /checklist, /attachments, /time, /github.
- PATCH/DELETE /tasks/{id}/checklist/{item}; GET /attachments/{id}.
- POST /timer/{task}/start; POST /timer/pause, /timer/stop.
- POST /invites/{token}/accept; POST /github/webhook/{repository}.
- GET /health provides database and Redis readiness.

OpenAPI is available at /api/docs. The bot uses the domain layer directly and rechecks membership for callbacks. GitHub signatures are checked over the original request bytes before parsing.

## Implementation and verification

1. Compose, models and migrations; verify schema on PostgreSQL.
2. Telegram authentication, private projects, membership, tasks and configurable statuses.
3. Inline bot workflow and a deterministic Russian command parser with confirmation.
4. Dashboard, Kanban, list and task editor.
5. Comments, validated attachments, checklist, search and activity.
6. Sprints, persistent timers, manual time and statistics.
7. Notification preferences, outbox retries, deadline jobs and signed GitHub events.
8. Integration tests, frontend build, deployment, HTTPS checks and restart verification.

GitHub authorization uses a user-supplied fine-grained access token encrypted at rest. No automatic status changes occur. Project data is never exposed through unauthenticated API routes. A Mini App login expires after one hour. Outbox delivery is at least once: Telegram offers no idempotency key, so a crash after sending but before marking success can repeat a notification.
