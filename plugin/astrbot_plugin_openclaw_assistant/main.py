import asyncio
import copy
import inspect
import json
import os
import pwd
import re
import shlex
import shutil
import time
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any, AsyncGenerator, Dict, List, Optional, Tuple

import aiohttp
from aiohttp import ClientTimeout

from astrbot.api import AstrBotConfig, logger
from astrbot.api.event import AstrMessageEvent, filter
from astrbot.api.star import Context, Star, StarTools, register
from astrbot.core.message.message_event_result import CommandResult, MessageEventResult
from astrbot.core.pipeline.context_utils import call_handler
from astrbot.core.star.filter.command import CommandFilter
from astrbot.core.star.filter.command_group import CommandGroupFilter
from astrbot.core.star.star import star_map
from astrbot.core.star.star_handler import (
    EventType,
    StarHandlerMetadata,
    star_handlers_registry,
)


PLUGIN_NAME = "astrbot_plugin_openclaw_assistant"

PERMISSION_ORDER = {
    "L0": 0,
    "L1": 1,
    "L2": 2,
    "L3": 3,
    "L4": 4,
}

DEFAULT_SHELL_BLACKLIST = [
    r"(^|\s)rm\s+-rf\s+/",
    r"(^|\s)mkfs(\.|$)",
    r"(^|\s)dd\s+if=",
    r"(^|\s)shutdown(\s|$)",
    r"(^|\s)reboot(\s|$)",
    r"(^|\s)poweroff(\s|$)",
    r"(^|\s)userdel(\s|$)",
    r"(^|\s)groupdel(\s|$)",
]

SENSITIVE_TEXT_PATTERNS = [
    re.compile(
        r"(?i)\b(token|api[_-]?key|secret|password|passwd|cookie)\b\s*[:=]\s*([^\s,;\"']+)"
    ),
    re.compile(r"(?i)\bBearer\s+[A-Za-z0-9\-._~+/]+=*"),
]

TOOL_ACTION_CATEGORY = {
    "astr_read_status": "astr_read",
    "astr_read_providers": "astr_read",
    "astr_read_plugins": "astr_read",
    "astr_read_commands": "astr_read",
    "astr_exec_command": "astr_exec_command",
    "astr_exec_tool": "astr_exec_tool",
    "host_exec": "host_exec",
    "host_file_op": "host_file_op",
}

DEFAULT_GATEWAY_PRIMARY_URL = "http://127.0.0.1:18789"


def _now_ts() -> float:
    return time.time()


def _iso_now() -> str:
    return datetime.utcnow().isoformat(timespec="seconds") + "Z"


def _safe_int(value: Any, default: int) -> int:
    try:
        return int(value)
    except Exception:
        return default


def _safe_bool(value: Any, default: bool = False) -> bool:
    if isinstance(value, bool):
        return value
    if value is None:
        return default
    s = str(value).strip().lower()
    if s in {"1", "true", "yes", "on", "开"}:
        return True
    if s in {"0", "false", "no", "off", "关"}:
        return False
    return default


def _truncate(s: str, limit: int = 2000) -> str:
    if len(s) <= limit:
        return s
    return s[:limit] + f"\n...[truncated {len(s) - limit} chars]"


@register(
    PLUGIN_NAME,
    "openclaw-astr",
    "OpenClaw × Astr QQ 私人助手（网关直连）",
    "0.1.3",
    "https://github.com/openclaw/openclaw",
)
class OpenClawAstrAssistant(Star):
    def __init__(self, context: Context, config: AstrBotConfig):
        super().__init__(context)
        self.context = context
        self.config = config

        self._data_dir = Path(StarTools.get_data_dir(PLUGIN_NAME))
        self._data_dir.mkdir(parents=True, exist_ok=True)
        self._audit_path = self._data_dir / "audit.jsonl"
        self._session_state_path = self._data_dir / "session_state.json"

        self._session_nonce: Dict[str, int] = {}
        self._pending_confirm_tokens: Dict[str, Dict[str, Any]] = {}
        self._scope_approvals: Dict[str, float] = {}
        self._block_counters: Dict[str, int] = {}

        self._primary_failures = 0
        self._primary_circuit_open_until = 0.0

        self._http_session: Optional[aiohttp.ClientSession] = None

        self._configured_parallel_turns = max(
            1,
            _safe_int(self.config.get("max_parallel_turns", 1), 1),
        )
        self._semaphore_limit = 1
        self._turn_semaphore = asyncio.Semaphore(self._semaphore_limit)
        if self._configured_parallel_turns != 1:
            logger.warning(
                f"[{PLUGIN_NAME}] max_parallel_turns={self._configured_parallel_turns} "
                "已被安全策略强制为 1。"
            )

        self._load_session_state()

    async def terminate(self):
        await self._close_http_session()

    @filter.command("助手")
    @filter.platform_adapter_type(filter.PlatformAdapterType.AIOCQHTTP)
    async def assistant_entry(self, event: AstrMessageEvent):
        if not _safe_bool(self.config.get("enable_slash_command", True), True):
            return
        event.stop_event()
        text = self._event_text(event)
        payload = self._extract_after_command(text, "助手")
        async for res in self._dispatch(event, payload, "slash"):
            yield res

    @filter.event_message_type(filter.EventMessageType.ALL)
    @filter.platform_adapter_type(filter.PlatformAdapterType.AIOCQHTTP)
    async def assistant_plain_entry(self, event: AstrMessageEvent):
        if not _safe_bool(self.config.get("enable_plain_prefix_command", True), True):
            return
        text = self._event_text(event).strip()
        if not text or text.startswith("/"):
            return
        prefix = str(self.config.get("trigger_prefix", "助手")).strip()
        if not prefix:
            return
        if not text.startswith(prefix):
            return
        suffix = text[len(prefix) :]
        if not suffix.startswith(" "):
            return
        payload = suffix.strip()
        event.stop_event()
        async for res in self._dispatch(event, payload, "plain"):
            yield res

    async def _dispatch(
        self, event: AstrMessageEvent, payload: str, trigger_mode: str
    ) -> AsyncGenerator[Any, None]:
        self._refresh_runtime_guards()

        auth_ok = self._is_authorized(event)
        if not auth_ok:
            self._inc_block("unauthorized")
            if not _safe_bool(self.config.get("silent_unauthorized", True), True):
                yield event.plain_result("无权限。")
            return

        payload = payload.strip()
        if not payload:
            yield event.plain_result(self._build_help_text())
            return

        head, tail = self._split_head_tail(payload)

        if head in {"帮助", "help", "h"}:
            yield event.plain_result(self._build_help_text())
            return
        if head in {"诊断", "状态", "健康", "status", "health"}:
            diag = await self._build_diagnostics(event)
            yield event.plain_result(diag)
            return
        if head in {"会话重置", "重置会话", "reset"}:
            msg = self._reset_session(event)
            yield event.plain_result(msg)
            return
        if head in {"模型导出JSON", "模型导出", "导出模型"}:
            msg = self._export_model_mapping(event)
            yield event.plain_result(msg)
            return
        if head in {"确认", "confirm"}:
            msg = self._handle_confirm_command(event, tail)
            yield event.plain_result(msg)
            return

        response = await self._handle_task(event, payload, trigger_mode)
        if response:
            if self._should_mask_output(event):
                response = self._mask_sensitive_text(response)
            yield event.plain_result(response)

    async def _handle_task(self, event: AstrMessageEvent, task_text: str, trigger_mode: str) -> str:
        mem_avail_mb = self._mem_available_mb()
        global_level = self._get_global_permission_level()
        effective_level = global_level

        if 0 < mem_avail_mb < 350 and global_level > PERMISSION_ORDER["L1"]:
            effective_level = PERMISSION_ORDER["L1"]
            self._inc_block("mem_force_read_only")

        if 0 < mem_avail_mb < 512 and self._is_heavy_request(event, task_text):
            self._inc_block("mem_heavy_reject")
            return (
                f"当前可用内存仅 {mem_avail_mb}MB，已触发保护："
                "暂不处理图片/大文件/重任务。"
            )

        async with self._turn_semaphore:
            start_ts = _now_ts()
            status = "ok"
            err_text = ""
            try:
                reply = await self._run_openclaw_turn(event, task_text, effective_level)
                return reply
            except Exception as e:
                status = "error"
                err_text = str(e)
                logger.exception(f"[{PLUGIN_NAME}] turn failed: {e}")
                return f"请求失败：{e}"
            finally:
                self._audit(
                    event=event,
                    action_type="assistant_turn",
                    params_summary={
                        "task_preview": task_text[:120],
                        "trigger_mode": trigger_mode,
                        "effective_level": effective_level,
                    },
                    high_risk=False,
                    confirmed=False,
                    action_category="assistant_turn",
                    status=status,
                    latency_ms=int((_now_ts() - start_ts) * 1000),
                    error=err_text,
                )

    async def _run_openclaw_turn(
        self, event: AstrMessageEvent, task_text: str, effective_level: int
    ) -> str:
        primary_url = (
            str(self.config.get("gateway_primary_url", DEFAULT_GATEWAY_PRIMARY_URL))
            .strip()
            .rstrip("/")
        )
        if not primary_url:
            raise RuntimeError("未配置 gateway_primary_url，当前处于仅诊断模式。")

        session_key = self._session_key(event)
        agent_id = str(self.config.get("gateway_agent_id", "main")).strip() or "main"
        model = f"openclaw:{agent_id}"

        tools = self._build_client_tools(effective_level)
        system_prompt = self._build_system_prompt(effective_level)

        req_payload: Dict[str, Any] = {
            "model": model,
            "stream": False,
            "user": session_key,
            "input": [
                self._build_openresponses_message("system", system_prompt),
                self._build_openresponses_message("user", task_text),
            ],
        }
        if tools:
            req_payload["tools"] = tools

        resp = await self._gateway_post_responses(req_payload)

        max_loops = 4
        loop_count = 0
        while loop_count < max_loops:
            function_calls = self._extract_function_calls(resp)
            if not function_calls:
                break

            outputs = []
            for call in function_calls:
                call_id = call.get("call_id") or call.get("id") or str(uuid.uuid4())
                tool_name = str(call.get("name") or "").strip()
                args = self._parse_tool_args(call.get("arguments"))
                result = await self._execute_local_tool(
                    event=event,
                    tool_name=tool_name,
                    args=args,
                    effective_level=effective_level,
                )
                outputs.append(
                    {
                        "type": "function_call_output",
                        "call_id": call_id,
                        "output": json.dumps(result, ensure_ascii=False),
                    }
                )

            req_payload = {
                "model": model,
                "stream": False,
                "user": session_key,
                "input": outputs,
            }
            if tools:
                req_payload["tools"] = tools
            resp = await self._gateway_post_responses(req_payload)
            loop_count += 1

        text = self._extract_output_text(resp)
        if not text:
            text = "已处理，但未返回文本内容。"
        return text.strip()

    def _build_openresponses_message(self, role: str, text: str) -> Dict[str, Any]:
        return {
            "type": "message",
            "role": role,
            "content": [{"type": "input_text", "text": text}],
        }

    def _build_system_prompt(self, effective_level: int) -> str:
        level_name = self._level_name_from_int(effective_level)
        return (
            "你是 AstrBot 的私有运维助手，服务对象是机器人管理员。\n"
            "请遵循：\n"
            "1) 优先使用 Astr 内置能力与已暴露工具。\n"
            "2) 仅在必要时调用工具；先读后写，先小范围后全局。\n"
            "3) 输出中文，简洁准确。\n"
            f"4) 当前权限等级为 {level_name}，不得越权。\n"
            "5) 如果工具返回需要确认，请明确提示用户执行 /助手 确认 <token>。\n"
        )

    def _build_client_tools(self, effective_level: int) -> List[Dict[str, Any]]:
        tools: List[Dict[str, Any]] = []

        if effective_level >= PERMISSION_ORDER["L1"]:
            tools.extend(
                [
                    self._tool_schema(
                        "astr_read_status",
                        "读取 Astr 运行状态摘要（平台、内存、会话、策略）。",
                        {},
                    ),
                    self._tool_schema(
                        "astr_read_providers",
                        "读取 Astr 当前可用模型提供商列表。",
                        {},
                    ),
                    self._tool_schema(
                        "astr_read_plugins",
                        "读取 Astr 已加载插件列表及激活状态。",
                        {},
                    ),
                    self._tool_schema(
                        "astr_read_commands",
                        "读取 Astr 可用命令目录。",
                        {
                            "plugin_name": {
                                "type": "string",
                                "description": "可选，按插件名过滤。",
                            }
                        },
                    ),
                ]
            )

        if effective_level >= PERMISSION_ORDER["L2"]:
            tools.extend(
                [
                    self._tool_schema(
                        "astr_exec_command",
                        "执行 Astr 命令（受权限/黑名单/高危确认约束）。",
                        {
                            "command_name": {"type": "string"},
                            "arguments": {"type": "string"},
                            "plugin_name": {"type": "string"},
                        },
                        required=["command_name"],
                    ),
                    self._tool_schema(
                        "astr_exec_tool",
                        "执行 Astr 已注册的 LLM 工具（受权限/黑名单/高危确认约束）。",
                        {
                            "tool_name": {"type": "string"},
                            "arguments": {"type": "object"},
                        },
                        required=["tool_name"],
                    ),
                ]
            )

        if effective_level >= PERMISSION_ORDER["L3"]:
            tools.extend(
                [
                    self._tool_schema(
                        "host_exec",
                        "执行主机 shell 命令（默认非 root）。",
                        {
                            "command": {"type": "string"},
                            "cwd": {"type": "string"},
                            "timeout_seconds": {"type": "integer", "minimum": 1, "maximum": 120},
                            "as_root": {"type": "boolean"},
                        },
                        required=["command"],
                    ),
                    self._tool_schema(
                        "host_file_op",
                        "执行主机文件操作（读/写/追加/删除/列目录）。",
                        {
                            "operation": {
                                "type": "string",
                                "enum": ["read", "write", "append", "delete", "list", "mkdir"],
                            },
                            "path": {"type": "string"},
                            "content": {"type": "string"},
                            "recursive": {"type": "boolean"},
                        },
                        required=["operation", "path"],
                    ),
                ]
            )

        deny_tools = self._string_set_from_cfg("blacklist_tools")
        if deny_tools:
            filtered = []
            for t in tools:
                fn = t.get("function", {})
                name = str(fn.get("name", "")).strip()
                if name in deny_tools:
                    continue
                filtered.append(t)
            tools = filtered

        return tools

    def _tool_schema(
        self,
        name: str,
        description: str,
        properties: Dict[str, Any],
        required: Optional[List[str]] = None,
    ) -> Dict[str, Any]:
        schema = {
            "type": "function",
            "function": {
                "name": name,
                "description": description,
                "parameters": {
                    "type": "object",
                    "properties": properties,
                    "additionalProperties": False,
                },
            },
        }
        if required:
            schema["function"]["parameters"]["required"] = required
        return schema

    def _extract_function_calls(self, resp: Dict[str, Any]) -> List[Dict[str, Any]]:
        calls: List[Dict[str, Any]] = []
        output = resp.get("output")
        if isinstance(output, list):
            for item in output:
                if not isinstance(item, dict):
                    continue
                item_type = str(item.get("type", "")).strip()
                if item_type in {"function_call", "tool_call"}:
                    calls.append(item)
                    continue
                if item_type == "message":
                    for content in item.get("content", []) or []:
                        if isinstance(content, dict) and content.get("type") == "function_call":
                            calls.append(content)

        # OpenAI ChatCompletions compatibility fallback.
        if not calls:
            try:
                choices = resp.get("choices", [])
                if choices and isinstance(choices, list):
                    msg = choices[0].get("message", {})
                    for tc in msg.get("tool_calls", []) or []:
                        fn = tc.get("function", {})
                        calls.append(
                            {
                                "type": "function_call",
                                "id": tc.get("id"),
                                "call_id": tc.get("id"),
                                "name": fn.get("name"),
                                "arguments": fn.get("arguments"),
                            }
                        )
            except Exception:
                pass
        return calls

    def _extract_output_text(self, resp: Dict[str, Any]) -> str:
        output_text = resp.get("output_text")
        if isinstance(output_text, str) and output_text.strip():
            return output_text

        texts: List[str] = []
        output = resp.get("output")
        if isinstance(output, list):
            for item in output:
                if not isinstance(item, dict):
                    continue
                if item.get("type") == "message":
                    content = item.get("content", [])
                    if isinstance(content, list):
                        for c in content:
                            if not isinstance(c, dict):
                                continue
                            ctype = c.get("type")
                            if ctype in {"output_text", "text"}:
                                txt = c.get("text")
                                if isinstance(txt, str):
                                    texts.append(txt)
                            elif ctype == "output_text_delta":
                                txt = c.get("delta")
                                if isinstance(txt, str):
                                    texts.append(txt)
                elif item.get("type") in {"output_text", "text"}:
                    txt = item.get("text")
                    if isinstance(txt, str):
                        texts.append(txt)

        if texts:
            return "".join(texts).strip()

        # ChatCompletions fallback.
        try:
            choices = resp.get("choices", [])
            if choices and isinstance(choices, list):
                message = choices[0].get("message", {})
                content = message.get("content")
                if isinstance(content, str):
                    return content
        except Exception:
            pass
        return ""

    def _parse_tool_args(self, raw_args: Any) -> Dict[str, Any]:
        if isinstance(raw_args, dict):
            return raw_args
        if isinstance(raw_args, str):
            try:
                parsed = json.loads(raw_args)
                if isinstance(parsed, dict):
                    return parsed
            except Exception:
                pass
        return {}

    async def _execute_local_tool(
        self,
        event: AstrMessageEvent,
        tool_name: str,
        args: Dict[str, Any],
        effective_level: int,
    ) -> Dict[str, Any]:
        start = _now_ts()
        status = "ok"
        error = ""
        high_risk = False
        confirmed = False

        tool_fn_map = {
            "astr_read_status": (self._tool_astr_read_status, PERMISSION_ORDER["L1"]),
            "astr_read_providers": (self._tool_astr_read_providers, PERMISSION_ORDER["L1"]),
            "astr_read_plugins": (self._tool_astr_read_plugins, PERMISSION_ORDER["L1"]),
            "astr_read_commands": (self._tool_astr_read_commands, PERMISSION_ORDER["L1"]),
            "astr_exec_command": (self._tool_astr_exec_command, PERMISSION_ORDER["L2"]),
            "astr_exec_tool": (self._tool_astr_exec_tool, PERMISSION_ORDER["L2"]),
            "host_exec": (self._tool_host_exec, PERMISSION_ORDER["L3"]),
            "host_file_op": (self._tool_host_file_op, PERMISSION_ORDER["L3"]),
        }

        deny_tools = self._string_set_from_cfg("blacklist_tools")
        if tool_name in deny_tools:
            self._inc_block("blacklist_tool")
            return {"ok": False, "error": f"工具 {tool_name} 在黑名单中"}

        if tool_name not in tool_fn_map:
            return {"ok": False, "error": f"未知工具: {tool_name}"}

        fn, required_level = tool_fn_map[tool_name]
        if effective_level < required_level:
            self._inc_block("permission_deny")
            return {
                "ok": False,
                "error": f"权限不足，{tool_name} 需要 {self._level_name_from_int(required_level)}",
            }

        high_risk = self._is_high_risk_action(tool_name, args)
        if high_risk and _safe_bool(self.config.get("high_risk_confirm_enabled", True), True):
            if not self._is_scope_approved(event):
                token = self._issue_confirm_token(event, tool_name, args)
                self._inc_block("confirm_required")
                return {
                    "ok": False,
                    "error": "high_risk_confirmation_required",
                    "token": token,
                    "message": f"高危操作需要确认，请执行：/助手 确认 {token}",
                }
            confirmed = True

        try:
            result = await fn(event, args, effective_level)
            return {"ok": True, "result": result}
        except Exception as e:
            status = "error"
            error = str(e)
            logger.exception(f"[{PLUGIN_NAME}] tool {tool_name} failed: {e}")
            return {"ok": False, "error": str(e)}
        finally:
            self._audit(
                event=event,
                action_type=tool_name,
                params_summary=args,
                high_risk=high_risk,
                confirmed=confirmed,
                action_category=self._tool_action_category(tool_name),
                status=status,
                latency_ms=int((_now_ts() - start) * 1000),
                error=error,
            )

    async def _tool_astr_read_status(
        self, event: AstrMessageEvent, args: Dict[str, Any], _: int
    ) -> Dict[str, Any]:
        using_provider = None
        try:
            provider = self.context.get_using_provider(getattr(event, "unified_msg_origin", None))
            if provider:
                meta = provider.meta()
                using_provider = {"id": meta.id, "model": meta.model}
        except Exception:
            using_provider = None

        return {
            "time": _iso_now(),
            "platform": event.get_platform_name(),
            "group_id": str(event.get_group_id() or ""),
            "user_id": str(event.get_sender_id() or ""),
            "permission_level": self._level_name_from_int(self._get_global_permission_level()),
            "mem_available_mb": self._mem_available_mb(),
            "using_provider": using_provider,
            "circuit_open_until": self._primary_circuit_open_until,
            "block_counters": self._block_counters,
        }

    async def _tool_astr_read_providers(
        self, _event: AstrMessageEvent, _args: Dict[str, Any], _: int
    ) -> Dict[str, Any]:
        providers = []
        try:
            for p in self.context.get_all_providers():
                try:
                    meta = p.meta()
                    providers.append({"id": meta.id, "model": meta.model})
                except Exception:
                    continue
        except Exception:
            pass
        return {"count": len(providers), "providers": providers}

    async def _tool_astr_read_plugins(
        self, _event: AstrMessageEvent, _args: Dict[str, Any], _: int
    ) -> Dict[str, Any]:
        items = []
        try:
            for s in self.context.get_all_stars():
                items.append(
                    {
                        "name": getattr(s, "name", ""),
                        "display_name": getattr(s, "display_name", ""),
                        "activated": bool(getattr(s, "activated", False)),
                        "module_path": getattr(s, "module_path", ""),
                    }
                )
        except Exception:
            pass
        return {"count": len(items), "plugins": items}

    async def _tool_astr_read_commands(
        self, _event: AstrMessageEvent, args: Dict[str, Any], _: int
    ) -> Dict[str, Any]:
        plugin_filter = str(args.get("plugin_name", "")).strip()
        commands = []
        for handler in star_handlers_registry:
            if not isinstance(handler, StarHandlerMetadata):
                continue
            command_name = None
            for f in handler.event_filters:
                if isinstance(f, CommandFilter):
                    command_name = f.command_name
                    break
                if isinstance(f, CommandGroupFilter):
                    command_name = f.group_name
                    break
            if not command_name:
                continue
            plugin_name = self._handler_plugin_name(handler)
            module_path = str(getattr(handler, "handler_module_path", "") or "")
            if plugin_filter and plugin_filter not in {plugin_name, module_path}:
                if plugin_filter not in module_path:
                    continue
            if plugin_name in self._string_set_from_cfg("blacklist_plugins"):
                continue
            commands.append(
                {
                    "plugin": plugin_name,
                    "command": command_name,
                    "desc": getattr(handler, "desc", "") or "",
                }
            )
        return {"count": len(commands), "commands": commands}

    async def _tool_astr_exec_command(
        self, event: AstrMessageEvent, args: Dict[str, Any], _: int
    ) -> Dict[str, Any]:
        command_name = str(args.get("command_name", "")).strip().lstrip("/")
        arguments = str(args.get("arguments", "")).strip()
        plugin_name = str(args.get("plugin_name", "")).strip()
        if not command_name:
            return {"ok": False, "error": "缺少 command_name"}

        full_command = f"{command_name} {arguments}".strip() if arguments else command_name
        head_command = full_command.split(maxsplit=1)[0] if full_command else command_name

        deny_commands = self._string_set_from_cfg("blacklist_commands")
        if command_name in deny_commands or head_command in deny_commands or full_command in deny_commands:
            self._inc_block("blacklist_command")
            return {"ok": False, "error": f"命令 {full_command} 在黑名单中"}
        deny_plugins = self._string_set_from_cfg("blacklist_plugins")
        if plugin_name and plugin_name in deny_plugins:
            self._inc_block("blacklist_plugin")
            return {"ok": False, "error": f"插件 {plugin_name} 在黑名单中"}

        trigger_prefix = str(self.config.get("trigger_prefix", "助手")).strip() or "助手"
        if head_command in {"助手", trigger_prefix}:
            self._inc_block("assistant_recursion_block")
            return {"ok": False, "error": "禁止通过 astr_exec_command 递归调用助手自身。"}

        matched_handlers, parse_errors = self._collect_command_matches(
            event=event,
            full_command=full_command,
            plugin_name_filter=plugin_name,
            deny_plugins=deny_plugins,
        )
        if not matched_handlers:
            if parse_errors:
                return {
                    "ok": False,
                    "error": "未匹配到可执行命令",
                    "details": parse_errors[:3],
                }
            return {"ok": False, "error": "未找到匹配命令处理器"}

        handler, parsed_params, matched_command = matched_handlers[0]
        resolved_plugin = self._handler_plugin_name(handler)
        if resolved_plugin in deny_plugins:
            self._inc_block("blacklist_plugin")
            return {"ok": False, "error": f"插件 {resolved_plugin} 在黑名单中"}
        if matched_command in deny_commands:
            self._inc_block("blacklist_command")
            return {"ok": False, "error": f"命令 {matched_command} 在黑名单中"}
        if resolved_plugin == PLUGIN_NAME:
            self._inc_block("assistant_recursion_block")
            return {"ok": False, "error": "禁止通过 astr_exec_command 调度本插件命令。"}

        outputs: List[str] = []
        state = self._snapshot_event_state(event)
        try:
            event.message_str = full_command
            event.is_at_or_wake_command = True
            event.is_wake = True
            wrapper = call_handler(event, handler.handler, **parsed_params)
            async for item in wrapper:
                if txt := self._normalize_handler_return_text(item):
                    outputs.append(txt)
                if isinstance(event.get_result(), MessageEventResult):
                    result = event.get_result()
                    assert isinstance(result, MessageEventResult)
                    if txt := result.get_plain_text(with_other_comps_mark=True).strip():
                        outputs.append(txt)
                    event.clear_result()
            if isinstance(event.get_result(), MessageEventResult):
                result = event.get_result()
                assert isinstance(result, MessageEventResult)
                if txt := result.get_plain_text(with_other_comps_mark=True).strip():
                    outputs.append(txt)
                event.clear_result()
        except Exception as e:
            logger.exception(f"[{PLUGIN_NAME}] 执行命令失败: {e}")
            return {
                "ok": False,
                "error": f"命令执行失败: {e}",
                "plugin": resolved_plugin,
                "handler": handler.handler_full_name,
            }
        finally:
            self._restore_event_state(event, state)

        return {
            "ok": True,
            "plugin": resolved_plugin,
            "handler": handler.handler_full_name,
            "matched_command": matched_command,
            "matched_handlers": len(matched_handlers),
            "parsed_params": parsed_params,
            "output_text": "\n".join(outputs).strip() if outputs else "",
        }

    async def _tool_astr_exec_tool(
        self, event: AstrMessageEvent, args: Dict[str, Any], _: int
    ) -> Dict[str, Any]:
        tool_name = str(args.get("tool_name", "")).strip()
        if not tool_name:
            return {"ok": False, "error": "缺少 tool_name"}
        tool_args = args.get("arguments", {})
        if not isinstance(tool_args, dict):
            tool_args = {}

        deny_tools = self._string_set_from_cfg("blacklist_tools")
        if tool_name in deny_tools:
            self._inc_block("blacklist_tool")
            return {"ok": False, "error": f"工具 {tool_name} 在黑名单中"}

        tool_manager = self.context.get_llm_tool_manager()
        tool = tool_manager.get_func(tool_name)
        if not tool:
            return {"ok": False, "error": f"未找到 Astr 工具: {tool_name}"}

        handler = getattr(tool, "handler", None)
        if handler is None:
            return {
                "ok": False,
                "error": (
                    f"工具 {tool_name} 不支持直接以 event 调度（可能为 MCP/Handoff 专用工具），"
                    "请使用 OpenClaw 主链路自动调用。"
                ),
            }

        module_path = str(getattr(tool, "handler_module_path", "") or getattr(handler, "__module__", ""))
        resolved_plugin = self._plugin_name_from_module_path(module_path)
        deny_plugins = self._string_set_from_cfg("blacklist_plugins")
        if resolved_plugin in deny_plugins:
            self._inc_block("blacklist_plugin")
            return {"ok": False, "error": f"插件 {resolved_plugin} 在黑名单中"}

        timeout_s = max(5, _safe_int(self.config.get("tool_call_timeout_seconds", 45), 45))
        start_ts = _now_ts()
        outputs: List[str] = []
        state = self._snapshot_event_state(event)
        try:
            event.is_at_or_wake_command = True
            event.is_wake = True
            outputs = await asyncio.wait_for(
                self._run_astr_tool_handler(event, handler, tool_args),
                timeout=timeout_s,
            )
        except asyncio.TimeoutError:
            return {
                "ok": False,
                "error": f"工具执行超时（>{timeout_s}s）",
                "tool_name": tool_name,
            }
        except Exception as e:
            logger.exception(f"[{PLUGIN_NAME}] 执行工具失败: {e}")
            return {"ok": False, "error": f"工具执行失败: {e}", "tool_name": tool_name}
        finally:
            self._restore_event_state(event, state)

        return {
            "ok": True,
            "tool_name": tool_name,
            "plugin": resolved_plugin,
            "latency_ms": int((_now_ts() - start_ts) * 1000),
            "output_text": "\n".join(outputs).strip() if outputs else "",
        }

    async def _tool_host_exec(
        self, _event: AstrMessageEvent, args: Dict[str, Any], effective_level: int
    ) -> Dict[str, Any]:
        command = str(args.get("command", "")).strip()
        if not command:
            return {"ok": False, "error": "command 不能为空"}

        if self._matches_shell_blacklist(command):
            self._inc_block("blacklist_shell")
            return {"ok": False, "error": "命中 shell 黑名单策略"}

        as_root = _safe_bool(args.get("as_root", False), False)
        if as_root and effective_level < PERMISSION_ORDER["L4"]:
            self._inc_block("permission_deny")
            return {"ok": False, "error": "as_root 需要 L4 权限"}

        timeout_seconds = _safe_int(args.get("timeout_seconds", 20), 20)
        timeout_seconds = max(1, min(timeout_seconds, 120))

        cwd = str(args.get("cwd", "")).strip() or None
        run_cmd = command
        process_is_root = os.geteuid() == 0
        if as_root:
            if not process_is_root:
                sudo_bin = shutil.which("sudo")
                if not sudo_bin:
                    return {"ok": False, "error": "当前非 root 且未安装 sudo"}
                run_cmd = f"sudo -n {command}"
        else:
            # L3 要求非 root 执行；当插件进程是 root 时，尝试自动降权。
            if process_is_root and effective_level < PERMISSION_ORDER["L4"]:
                wrapped_cmd, err = self._wrap_as_non_root_shell(command)
                if not wrapped_cmd:
                    self._inc_block("root_runtime_l3_guard")
                    return {
                        "ok": False,
                        "error": (
                            "当前进程为 root，且无法降权执行 L3 命令："
                            f"{err}。请安装 runuser/su/sudo，或将权限提升到 L4。"
                        ),
                    }
                run_cmd = wrapped_cmd

        proc = await asyncio.create_subprocess_shell(
            run_cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            cwd=cwd,
        )
        try:
            stdout_b, stderr_b = await asyncio.wait_for(proc.communicate(), timeout=timeout_seconds)
        except asyncio.TimeoutError:
            proc.kill()
            await proc.wait()
            return {"ok": False, "error": f"命令超时（>{timeout_seconds}s）"}

        stdout = stdout_b.decode("utf-8", errors="replace")
        stderr = stderr_b.decode("utf-8", errors="replace")
        return {
            "ok": proc.returncode == 0,
            "returncode": proc.returncode,
            "stdout": _truncate(stdout, 4000),
            "stderr": _truncate(stderr, 4000),
        }

    async def _tool_host_file_op(
        self, _event: AstrMessageEvent, args: Dict[str, Any], effective_level: int
    ) -> Dict[str, Any]:
        operation = str(args.get("operation", "")).strip().lower()
        path = str(args.get("path", "")).strip()
        if not operation or not path:
            return {"ok": False, "error": "operation/path 不能为空"}

        p = Path(path).expanduser()
        if os.geteuid() == 0 and effective_level < PERMISSION_ORDER["L4"]:
            self._inc_block("root_runtime_l3_guard")
            return {
                "ok": False,
                "error": (
                    "当前进程为 root，L3 文件操作为防越权已禁用。"
                    "请将插件进程降为非 root 运行，或将权限提升到 L4。"
                ),
            }
        if operation in {"write", "append", "delete", "mkdir"} and effective_level < PERMISSION_ORDER["L3"]:
            self._inc_block("permission_deny")
            return {"ok": False, "error": "该文件操作需要 L3 权限"}

        if operation == "read":
            if not p.exists() or not p.is_file():
                return {"ok": False, "error": "文件不存在"}
            text = p.read_text(encoding="utf-8", errors="replace")
            return {"ok": True, "path": str(p), "content": _truncate(text, 12000)}

        if operation == "write":
            content = str(args.get("content", ""))
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(content, encoding="utf-8")
            return {"ok": True, "path": str(p), "bytes": len(content.encode("utf-8"))}

        if operation == "append":
            content = str(args.get("content", ""))
            p.parent.mkdir(parents=True, exist_ok=True)
            with p.open("a", encoding="utf-8") as f:
                f.write(content)
            return {"ok": True, "path": str(p), "bytes_appended": len(content.encode("utf-8"))}

        if operation == "delete":
            recursive = _safe_bool(args.get("recursive", False), False)
            if not p.exists():
                return {"ok": True, "path": str(p), "deleted": False}
            if p.is_dir():
                if recursive:
                    shutil.rmtree(p)
                else:
                    p.rmdir()
            else:
                p.unlink()
            return {"ok": True, "path": str(p), "deleted": True}

        if operation == "list":
            if not p.exists() or not p.is_dir():
                return {"ok": False, "error": "目录不存在"}
            items = []
            for child in sorted(p.iterdir(), key=lambda x: x.name)[:200]:
                items.append(
                    {
                        "name": child.name,
                        "type": "dir" if child.is_dir() else "file",
                        "size": child.stat().st_size if child.is_file() else None,
                    }
                )
            return {"ok": True, "path": str(p), "items": items}

        if operation == "mkdir":
            p.mkdir(parents=True, exist_ok=True)
            return {"ok": True, "path": str(p)}

        return {"ok": False, "error": f"不支持的 operation: {operation}"}

    async def _gateway_post_responses(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        urls = self._gateway_url_candidates()
        if not urls:
            raise RuntimeError("未配置网关地址")

        timeout_s = max(5, _safe_int(self.config.get("request_timeout_seconds", 90), 90))
        headers = self._gateway_headers()

        last_err = None
        for idx, url in enumerate(urls):
            is_primary = idx == 0
            if is_primary and _now_ts() < self._primary_circuit_open_until:
                self._inc_block("circuit_open")
                last_err = RuntimeError("主网关熔断中，暂不请求")
                continue

            full_url = f"{url}/v1/responses"
            try:
                session = await self._get_http_session()
                async with session.post(
                    full_url,
                    headers=headers,
                    json=payload,
                    timeout=ClientTimeout(total=timeout_s),
                ) as resp:
                    text = await resp.text()
                    if resp.status >= 400:
                        raise RuntimeError(f"HTTP {resp.status}: {_truncate(text, 500)}")
                    data = json.loads(text)
                    if is_primary:
                        self._primary_failures = 0
                        self._primary_circuit_open_until = 0.0
                    return data
            except Exception as e:
                last_err = e
                if is_primary:
                    self._primary_failures += 1
                    if self._primary_failures >= 2:
                        self._primary_circuit_open_until = _now_ts() + 60
                continue

        if last_err is None:
            last_err = RuntimeError("无可用网关")
        raise RuntimeError(f"网关请求失败: {last_err}")

    async def _get_http_session(self) -> aiohttp.ClientSession:
        if self._http_session and not self._http_session.closed:
            return self._http_session
        self._http_session = aiohttp.ClientSession()
        return self._http_session

    async def _close_http_session(self):
        if self._http_session and not self._http_session.closed:
            await self._http_session.close()
        self._http_session = None

    def _gateway_url_candidates(self) -> List[str]:
        primary = (
            str(self.config.get("gateway_primary_url", DEFAULT_GATEWAY_PRIMARY_URL))
            .strip()
            .rstrip("/")
        )
        backup = str(self.config.get("gateway_backup_url", "")).strip().rstrip("/")
        urls = []
        if primary:
            urls.append(primary)
        if backup:
            urls.append(backup)
        return urls

    def _gateway_headers(self) -> Dict[str, str]:
        token = str(self.config.get("gateway_bearer_token", "")).strip()
        if not token:
            return {"Content-Type": "application/json"}
        return {
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
        }

    def _build_diagnostics_sync(self, event: AstrMessageEvent) -> Dict[str, Any]:
        node_v = self._run_simple_cmd("node -v")
        mem_mb = self._mem_available_mb()
        warnings = self._build_runtime_warnings(node_v, mem_mb)
        executors = {
            "openclaw": bool(shutil.which("openclaw")),
            "codex": bool(shutil.which("codex")),
            "gemini": bool(shutil.which("gemini")),
            "node": node_v.strip() if node_v else "not_found",
        }

        auth_scope = {
            "is_admin": self._is_admin_user(str(event.get_sender_id() or "")),
            "is_whitelist_user": str(event.get_sender_id() or "")
            in self._string_set_from_cfg("whitelist_user_ids"),
            "is_group_trigger": bool(event.get_group_id()),
        }

        return {
            "time": _iso_now(),
            "gateway_primary_url": str(
                self.config.get("gateway_primary_url", DEFAULT_GATEWAY_PRIMARY_URL)
            ).strip(),
            "gateway_backup_url": str(self.config.get("gateway_backup_url", "")).strip(),
            "gateway_agent_id": str(self.config.get("gateway_agent_id", "main")).strip() or "main",
            "executor_priority": str(
                self.config.get("executor_priority", "codex_then_gemini_then_shell")
            ).strip(),
            "permission_level": self._level_name_from_int(self._get_global_permission_level()),
            "max_parallel_turns": self._semaphore_limit,
            "configured_parallel_turns": self._configured_parallel_turns,
            "effective_parallel_turns": self._semaphore_limit,
            "parallel_clamped": self._configured_parallel_turns != self._semaphore_limit,
            "mem_available_mb": mem_mb,
            "runtime_uid": os.geteuid(),
            "circuit_open_until": self._primary_circuit_open_until,
            "primary_failures": self._primary_failures,
            "block_counters": self._block_counters,
            "executors": executors,
            "auth_scope": auth_scope,
            "warnings": warnings,
        }

    async def _build_diagnostics(self, event: AstrMessageEvent) -> str:
        data = self._build_diagnostics_sync(event)
        gateway_ok = False
        gateway_msg = ""
        responses_error_type = ""
        try:
            probe_payload = {
                "model": f"openclaw:{str(self.config.get('gateway_agent_id', 'main')).strip() or 'main'}",
                "stream": False,
                "user": f"diag:{uuid.uuid4().hex[:8]}",
                "input": [self._build_openresponses_message("user", "reply with pong only")],
            }
            resp = await self._gateway_post_responses(probe_payload)
            out = self._extract_output_text(resp)
            gateway_ok = True
            gateway_msg = (out or "ok").strip()
        except Exception as e:
            gateway_msg = str(e)
            responses_error_type = self._classify_gateway_probe_error(gateway_msg)

        lines = [
            "助手诊断",
            f"- 时间: {data['time']}",
            f"- 触发者: {event.get_sender_name()} ({event.get_sender_id()})",
            f"- 会话: {getattr(event, 'unified_msg_origin', '')}",
            f"- 权限等级: {data['permission_level']}",
            f"- 可用内存: {data['mem_available_mb']}MB",
            (
                f"- 并发上限: {data['effective_parallel_turns']} "
                f"(配置值={data['configured_parallel_turns']}, "
                f"强制钳制={'是' if data['parallel_clamped'] else '否'})"
            ),
            f"- 网关主地址: {data['gateway_primary_url'] or '(未配置)'}",
            f"- 网关备地址: {data['gateway_backup_url'] or '(未配置)'}",
            f"- 执行器优先级: {data['executor_priority']}",
            f"- 网关探测: {'OK' if gateway_ok else 'FAIL'} ({_truncate(gateway_msg, 220)})",
            (
                "- Responses端点状态: "
                + ("ok" if gateway_ok else f"fail/{responses_error_type or 'unknown'}")
            ),
            f"- 熔断状态: {'OPEN' if _now_ts() < data['circuit_open_until'] else 'CLOSED'}",
            f"- 主网关连续失败: {data['primary_failures']}",
            (
                f"- 执行器探测: openclaw={data['executors']['openclaw']}, "
                f"codex={data['executors']['codex']}, gemini={data['executors']['gemini']}, "
                f"node={data['executors']['node']}"
            ),
            f"- 运行用户UID: {data['runtime_uid']}",
            f"- 拦截计数: {json.dumps(data['block_counters'], ensure_ascii=False)}",
        ]
        for item in data.get("warnings", []) or []:
            lines.append(f"- 告警: {item}")
        return "\n".join(lines)

    def _reset_session(self, event: AstrMessageEvent) -> str:
        key = self._umo_key(event)
        self._session_nonce[key] = self._session_nonce.get(key, 0) + 1
        self._save_session_state()
        return "当前会话上下文已重置。"

    def _export_model_mapping(self, event: AstrMessageEvent) -> str:
        providers = []
        try:
            for p in self.context.get_all_providers():
                try:
                    meta = p.meta()
                    providers.append(
                        {
                            "id": meta.id,
                            "model": meta.model,
                        }
                    )
                except Exception:
                    continue
        except Exception:
            providers = []

        using = None
        try:
            cur = self.context.get_using_provider(getattr(event, "unified_msg_origin", None))
            if cur:
                m = cur.meta()
                using = {"id": m.id, "model": m.model}
        except Exception:
            using = None

        payload = {
            "exported_at": _iso_now(),
            "astr_provider_count": len(providers),
            "using_provider": using,
            "providers": providers,
        }
        out_path = self._data_dir / "astr_provider_export.json"
        out_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        return (
            f"模型映射已导出：{out_path}\n"
            f"提供商数量：{len(providers)}\n"
            f"当前会话模型：{json.dumps(using, ensure_ascii=False)}"
        )

    def _build_help_text(self) -> str:
        prefix = str(self.config.get("trigger_prefix", "助手")).strip() or "助手"
        level = self._level_name_from_int(self._get_global_permission_level())
        return (
            "OpenClaw × Astr QQ 助手\n"
            f"- 当前前缀: {prefix}\n"
            f"- 当前权限等级: {level}\n"
            "- 命令:\n"
            f"  1) /助手 <任务> 或 {prefix} <任务>\n"
            "  2) /助手 诊断\n"
            "  3) /助手 会话重置\n"
            "  4) /助手 模型导出JSON\n"
            "  5) /助手 帮助\n"
            "  6) /助手 确认 <token>\n"
            "- 说明: 群聊默认不触发，需白名单群 + 白名单/管理员用户。"
        )

    def _handle_confirm_command(self, event: AstrMessageEvent, tail: str) -> str:
        token = (tail or "").strip()
        if not token:
            return "缺少确认令牌。用法：/助手 确认 <token>"

        now = _now_ts()
        item = self._pending_confirm_tokens.get(token)
        if not item:
            return "确认令牌不存在或已失效。"

        if item.get("expires_at", 0.0) < now:
            self._pending_confirm_tokens.pop(token, None)
            return "确认令牌已过期。"

        if item.get("scope") != self._scope_key(event):
            return "确认令牌与当前会话不匹配。"

        ttl = max(30, _safe_int(self.config.get("confirm_ttl_seconds", 300), 300))
        self._scope_approvals[self._scope_key(event)] = now + ttl
        self._pending_confirm_tokens.pop(token, None)
        return f"高危确认已生效（{ttl}s），请重试刚才的任务。"

    def _issue_confirm_token(self, event: AstrMessageEvent, action: str, args: Dict[str, Any]) -> str:
        token = uuid.uuid4().hex[:8]
        ttl = max(30, _safe_int(self.config.get("confirm_ttl_seconds", 300), 300))
        self._pending_confirm_tokens[token] = {
            "scope": self._scope_key(event),
            "action": action,
            "args_preview": str(args)[:200],
            "expires_at": _now_ts() + ttl,
        }
        return token

    def _is_scope_approved(self, event: AstrMessageEvent) -> bool:
        exp = self._scope_approvals.get(self._scope_key(event), 0.0)
        return _now_ts() < exp

    def _scope_key(self, event: AstrMessageEvent) -> str:
        gid = str(event.get_group_id() or "")
        uid = str(event.get_sender_id() or "")
        return f"{gid}:{uid}"

    def _is_high_risk_action(self, tool_name: str, args: Dict[str, Any]) -> bool:
        if tool_name == "host_exec":
            cmd = str(args.get("command", "")).strip()
            if _safe_bool(args.get("as_root", False), False):
                return True
            return bool(
                re.search(
                    r"(?i)(\brm\b|\bmkfs\b|\bdd\b|\bshutdown\b|\breboot\b|\bpoweroff\b|\buserdel\b|\bgroupdel\b)",
                    cmd,
                )
            )

        if tool_name == "host_file_op":
            op = str(args.get("operation", "")).strip().lower()
            if op in {"write", "append", "delete"}:
                return True
        if tool_name == "astr_exec_command":
            cmd = str(args.get("command_name", "")).strip().lower()
            for kw in (
                "reload",
                "restart",
                "重载",
                "重启",
                "禁用",
                "删除",
                "卸载",
                "stop",
                "kill",
            ):
                if kw in cmd:
                    return True
        if tool_name == "astr_exec_tool":
            t = str(args.get("tool_name", "")).strip().lower()
            for kw in ("exec", "shell", "file", "delete", "write", "rm", "sudo"):
                if kw in t:
                    return True
        return False

    def _is_authorized(self, event: AstrMessageEvent) -> bool:
        uid = str(event.get_sender_id() or "")
        gid = str(event.get_group_id() or "")

        if gid:
            if gid not in self._string_set_from_cfg("whitelist_group_ids"):
                return False
            return self._is_admin_user(uid) or uid in self._string_set_from_cfg("whitelist_user_ids")

        return self._is_admin_user(uid) or uid in self._string_set_from_cfg("whitelist_user_ids")

    def _is_admin_user(self, uid: str) -> bool:
        if not uid:
            return False
        admin_cfg = self._string_set_from_cfg("admin_user_ids")
        if uid in admin_cfg:
            return True
        try:
            astr_admins = self.context.get_config().get("admins_id", [])
            return uid in {str(x) for x in astr_admins}
        except Exception:
            return False

    def _get_global_permission_level(self) -> int:
        raw = str(self.config.get("global_permission_level", "L2")).strip().upper()
        return PERMISSION_ORDER.get(raw, PERMISSION_ORDER["L2"])

    def _tool_action_category(self, tool_name: str) -> str:
        return TOOL_ACTION_CATEGORY.get(tool_name, "unknown")

    def _level_name_from_int(self, lv: int) -> str:
        for k, v in PERMISSION_ORDER.items():
            if v == lv:
                return k
        return "L0"

    def _string_set_from_cfg(self, key: str) -> set[str]:
        value = self.config.get(key, [])
        if isinstance(value, list):
            return {str(v).strip() for v in value if str(v).strip()}
        if isinstance(value, str):
            parts = re.split(r"[,\n;]+", value)
            return {p.strip() for p in parts if p.strip()}
        return set()

    def _event_text(self, event: AstrMessageEvent) -> str:
        text = ""
        try:
            text = (event.get_message_str() or "").strip()
        except Exception:
            text = ""
        if not text:
            text = str(getattr(event, "message_str", "") or "").strip()
        return text

    def _extract_after_command(self, text: str, command_name: str) -> str:
        s = (text or "").strip()
        if s.startswith("/"):
            s = s[1:].strip()
        if s.startswith(command_name):
            return s[len(command_name) :].strip()
        return s

    def _split_head_tail(self, text: str) -> Tuple[str, str]:
        parts = text.strip().split(maxsplit=1)
        if not parts:
            return "", ""
        if len(parts) == 1:
            return parts[0], ""
        return parts[0], parts[1]

    def _snapshot_event_state(self, event: AstrMessageEvent) -> Dict[str, Any]:
        extras = {}
        try:
            extras_raw = event.get_extra(default={})
            if isinstance(extras_raw, dict):
                extras = dict(extras_raw)
        except Exception:
            extras = {}
        result = event.get_result()
        return {
            "message_str": getattr(event, "message_str", ""),
            "is_at_or_wake_command": getattr(event, "is_at_or_wake_command", False),
            "is_wake": getattr(event, "is_wake", False),
            "call_llm": getattr(event, "call_llm", False),
            "extras": extras,
            "result": result,
        }

    def _restore_event_state(self, event: AstrMessageEvent, state: Dict[str, Any]):
        event.message_str = state.get("message_str", "")
        event.is_at_or_wake_command = bool(state.get("is_at_or_wake_command", False))
        event.is_wake = bool(state.get("is_wake", False))
        event.call_llm = bool(state.get("call_llm", False))
        event._extras = copy.copy(state.get("extras", {}))
        old_result = state.get("result")
        if old_result is None:
            event.clear_result()
        else:
            event.set_result(old_result)

    def _plugin_name_from_module_path(self, module_path: str) -> str:
        if not module_path:
            return ""
        md = star_map.get(module_path)
        if md and getattr(md, "name", None):
            return str(md.name)
        parts = module_path.split(".")
        if "plugins" in parts:
            idx = parts.index("plugins")
            if idx + 1 < len(parts):
                return parts[idx + 1]
        return module_path

    def _handler_plugin_name(self, handler: StarHandlerMetadata) -> str:
        return self._plugin_name_from_module_path(
            str(getattr(handler, "handler_module_path", "") or "")
        )

    def _normalize_handler_return_text(self, ret: Any) -> str:
        if ret is None:
            return ""
        if isinstance(ret, MessageEventResult | CommandResult):
            return ret.get_plain_text(with_other_comps_mark=True).strip()
        if isinstance(ret, (str, int, float, bool)):
            return str(ret).strip()
        if isinstance(ret, (dict, list)):
            return json.dumps(ret, ensure_ascii=False)
        return ""

    def _collect_command_matches(
        self,
        event: AstrMessageEvent,
        full_command: str,
        plugin_name_filter: str,
        deny_plugins: set[str],
    ) -> Tuple[List[Tuple[StarHandlerMetadata, Dict[str, Any], str]], List[str]]:
        parse_errors: List[str] = []
        matches: List[Tuple[StarHandlerMetadata, Dict[str, Any], str]] = []

        cfg = self.context.get_config(getattr(event, "unified_msg_origin", None))
        for handler in star_handlers_registry:
            if not isinstance(handler, StarHandlerMetadata):
                continue
            if handler.event_type != EventType.AdapterMessageEvent:
                continue

            plugin_name = self._handler_plugin_name(handler)
            module_path = str(getattr(handler, "handler_module_path", "") or "")
            if plugin_name in deny_plugins:
                continue
            if plugin_name_filter and plugin_name_filter not in {plugin_name, module_path}:
                if plugin_name_filter not in module_path:
                    continue

            for f in handler.event_filters:
                if not isinstance(f, CommandFilter):
                    continue
                state = self._snapshot_event_state(event)
                try:
                    event.message_str = full_command
                    event.is_at_or_wake_command = True
                    if f.filter(event, cfg):
                        extra_filters_ok = True
                        for ef in handler.event_filters:
                            if isinstance(ef, CommandFilter | CommandGroupFilter):
                                continue
                            if not hasattr(ef, "filter"):
                                continue
                            try:
                                if not ef.filter(event, cfg):
                                    extra_filters_ok = False
                                    break
                            except Exception as e:
                                parse_errors.append(str(e))
                                extra_filters_ok = False
                                break
                        if not extra_filters_ok:
                            continue
                        parsed = event.get_extra("parsed_params", {}) or {}
                        if not isinstance(parsed, dict):
                            parsed = {}
                        matches.append((handler, parsed, f.command_name))
                        break
                except Exception as e:
                    msg = str(e).strip()
                    if msg:
                        parse_errors.append(msg)
                finally:
                    event._extras.pop("parsed_params", None)
                    self._restore_event_state(event, state)

        return matches, parse_errors

    async def _run_astr_tool_handler(
        self,
        event: AstrMessageEvent,
        handler: Any,
        tool_args: Dict[str, Any],
    ) -> List[str]:
        outputs: List[str] = []
        call_obj = handler(event, **tool_args)
        if inspect.isasyncgen(call_obj):
            async for item in call_obj:
                if txt := self._normalize_handler_return_text(item):
                    outputs.append(txt)
                if isinstance(event.get_result(), MessageEventResult):
                    result = event.get_result()
                    assert isinstance(result, MessageEventResult)
                    if txt := result.get_plain_text(with_other_comps_mark=True).strip():
                        outputs.append(txt)
                    event.clear_result()
            if isinstance(event.get_result(), MessageEventResult):
                result = event.get_result()
                assert isinstance(result, MessageEventResult)
                if txt := result.get_plain_text(with_other_comps_mark=True).strip():
                    outputs.append(txt)
                event.clear_result()
            return outputs

        if inspect.iscoroutine(call_obj):
            ret = await call_obj
            if txt := self._normalize_handler_return_text(ret):
                outputs.append(txt)
            if isinstance(event.get_result(), MessageEventResult):
                result = event.get_result()
                assert isinstance(result, MessageEventResult)
                if txt := result.get_plain_text(with_other_comps_mark=True).strip():
                    outputs.append(txt)
                event.clear_result()
            return outputs

        if txt := self._normalize_handler_return_text(call_obj):
            outputs.append(txt)
        return outputs

    def _session_key(self, event: AstrMessageEvent) -> str:
        base = self._umo_key(event)
        nonce = self._session_nonce.get(base, 0)
        return f"astr:{base}:{nonce}"

    def _umo_key(self, event: AstrMessageEvent) -> str:
        umo = str(getattr(event, "unified_msg_origin", "") or "").strip()
        if umo:
            return re.sub(r"[^a-zA-Z0-9:_-]", "_", umo)
        gid = str(event.get_group_id() or "")
        uid = str(event.get_sender_id() or "")
        pid = str(event.get_platform_name() or "qq")
        return f"{pid}:{gid or 'private'}:{uid}"

    def _load_session_state(self):
        if not self._session_state_path.exists():
            return
        try:
            data = json.loads(self._session_state_path.read_text(encoding="utf-8"))
            if isinstance(data, dict):
                nonce = data.get("session_nonce", {})
                if isinstance(nonce, dict):
                    self._session_nonce = {str(k): _safe_int(v, 0) for k, v in nonce.items()}
        except Exception as e:
            logger.warning(f"[{PLUGIN_NAME}] failed to load session state: {e}")

    def _save_session_state(self):
        data = {"session_nonce": self._session_nonce, "updated_at": _iso_now()}
        self._session_state_path.write_text(
            json.dumps(data, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

    def _refresh_runtime_guards(self):
        configured = max(1, _safe_int(self.config.get("max_parallel_turns", 1), 1))
        if configured != self._configured_parallel_turns:
            self._configured_parallel_turns = configured
            if configured != 1:
                logger.warning(
                    f"[{PLUGIN_NAME}] max_parallel_turns={configured} 已被安全策略强制为 1。"
                )
        if self._semaphore_limit != 1:
            self._semaphore_limit = 1
            self._turn_semaphore = asyncio.Semaphore(1)

    def _mem_available_mb(self) -> int:
        try:
            with open("/proc/meminfo", "r", encoding="utf-8") as f:
                for line in f:
                    if line.startswith("MemAvailable:"):
                        parts = line.split()
                        kb = int(parts[1])
                        return kb // 1024
        except Exception:
            pass
        return -1

    def _is_heavy_request(self, event: AstrMessageEvent, text: str) -> bool:
        lower = text.lower()
        for kw in ("image", "图片", "video", "视频", "file", "文件", "pdf", "截图", "绘图"):
            if kw in lower:
                return True
        msg_obj = getattr(event, "message_obj", None)
        chain = getattr(msg_obj, "message", None)
        if isinstance(chain, list):
            for seg in chain:
                seg_type = str(getattr(seg, "type", "")).lower()
                cls_name = seg.__class__.__name__.lower()
                if any(k in seg_type for k in ("image", "video", "file", "record")):
                    return True
                if any(k in cls_name for k in ("image", "video", "file", "record")):
                    return True
        return False

    def _matches_shell_blacklist(self, command: str) -> bool:
        all_patterns = list(DEFAULT_SHELL_BLACKLIST)
        user_patterns = self._string_set_from_cfg("blacklist_shell_patterns")
        all_patterns.extend(sorted(user_patterns))
        for pat in all_patterns:
            try:
                if re.search(pat, command, flags=re.IGNORECASE):
                    return True
            except re.error:
                # 兼容用户输入的普通子串。
                if pat.lower() in command.lower():
                    return True
        return False

    def _run_simple_cmd(self, cmd: str) -> str:
        argv = shlex.split(cmd)
        if not argv:
            return ""
        try:
            import subprocess

            out = subprocess.check_output(argv, stderr=subprocess.STDOUT, timeout=3)
            return out.decode("utf-8", errors="replace").strip()
        except Exception:
            return ""

    def _parse_node_major(self, node_version: str) -> int:
        m = re.search(r"v?(\d+)", node_version or "")
        if not m:
            return -1
        try:
            return int(m.group(1))
        except Exception:
            return -1

    def _build_runtime_warnings(self, node_version: str, mem_mb: int) -> List[str]:
        warnings: List[str] = []
        major = self._parse_node_major(node_version)
        if major > 0 and major < 22:
            warnings.append(
                f"检测到宿主 Node={node_version}，低于 OpenClaw 推荐 Node>=22；"
                "请优先通过 sidecar 容器运行 Gateway。"
            )
        if mem_mb > 0 and mem_mb < 512:
            warnings.append("可用内存低于 512MB，重任务会被拒绝。")
        if mem_mb > 0 and mem_mb < 350:
            warnings.append("可用内存低于 350MB，已强制降级为只读（L1）。")
        if os.geteuid() == 0 and self._get_global_permission_level() < PERMISSION_ORDER["L4"]:
            warnings.append("当前进程以 root 运行，L3 文件操作已被安全门禁用。")
        if (
            not str(self.config.get("gateway_backup_url", "")).strip()
            and str(
                self.config.get("gateway_primary_url", DEFAULT_GATEWAY_PRIMARY_URL)
            ).strip()
        ):
            warnings.append("未配置备网关，主网关故障时将直接失败。")
        if self._configured_parallel_turns != 1:
            warnings.append(
                f"检测到配置并发={self._configured_parallel_turns}，当前运行已强制为 1。"
            )
        return warnings

    def _classify_gateway_probe_error(self, msg: str) -> str:
        s = (msg or "").lower()
        if "http 401" in s or "unauthorized" in s:
            return "auth_failed"
        if "http 404" in s:
            return "responses_endpoint_not_enabled_or_not_found"
        if "http 405" in s:
            return "method_not_allowed"
        if "circuit" in s or "熔断" in s:
            return "circuit_open"
        if (
            "connection refused" in s
            or "cannot connect" in s
            or "name or service not known" in s
            or "timed out" in s
            or "timeout" in s
        ):
            return "network_or_unreachable"
        return "unknown"

    def _pick_non_root_user(self) -> str:
        for name in ("nobody", "daemon"):
            try:
                info = pwd.getpwnam(name)
                if info.pw_uid != 0:
                    return name
            except KeyError:
                continue
            except Exception:
                continue
        return ""

    def _wrap_as_non_root_shell(self, command: str) -> Tuple[Optional[str], str]:
        user = self._pick_non_root_user()
        if not user:
            return None, "未找到可用的非 root 用户（如 nobody）"

        user_q = shlex.quote(user)
        cmd_q = shlex.quote(command)

        if shutil.which("runuser"):
            return f"runuser -u {user_q} -- sh -lc {cmd_q}", ""
        if shutil.which("su"):
            return f"su -s /bin/sh {user_q} -c {cmd_q}", ""
        if shutil.which("sudo"):
            return f"sudo -n -u {user_q} sh -lc {cmd_q}", ""
        return None, "缺少 runuser/su/sudo"

    def _mask_sensitive_text(self, text: str) -> str:
        masked = text
        for pat in SENSITIVE_TEXT_PATTERNS:
            if "Bearer" in pat.pattern:
                masked = pat.sub("Bearer ********", masked)
            else:
                masked = pat.sub(lambda m: f"{m.group(1)}=********", masked)

        # Long token-ish substrings.
        masked = re.sub(r"\b[A-Za-z0-9_\-]{28,}\b", "********", masked)
        return masked

    def _should_mask_output(self, event: AstrMessageEvent) -> bool:
        if not _safe_bool(self.config.get("privacy_mask_enabled", True), True):
            return False

        uid = str(event.get_sender_id() or "")
        gid = str(event.get_group_id() or "")
        is_auth_user = self._is_admin_user(uid) or uid in self._string_set_from_cfg("whitelist_user_ids")

        if not gid:
            if is_auth_user and _safe_bool(self.config.get("privacy_exempt_private", True), True):
                return False
            return True

        if gid in self._string_set_from_cfg("manage_group_ids") and _safe_bool(
            self.config.get("privacy_exempt_manage_groups", True), True
        ):
            return False
        return True

    def _inc_block(self, reason: str):
        self._block_counters[reason] = self._block_counters.get(reason, 0) + 1

    def _audit(
        self,
        event: AstrMessageEvent,
        action_type: str,
        params_summary: Dict[str, Any],
        high_risk: bool,
        confirmed: bool,
        status: str,
        latency_ms: int,
        error: str,
        action_category: str = "",
    ):
        try:
            rec = {
                "time": _iso_now(),
                "operator_id": str(event.get_sender_id() or ""),
                "operator_name": str(event.get_sender_name() or ""),
                "platform": str(event.get_platform_name() or ""),
                "group_id": str(event.get_group_id() or ""),
                "session": str(getattr(event, "unified_msg_origin", "") or ""),
                "action_type": action_type,
                "action_category": action_category or action_type,
                "params_summary": params_summary,
                "high_risk": high_risk,
                "confirmed": confirmed,
                "status": status,
                "latency_ms": latency_ms,
                "error": error,
            }
            with self._audit_path.open("a", encoding="utf-8") as f:
                f.write(json.dumps(rec, ensure_ascii=False) + "\n")
        except Exception as e:
            logger.warning(f"[{PLUGIN_NAME}] audit write failed: {e}")
