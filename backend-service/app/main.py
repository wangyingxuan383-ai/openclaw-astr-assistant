import asyncio
import json
import os
import re
import shutil
import sqlite3
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from threading import Lock
from typing import Any, Dict, List, Optional
from urllib import error as urlerror
from urllib import request as urlrequest

from fastapi import APIRouter, Depends, FastAPI, Header, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel, Field

SERVICE_VERSION = "0.3.0"
PERMISSION_ORDER = {"L0": 0, "L1": 1, "L2": 2, "L3": 3, "L4": 4}
SENSITIVE_TEXT_PATTERNS = [
    re.compile(
        r"(?i)\b(token|api[_-]?key|secret|password|passwd|cookie)\b\s*[:=]\s*([^\s,;\"']+)"
    ),
    re.compile(r"(?i)\bBearer\s+[A-Za-z0-9._~+/=-]+"),
]


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


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


def _safe_int(value: Any, default: int, minimum: Optional[int] = None) -> int:
    try:
        n = int(value)
    except Exception:
        n = default
    if minimum is not None and n < minimum:
        return minimum
    return n


def _truncate(text: str, limit: int = 8000) -> str:
    if len(text) <= limit:
        return text
    return text[:limit] + f"\n...[truncated {len(text) - limit} chars]"


def _mask_sensitive_text(text: str) -> str:
    masked = text
    for pat in SENSITIVE_TEXT_PATTERNS:
        if "Bearer" in pat.pattern:
            masked = pat.sub("Bearer ********", masked)
        else:
            masked = pat.sub(lambda m: f"{m.group(1)}=********", masked)
    return re.sub(r"\\b[A-Za-z0-9_\\-]{28,}\\b", "********", masked)


def _load_env_file(path: Path) -> None:
    if not path.exists() or not path.is_file():
        return
    for raw in path.read_text(encoding="utf-8", errors="replace").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        key = k.strip()
        if not key:
            continue
        value = v.strip().strip("\"'")
        os.environ.setdefault(key, value)


@dataclass
class Settings:
    backend_host: str
    backend_port: int
    backend_api_token: str
    backend_db_path: Path
    backend_log_dir: Path
    backend_audit_path: Path
    backend_request_timeout_seconds: int
    openclaw_gateway_url: str
    openclaw_gateway_token: str
    executor_codex_bin: str
    executor_gemini_bin: str
    executor_timeout_seconds: int
    executor_max_task_chars: int
    executor_allow_global_workdir: bool
    executor_allowed_workdirs: List[Path]

    @classmethod
    def from_env(cls) -> "Settings":
        env_file = Path(
            os.environ.get(
                "BACKEND_ENV_FILE",
                "/root/openclaw-assistant-backend/.env",
            )
        )
        _load_env_file(env_file)

        backend_host = str(os.environ.get("BACKEND_HOST", "127.0.0.1")).strip() or "127.0.0.1"
        backend_port = _safe_int(os.environ.get("BACKEND_PORT", 18889), 18889, 1)
        backend_api_token = str(os.environ.get("BACKEND_API_TOKEN", "")).strip()

        backend_db_path = Path(
            str(
                os.environ.get(
                    "BACKEND_DB_PATH",
                    "/root/openclaw-assistant-backend/data/backend_state.db",
                )
            ).strip()
        )
        backend_log_dir = Path(
            str(
                os.environ.get(
                    "BACKEND_LOG_DIR",
                    "/root/openclaw-assistant-backend/logs/backend",
                )
            ).strip()
        )
        backend_audit_path = Path(
            str(
                os.environ.get(
                    "BACKEND_AUDIT_PATH",
                    "/root/openclaw-assistant-backend/logs/backend/audit.jsonl",
                )
            ).strip()
        )
        backend_request_timeout_seconds = _safe_int(
            os.environ.get("BACKEND_REQUEST_TIMEOUT_SECONDS", 15),
            15,
            1,
        )

        openclaw_gateway_url = (
            str(os.environ.get("OPENCLAW_GATEWAY_URL", "http://127.0.0.1:18789")).strip().rstrip("/")
        )
        openclaw_gateway_token = str(os.environ.get("OPENCLAW_GATEWAY_TOKEN", "")).strip()

        executor_codex_bin = str(os.environ.get("EXECUTOR_CODEX_BIN", "codex")).strip() or "codex"
        executor_gemini_bin = str(os.environ.get("EXECUTOR_GEMINI_BIN", "gemini")).strip() or "gemini"
        executor_timeout_seconds = _safe_int(os.environ.get("EXECUTOR_TIMEOUT_SECONDS", 180), 180, 10)
        executor_max_task_chars = _safe_int(os.environ.get("EXECUTOR_MAX_TASK_CHARS", 8000), 8000, 256)
        executor_allow_global_workdir = _safe_bool(
            os.environ.get("EXECUTOR_ALLOW_GLOBAL_WORKDIR", "true"),
            True,
        )
        allowed_raw = str(
            os.environ.get(
                "EXECUTOR_ALLOWED_WORKDIRS",
                "/root/AstrBot,/root/openclaw-assistant-backend,/root/openclaw-astr-assistant,/root/astrbot_plugin_openclaw_assistant",
            )
        )
        allowed_workdirs = [
            Path(p.strip()).expanduser().resolve()
            for p in allowed_raw.split(",")
            if p.strip()
        ]

        return cls(
            backend_host=backend_host,
            backend_port=backend_port,
            backend_api_token=backend_api_token,
            backend_db_path=backend_db_path,
            backend_log_dir=backend_log_dir,
            backend_audit_path=backend_audit_path,
            backend_request_timeout_seconds=backend_request_timeout_seconds,
            openclaw_gateway_url=openclaw_gateway_url,
            openclaw_gateway_token=openclaw_gateway_token,
            executor_codex_bin=executor_codex_bin,
            executor_gemini_bin=executor_gemini_bin,
            executor_timeout_seconds=executor_timeout_seconds,
            executor_max_task_chars=executor_max_task_chars,
            executor_allow_global_workdir=executor_allow_global_workdir,
            executor_allowed_workdirs=allowed_workdirs,
        )

    def ensure_runtime_paths(self) -> None:
        self.backend_db_path.parent.mkdir(parents=True, exist_ok=True)
        self.backend_log_dir.mkdir(parents=True, exist_ok=True)
        self.backend_audit_path.parent.mkdir(parents=True, exist_ok=True)


SETTINGS = Settings.from_env()


class StateStore:
    def __init__(self, db_path: Path):
        self._conn = sqlite3.connect(db_path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._lock = Lock()
        self._init_schema()

    def _init_schema(self) -> None:
        with self._lock:
            c = self._conn.cursor()
            c.execute(
                """
                CREATE TABLE IF NOT EXISTS model_snapshots (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    provider_id TEXT NOT NULL,
                    model TEXT NOT NULL,
                    provider_type TEXT NOT NULL DEFAULT '',
                    base_url TEXT NOT NULL DEFAULT '',
                    imported_at TEXT NOT NULL,
                    source TEXT NOT NULL,
                    trace_id TEXT NOT NULL
                )
                """
            )
            c.execute(
                """
                CREATE TABLE IF NOT EXISTS sync_events (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    imported_at TEXT NOT NULL,
                    source TEXT NOT NULL,
                    provider_count INTEGER NOT NULL,
                    trace_id TEXT NOT NULL
                )
                """
            )
            c.execute(
                """
                CREATE TABLE IF NOT EXISTS executor_jobs (
                    job_id TEXT PRIMARY KEY,
                    state TEXT NOT NULL,
                    executor TEXT NOT NULL,
                    task TEXT NOT NULL,
                    cwd TEXT NOT NULL,
                    permission_level TEXT NOT NULL,
                    allow_danger INTEGER NOT NULL,
                    created_at TEXT NOT NULL,
                    started_at TEXT,
                    finished_at TEXT,
                    result_text TEXT,
                    error_code TEXT,
                    error TEXT,
                    trace_id TEXT NOT NULL
                )
                """
            )
            self._conn.commit()

    def replace_model_snapshot(self, providers: List[Dict[str, str]], source: str, trace_id: str) -> Dict[str, Any]:
        imported_at = _now_iso()
        with self._lock:
            c = self._conn.cursor()
            c.execute("DELETE FROM model_snapshots")
            for p in providers:
                c.execute(
                    """
                    INSERT INTO model_snapshots (
                        provider_id, model, provider_type, base_url, imported_at, source, trace_id
                    ) VALUES (?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        p.get("provider_id", ""),
                        p.get("model", ""),
                        p.get("provider_type", ""),
                        p.get("base_url", ""),
                        imported_at,
                        source,
                        trace_id,
                    ),
                )
            c.execute(
                "INSERT INTO sync_events (imported_at, source, provider_count, trace_id) VALUES (?, ?, ?, ?)",
                (imported_at, source, len(providers), trace_id),
            )
            self._conn.commit()
        return {
            "imported_at": imported_at,
            "source": source,
            "provider_count": len(providers),
            "trace_id": trace_id,
        }

    def get_models(self) -> List[Dict[str, Any]]:
        with self._lock:
            rows = self._conn.execute(
                """
                SELECT provider_id, model, provider_type, base_url, imported_at, source
                FROM model_snapshots
                ORDER BY provider_id ASC
                """
            ).fetchall()
        return [dict(r) for r in rows]

    def get_last_sync(self) -> Optional[Dict[str, Any]]:
        with self._lock:
            row = self._conn.execute(
                "SELECT imported_at, source, provider_count, trace_id FROM sync_events ORDER BY id DESC LIMIT 1"
            ).fetchone()
        return dict(row) if row else None

    def insert_job(self, job: Dict[str, Any]) -> None:
        with self._lock:
            self._conn.execute(
                """
                INSERT INTO executor_jobs (
                    job_id, state, executor, task, cwd, permission_level, allow_danger,
                    created_at, started_at, finished_at, result_text, error_code, error, trace_id
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    job["job_id"],
                    job["state"],
                    job["executor"],
                    job["task"],
                    job["cwd"],
                    job["permission_level"],
                    1 if job.get("allow_danger") else 0,
                    job["created_at"],
                    job.get("started_at"),
                    job.get("finished_at"),
                    job.get("result_text", ""),
                    job.get("error_code", ""),
                    job.get("error", ""),
                    job.get("trace_id", ""),
                ),
            )
            self._conn.commit()

    def update_job(self, job_id: str, **fields: Any) -> None:
        if not fields:
            return
        parts = []
        values: List[Any] = []
        for k, v in fields.items():
            parts.append(f"{k} = ?")
            values.append(v)
        values.append(job_id)
        sql = f"UPDATE executor_jobs SET {', '.join(parts)} WHERE job_id = ?"
        with self._lock:
            self._conn.execute(sql, tuple(values))
            self._conn.commit()

    def get_job(self, job_id: str) -> Optional[Dict[str, Any]]:
        with self._lock:
            row = self._conn.execute("SELECT * FROM executor_jobs WHERE job_id = ?", (job_id,)).fetchone()
        if not row:
            return None
        data = dict(row)
        data["allow_danger"] = bool(data.get("allow_danger"))
        return data


class RuntimeState:
    def __init__(self) -> None:
        self.job_queue: asyncio.Queue[str] = asyncio.Queue()
        self.worker_task: Optional[asyncio.Task] = None
        self.running_processes: Dict[str, asyncio.subprocess.Process] = {}
        self.cancel_requests: set[str] = set()


class ModelImportRequest(BaseModel):
    exported_at: str = ""
    astr_provider_count: int = 0
    using_provider: Dict[str, Any] | None = None
    providers: List[Dict[str, Any]] = Field(default_factory=list)
    source: str = "plugin_push"


class ExecutorJobCreateRequest(BaseModel):
    executor: str = "codex"
    task: str
    cwd: str = "/root"
    permission_level: str = "L3"
    allow_danger: bool = False


SETTINGS.ensure_runtime_paths()
STORE = StateStore(SETTINGS.backend_db_path)
RUNTIME = RuntimeState()
APP = FastAPI(title="OpenClaw Astr Backend Service", version=SERVICE_VERSION)
TEMPLATES = Jinja2Templates(directory=str(Path(__file__).resolve().parent / "templates"))


def _audit_write(record: Dict[str, Any]) -> None:
    try:
        rec = {"time": _now_iso(), **record}
        with SETTINGS.backend_audit_path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")
    except Exception:
        pass


def _extract_bearer(authorization: str) -> str:
    raw = (authorization or "").strip()
    if not raw:
        return ""
    if raw.lower().startswith("bearer "):
        return raw[7:].strip()
    return ""


def _http_error(status_code: int, error_code: str, message: str) -> None:
    raise HTTPException(status_code=status_code, detail={"error_code": error_code, "message": message})


async def _require_api_auth(
    request: Request,
    authorization: str = Header(default=""),
) -> None:
    expected = SETTINGS.backend_api_token.strip()
    if not expected:
        _http_error(503, "auth_not_configured", "BACKEND_API_TOKEN 未配置。")

    token = _extract_bearer(authorization)
    if not token:
        _http_error(401, "auth_failed", "缺少 Bearer token。")
    if token != expected:
        _http_error(403, "auth_failed", "Bearer token 无效。")
    request.state.authenticated = True


def _response_ok(request: Request, **data: Any) -> Dict[str, Any]:
    return {"ok": True, "trace_id": getattr(request.state, "trace_id", ""), **data}


def _classify_gateway_probe_error(text: str) -> str:
    s = (text or "").lower()
    if "http 401" in s or "http 403" in s or "unauthorized" in s or "forbidden" in s:
        return "auth_failed"
    if "http 404" in s:
        return "responses_endpoint_not_enabled_or_not_found"
    if "timed out" in s or "timeout" in s:
        return "network_or_unreachable"
    if "connection refused" in s or "name or service not known" in s:
        return "network_or_unreachable"
    return "unknown"


def _probe_gateway_sync() -> Dict[str, Any]:
    url = SETTINGS.openclaw_gateway_url.rstrip("/") + "/v1/responses"
    payload = {
        "model": "openclaw:main",
        "stream": False,
        "user": f"probe:{uuid.uuid4().hex[:8]}",
        "input": [
            {
                "type": "message",
                "role": "user",
                "content": [{"type": "input_text", "text": "reply with pong only"}],
            }
        ],
    }
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    headers = {"Content-Type": "application/json"}
    if SETTINGS.openclaw_gateway_token:
        headers["Authorization"] = f"Bearer {SETTINGS.openclaw_gateway_token}"

    req = urlrequest.Request(url=url, data=body, headers=headers, method="POST")
    try:
        with urlrequest.urlopen(req, timeout=SETTINGS.backend_request_timeout_seconds) as resp:
            raw = resp.read().decode("utf-8", errors="replace")
            return {
                "ok": True,
                "status_code": int(getattr(resp, "status", 200)),
                "message": _truncate(raw, 200),
                "error_type": "",
            }
    except urlerror.HTTPError as e:
        content = e.read().decode("utf-8", errors="replace") if hasattr(e, "read") else str(e)
        msg = f"HTTP {e.code}: {_truncate(content, 220)}"
        return {
            "ok": False,
            "status_code": int(getattr(e, "code", 0) or 0),
            "message": msg,
            "error_type": _classify_gateway_probe_error(msg),
        }
    except Exception as e:
        msg = str(e)
        return {
            "ok": False,
            "status_code": 0,
            "message": _truncate(msg, 220),
            "error_type": _classify_gateway_probe_error(msg),
        }


async def _probe_gateway() -> Dict[str, Any]:
    return await asyncio.to_thread(_probe_gateway_sync)


def _executor_status() -> Dict[str, Any]:
    codex_path = shutil.which(SETTINGS.executor_codex_bin) or ""
    gemini_path = shutil.which(SETTINGS.executor_gemini_bin) or ""
    return {
        "codex": {
            "available": bool(codex_path),
            "enabled": bool(codex_path),
            "bin": SETTINGS.executor_codex_bin,
            "path": codex_path,
            "reason": "",
        },
        "gemini": {
            "available": bool(gemini_path),
            "enabled": False,
            "bin": SETTINGS.executor_gemini_bin,
            "path": gemini_path,
            "reason": "v1.1_disabled",
        },
        "shell": {
            "available": True,
            "enabled": False,
            "bin": "/bin/sh",
            "path": "/bin/sh",
            "reason": "v1.1_not_enabled",
        },
    }


def _permission_int(level: str) -> int:
    lv = str(level or "").strip().upper()
    return PERMISSION_ORDER.get(lv, PERMISSION_ORDER["L0"])


def _cwd_allowed(path: Path) -> bool:
    if SETTINGS.executor_allow_global_workdir:
        return True
    p = path.resolve()
    for allow in SETTINGS.executor_allowed_workdirs:
        try:
            p.relative_to(allow)
            return True
        except ValueError:
            continue
    return False


async def _run_codex_job(job: Dict[str, Any]) -> Dict[str, Any]:
    job_id = job["job_id"]
    cwd = str(job.get("cwd", "")).strip() or "/root"
    task = str(job.get("task", "")).strip()
    allow_danger = bool(job.get("allow_danger"))

    args = [
        SETTINGS.executor_codex_bin,
        "exec",
        "--skip-git-repo-check",
        "--cd",
        cwd,
        "--sandbox",
        "workspace-write",
        task,
    ]
    if allow_danger:
        args.insert(-1, "--dangerously-bypass-approvals-and-sandbox")

    try:
        proc = await asyncio.create_subprocess_exec(
            *args,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
    except FileNotFoundError:
        return {
            "state": "failed",
            "result_text": "",
            "error_code": "executor_not_available",
            "error": f"找不到执行器: {SETTINGS.executor_codex_bin}",
        }
    except Exception as e:
        return {
            "state": "failed",
            "result_text": "",
            "error_code": "executor_start_failed",
            "error": str(e),
        }

    RUNTIME.running_processes[job_id] = proc
    try:
        stdout_b, stderr_b = await asyncio.wait_for(
            proc.communicate(),
            timeout=SETTINGS.executor_timeout_seconds,
        )
    except asyncio.TimeoutError:
        proc.kill()
        await proc.wait()
        return {
            "state": "failed",
            "result_text": "",
            "error_code": "executor_timeout",
            "error": f"执行超时（>{SETTINGS.executor_timeout_seconds}s）",
        }
    finally:
        RUNTIME.running_processes.pop(job_id, None)

    if job_id in RUNTIME.cancel_requests:
        RUNTIME.cancel_requests.discard(job_id)
        return {
            "state": "canceled",
            "result_text": "",
            "error_code": "canceled_by_user",
            "error": "任务已取消",
        }

    stdout = stdout_b.decode("utf-8", errors="replace") if stdout_b else ""
    stderr = stderr_b.decode("utf-8", errors="replace") if stderr_b else ""
    merged = (stdout or "") + ("\n" + stderr if stderr else "")
    merged = _mask_sensitive_text(_truncate(merged.strip(), 12000))

    if proc.returncode != 0:
        return {
            "state": "failed",
            "result_text": merged,
            "error_code": "executor_nonzero_exit",
            "error": f"codex exit code={proc.returncode}",
        }

    return {
        "state": "succeeded",
        "result_text": merged or "(empty)",
        "error_code": "",
        "error": "",
    }


async def _executor_worker() -> None:
    while True:
        job_id = await RUNTIME.job_queue.get()
        started = _now_iso()
        try:
            job = STORE.get_job(job_id)
            if not job:
                continue

            if job.get("state") == "canceled":
                continue

            STORE.update_job(job_id, state="running", started_at=started, error_code="", error="")
            job = STORE.get_job(job_id) or job
            result = await _run_codex_job(job)
            STORE.update_job(
                job_id,
                state=result["state"],
                finished_at=_now_iso(),
                result_text=result.get("result_text", ""),
                error_code=result.get("error_code", ""),
                error=result.get("error", ""),
            )
            _audit_write(
                {
                    "trace_id": job.get("trace_id", ""),
                    "action_category": "host_exec",
                    "action_type": "backend_exec_job",
                    "job_id": job_id,
                    "status": result["state"],
                    "error_code": result.get("error_code", ""),
                    "error": result.get("error", ""),
                    "denied": result["state"] in {"failed", "canceled"},
                    "decision_reason": "executor_worker",
                }
            )
        except asyncio.CancelledError:
            raise
        except Exception as e:
            STORE.update_job(
                job_id,
                state="failed",
                finished_at=_now_iso(),
                error_code="internal_error",
                error=str(e),
            )
            _audit_write(
                {
                    "trace_id": "",
                    "action_category": "host_exec",
                    "action_type": "backend_exec_job",
                    "job_id": job_id,
                    "status": "failed",
                    "error_code": "internal_error",
                    "error": str(e),
                    "denied": True,
                    "decision_reason": "executor_worker_exception",
                }
            )
        finally:
            RUNTIME.job_queue.task_done()


async def _build_status_payload() -> Dict[str, Any]:
    probe = await _probe_gateway()
    return {
        "service": {
            "name": "openclaw-astr-backend-service",
            "version": SERVICE_VERSION,
            "time": _now_iso(),
            "bind": f"{SETTINGS.backend_host}:{SETTINGS.backend_port}",
        },
        "worker": {
            "queue_size": RUNTIME.job_queue.qsize(),
            "running": bool(RUNTIME.worker_task and not RUNTIME.worker_task.done()),
            "concurrency": 1,
        },
        "executors": _executor_status(),
        "last_model_sync": STORE.get_last_sync(),
        "sidecar_probe": probe,
    }


@APP.middleware("http")
async def _trace_middleware(request: Request, call_next):
    trace_id = request.headers.get("X-Trace-Id") or uuid.uuid4().hex[:12]
    request.state.trace_id = trace_id
    response = await call_next(request)
    response.headers["X-Trace-Id"] = trace_id
    return response


@APP.exception_handler(HTTPException)
async def _http_exception_handler(request: Request, exc: HTTPException):
    detail = exc.detail if isinstance(exc.detail, dict) else {"message": str(exc.detail)}
    payload = {
        "ok": False,
        "trace_id": getattr(request.state, "trace_id", ""),
        "error_code": detail.get("error_code", "http_error"),
        "error": detail.get("message", "请求失败"),
    }
    return JSONResponse(status_code=exc.status_code, content=payload)


@APP.exception_handler(Exception)
async def _unhandled_exception_handler(request: Request, exc: Exception):
    payload = {
        "ok": False,
        "trace_id": getattr(request.state, "trace_id", ""),
        "error_code": "internal_error",
        "error": str(exc),
    }
    return JSONResponse(status_code=500, content=payload)


API = APIRouter(prefix="/api/v1", dependencies=[Depends(_require_api_auth)])


@API.get("/status")
async def api_status(request: Request):
    return _response_ok(request, state=await _build_status_payload())


@API.get("/executors")
async def api_executors(request: Request):
    return _response_ok(request, executors=_executor_status())


@API.get("/models")
async def api_models(request: Request):
    return _response_ok(
        request,
        models=STORE.get_models(),
        last_sync=STORE.get_last_sync(),
    )


@API.post("/models/import-astr")
async def api_models_import_astr(request: Request, payload: ModelImportRequest):
    providers: List[Dict[str, str]] = []
    for raw in payload.providers:
        provider_id = str(raw.get("id") or raw.get("provider_id") or "").strip()
        model = str(raw.get("model") or "").strip()
        if not provider_id or not model:
            continue
        providers.append(
            {
                "provider_id": provider_id,
                "model": model,
                "provider_type": str(raw.get("provider_type") or "").strip(),
                "base_url": str(raw.get("base_url") or "").strip(),
            }
        )

    source = str(payload.source or "manual_api").strip() or "manual_api"
    rec = STORE.replace_model_snapshot(
        providers=providers,
        source=source,
        trace_id=getattr(request.state, "trace_id", ""),
    )

    _audit_write(
        {
            "trace_id": getattr(request.state, "trace_id", ""),
            "action_category": "model_sync",
            "action_type": "import_astr_models",
            "status": "ok",
            "error_code": "",
            "error": "",
            "denied": False,
            "decision_reason": "model_import",
            "params_summary": {
                "source": source,
                "provider_count": len(providers),
            },
        }
    )

    return _response_ok(request, imported=rec)


@API.post("/executor/jobs")
async def api_executor_jobs_create(request: Request, payload: ExecutorJobCreateRequest):
    permission_level = str(payload.permission_level or "L0").strip().upper()
    if _permission_int(permission_level) < PERMISSION_ORDER["L3"]:
        _http_error(403, "permission_deny", "提交执行器任务至少需要 L3。")

    allow_danger = bool(payload.allow_danger)
    if allow_danger and _permission_int(permission_level) < PERMISSION_ORDER["L4"]:
        _http_error(403, "permission_deny", "危险模式需要 L4。")

    executor = str(payload.executor or "codex").strip().lower() or "codex"
    if executor == "gemini":
        _http_error(400, "executor_not_available", "Gemini 执行器在 V1.1 未启用。")
    if executor != "codex":
        _http_error(400, "executor_not_available", f"不支持的执行器: {executor}")

    if not shutil.which(SETTINGS.executor_codex_bin):
        _http_error(503, "executor_not_available", f"未检测到 {SETTINGS.executor_codex_bin}。")

    task = str(payload.task or "").strip()
    if not task:
        _http_error(400, "missing_task", "task 不能为空。")
    if len(task) > SETTINGS.executor_max_task_chars:
        _http_error(
            400,
            "task_too_large",
            f"task 过长，最大 {SETTINGS.executor_max_task_chars} 字符。",
        )

    cwd = str(payload.cwd or "").strip() or "/root"
    cwd_path = Path(cwd).expanduser()
    if not cwd_path.exists() or not cwd_path.is_dir():
        _http_error(400, "invalid_cwd", f"cwd 不存在或不是目录: {cwd}")
    if not _cwd_allowed(cwd_path):
        _http_error(403, "workdir_not_allowed", f"cwd 不在允许范围内: {cwd}")

    job_id = uuid.uuid4().hex[:16]
    now = _now_iso()
    job = {
        "job_id": job_id,
        "state": "queued",
        "executor": executor,
        "task": task,
        "cwd": str(cwd_path),
        "permission_level": permission_level,
        "allow_danger": allow_danger,
        "created_at": now,
        "started_at": None,
        "finished_at": None,
        "result_text": "",
        "error_code": "",
        "error": "",
        "trace_id": getattr(request.state, "trace_id", ""),
    }
    STORE.insert_job(job)
    await RUNTIME.job_queue.put(job_id)

    _audit_write(
        {
            "trace_id": getattr(request.state, "trace_id", ""),
            "action_category": "host_exec",
            "action_type": "backend_exec_job",
            "job_id": job_id,
            "status": "queued",
            "error_code": "",
            "error": "",
            "denied": False,
            "decision_reason": "executor_job_submitted",
            "params_summary": {
                "executor": executor,
                "cwd": str(cwd_path),
                "permission_level": permission_level,
                "allow_danger": allow_danger,
            },
        }
    )

    return _response_ok(request, job_id=job_id, state="queued")


@API.get("/executor/jobs/{job_id}")
async def api_executor_jobs_get(request: Request, job_id: str):
    job = STORE.get_job(job_id)
    if not job:
        _http_error(404, "job_not_found", "job_id 不存在。")
    return _response_ok(request, job=job)


@API.post("/executor/jobs/{job_id}/cancel")
async def api_executor_jobs_cancel(request: Request, job_id: str):
    job = STORE.get_job(job_id)
    if not job:
        _http_error(404, "job_not_found", "job_id 不存在。")

    state = str(job.get("state", ""))
    if state in {"succeeded", "failed", "canceled"}:
        return _response_ok(request, job=job)

    if state == "queued":
        STORE.update_job(
            job_id,
            state="canceled",
            finished_at=_now_iso(),
            error_code="canceled_by_user",
            error="任务在排队阶段被取消",
        )
    elif state == "running":
        RUNTIME.cancel_requests.add(job_id)
        proc = RUNTIME.running_processes.get(job_id)
        if proc and proc.returncode is None:
            proc.terminate()

    job = STORE.get_job(job_id)
    _audit_write(
        {
            "trace_id": getattr(request.state, "trace_id", ""),
            "action_category": "host_exec",
            "action_type": "cancel_backend_exec_job",
            "job_id": job_id,
            "status": str(job.get("state", "")),
            "error_code": str(job.get("error_code", "")),
            "error": str(job.get("error", "")),
            "denied": False,
            "decision_reason": "executor_job_cancel",
        }
    )
    return _response_ok(request, job=job)


@APP.get("/", response_class=HTMLResponse)
async def web_root(request: Request):
    status = await _build_status_payload()
    return TEMPLATES.TemplateResponse("status.html", {"request": request, "status": status})


@APP.get("/web/status", response_class=HTMLResponse)
async def web_status(request: Request):
    status = await _build_status_payload()
    return TEMPLATES.TemplateResponse("status.html", {"request": request, "status": status})


@APP.get("/web/models", response_class=HTMLResponse)
async def web_models(request: Request):
    models = STORE.get_models()
    last_sync = STORE.get_last_sync()
    return TEMPLATES.TemplateResponse(
        "models.html",
        {
            "request": request,
            "models": models,
            "last_sync": last_sync,
        },
    )


APP.include_router(API)


@APP.on_event("startup")
async def _startup() -> None:
    if RUNTIME.worker_task and not RUNTIME.worker_task.done():
        return
    RUNTIME.worker_task = asyncio.create_task(_executor_worker(), name="executor-worker")


@APP.on_event("shutdown")
async def _shutdown() -> None:
    task = RUNTIME.worker_task
    if task and not task.done():
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass


app = APP
