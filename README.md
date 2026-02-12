# OpenClaw × Astr QQ Assistant (V1)

OpenClaw Agent capability packaged as an AstrBot plugin for QQ, with strict permission, privacy, and audit boundaries.

## What This Project Is
- Astr plugin as the only business entrypoint.
- OpenClaw Gateway deployed as a local Docker sidecar.
- QQ-focused trigger strategy: admin/whitelist first, group disabled by default.
- Security chain: permission -> blacklist -> high-risk confirmation -> execute -> audit.

## Repository Layout
- `plugin/astrbot_plugin_openclaw_assistant/`: plugin source, config schema, plugin README.
- `deploy/openclaw-sidecar/`: sidecar compose/config template and runbook.
- `docs/screenshots/`: screenshot guide and expected filenames.
- `OPENCLAW_ASTR_ASSISTANT_MASTER_PLAN.md`: frozen implementation baseline and changelog.

## Prerequisites
- Linux host with Docker and Docker Compose.
- AstrBot + QQ adapter already running.
- Recommended host resources: `2 vCPU / 4GiB RAM` minimum, `4 vCPU / 8GiB RAM` for stable use.

## Installation
1. Prepare sidecar runtime directory.

```bash
mkdir -p /root/openclaw-assistant-backend/data/openclaw_home \
         /root/openclaw-assistant-backend/data/openclaw_workspace \
         /root/openclaw-assistant-backend/logs
```

2. Copy sidecar deployment files.

```bash
cp deploy/openclaw-sidecar/openclaw_sidecar.compose.yml /root/openclaw-assistant-backend/
cp deploy/openclaw-sidecar/openclaw_sidecar.config.json5 /root/openclaw-assistant-backend/
cp deploy/openclaw-sidecar/.env.example /root/openclaw-assistant-backend/.env
```

3. Edit `/root/openclaw-assistant-backend/.env` and set a strong token.

```env
OPENCLAW_GATEWAY_TOKEN=replace_with_strong_random_token
```

4. Start sidecar.

```bash
cd /root/openclaw-assistant-backend
docker compose -f openclaw_sidecar.compose.yml up -d
docker compose -f openclaw_sidecar.compose.yml ps
```

5. Install/enable plugin in AstrBot and set plugin config.
- `gateway_primary_url=http://127.0.0.1:18789`
- `gateway_bearer_token=<same as OPENCLAW_GATEWAY_TOKEN>`
- `gateway_agent_id=<your OpenClaw agent id>`

6. Verify in QQ.
- `/助手 诊断`
- `/助手 帮助`

## Key Runtime Defaults
- Trigger prefix: `助手`
- Unauthorized feedback: silent
- Privacy masking: enabled
- High-risk confirmation: enabled
- Execution strategy: allow all + blacklist
- Effective concurrency: forced `1` (config value kept only for compatibility)

## Screenshots
- Screenshot guide: `docs/screenshots/README.md`
- Suggested filenames:
- `docs/screenshots/diag-ok.png`
- `docs/screenshots/diag-auth-failed.png`
- `docs/screenshots/help-menu.png`
- `docs/screenshots/high-risk-confirm.png`

## Security and Risk Statement
- This project can expose host command and file actions at higher permission levels (`L3/L4`).
- Misconfiguration may cause destructive operations on the host.
- Do not run with high permission in public groups.
- Keep `high_risk_confirm_enabled=true` unless you fully accept the risk.
- Always keep `blacklist_shell_patterns` and sensitive command/tool blacklists maintained.

## Known Constraints (V1)
- QQ only.
- No standalone adapter service.
- No complex web admin panel.
- On low-memory hosts, heavy tasks are rejected and read-only downgrade may be forced.

## License
MIT
