# OpenClaw × Astr QQ 助手（V1）

将 OpenClaw Agent 能力封装为 AstrBot QQ 插件，内置权限、隐私、确认与审计安全边界。

## 仓库定位（先回答你的问题）
- 当前是**一个仓库**，不是“只有插件端”或“只有后端”。
- 该仓库同时包含：
- `plugin/`：插件端代码（Astr 内运行）。
- `deploy/openclaw-sidecar/`：后端 sidecar 的部署模板（Docker 网关）。
- 这样做的目的：发布、版本对齐、问题追踪在同一处完成；避免插件和部署脚本版本漂移。

## 项目架构
- 主入口：Astr 插件（命令触发、权限判断、隐私处理、审计记录）。
- 推理后端：OpenClaw Gateway sidecar（本机 Docker 容器）。
- 通信方式：插件通过 `http://127.0.0.1:18789` 调用网关。

## 目录结构
- `plugin/astrbot_plugin_openclaw_assistant/`：插件源码、配置 schema、插件说明。
- `deploy/openclaw-sidecar/`：sidecar compose、配置、`.env` 模板、运维手册。
- `docs/screenshots/`：截图说明与建议命名。
- `OPENCLAW_ASTR_ASSISTANT_MASTER_PLAN.md`：冻结基线与变更记录。

## 环境要求
- Linux + Docker + Docker Compose。
- AstrBot 与 QQ 适配器已运行。
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

5. 在 AstrBot 插件配置中填写：
- `gateway_primary_url=http://127.0.0.1:18789`
- `gateway_bearer_token=<与 OPENCLAW_GATEWAY_TOKEN 一致>`
- `gateway_agent_id=<你的 OpenClaw agent id>`

6. 在 QQ 内验证：
- `/助手 诊断`
- `/助手 帮助`

## 运行默认策略
- 默认前缀：`助手`
- 默认未授权反馈：静默
- 默认隐私脱敏：开启
- 默认高危确认：开启
- 默认执行策略：全量执行 + 黑名单
- 运行并发：强制 `1`（配置值仅兼容保留）

## 截图说明
- 指南文件：`docs/screenshots/README.md`
- 建议截图名：
- `docs/screenshots/diag-ok.png`
- `docs/screenshots/diag-auth-failed.png`
- `docs/screenshots/help-menu.png`
- `docs/screenshots/high-risk-confirm.png`

## 风险声明
- 在 `L3/L4` 权限下可触发主机命令与文件操作，配置错误会放大风险。
- 不要在公共群中开启高权限执行。
- 除非完全理解后果，不建议关闭 `high_risk_confirm_enabled`。
- 请持续维护 `blacklist_shell_patterns` 与工具/命令黑名单。

## V1 限制
- 仅支持 QQ。
- 不提供独立 Adapter 服务。
- 不提供复杂 Web 管理台。
- 低内存场景会触发重任务拒绝与只读降级。

## License
MIT
