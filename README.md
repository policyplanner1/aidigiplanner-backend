# aidigiplanner-backend

Authentication, multi-tenancy, RBAC, company onboarding/approval, social-account bookkeeping, and AI creative generation (ad copy, images, carousels, and story/avatar reels via Gemini) for the aiDigiPlanner social media automation SaaS. Publishing/scheduling and billing are out of scope for this codebase — see [Out of scope](#out-of-scope) below.

Tenancy model: **Company → Project → Social Accounts**. Three roles:

- **Super Admin** (platform-wide, no company membership) — approves or rejects newly registered companies (see [Company onboarding & approval](#company-onboarding--approval)) and has read access to every company's admins, users, and connected social accounts via `/api/v1/admin/*`.
- **Company Admin** — self-registers, which creates their company (starts `pending_approval`); once a Super Admin approves it, they can add/remove company users by email (the system generates and emails login credentials — see below) and connect social accounts to their projects.
- **Company User** (`member` role) — added by a Company Admin, can connect social accounts to projects they're a member of and change their own password (`POST /auth/change-password`).

## Stack

Python 3.12 · FastAPI · MySQL 8 (InnoDB, utf8mb4) · SQLAlchemy 2.0 async (`aiomysql`) · Alembic · Pydantic v2 · `argon2-cffi` · `PyJWT` · `structlog` · `uv`

Creative generation additionally uses: `arq` + Redis (job queue), `google-genai` (Gemini text/image/video), `Pillow` (image compositing), `ffmpeg-python` + a real `ffmpeg` binary on `PATH` (reel assembly), `Jinja2` (prompt templates), `tenacity` (provider retries).

## Setup

1. **Python & dependencies** — requires [uv](https://docs.astral.sh/uv/).

   ```bash
   uv sync
   ```

2. **Database** — a MySQL 8 (or compatible) server, reachable from `DATABASE_URL`. Create the two databases the app expects (dev + test — the test suite never touches the dev database):

   ```sql
   CREATE DATABASE aidigiplanner      CHARACTER SET utf8mb4;
   CREATE DATABASE aidigiplanner_test CHARACTER SET utf8mb4;
   ```

   *(Developed and verified against a local XAMPP MySQL/MariaDB instance rather than Docker for this environment — collation was `utf8mb4_general_ci` there since MariaDB doesn't have MySQL 8's `utf8mb4_0900_ai_ci`. On real MySQL 8, `utf8mb4_0900_ai_ci` per the original spec works identically; nothing in the schema depends on the specific collation.)*

3. **Config** — copy `.env.example` to `.env` and fill in real values (a random `JWT_SECRET`, your DB credentials). Every variable is documented inline in `.env.example`.

4. **Migrations**:

   ```bash
   uv run alembic upgrade head
   ```

5. **Run**:

   ```bash
   uv run uvicorn app.main:app --reload
   ```

   Interactive API docs at `http://127.0.0.1:8000/docs`, health check at `/health`.

6. **Creative generation prerequisites** (skip this if you're not touching `/api/v1/projects/{id}/creatives/*`):

   - **Redis**, reachable at `REDIS_URL` (default `redis://127.0.0.1:6379/0`) — the `arq` job queue's broker. On Windows without Docker/WSL2 available, a portable build works fine for local dev: download a Windows Redis release (e.g. [tporadowski/redis](https://github.com/tporadowski/redis/releases)), extract it, and run `redis-server.exe redis.windows.conf` in its own terminal. No installation/service registration needed.
   - **`ffmpeg`** on `PATH` — required for reel assembly (scene normalization, end card, concat) and used by the mock video provider too (to produce a real tiny playable clip for dry runs/tests). `ffmpeg -version` should print something.
   - **The worker process** — a separate process from the API server consumes the queue:

     ```bash
     uv run arq app.modules.creatives.worker.WorkerSettings
     ```

     Without a running worker, `POST .../creatives/generate` still succeeds (202) but the job sits in `queued` forever.
   - **`GEMINI_API_KEY`** (optional) — leave unset to use the deterministic mock providers everywhere (zero cost, no network — this is what the test suite does, and what `dry_run: true` on a generate request forces even with a real key configured). Set it in `.env` for real Gemini calls.

7. **First Super Admin** — there's no HTTP endpoint that can create one (deliberately). Run:

   ```bash
   uv run python scripts/seed_super_admin.py --email you@example.com
   ```

   (Omit `--email`/`--password` to be prompted; `SUPER_ADMIN_EMAIL`/`SUPER_ADMIN_PASSWORD`/`SUPER_ADMIN_NAME` env vars also work, for non-interactive provisioning.) It's idempotent — re-running with the same email that's already a Super Admin is a no-op — and refuses to silently promote an existing non-super-admin account.

## Migrations

Standard Alembic workflow, `alembic/env.py` reads `DATABASE_URL` from the app's own settings (not `alembic.ini`), so it's always in sync with whatever `.env` the app uses:

```bash
uv run alembic upgrade head              # apply
uv run alembic revision --autogenerate -m "add foo"   # generate a new one after changing app/models/
uv run alembic downgrade -1              # roll back one
```

`app/models/__init__.py` imports every model so autogenerate can see the full schema — add new models there too.

## Tests

```bash
uv run pytest
```

Tests run against `TEST_DATABASE_URL` (a separate database — see Setup). `tests/conftest.py` builds the schema directly from SQLAlchemy metadata once per test session (not via Alembic — keeps the suite fast and independent of migration history), then wraps each test in an outer transaction + `SAVEPOINT`. Service code calling `session.commit()` only ends the savepoint (SQLAlchemy's `join_transaction_mode="create_savepoint"` restarts it automatically), so the whole test — everything it wrote — rolls back at teardown. No manual cleanup, no cross-test pollution, no need to run tests in any particular order.

Email sending is swapped for `tests/fakes.py::RecordingEmailService` (records the token/OTP instead of sending it), so verification/reset/provisioning flows can be driven end-to-end without scraping console output. Creative generation similarly swaps in `FakeArqPool` (runs the worker task inline against the test's own DB session instead of a real Redis broker) and `InMemoryStorageService` (in-memory instead of local disk) — so the full generate → poll → review → download flow runs in CI with **no live Redis and no `GEMINI_API_KEY`**, via the deterministic mock providers.

Coverage: registration (incl. duplicate-email conflict, slug collision handling), login (success, wrong password, unverified-blocked, suspended-blocked, company-pending/rejected-blocked, no-enumeration between wrong-password and nonexistent-email), refresh rotation, refresh **reuse detection** (presenting an already-rotated token revokes the whole family, including tokens issued after the compromise), logout / logout-all, OTP-based password reset (wrong-code handling, attempt lockout, single-use OTP and reset token) and change-password (both invalidate existing sessions via `token_version`), email verification (single-use), company approval/rejection by Super Admin, company-member provisioning by email (new user gets generated credentials, existing user just gets linked), social account CRUD, and RBAC boundaries — including the cross-tenant 404-not-403 rule, last-active-admin protection, and a Super Admin bypass check.

Creative generation adds: brand-profile CRUD + RBAC, including the four image upload slots and every new voice/pillars/knowledge-base field; the full generate→poll→review→download flow for post/carousel formats, and generate→awaiting_render→render-assets→review→download for story-reel/avatar-reel formats; render-assets rejected (400) on a post/carousel job or a second call on the same reel job; idempotency (repeat brief returns the same job unless `force`); cost guardrails (per-run cap, and per-day cap tripping across multiple real jobs, re-checked again at render-assets time); a deterministic compliance-rejection scenario proving rejected concepts are persisted with a reason, never dropped; the `partially_failed` degradation path (a monkeypatched image-provider failure proves already-committed concepts survive an asset-rendering failure); audit-log entries across the job/concept lifecycle; and pure-unit coverage (no DB) for the compliance gate's keyword checks, `Brief`/`GeneratedConcept` validation, pricing math, the mock providers, and Pillow post-processing — ported from the prototype's own test suite. Reel-format tests shell out to real `ffmpeg` for assembly (the mock video provider does too, to produce a real tiny clip) — they'll fail if `ffmpeg` isn't on `PATH`.

## Token model

- **Access token** — JWT, 15 min TTL (`ACCESS_TOKEN_TTL_MINUTES`), HS256. Claims: `sub`, `jti`, `type: "access"`, `is_super_admin`, `token_version`, `iat`, `exp`.

  Company/project permissions are **not** in the JWT — they'd go stale the moment a role changes. Every request that needs them (`app/core/deps.py`) resolves fresh from `company_members`/`project_members`. The `is_super_admin` claim is informational only; the dependency layer always re-checks `current_user.is_super_admin` from the freshly-loaded DB row, not the token.

- **Refresh token** — opaque (`secrets.token_urlsafe(48)`), 30 day TTL, only its SHA-256 hash is ever stored. Every `/auth/refresh` call **rotates**: issues a new token in the same `family_id`, links it via `parent_id`, and revokes the one just used (`revoked_reason="rotated"`).

  **Reuse detection**: if a token that's already `revoked_at IS NOT NULL` gets presented again, the *entire family* is revoked (`reuse_detected`) and an `auth.refresh.reuse_detected` audit row is written. This is what catches a stolen refresh token: the legitimate client rotates past token N, an attacker replays token N later, gets rejected, and — critically — the token the legitimate client is now holding (issued from that same rotation) is revoked too, forcing everyone back through login. See `AuthService.refresh` and `tests/test_auth_refresh.py`.

- **`token_version`** (on `users`) — bumped on password change and `logout-all`. Every access token issued before the bump fails `get_current_user`'s version check immediately (no waiting for the 15-minute TTL to expire), and all that user's refresh tokens are revoked in the same operation.

- Single-use tokens (password reset session, company-member/product-invite credentials) follow the same shape: opaque, hashed at rest, `used_at` timestamps enforce single use, short TTLs (`PASSWORD_RESET_TTL_MINUTES=60`).

- **Both email verification and password reset are OTP-based, not link-based**: `POST /auth/register` (and `POST /auth/resend-verification`) email a 6-digit code (`EmailVerificationOtp`, hashed at rest, `EMAIL_VERIFICATION_OTP_TTL_MINUTES=15` TTL, same `OTP_MAX_ATTEMPTS` lockout as password reset) that `POST /auth/verify-email` takes directly alongside the email address. `POST /auth/forgot-password` emails a separate 6-digit code (`PasswordResetOtp`, hashed at rest, `OTP_TTL_MINUTES=10` TTL, locked out after `OTP_MAX_ATTEMPTS=5` wrong guesses). `POST /auth/verify-reset-otp` consumes that code and issues a short-lived opaque `reset_token` (a `PasswordResetToken`, same shape as before). `POST /auth/reset-password` takes that `reset_token` — never the OTP itself — to actually set the new password. Three separate steps so a frontend can show "enter code" → "set new password" as distinct screens, and so the OTP can't be replayed once verified.

## RBAC — the contract other modules build on

`app/core/deps.py` exports `get_current_user` / `CurrentUser`, `require_super_admin()`, `require_company_role(*roles)`, `require_project_access(*roles)`. Resolution rules (also exercised in `tests/test_rbac.py`):

- **Super Admin** passes every check.
- **Company Admin** passes every project check inside their own company, even with no explicit `project_members` row.
- **Project User** passes only for projects they're explicitly in `project_members` for.
- A user with **no relationship at all** to a company/project gets **404**, never 403 — existence of other tenants' resources is never leaked. A user who *is* a member but lacks the specific role gets 403 (they already know it exists).
- Every company additionally protects against ending up with **zero active admins** — the last `company_admin` can't be demoted or removed via the members API.

Any future module (social OAuth, scheduling/publishing) should depend on these the same way `app/modules/companies`, `app/modules/projects`, `app/modules/social_accounts`, and `app/modules/creatives` already do — not reinvent access checks.

## Company onboarding & approval

`Company.status` lifecycle: `pending_approval` → `active` (approved) or `rejected`, plus the pre-existing `suspended`.

- `POST /auth/register` always creates the company as `pending_approval` — registering does **not** grant access on its own.
- `AuthService.login` blocks with a `403` (`company_pending_approval` / `company_rejected` / `company_suspended`) for any non-super-admin user whose companies are all in a non-`active` state. This is on top of the existing per-user `email_not_verified` gate — a Company Admin needs *both* a verified email and an approved company before they can log in.
- `app/modules/admin` (`/api/v1/admin/*`, Super Admin only) is where approval happens: `GET /companies` (list, optional `?status=` filter), `GET /companies/{id}` (full detail — company + members + rolled-up social accounts, across every project in that company), `POST /companies/{id}/approve`, `POST /companies/{id}/reject` (body `{"reason": "..."}`). Approving/rejecting emails every `company_admin` member of that company.

## Social accounts

Six supported platforms (`app.models.enums.SocialPlatform`): Instagram, Facebook, YouTube, Google, Twitter, LinkedIn. Accounts are project-scoped (`app/modules/social_accounts`, `/api/v1/projects/{project_id}/social-accounts`) — any project member (company admin or company user) can list/add one; removing one requires `project_admin` (company admins bypass this, same as everywhere else). This is metadata only (platform, handle, profile URL) for bookkeeping — no OAuth, no stored access tokens, no publishing; see [Out of scope](#out-of-scope).

## Creative generation

Ports a standalone Gemini-based creative-generation prototype (`../creative workflow/`, a single-brand CLI tool) into a real multi-tenant, async, DB-backed pipeline. The journey: **Brand Profile → generate → review**.

1. **Brand Profile** (`app/modules/brand_profiles`, `/api/v1/projects/{project_id}/brand-profile`) — **one per project** (not per company: a company can run several distinct brands as separate projects). Holds voice/tone (including a long-form `voice` description and `ai_instructions` freeform escape hatch, both injected verbatim into the ideation prompt), content `pillars`, `website_url`/`domains`, a `knowledge_notes`/`knowledge_urls` reference base (notes are injected into the prompt; URLs are stored only -- no fetching/scraping yet), visual identity (palette, `heading_font`/`body_font`, style keywords), compliance rules (mandatory disclaimer, banned claims, partner-naming rules), CTA/hashtag banks, and product lines (each with its own partners/hooks). A generation request 400s if the project has no brand profile yet — `PUT` one first. `project_admin` only for writes. Every field round-trips as plain JSON on the `PUT`/`GET` body -- there's no separate JSON-blob field to manage.

   **Image uploads** — four independent slots, each `PUT/GET /api/v1/projects/{project_id}/brand-profile/{slot}` (multipart upload / raw-bytes download), one current image per slot per project: `avatar` (for avatar-style reels — see below), `logo` (composited onto every generated image and reel end card), `logo-dark`, `icon`. `dark_logo`/`icon` are asset-management only today -- not yet consumed by the generation pipeline.

2. **Generate** — `POST /api/v1/projects/{project_id}/creatives/generate` (`project_admin`/`editor`; this spends money once a real API key is configured). Body: `product_line` (validated against the brand profile's product lines), `topic`, `format` (`post`/`carousel`/`reel`), `concept_count`, `quality` (`draft`/`standard`/`hero`), and format-specific fields (`carousel_slides`; `reel_duration_s`, `voiceover`, `reel_style` for reels). Returns `202` with a `GenerationJob` immediately — generation runs asynchronously on the `arq` queue (see prerequisites above).

   - **Idempotency**: an identical brief (same product_line/topic/format/reel_style) returns the existing job instead of creating a duplicate, unless `force: true`.
   - **Cost guardrails**: a pre-flight ₹ estimate (`CREATIVE_MAX_COST_PER_RUN_INR`, default 500) rejects an over-budget request with `400` before anything is enqueued; a second check sums the company's spend so far today against `CREATIVE_MAX_COST_PER_DAY_INR` (default 2000).
   - **`dry_run: true`** forces the mock providers even with a real `GEMINI_API_KEY` configured, for zero-cost testing on demand.
   - **Reel styles** (`reel_style`, only for `format: "reel"`): `"story"` (default) — scene-by-scene generated b-roll, each concept's own cover image used as the video's visual reference. `"avatar"` — every scene instead keyed off the project's uploaded brand-profile avatar image, so the same face appears throughout; requires an avatar to already be uploaded (400 otherwise).
   - **Reels are two-step, post/carousel are one-step**: for `format: "post"`/`"carousel"`, `generate` runs ideation and asset rendering in the same call, landing straight on `succeeded`/`partially_failed`/`failed` -- unchanged single-call behavior. For `format: "reel"`, `generate` runs ideation + compliance only and stops at `awaiting_render` (scripts/captions are persisted, no image or video has rendered yet, so nothing gets rendered before a human has reviewed the reel's script) -- see step 4 below to proceed.

3. **The pipeline** (`app/modules/creatives/worker.py`, runs in the `arq` worker process): LLM ideation → the **compliance gate** (keyword checks + a second LLM reviewer pass; a concept that still fails after one retry-with-feedback is rejected **and persisted with its reason** — never silently dropped) → image rendering for every accepted concept (post/carousel slides, or a reel's cover/first-frame) → for reels, per-scene video clips + `ffmpeg` assembly (normalize, burn in on-screen text, append a logo/CTA/disclaimer end card, concat). Ideation and asset rendering are two separate arq tasks (`generate_creatives_job` / `render_creative_assets_job`): for post/carousel, `generate_creatives_job` runs both, one after the other, in the same call; for reels, `generate_creatives_job` stops after ideation and `render_creative_assets_job` runs later, triggered by step 4's `render-assets` call. Either way, ideation's concepts commit first, so a later image/video failure degrades the job to `partially_failed` (concepts and any assets already rendered survive) instead of discarding paid-for ideation work as `failed`.

4. **Review, and for reels, render** — `GET .../creatives` (list concepts + their assets, filterable by `?job_id=`/`?review_status=`) or `GET .../creatives/concepts/{id}` (detail), `POST .../concepts/{id}/approve` / `.../reject` (body `{"reason": "..."}`), `GET .../creatives/assets/{id}/download` (raw bytes, access-checked through asset → concept → job → project). `GET .../creatives/jobs/{id}` polls a job's status (`queued` → `running` → (`awaiting_render` for reels only) → `succeeded`/`partially_failed`/`failed`). For a reel job sitting at `awaiting_render`, `POST .../creatives/jobs/{id}/render-assets` (`project_admin`/`editor`) re-checks the cost guardrails (time may have passed since the original estimate) and enqueues the image/video rendering pass; called on any job not currently `awaiting_render` -- a post/carousel job, or a reel job a second time -- it 400s.

**Provider architecture**: `app/modules/creatives/providers/` — ABCs (`LLMProvider`/`ImageProvider`/`VideoProvider`) with a `Gemini*` implementation and a deterministic `Mock*` implementation each, selected by `providers/factory.py` based on `dry_run` and whether `GEMINI_API_KEY` is configured (same pattern as `EmailService`). Two video backends: Veo (native-audio reels) and Omni Flash (silent/on-screen-text reels, cheaper) — `domain.video_backend_for()` picks based on `voiceover`.

**Storage**: `app/modules/storage/` — `StorageService` ABC, `LocalDiskStorage` implementation (`CREATIVE_STORAGE_LOCAL_ROOT`, default `./storage/creatives`), selected by `storage/provider.py`. Swapping in S3-compatible storage later is a new class + one branch, no call-site changes — every asset is addressed by an opaque storage key, never a raw filesystem path.

## Project layout

```
app/
  core/       config, security (hashing/JWT/opaque tokens), exceptions, structured logging, rate limiting, RBAC deps
  db/         declarative Base, mixins (UUIDv7 PK, timestamps, soft delete), session
  models/     one SQLAlchemy model per file
  modules/
    auth/            register/login/refresh/logout/me/verify/reset/change-password
    companies/       company member management (add-by-email provisioning)
    projects/        project CRUD + project member management
    social_accounts/ project-scoped social account CRUD
    brand_profiles/  brand voice/compliance profile CRUD + avatar upload/download
    creatives/       generation pipeline: domain models, compliance gate, pricing,
                      providers (Gemini + mock), pipeline/ (ideate, render_image,
                      render_video, assemble, postprocess), worker.py (arq task),
                      prompts/ (Jinja2 templates)
    storage/         StorageService interface + LocalDiskStorage, provider.py picks one
    admin/           Super Admin company approval/rejection + platform-wide visibility
    users/           (thin — /auth/me shaping lives in auth for now)
    audit/           AuditService, used by every other service
    email/           EmailService interface + console/SMTP implementations, provider.py picks one
  main.py     app factory, middleware, exception handlers, router mounting
alembic/      migrations
scripts/      seed_super_admin.py
tests/        pytest suite (see Tests above)
```

Routers are thin (HTTP only); business logic lives in service classes that take an `AsyncSession` and are callable outside a request context (the seed script does exactly this, bypassing HTTP entirely).

## Out of scope

Per the brief, these are **not** built here — only the interface boundary exists where a future module will plug in:

- **Social platform OAuth / token storage / publishing** — `app/modules/social_accounts` only stores basic metadata (platform, handle, profile URL) for bookkeeping and Super Admin visibility. No OAuth flow, no access/refresh token storage, no posting/publishing — that's a future module. Creative generation produces assets; it does not publish them anywhere.
- **S3-compatible object storage** — `StorageService` has one implementation (`LocalDiskStorage`); see [Creative generation](#creative-generation). Every asset is addressed by an opaque storage key already, so adding `S3Storage` later is additive, not a rewrite.
- **Frontend** — none; `/docs` (Swagger UI) is the only UI.
- **Billing/subscriptions** — none.
- **Email delivery** — `app/modules/email/provider.py::get_email_service()` picks the implementation based on config: if `SMTP_HOST` is set, real mail goes out via `SmtpEmailService` (`app/modules/email/smtp.py`, stdlib `smtplib` run off the event loop via `asyncio.to_thread`); otherwise it falls back to `ConsoleEmailService`, which logs verification/reset tokens, generated member credentials, and company approval/rejection notices to a dedicated `email` logger instead of sending anything. This is the one place tokens/passwords are deliberately logged; the app's general request logger never logs tokens, passwords, or full email bodies (see `app/core/logging_setup.py`). For Gmail, `SMTP_PASS` must be an [App Password](https://myaccount.google.com/apppasswords), not the account password. SMTP send failures are logged and swallowed, not raised — the triggering DB change has already committed by the time the email is sent, and every affected flow has a retry path (resend-verification, forgot-password, or re-adding a member).

## Security notes

- Passwords: `argon2-cffi` (argon2id), never logged or returned.
- Login / forgot-password / resend-verification return **identical responses** whether or not the account exists. Login additionally always runs a real Argon2 verify (against a fixed dummy hash when there's no user to check), so a wrong-password and a nonexistent-email attempt take about the same time.
- Rate limiting: in-memory sliding window (`app/core/rate_limit.py`), per-IP and per-email, behind a `RateLimiter` Protocol — swap in a Redis-backed implementation later without touching call sites.
- Structured JSON logs (`structlog`) carry a request ID (`X-Request-ID`, generated or echoed) through every log line for a request.
