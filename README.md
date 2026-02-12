# OpenClaw Astr Sidecar Deploy

本仓库现在是**纯后端部署仓**，仅维护 OpenClaw Gateway sidecar 的部署模板与运维文档。

## 仓库边界
- 本仓库：后端 sidecar 部署与运维（Docker）。
- 插件仓库（已独立）：`https://github.com/wangyingxuan383-ai/astrbot_plugin_openclaw_assistant`

## 目录结构
- `deploy/openclaw-sidecar/`：sidecar compose、配置、`.env` 模板、运维手册。
- `OPENCLAW_ASTR_ASSISTANT_MASTER_PLAN.md`：主计划与变更记录。

## 环境要求（后端）
- Linux + Docker + Docker Compose。
- AstrBot 与 QQ 适配器已运行（用于插件侧调用）。
- 资源建议：
- 最低：`2 vCPU / 4GiB RAM`
- 推荐：`4 vCPU / 8GiB RAM`

## 安装步骤
1. 准备后端运行目录：

```bash
mkdir -p /root/openclaw-assistant-backend/data/openclaw_home \
         /root/openclaw-assistant-backend/data/openclaw_workspace \
         /root/openclaw-assistant-backend/logs
```

2. 拷贝 sidecar 部署文件：

```bash
cp deploy/openclaw-sidecar/openclaw_sidecar.compose.yml /root/openclaw-assistant-backend/
cp deploy/openclaw-sidecar/openclaw_sidecar.config.json5 /root/openclaw-assistant-backend/
cp deploy/openclaw-sidecar/.env.example /root/openclaw-assistant-backend/.env
```

3. 编辑 `/root/openclaw-assistant-backend/.env`：

```env
OPENCLAW_GATEWAY_TOKEN=replace_with_strong_random_token
```

4. 启动 sidecar：

```bash
cd /root/openclaw-assistant-backend
docker compose -f openclaw_sidecar.compose.yml up -d
docker compose -f openclaw_sidecar.compose.yml ps
```

5. 在 AstrBot 插件配置中填写（插件仓见上方链接）：
- `gateway_primary_url=http://127.0.0.1:18789`
- `gateway_bearer_token=<与 OPENCLAW_GATEWAY_TOKEN 一致>`
- `gateway_agent_id=<你的 OpenClaw agent id>`

6. 在 QQ 内验证：
- `/助手 诊断`
- `/助手 帮助`

## 风险声明（后端）
- sidecar 对外仅映射本机回环端口：`127.0.0.1:18789`，不要直接暴露公网。
- `OPENCLAW_GATEWAY_TOKEN` 必须使用强随机值，并与插件配置严格一致。
- 低内存机器上请维持插件并发=1和内存闸门策略。

## License
MIT
