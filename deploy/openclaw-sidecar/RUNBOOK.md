# OpenClaw 后端 Runbook（sidecar + backend-service）

## 1. 目标
在不引入复杂 Adapter 的前提下，运行两类后端组件：
- OpenClaw Gateway sidecar（服务插件主链路）
- backend-service（Web 状态页/模型同步页/Codex 执行器 API）

## 2. 目录约定
- 运行根目录：`/root/openclaw-assistant-backend`
- sidecar 编排：`/root/openclaw-assistant-backend/openclaw_sidecar.compose.yml`
- sidecar 配置：`/root/openclaw-assistant-backend/openclaw_sidecar.config.json5`
- 环境文件：`/root/openclaw-assistant-backend/.env`
- backend-service：`/root/openclaw-assistant-backend/backend-service`
- 数据目录：
- `/root/openclaw-assistant-backend/data/openclaw_home`
- `/root/openclaw-assistant-backend/data/openclaw_workspace`
- `/root/openclaw-assistant-backend/data/backend_state.db`
- 日志目录：`/root/openclaw-assistant-backend/logs/backend`

## 3. 统一准备

```bash
mkdir -p /root/openclaw-assistant-backend/data/openclaw_home \
         /root/openclaw-assistant-backend/data/openclaw_workspace \
         /root/openclaw-assistant-backend/logs/backend
```

`.env` 至少包含：

```env
OPENCLAW_GATEWAY_TOKEN=replace_with_strong_random_token
OPENCLAW_VERSION=2026.2.9
BACKEND_API_TOKEN=replace_with_strong_random_token
```

## 4. sidecar 启动/停止

```bash
cd /root/openclaw-assistant-backend
cp -n .env.example .env
# 编辑 .env

docker compose -f openclaw_sidecar.compose.yml up -d
docker compose -f openclaw_sidecar.compose.yml ps
```

停止：

```bash
cd /root/openclaw-assistant-backend
docker compose -f openclaw_sidecar.compose.yml down
```

## 5. backend-service 启动/停止

```bash
cp -r /root/openclaw-astr-assistant/backend-service /root/openclaw-assistant-backend/backend-service
cd /root/openclaw-assistant-backend/backend-service

python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt

export BACKEND_ENV_FILE=/root/openclaw-assistant-backend/.env
uvicorn app.main:app --host 127.0.0.1 --port 18889
```

若 `python3 -m venv` 提示 `ensurepip is not available`，先执行：

```bash
apt update && apt install -y python3.12-venv
```

停止：按运行方式结束进程（前台 Ctrl+C，或由 systemd/supervisor 管理）。

## 6. 健康检查

```bash
# sidecar
ss -lntp | rg 18789
curl -i -X POST http://127.0.0.1:18789/v1/responses \
  -H 'Content-Type: application/json' \
  -d '{"model":"openclaw:main","stream":false,"input":[{"type":"message","role":"user","content":[{"type":"input_text","text":"ping"}]}]}'

# backend-service
ss -lntp | rg 18889
curl -i http://127.0.0.1:18889/web/status
curl -i http://127.0.0.1:18889/web/models
curl -i http://127.0.0.1:18889/api/v1/status
```

说明：
- 最后一条未带 token，预期 `401/403`。
- 带 token 调试示例：

```bash
curl -i http://127.0.0.1:18889/api/v1/status \
  -H "Authorization: Bearer ${BACKEND_API_TOKEN}"
```

## 7. 插件侧最小配置
- `gateway_primary_url=http://127.0.0.1:18789`
- `gateway_bearer_token=<OPENCLAW_GATEWAY_TOKEN>`
- `backend_api_url=http://127.0.0.1:18889`
- `backend_api_token=<BACKEND_API_TOKEN>`

验证：
- `/助手 诊断`
- `/助手 模型导出JSON`

## 8. 常见故障与处理
- `auth_failed`
- 原因：token 不一致（sidecar 或 backend-service）
- 处理：统一插件配置与 `.env` 中 token
- `responses_endpoint_not_enabled_or_not_found`
- 原因：sidecar 未启用 responses 端点
- 处理：确认 `gateway.http.endpoints.responses.enabled=true`
- `backend_unreachable`
- 原因：backend-service 未启动或地址错误
- 处理：检查 `ss -lntp | rg 18889`、进程日志
- `executor_not_available`
- 原因：后端未检测到 `codex` 二进制
- 处理：安装/修复 Codex CLI 或调整 `EXECUTOR_CODEX_BIN`

## 9. 版本升级与回滚
- sidecar 默认固定：`OPENCLAW_VERSION=2026.2.9`
- 升级 sidecar：改 `.env` 后执行

```bash
cd /root/openclaw-assistant-backend
docker compose -f openclaw_sidecar.compose.yml up -d --force-recreate
```

- 回滚：把 `OPENCLAW_VERSION` 改回旧值并重建。
- backend-service 回滚：切回仓库旧 tag，并重新部署 `backend-service/` 目录。

## 10. 稳定性约束（低配机器必须遵守）
- 插件并发固定为 `1`
- backend-service worker 并发固定为 `1`
- 可用内存 `<512MB` 拒绝重任务
- 可用内存 `<350MB` 强制只读（L1）
- 主网关连续失败 2 次熔断 60 秒
