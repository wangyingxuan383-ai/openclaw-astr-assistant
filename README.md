# OpenClaw × Astr QQ Assistant (V1)

OpenClaw Agent capability packaged as an AstrBot plugin for QQ, with strict permission, privacy, and audit boundaries.

## Highlights
- Astr plugin first, OpenClaw gateway sidecar
- QQ-focused trigger and authorization policy
- Security chain: permission -> blacklist -> high-risk confirmation -> execute -> audit
- Runtime stability guards for low-memory hosts

## Repository Layout
- `plugin/astrbot_plugin_openclaw_assistant/`: Astr plugin source and schema
- `deploy/openclaw-sidecar/`: OpenClaw sidecar compose/config templates and runbook
- `OPENCLAW_ASTR_ASSISTANT_MASTER_PLAN.md`: frozen master plan and changelog

## Quick Start
1. Deploy sidecar with `deploy/openclaw-sidecar/*` to `/root/openclaw-assistant-backend`.
2. Set plugin config:
- `gateway_primary_url=http://127.0.0.1:18789`
- `gateway_bearer_token=<same as OPENCLAW_GATEWAY_TOKEN>`
- `gateway_agent_id=<your agent id>`
3. Run `/助手 诊断` in QQ.

## Runtime Notes
- Effective parallel turns are forced to `1`.
- Memory guardrails:
- `<512MB` reject heavy tasks
- `<350MB` force read-only (L1)

## License
MIT
