# Changelog

## v0.3.0 (2026-02-12)
- Added `backend-service/` (FastAPI + Jinja2 + sqlite):
  - Web pages: `GET /web/status`, `GET /web/models`
  - API: `status`, `executors`, `models`, `models/import-astr`, executor job lifecycle APIs
  - Async executor queue (concurrency fixed to `1`) with Codex real dispatch
  - Gemini executor reserved but disabled in V1.1
- Added backend audit trail fields and trace-id propagation.
- Added `backend-service/.env.example` and `requirements.txt`.
- Updated deployment docs:
  - Root `README.md` now covers sidecar + backend-service dual deployment
  - `deploy/openclaw-sidecar/RUNBOOK.md` expanded for backend-service operations
  - `.env.example` now includes `BACKEND_API_TOKEN`

## v0.1.0 (2026-02-12)
- Initial public release skeleton.
- Added Astr plugin source (`plugin/astrbot_plugin_openclaw_assistant`).
- Added sidecar deployment templates (`deploy/openclaw-sidecar`).
- Added frozen master plan document.
- Added independent backend path convention: `/root/openclaw-assistant-backend`.

## v0.1.1 (2026-02-12)
- Enhanced repository `README.md` with full installation/deployment steps.
- Added screenshot documentation guide at `docs/screenshots/README.md`.
- Added explicit security and risk statement for high-permission operations.

## v0.1.2 (2026-02-12)
- Converted root `README.md` to Chinese.
- Clarified repository boundary: single repo includes both plugin source and backend sidecar deployment templates.

## v0.2.0 (2026-02-12)
- Split into dual repositories.
- This repository is now backend-only (`deploy/openclaw-sidecar/*`).
- Removed plugin source from this repository.
- Standalone plugin repository: `https://github.com/wangyingxuan383-ai/astrbot_plugin_openclaw_assistant`

## v0.2.1 (2026-02-12)
- Deployment reproducibility hardening:
  - Pinned OpenClaw installation to `openclaw@${OPENCLAW_VERSION}` (default `2026.2.9`).
  - Added `OPENCLAW_VERSION` to `.env.example`.
- Runbook hardening:
  - Health check switched to `POST /v1/responses`.
  - Auth failure guidance unified as `401/403 -> auth_failed`.
  - Added explicit upgrade and rollback workflow for version pinning.
