# `OPENCLAW_ASTR_ASSISTANT_MASTER_PLAN.md` 落盘方案（冻结版）

## Summary
把当前已确认的架构、边界、权限、稳定性约束、性能预算、实施里程碑、测试标准、风险与回滚策略，统一固化到一个主文件中，后续任何实现都以此为唯一基线，避免需求漂移和遗忘。

## 记录文件路径（约定）
- 建议路径：`/root/AstrBot/OPENCLAW_ASTR_ASSISTANT_MASTER_PLAN.md`
- 约定：后续仅追加版本段，不覆盖历史决策。

## 文件内容（建议一次性写入）
### 1. 项目目标与边界
- 目标：构建“私人性质”的 AstrBot QQ 助手，主入口在 Astr 插件，集成 OpenClaw Agent 能力。
- 用户范围：默认仅 Astr 管理员；可加白名单用户。
- 群策略：默认群聊不触发；仅白名单群 + 白名单/管理员触发。
- 明确不做：
  - 唤醒触发
  - OpenClaw CLI 全量透传
  - 复杂后端管理台（V1）

### 2. 已冻结核心决策
- 架构：Astr 插件主导 + 轻量 Adapter 后端 + OpenClaw 推理层。
- 命令触发：`斜杠 + 裸命令`，且裸命令必须“前缀开头+空格”。
- 前缀：可配置，默认 `助手`。
- 执行策略：`全量执行 + 黑名单`。
- 黑名单维度：插件 / 命令 / 工具 / Shell危险模式。
- 权限模型：全局单级权限（L0-L4）。
- 高危确认：默认开启，可配置关闭。
- OpenClaw模式：Gateway优先，失败回退Local。
- 隐私：默认脱敏；管理员/白名单私聊、管理群关闭脱敏。
- 未授权反馈：统一静默。
- Web（V1）：状态页 + 模型同步页（手动JSON导入）。

### 3. 系统架构与职责
- Astr 插件：
  - 权限判定、触发判定、隐私策略、Astr联动（读/执行）、消息渲染。
- Adapter 后端：
  - OpenClaw 调用编排、执行器调度（Codex/Gemini/Shell）、API与Web。
- OpenClaw：
  - Agent 推理与工具循环能力。

### 4. 命令面定义（V1）
- `/助手 <任务>`、`助手 <任务>`：主入口。
- `/助手 诊断`：状态+健康+执行器可用性+能力摘要（合并输出）。
- `/助手 会话重置`：清空当前会话上下文。
- `/助手 模型导出JSON`：导出可供Web导入的 Astr provider 映射数据。
- `/助手 帮助`：展示命令与当前权限。

### 5. 权限分级定义
- `L0`：普通对话
- `L1`：读取 Astr 状态/配置摘要/能力目录
- `L2`：执行 Astr 命令与工具（黑名单仍生效）
- `L3`：主机非root执行与文件改写
- `L4`：root级动作
- 执行前统一流程：权限校验 -> 黑名单校验 -> 高危确认（若开启） -> 审计记录

### 6. 隐私与敏感信息策略
- 默认脱敏字段：token/key/cookie/secret/password/路径敏感段等。
- 关闭脱敏场景：
  - 管理员或白名单私聊
  - 管理群（`manage_group_ids`）
- 注意：管理群“隐私放开”不等于“高危放开”；高危仍按权限与确认策略执行。

### 7. Public API / 接口契约（Adapter）
- `POST /api/v1/turn/start`
- `POST /api/v1/turn/continue`
- `GET /api/v1/status`
- `GET /api/v1/executors`
- `POST /api/v1/models/import-astr`
- `GET /web/status`
- `GET /web/models`
- 响应统一字段：
  - `state`, `reply_text`, `reply_media[]`, `runtime_mode`, `trace_id`, `latency_ms`, `errors[]`

### 8. 执行器策略
- 执行器优先级改为配置项枚举（非手填）：
  - `codex_then_gemini_then_shell`
  - `gemini_then_codex_then_shell`
  - `shell_only`
- 启动自检：`codex/gemini/openclaw/node版本`，结果写入诊断输出。

### 9. 稳定性与性能预算（必须记录）
- 当前机器实况（样例记录）：
  - 2 vCPU / 2.4GiB RAM / Swap 1.2GiB
  - 可用内存偏低，不适合重并发+高权限混跑
  - Node 当前 v18，不满足 OpenClaw Node>=22 要求
- 资源建议：
  - 最低可跑：2 vCPU / 4GiB RAM
  - 推荐稳定：4 vCPU / 8GiB RAM
- 运行保护：
  - 并发上限 `1`
  - Local回退并发 `1`
  - 内存阈值触发降级（禁重任务、禁回退可选）

### 10. 风险清单与缓解
- 风险：配置漂移（Astr与后端模型不同步）
  - 缓解：`模型导出JSON + Web导入` + 诊断提示“最后同步时间”
- 风险：全量执行误操作
  - 缓解：黑名单+高危确认+审计
- 风险：低内存导致超时/OOM
  - 缓解：限流+回退断路器+降级策略
- 风险：静默拒绝导致排障难
  - 缓解：管理员诊断页记录拦截计数（不对外提示）

### 11. 分阶段实施计划
- Phase 1：主链路与策略框架（主入口/诊断/会话重置/权限拦截）
- Phase 2：Astr联动增强（读全量+执行动作流）
- Phase 3：高权限执行（L3/L4、确认、审计、执行器）
- Phase 4：Web同步与运维收口（状态页+模型导入）

### 12. 测试与验收
- 功能：私聊、群聊白名单、会话重置、回退链路
- 安全：权限边界、黑名单命中、高危确认
- 稳定：并发1长会话、网关故障回退、低内存降级
- 隐私：脱敏与豁免场景验证
- 运维：诊断输出完整、执行器状态可见

### 13. 审计与追踪
- 审计字段：时间、操作者、会话、动作类型、参数摘要、是否高危、确认结果、执行结果、耗时、错误。
- 日志分级：info（常规）/warn（策略拦截）/error（执行失败）。

### 14. 变更记录模板（文件末尾固定）
- `## Changelog`
- `### v0.1 (日期)`
  - 新增
  - 调整
  - 删除
  - 风险评估变化
  - 资源预算变化

## Important API/Interface Changes（汇总）
- 新增 Adapter API（`turn/start`, `turn/continue`, `status`, `executors`, `models/import-astr`）。
- 新增统一动作类型 `Action`（`astr_read`, `astr_exec_command`, `astr_exec_tool`, `host_exec`, `host_file_op`）。
- 新增插件配置项：触发、权限、隐私、黑名单、执行器优先级、回退策略、后端连接参数。

## Test Cases and Scenarios（最低清单）
- 未授权私聊/群聊静默拦截
- 白名单用户私聊成功
- 白名单群+白名单用户成功
- `L1`可读但不可写，`L3/L4`写操作边界正确
- 黑名单拦截生效（插件/命令/工具/Shell）
- 高危确认开/关两态正确
- Gateway不可用时成功回退Local
- 诊断输出包含执行器探测与Node版本告警
- 脱敏规则在四类场景均符合预期

## Assumptions and Defaults
- 默认前缀：`助手`
- 默认高危确认：开启
- 默认隐私脱敏：开启
- 默认未授权反馈：静默
- 默认执行策略：全量执行+黑名单
- 默认OpenClaw模式：Gateway优先+Local回退
- 默认Web同步方式：手动JSON导入

## Changelog

### v0.2 (2026-02-12)
- 新增
  - V1 路线冻结为 `Astr 插件直连 OpenClaw Gateway HTTP`，不新增独立 Adapter 进程。
  - 新增 `openclaw_sidecar.compose.yml`，通过 Node22 sidecar 解决宿主 Node18 与 OpenClaw 的版本差异。
  - 插件命令落地：`/助手 <任务>`、`/助手 诊断`、`/助手 会话重置`、`/助手 模型导出JSON`、`/助手 帮助`、`/助手 确认 <token>`。
  - 网关熔断策略落地：主网关连续失败 2 次熔断 60 秒；未配置备网关则不回退。
  - 安全骨架落地：全量执行 + 黑名单 + 高危确认 + 审计。
- 调整
  - 模式默认更新为 `Gateway 主优先 + 可选备网关`，取消 V1 的 Local 回退承诺。
  - 稳定性补丁明确化：并发上限 `1`，内存阈值 `<512MB` 禁重任务，`<350MB` 强制 L1。
  - Root 运行安全门：L3 下 `host_exec` 自动尝试降权；`host_file_op` 在 root 进程下拒绝执行（需 L4）。
- 删除
  - V1 范围内不实现复杂 Web 管理台与 OpenClaw CLI 全量透传。
- 风险评估变化
  - 新增风险：插件进程若以 root 运行会放大误操作影响；通过 L3 root 门禁与高危确认缓解。
  - 新增风险：无备网关时故障不可自动切换；通过诊断告警与审计暴露。
- 资源预算变化
  - 延续基线：2 vCPU / 2.4GiB RAM / 1.2GiB Swap 可开发验证但稳定性偏紧。
  - 推荐稳定资源维持：4 vCPU / 8GiB RAM。

### v0.3 (2026-02-12)
- 新增
  - `astr_exec_command` 从“仅 reload 插件”扩展为通用命令调度（基于 `CommandFilter` 解析参数并执行 handler）。
  - `astr_exec_tool` 从占位实现升级为真实调度 Astr LLM 工具（支持超时控制）。
  - 新增配置项 `tool_call_timeout_seconds`（默认 45s）。
  - 新增 `openclaw_sidecar.config.json5`，显式设置 `gateway.auth.mode=token` 与 `gateway.http.endpoints.responses.enabled=true`。
- 调整
  - 运行并发策略收口：`max_parallel_turns` 作为兼容保留项，运行时强制钳制为 `1`。
  - `/助手 诊断` 增加并发钳制状态、Responses 端点错误分类（鉴权/端点未启用/网络）与可操作告警。
  - 更新 sidecar compose，改为通过 `OPENCLAW_CONFIG_PATH` 加载配置文件，避免依赖默认行为。
- 删除
  - 移除 `astr_exec_tool` 的“V1 暂不开放”占位路径。
- 风险评估变化
  - 新增风险：命令/工具全量调度带来更高误调用风险；通过黑名单、高危确认、递归防护与审计控制。
  - 降低风险：Responses 端点启用方式从“隐式默认”改为“显式配置”，部署不确定性下降。
- 资源预算变化
  - 并发固定 `1` 后高峰吞吐下降，但低内存场景稳定性提升。

### v0.4 (2026-02-12)
- 新增
  - 后端部署根目录冻结为 `/root/openclaw-assistant-backend`，实现运行目录与 AstrBot 代码目录解耦。
  - 新增 sidecar 运行环境文件约定 `/root/openclaw-assistant-backend/.env`（至少包含 `OPENCLAW_GATEWAY_TOKEN`）。
  - 新增运维手册 `deploy/openclaw-sidecar/RUNBOOK.md`（启动/停止/健康检查/故障码）。
- 调整
  - sidecar compose 更新为独立根目录挂载策略，默认卷路径均指向 `/root/openclaw-assistant-backend/data/*`。
  - sidecar 模板统一收口到 `deploy/openclaw-sidecar/` 以便公开仓库发布。
  - 插件 README 中 sidecar 路径示例切换为独立后端路径。
- 删除
  - 不再建议从 `/root/AstrBot` 目录直接运行 sidecar。
- 风险评估变化
  - 降低风险：后端运行态与业务代码解耦后，升级/回滚 sidecar 时对 AstrBot 主目录污染更小。
  - 保留风险：低内存机器仍需并发=1与内存闸门，否则 sidecar 叠加运行可能触发 swap 抖动。
- 资源预算变化
  - 基线结论不变：2 vCPU/2.4GiB RAM 仅适合低并发验证；推荐提升至 4GiB+ 内存。

### v0.5 (2026-02-12)
- 新增
  - 初始化公开发布仓库骨架：`/root/openclaw-astr-assistant`（MIT、README、CHANGELOG、.gitignore、插件与sidecar模板）。
- 调整
  - 插件 `gateway_primary_url` 默认值收口为 `http://127.0.0.1:18789`（代码与 schema 一致）。
  - 插件版本更新至 `v0.1.3`。
- 删除
  - 无。
- 风险评估变化
  - 降低风险：默认网关地址一致后，首次部署配置错误概率下降。
- 资源预算变化
  - 无新增变化。
