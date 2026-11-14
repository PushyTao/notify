# Douyin Notify Implementation Plan

> **For agentic workers:** Use subagent-driven-development for bounded adapter/UI tasks and requesting-code-review for final review. Continue execution in this chat.

**Goal:** Deliver the approved self-hosted multi-author transcription, summary, email and record browsing system.
**Architecture:** FastAPI serves templates and authenticated JSON administration. A separate persistent SQLite worker polls authors, processes videos and delivers individual emails. Integrations have explicit interfaces.
**Tech Stack:** Python 3.12, FastAPI, sqlite3, httpx, Jinja2, faster-whisper, Docker Compose.
**Spec:** ../specs/2026-11-14-douyin-notify-design.md (approved by user 2026-11-14).

## Global constraints

- Both pages and emails use 更新时间、博主名称、总结内容、内容全文、原视频标题、原视频链接 in this exact order.
- Newest original publication time first. No public admin link. Password-protected /admin and authenticated administration APIs.
- Multiple authors/subscribers/models; one active summary model; configurable prompt with default restore; real connectivity test.
- Self-hosted collection preferred; generic third-party JSON contract also supported; no automatic paid fallback.
- Persistent retry state, no fake transcript from description, no credentials exposed publicly or in logs.
- Local Docker deployment, no Sites publication. SQLite is for a single host; one worker process by default.

## Task 1: Persistence, security and job lifecycle
Files: app/db.py, app/security.py, app/config.py, app/service.py, tests/test_service.py.
- [x] Write tests for baseline vs backfill, deduplication, ordering, encrypted secret roundtrip, lease recovery and per-recipient retries.
- [x] Run tests and observe missing implementation failure.
- [x] Implement SQLite schema, transactional leasing, settings encryption and admin session/password handling.
- [x] Verify with `rtk proxy .venv/Scripts/python -m pytest tests/test_service.py -q`.

Core public interfaces: `Store(path)` with `connect()` context manager returning sqlite3 connection with Row factory; `Settings(store, secret_key).get(group)` / `.save(group, values)`; `Service(store, settings).poll_author(author, collector)` and `.process_video(video, integrations)` / `.deliver(delivery, sender)`.

## Task 2: Collection, transcription, model and SMTP adapters
Files: app/integrations.py, tests/test_integrations.py, docs/providers.md.
- [x] Write contract tests using HTTP mock transport and local protocol fixtures; reject malformed list responses, unsafe media and empty transcript/model responses.
- [x] Verify failures before implementation.
- [x] Implement independent synchronous functions: `fetch_posts(author: dict, settings: dict) -> list[dict]`; `transcribe(media_url: str, settings: dict) -> str`; `summarize(text: str, model: dict, prompt: str) -> str`; `test_model(model: dict) -> str`; `send_email(recipient: str, record: dict, settings: dict) -> None`.
- [x] Post fields: video_id, author_name, published_at (Unix seconds), title, url, media_url. record adds transcript and summary. Settings passed already decrypted; adapters never persist credentials.
- [x] All network operations have bounded timeouts and fail explicitly. Self-hosted provider contract is pinned/documented. Generic feed accepts author_id and cursor with items/next_cursor response.
- [x] Run adapter tests; report exact provider deployment requirements.

## Task 3: HTTP routes and front/back office
Files: app/main.py, app/templates/*.html, app/static/*, tests/test_web.py.
- [x] Test unauthenticated API rejection, CSRF rejection, login/logout, CRUD validation and public secret exclusion.
- [x] Implement public GET / and /records/{id}; GET /admin login/dashboard; /api/admin/* JSON management; GET /api/admin/state returns authors/subscribers/models/settings/videos/deliveries/status/default_prompt/csrf.
- [x] Implement forms for authors, subscribers (all or selected authors), model configuration/activation/test, prompt restore, collection/transcription/SMTP settings, test email, retry and change password.
- [x] Browser uses JSON fetch with X-CSRF-Token; session cookies are HttpOnly/SameSite. Templates autoescape all content.
- [x] Verify field order, sorting, paging, responsive theme and admin workflows.

## Task 4: Worker, deployment and end-to-end verification
Files: app/worker.py, Dockerfile, compose.yaml, .env.example, README.md, tests/test_pipeline.py.
- [x] Test full controlled pipeline and restart/retry behavior against real SQLite; replace only external services.
- [x] Wire independent leased poll/video/email task loops with capped exponential retry and status reporting; maintain heartbeat while work runs.
- [x] Container runs as unprivileged user, persistent volume stores DB and model cache; healthchecks check app and worker. README includes startup, initial password/key setup, collector integration, backups, limitations and credentials required for real tests.
- [x] Lock dependencies, run complete pytest suite, render/browser QA and Docker checks when daemon available.
- [x] Request independent code review, fix important findings, rerun affected tests, report verified outcomes and remaining external setup.

## Execution decisions

- Work in the supplied clean repository on `codex/douyin-notify`; no additional worktree is needed for this new repository.
- Docker CLI is installed but its engine is not running at initial inspection. Implement and validate configuration; attempt runtime verification separately.
- Python 3.12 is installed via uv; use project-local venv/cache rather than default Python 3.8.

## Completion evidence

Tasks 1–4 implemented. Full suite: 55 passed. Independent code review and scoped fix review completed. Final Docker image built; Web/Worker healthy; HTTP authentication, configuration persistence after restart and real FFmpeg MP4 extraction passed. Browser QA covered public ordering, theme, login, forms and mobile overflow. See ../../verification.md for exact boundaries. Real Douyin/AI/SMTP credentials are intentionally left for administrator configuration; no claim of live external end-to-end validation.
