# OpenClaw Sidecar Runbook（V1）

## 1. 目标
将 OpenClaw Gateway 作为 Astr 插件的本机 sidecar 运行，不新增独立 Adapter 服务。

## 2. 目录约定
- 运行根目录：`/root/openclaw-assistant-backend`
- 编排文件：`/root/openclaw-assistant-backend/openclaw_sidecar.compose.yml`
- 配置文件：`/root/openclaw-assistant-backend/openclaw_sidecar.config.json5`
- 环境文件：`/root/openclaw-assistant-backend/.env`
- 数据目录：`/root/openclaw-assistant-backend/data/openclaw_home`、`/root/openclaw-assistant-backend/data/openclaw_workspace`

## 3. 启动与停止

```bash
cd /root/openclaw-assistant-backend
cp -n .env.example .env
# 编辑 .env，写入 OPENCLAW_GATEWAY_TOKEN（并确认 OPENCLAW_VERSION）

docker compose -f openclaw_sidecar.compose.yml up -d
docker compose -f openclaw_sidecar.compose.yml ps
```

停止：

```bash
cd /root/openclaw-assistant-backend
docker compose -f openclaw_sidecar.compose.yml down
```

## 4. 健康检查

```bash
# 容器状态
docker ps --filter name=openclaw-gateway

# 本机端口
ss -lntp | rg 18789

# 网关连通性（无 token 时应 401/403）
curl -i -X POST http://127.0.0.1:18789/v1/responses \
  -H 'Content-Type: application/json' \
  -d '{"model":"openclaw:main","stream":false,"input":[{"type":"message","role":"user","content":[{"type":"input_text","text":"ping"}]}]}'
```

## 5. 插件侧最小配置
- `gateway_primary_url=http://127.0.0.1:18789`
- `gateway_bearer_token=<与 .env 中一致>`
- `gateway_agent_id=<你的 agent id>`

## 6. 常见故障与处理
- `auth_failed`
  - 原因：插件 token 与 sidecar token 不一致（常见为 401/403）
  - 处理：统一 `gateway_bearer_token` 与 `.env` 中 `OPENCLAW_GATEWAY_TOKEN`
- `responses_endpoint_not_enabled_or_not_found`
  - 原因：`openclaw_sidecar.config.json5` 未启用 responses
  - 处理：确认 `gateway.http.endpoints.responses.enabled=true` 并重启容器
- `network_or_unreachable`
  - 原因：容器未启动、端口未监听、网络问题
  - 处理：检查 `docker compose ps`、`docker logs openclaw-gateway`、`ss -lntp`

## 7. 版本升级与回滚
- 本模板默认固定 `OPENCLAW_VERSION=2026.2.9`，避免 `latest` 漂移。
- 升级：修改 `.env` 中 `OPENCLAW_VERSION` 后执行：

```bash
cd /root/openclaw-assistant-backend
docker compose -f openclaw_sidecar.compose.yml up -d --force-recreate
```

- 回滚：将 `OPENCLAW_VERSION` 改回上一版本并重建容器。

## 8. 稳定性约束（本机低配必须遵守）
- 插件并发固定为 `1`
- 可用内存 `<512MB` 拒绝重任务
- 可用内存 `<350MB` 强制只读（L1）
- 连续失败 2 次熔断 60 秒
