# backend-service (V1.1)

独立轻后端服务，提供：
- `GET /web/status`
- `GET /web/models`
- `POST /web/models/pull-astr`
- `POST /api/v1/models/import-astr`
- `POST /api/v1/models/pull-astr`
- `POST /api/v1/executor/jobs`

## 快速启动

```bash
cd /root/openclaw-astr-assistant/backend-service
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp -n .env.example /root/openclaw-assistant-backend/.env
export BACKEND_ENV_FILE=/root/openclaw-assistant-backend/.env
uvicorn app.main:app --host 127.0.0.1 --port 18889
```

如果 `python3 -m venv` 报错 `ensurepip is not available`，先安装：

```bash
apt update && apt install -y python3.12-venv
```

## 默认安全策略

- API 统一 Bearer Token 鉴权。
- Web/API 仅建议监听 `127.0.0.1`。
- 模型同步只保存元数据，不保存密钥。
- 执行器并发固定 `1`，Gemini 在 V1.1 预留未启用。

## Web 一键拉取 Astr 配置

- 模型页按钮：`POST /web/models/pull-astr`
- API：`POST /api/v1/models/pull-astr`（需要 Bearer Token）
- 拉取优先级（默认）：
1. `ASTRBOT_CMD_CONFIG_PATH`（`/root/AstrBot/data/cmd_config.json`，`utf-8-sig` 兼容）
2. `ASTRBOT_PLUGIN_EXPORT_PATH`（插件导出 JSON 回退）
- 仅同步 provider 元数据字段：`provider_id/model/provider_type/base_url`。
- 默认只同步 `enable=true` 的 provider（`ASTR_PULL_REQUIRE_ENABLED_PROVIDER=true`）。
