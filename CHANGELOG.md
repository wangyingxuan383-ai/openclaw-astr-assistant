# Changelog

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
