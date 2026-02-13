# OpenClaw Astr 后端仓（sidecar + backend-service）

本仓库是 **后端独立仓**，用于 Astr 插件配套的运行与运维：
- OpenClaw Gateway sidecar（Node22 Docker）
- backend-service（Web 状态页 / 模型同步页 / Codex 执行器 API）

插件仓：`https://github.com/wangyingxuan383-ai/astrbot_plugin_openclaw_assistant`

## 目录结构
- `deploy/openclaw-sidecar/`：Gateway sidecar compose/config/runbook
- `backend-service/`：独立轻后端服务（FastAPI + Jinja2 + sqlite）
- `OPENCLAW_ASTR_ASSISTANT_MASTER_PLAN.md`：主计划与变更记录

## 运行根目录约定
后端运行目录固定为：`/root/openclaw-assistant-backend`

建议目录：
- `/root/openclaw-assistant-backend/openclaw_sidecar.compose.yml`
- `/root/openclaw-assistant-backend/openclaw_sidecar.config.json5`
- `/root/openclaw-assistant-backend/.env`
- `/root/openclaw-assistant-backend/backend-service/`
- `/root/openclaw-assistant-backend/data/openclaw_home`
- `/root/openclaw-assistant-backend/data/openclaw_workspace`
- `/root/openclaw-assistant-backend/data/backend_state.db`
- `/root/openclaw-assistant-backend/logs/backend/`

## 环境要求
- Linux + Docker + Docker Compose
- Python 3.10+
- 资源建议：
- 最低：`2 vCPU / 4GiB RAM`
- 推荐：`4 vCPU / 8GiB RAM`

## 1) 部署 sidecar

```bash
mkdir -p /root/openclaw-assistant-backend/data/openclaw_home \
         /root/openclaw-assistant-backend/data/openclaw_workspace \
         /root/openclaw-assistant-backend/logs/backend

cp deploy/openclaw-sidecar/openclaw_sidecar.compose.yml /root/openclaw-assistant-backend/
cp deploy/openclaw-sidecar/openclaw_sidecar.config.json5 /root/openclaw-assistant-backend/
cp deploy/openclaw-sidecar/.env.example /root/openclaw-assistant-backend/.env

# 编辑 /root/openclaw-assistant-backend/.env
# OPENCLAW_GATEWAY_TOKEN=...
# OPENCLAW_VERSION=2026.2.9

cd /root/openclaw-assistant-backend
docker compose -f openclaw_sidecar.compose.yml up -d
```

## 2) 部署 backend-service

```bash
cp -r backend-service /root/openclaw-assistant-backend/backend-service
cd /root/openclaw-assistant-backend/backend-service

python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt

# 使用运行根目录 .env（包含 BACKEND_API_TOKEN）
export BACKEND_ENV_FILE=/root/openclaw-assistant-backend/.env

uvicorn app.main:app --host 127.0.0.1 --port 18889
```

如果 `python3 -m venv` 报 `ensurepip is not available`，先安装：

```bash
apt update && apt install -y python3.12-venv
```

## backend-service 主要接口
- `GET /web/status`
- `GET /web/models`
- `GET /api/v1/status`
- `GET /api/v1/executors`
- `GET /api/v1/models`
- `POST /api/v1/models/import-astr`
- `POST /api/v1/executor/jobs`
- `GET /api/v1/executor/jobs/{job_id}`
- `POST /api/v1/executor/jobs/{job_id}/cancel`

说明：
- `/api/*` 默认 Bearer Token 鉴权。
- Web/API 建议仅监听 `127.0.0.1`。
- 模型同步只保存 provider 元数据，不保存 API Key/Token。

## 插件侧最小对接
在插件配置中至少填写：
- `gateway_primary_url=http://127.0.0.1:18789`
- `gateway_bearer_token=<OPENCLAW_GATEWAY_TOKEN>`
- `backend_api_url=http://127.0.0.1:18889`
- `backend_api_token=<BACKEND_API_TOKEN>`

验证命令：
- `/助手 诊断`
- `/助手 模型导出JSON`

## 版本固定与升级
- sidecar 默认固定：`OPENCLAW_VERSION=2026.2.9`
- 升级：修改 `.env` 中 `OPENCLAW_VERSION` 后重建容器
- 回滚：改回旧版本并重建

## 安全说明
- sidecar 端口保持 `127.0.0.1:18789`，不直接暴露公网。
- backend-service 建议 `127.0.0.1:18889`，不直接暴露公网。
- `BACKEND_API_TOKEN` 与 `OPENCLAW_GATEWAY_TOKEN` 必须强随机且分离。

## License
MIT
