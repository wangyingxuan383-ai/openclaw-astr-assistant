# OpenClaw Astr 助手插件（QQ / V1）

## 命令
- `/助手 <任务>` 或 `助手 <任务>`（裸命令必须“前缀+空格”）
- `/助手 诊断`
- `/助手 会话重置`
- `/助手 模型导出JSON`
- `/助手 帮助`
- `/助手 确认 <token>`

## 触发与鉴权
- 默认仅 Astr 管理员或 `whitelist_user_ids` 可触发。
- 群聊默认不触发；仅 `whitelist_group_ids` 且触发者为管理员/白名单时触发。
- 未授权默认静默（`silent_unauthorized=true`）。

## 权限分级
- `L0`: 普通对话
- `L1`: 只读 Astr 状态/配置摘要/能力目录
- `L2`: 可执行 Astr 命令与 Astr 工具
- `L3`: 主机操作（非 root 预期）
- `L4`: root 级动作

## 安全策略
- 执行链固定：权限校验 -> 黑名单校验 -> 高危确认 -> 执行 -> 审计。
- 黑名单维度：`blacklist_plugins` / `blacklist_commands` / `blacklist_tools` / `blacklist_shell_patterns`。
- 高危确认默认开启：命中高危动作后返回 token，需 `/助手 确认 <token>` 才可执行。
- 审计日志：`data/plugin_data/astrbot_plugin_openclaw_assistant/audit.jsonl`（追加写入）。

## 资源保护
- V1 运行并发固定为 `1`（`max_parallel_turns` 仅兼容保留，诊断中会显示是否被钳制）。
- 可用内存 `<512MB`：拒绝图片/大文件/重任务。
- 可用内存 `<350MB`：强制降级为只读（L1）。

## Root 运行说明
- 若插件进程本身是 root：
- `host_exec` 在 L3 会尝试自动降权到非 root 用户执行。
- `host_file_op` 在 L3 会被安全门禁直接拒绝，避免“名义 L3、实际 root 写盘”。
- 需要 root 写盘能力时，请将权限提升至 `L4`。

## 网关配置（插件侧）
至少配置：
- `gateway_primary_url`（建议 `http://127.0.0.1:18789`）
- `gateway_bearer_token`
- `gateway_agent_id`

可选：
- `gateway_backup_url`（不配则主网关故障时直接失败）
- `tool_call_timeout_seconds`（`astr_exec_tool` 超时控制，默认 45s）

未配置主网关时，插件进入“仅诊断模式”。

## Sidecar 目录（独立后端路径）
运行目录固定建议：
- `/root/openclaw-assistant-backend/openclaw_sidecar.compose.yml`
- `/root/openclaw-assistant-backend/openclaw_sidecar.config.json5`
- `/root/openclaw-assistant-backend/.env`
- `/root/openclaw-assistant-backend/data/openclaw_home`
- `/root/openclaw-assistant-backend/data/openclaw_workspace`

仓库模板目录：
- `deploy/openclaw-sidecar/openclaw_sidecar.compose.yml`
- `deploy/openclaw-sidecar/openclaw_sidecar.config.json5`
- `deploy/openclaw-sidecar/.env.example`
- `deploy/openclaw-sidecar/RUNBOOK.md`

## Sidecar 快速启动
在 `/root/openclaw-assistant-backend` 下：

```bash
cp -n .env.example .env
# 编辑 .env：设置 OPENCLAW_GATEWAY_TOKEN

docker compose -f openclaw_sidecar.compose.yml up -d
docker compose -f openclaw_sidecar.compose.yml ps
```

## 诊断排障要点
`/助手 诊断` 会显示：
- 并发钳制状态（配置值 vs 实际值）
- Responses 端点状态（`auth_failed` / `responses_endpoint_not_enabled_or_not_found` / `network_or_unreachable` 等）
- Node 版本告警、执行器探测、熔断状态与拦截计数

常见错误码：
- `auth_failed`: token 不一致或缺失
- `responses_endpoint_not_enabled_or_not_found`: sidecar 未开启 responses 端点
- `network_or_unreachable`: 端口未监听/容器未启动/网络异常
