import argparse
import hashlib
import json
import os
import re
import shutil
import signal
import socket
import subprocess
import sys
import tempfile
import time
import zipfile
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Optional, TypeVar

import requests

if os.name == "nt":
    import msvcrt
else:
    import fcntl

APP_VERSION = "0.0.0-dev"

GITHUB_REPO = os.getenv("UPDATER_GITHUB_REPO", "HKLHaoBin/LyricSphere")
GITHUB_RELEASE_LATEST_API = "https://api.github.com/repos/{repo}/releases/latest"
RELEASE_ZIP_NAME = "LyricSphere.exe.zip"
RELEASE_SHA256_NAME = "LyricSphere.exe.zip.sha256"

LOCK_FILE_NAME = ".updater.pid"
STATUS_FILE_NAME = ".updater.status.json"
RUNTIME_FILE_NAME = ".updater.runtime.json"
ALLOWED_SINGLE_FILES = {"backend.exe"}
ALLOWED_PREFIXES = (
    "templates/",
    "static/assets/",
    "static/public/",
    "static/icons/",
    "static/monaco/",
)
ALLOWED_RESOURCE_DIRS = (
    "templates",
    "static/assets",
    "static/public",
    "static/icons",
    "static/monaco",
)
FORBIDDEN_PREFIXES = ("static/songs/", "static/backups/")
IO_RETRY_DELAY_MS = 500
IO_RETRY_MAX_ATTEMPTS = 6

T = TypeVar("T")


@dataclass
class InstanceLock:
    path: Path
    file_obj: Any


def now_iso() -> str:
    return datetime.now().isoformat(timespec="seconds")


def trace(work_dir: Path, stage: str, **extra: Any) -> None:
    payload: dict[str, Any] = {
        "timestamp": now_iso(),
        "stage": stage,
    }
    payload.update(extra)
    line = json.dumps(payload, ensure_ascii=False)
    print(line, flush=True)


def write_status(work_dir: Path, state: str, message: str, extra: Optional[dict[str, Any]] = None) -> None:
    payload: dict[str, Any] = {
        "timestamp": now_iso(),
        "state": state,
        "message": message,
    }
    if extra:
        payload.update(extra)
    status_file = work_dir / STATUS_FILE_NAME
    status_file.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def write_phase_status(
    work_dir: Path,
    state: str,
    message: str,
    phase: str,
    extra: Optional[dict[str, Any]] = None,
) -> None:
    payload = dict(extra or {})
    payload["phase"] = phase
    write_status(work_dir, state, message, payload)


def parse_version(value: str) -> Optional[tuple[int, int, int]]:
    text = (value or "").strip()
    match = re.fullmatch(r"v?(\d+)\.(\d+)\.(\d+)", text)
    if not match:
        return None
    return int(match.group(1)), int(match.group(2)), int(match.group(3))


def is_remote_newer(local_version: str, remote_tag: str) -> bool:
    remote = parse_version(remote_tag)
    if remote is None:
        return False
    local = parse_version(local_version)
    if local is None:
        return True
    return remote > local


def is_pid_running(pid: int) -> bool:
    if pid <= 0:
        return False
    if os.name == "nt":
        try:
            proc = subprocess.run(
                ["tasklist", "/FI", f"PID eq {pid}", "/FO", "CSV", "/NH"],
                capture_output=True,
                text=True,
            )
            if proc.returncode == 0:
                output = (proc.stdout or "").strip()
                if not output:
                    return False
                lowered = output.lower()
                if "no tasks are running" in lowered:
                    return False
                return str(pid) in output
        except Exception:
            pass
    try:
        os.kill(pid, 0)
        return True
    except Exception:
        return False


def acquire_single_instance_lock(work_dir: Path) -> Optional[InstanceLock]:
    lock_path = work_dir / LOCK_FILE_NAME
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    file_obj = lock_path.open("a+", encoding="utf-8")

    try:
        if os.name == "nt":
            file_obj.seek(0)
            marker = file_obj.read(1)
            if not marker:
                file_obj.write("\0")
                file_obj.flush()
            file_obj.seek(0)
            msvcrt.locking(file_obj.fileno(), msvcrt.LK_NBLCK, 1)
        else:
            fcntl.flock(file_obj.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except Exception:
        file_obj.close()
        return None

    file_obj.seek(0)
    file_obj.truncate()
    file_obj.write(str(os.getpid()))
    file_obj.flush()
    return InstanceLock(path=lock_path, file_obj=file_obj)


def release_single_instance_lock(lock: Optional[InstanceLock]) -> None:
    if lock is None:
        return
    try:
        if os.name == "nt":
            lock.file_obj.seek(0)
            msvcrt.locking(lock.file_obj.fileno(), msvcrt.LK_UNLCK, 1)
        else:
            fcntl.flock(lock.file_obj.fileno(), fcntl.LOCK_UN)
    except Exception:
        pass
    try:
        lock.file_obj.close()
    except Exception:
        pass


def is_retryable_io_error(exc: BaseException) -> bool:
    if isinstance(exc, PermissionError):
        return True
    if isinstance(exc, OSError):
        if exc.errno in {5, 13, 16, 26, 32, 33}:  # access denied, busy, text file busy, sharing violation
            return True
        if getattr(exc, "winerror", None) in {5, 32, 33}:  # type: ignore[arg-type]
            return True
    return False


def retry_io(
    operation: str,
    func: Callable[[], T],
    *,
    work_dir: Optional[Path] = None,
    max_attempts: int = IO_RETRY_MAX_ATTEMPTS,
    delay_ms: int = IO_RETRY_DELAY_MS,
    trace_stage: str = "io:retry",
) -> T:
    if max_attempts < 1:
        max_attempts = 1
    last_exc: Optional[BaseException] = None
    for attempt in range(1, max_attempts + 1):
        try:
            result = func()
            if work_dir is not None and attempt > 1:
                trace(work_dir, f"{trace_stage}:ok", operation=operation, attempt=attempt)
            return result
        except Exception as exc:
            last_exc = exc
            can_retry = is_retryable_io_error(exc) and attempt < max_attempts
            if work_dir is not None:
                trace(
                    work_dir,
                    f"{trace_stage}:error",
                    operation=operation,
                    attempt=attempt,
                    retry=can_retry,
                    error=repr(exc),
                )
            if not can_retry:
                raise
            time.sleep(max(0.01, float(delay_ms) / 1000.0))
    if last_exc is not None:
        raise last_exc
    raise RuntimeError(f"retry failed without exception: {operation}")


def github_latest_release(repo: str, timeout: int = 20) -> dict[str, Any]:
    url = GITHUB_RELEASE_LATEST_API.format(repo=repo)
    headers = {
        "Accept": "application/vnd.github+json",
        "User-Agent": "famyliam-updater",
    }
    response = requests.get(url, headers=headers, timeout=timeout)
    response.raise_for_status()
    return response.json()


def find_asset_url(release: dict[str, Any], asset_name: str) -> Optional[str]:
    assets = release.get("assets") or []
    for asset in assets:
        if str(asset.get("name") or "") == asset_name:
            return asset.get("browser_download_url")
    return None


def download_to(url: str, output_path: Path, timeout: int = 60) -> None:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with requests.get(url, stream=True, timeout=timeout) as response:
        response.raise_for_status()
        with output_path.open("wb") as fp:
            for chunk in response.iter_content(chunk_size=1024 * 1024):
                if chunk:
                    fp.write(chunk)


def parse_sha256_file(path: Path) -> str:
    content = path.read_text(encoding="utf-8", errors="ignore").strip()
    token = content.split()[0] if content else ""
    if not re.fullmatch(r"[a-fA-F0-9]{64}", token):
        raise ValueError("invalid sha256 file format")
    return token.lower()


def file_sha256(path: Path) -> str:
    hasher = hashlib.sha256()
    with path.open("rb") as fp:
        while True:
            block = fp.read(1024 * 1024)
            if not block:
                break
            hasher.update(block)
    return hasher.hexdigest().lower()


def normalize_rel_path(path: Path, root: Path) -> str:
    return path.relative_to(root).as_posix()


def is_forbidden(rel_path: str) -> bool:
    rel = rel_path.strip("/")
    if rel == "updater.exe":
        return True
    if any(rel.startswith(prefix) for prefix in FORBIDDEN_PREFIXES):
        return True
    if rel.startswith("static/") and rel.count("/") == 1 and rel.endswith(".json"):
        return True
    return False


def is_allowed(rel_path: str) -> bool:
    rel = rel_path.strip("/")
    if rel in ALLOWED_SINGLE_FILES:
        return True
    return any(rel.startswith(prefix) for prefix in ALLOWED_PREFIXES)


def backup_targets(work_dir: Path, backup_dir: Path) -> None:
    backup_dir.mkdir(parents=True, exist_ok=True)

    single_files = ["backend.exe"]
    for name in single_files:
        src = work_dir / name
        if src.exists() and src.is_file():
            dst = backup_dir / name
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src, dst)

    dirs = [
        "templates",
        "static/assets",
        "static/public",
        "static/icons",
        "static/monaco",
    ]
    for item in dirs:
        src = work_dir / item
        if src.exists() and src.is_dir():
            dst = backup_dir / item
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.copytree(src, dst, dirs_exist_ok=True)


def stop_backend_process(pid: int, wait_seconds: int = 25, work_dir: Optional[Path] = None) -> bool:
    if pid <= 0:
        return True

    def _trace(stage: str, **extra: Any) -> None:
        if work_dir is not None:
            trace(work_dir, stage, **extra)

    pid_running = is_pid_running(pid)
    _trace("backend:pid-check", pid=pid, pid_running=pid_running)
    if not pid_running:
        return True

    if os.name == "nt":
        _trace("backend:taskkill:start", pid=pid)
        taskkill_result = subprocess.run(["taskkill", "/PID", str(pid), "/F"], capture_output=True, text=True)
        _trace(
            "backend:taskkill:result",
            pid=pid,
            returncode=taskkill_result.returncode,
            stdout=(taskkill_result.stdout or "").strip(),
            stderr=(taskkill_result.stderr or "").strip(),
        )
    else:
        try:
            os.kill(pid, signal.SIGTERM)
        except OSError:
            pass

    start = time.time()
    while time.time() - start < wait_seconds:
        if not is_pid_running(pid):
            return True
        time.sleep(0.5)

    if os.name != "nt":
        try:
            os.kill(pid, signal.SIGKILL)
        except OSError:
            pass
        return not is_pid_running(pid)
    return False


def is_port_open(port: int, host: str = "127.0.0.1", timeout: float = 0.5) -> bool:
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except OSError:
        return False


def start_backend_detached(command: list[str], work_dir: Path) -> int:
    if os.name == "nt":
        creation_flags = subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP  # type: ignore[attr-defined]
        process = subprocess.Popen(
            command,
            cwd=str(work_dir),
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            stdin=subprocess.DEVNULL,
            creationflags=creation_flags,
            close_fds=True,
        )
    else:
        process = subprocess.Popen(
            command,
            cwd=str(work_dir),
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            stdin=subprocess.DEVNULL,
            close_fds=True,
            start_new_session=True,
        )
    return process.pid


def verify_backend_restart(ctx: "RuntimeContext", new_pid: int, restart_started_at: float, timeout_seconds: float = 12.0) -> tuple[bool, str, dict[str, Any]]:
    deadline = time.time() + timeout_seconds
    runtime_seen = False
    runtime_matched = False

    while time.time() < deadline:
        if not is_pid_running(new_pid):
            return False, "backend process exited early", {"new_backend_pid": new_pid}

        runtime_file = ctx.work_dir / RUNTIME_FILE_NAME
        if runtime_file.exists():
            runtime_seen = True
            try:
                payload = json.loads(runtime_file.read_text(encoding="utf-8"))
                runtime_pid = int(payload.get("backend_pid") or 0)
            except Exception:
                runtime_pid = 0

            try:
                runtime_mtime = runtime_file.stat().st_mtime
            except Exception:
                runtime_mtime = 0.0

            if runtime_pid == new_pid and runtime_mtime >= (restart_started_at - 0.5):
                runtime_matched = True
                return True, "runtime file refreshed by new backend", {
                    "new_backend_pid": new_pid,
                    "runtime_pid": runtime_pid,
                    "runtime_mtime": runtime_mtime,
                }

        if is_port_open(ctx.port):
            return True, "backend port is listening", {
                "new_backend_pid": new_pid,
                "port": ctx.port,
                "runtime_seen": runtime_seen,
                "runtime_matched": runtime_matched,
            }

        time.sleep(0.5)

    return False, "backend restart verification timed out", {
        "new_backend_pid": new_pid,
        "runtime_seen": runtime_seen,
        "runtime_matched": runtime_matched,
        "pid_alive": is_pid_running(new_pid),
        "port": ctx.port,
    }


@dataclass
class RuntimeContext:
    work_dir: Path
    port: int
    backend_pid: int
    backend_mode: str
    backend_executable: Optional[Path]
    backend_script: Optional[Path]
    python_executable: Optional[Path]
    app_version: str


def sync_runtime_context(ctx: RuntimeContext) -> None:
    runtime_file = ctx.work_dir / RUNTIME_FILE_NAME
    if not runtime_file.exists():
        return
    try:
        payload = json.loads(runtime_file.read_text(encoding="utf-8"))
    except Exception:
        return

    try:
        if "port" in payload:
            ctx.port = int(payload["port"])
    except Exception:
        pass

    try:
        if "backend_pid" in payload:
            ctx.backend_pid = int(payload["backend_pid"])
    except Exception:
        pass

    mode = str(payload.get("backend_mode") or "").strip()
    if mode in {"auto", "exe", "python"}:
        ctx.backend_mode = mode

    for field_name in ("backend_executable", "backend_script", "python_executable"):
        raw_value = str(payload.get(field_name) or "").strip()
        if raw_value:
            setattr(ctx, field_name, Path(raw_value).resolve())

    runtime_version = str(payload.get("app_version") or "").strip()
    if runtime_version:
        ctx.app_version = runtime_version


def resolve_restart_command(ctx: RuntimeContext) -> list[str]:
    if ctx.backend_mode == "exe":
        executable = ctx.backend_executable or (ctx.work_dir / "backend.exe")
        return [str(executable), str(ctx.port)]
    if ctx.backend_mode == "python":
        python_exec = ctx.python_executable or Path(sys.executable)
        backend_script = ctx.backend_script or (ctx.work_dir / "backend.py")
        return [str(python_exec), str(backend_script), str(ctx.port)]

    fallback_exe = ctx.backend_executable or (ctx.work_dir / "backend.exe")
    if fallback_exe.exists():
        return [str(fallback_exe), str(ctx.port)]
    python_exec = ctx.python_executable or Path(sys.executable)
    backend_script = ctx.backend_script or (ctx.work_dir / "backend.py")
    return [str(python_exec), str(backend_script), str(ctx.port)]


@dataclass
class PreparedUpdate:
    repo: str
    latest_tag: str
    extract_path: Path
    backup_dir: Path


class UpdateApplyError(RuntimeError):
    def __init__(self, message: str, new_backend_pid: int = 0):
        super().__init__(message)
        self.new_backend_pid = int(new_backend_pid)


def apply_file_copy(src: Path, dst: Path, rel: str, work_dir: Path, stage: str) -> None:
    dst.parent.mkdir(parents=True, exist_ok=True)
    trace(work_dir, stage, action="copy:start", rel=rel, src=str(src), dst=str(dst))
    try:
        retry_io(
            f"copy {rel}",
            lambda: shutil.copy2(src, dst),
            work_dir=work_dir,
            trace_stage=stage,
        )
    except PermissionError as exc:
        raise PermissionError(f"copy failed for {rel} ({src} -> {dst}): {exc}") from exc
    trace(work_dir, stage, action="copy:done", rel=rel)


def replace_backend_executable(src: Path, dst: Path, work_dir: Path) -> None:
    rel = "backend.exe"
    temp_old = dst.with_name(f"{dst.name}.old.{int(time.time())}")
    trace(work_dir, "apply:file", action="backend-replace:start", src=str(src), dst=str(dst))

    renamed_old = False
    replace_ok = False
    try:
        if dst.exists():
            retry_io(
                "rename old backend.exe",
                lambda: dst.rename(temp_old),
                work_dir=work_dir,
                trace_stage="apply:file",
            )
            renamed_old = True
        apply_file_copy(src, dst, rel, work_dir, "apply:file")
        replace_ok = True
    except Exception:
        if renamed_old and temp_old.exists():
            if dst.exists():
                retry_io(
                    "remove partial backend.exe before restore",
                    lambda: dst.unlink(),
                    work_dir=work_dir,
                    trace_stage="rollback:file",
                )
            retry_io(
                "restore old backend.exe",
                lambda: temp_old.rename(dst),
                work_dir=work_dir,
                trace_stage="rollback:file",
            )
        raise
    finally:
        if replace_ok and temp_old.exists() and dst.exists():
            retry_io(
                "cleanup old backend.exe temp",
                lambda: temp_old.unlink(),
                work_dir=work_dir,
                trace_stage="apply:file",
            )

    trace(work_dir, "apply:file", action="backend-replace:done", rel=rel)


def replace_directory_from_stage(src_dir: Path, dst_dir: Path, rel: str, stage_root: Path, work_dir: Path) -> None:
    staged_dir = stage_root / rel
    staged_dir.parent.mkdir(parents=True, exist_ok=True)
    if staged_dir.exists():
        retry_io(
            f"cleanup stage {rel}",
            lambda: shutil.rmtree(staged_dir),
            work_dir=work_dir,
            trace_stage="apply:file",
        )

    trace(work_dir, "apply:file", action="stage-dir:start", rel=rel)
    retry_io(
        f"copy stage {rel}",
        lambda: shutil.copytree(src_dir, staged_dir),
        work_dir=work_dir,
        trace_stage="apply:file",
    )

    backup_old = stage_root / "old" / rel
    backup_old.parent.mkdir(parents=True, exist_ok=True)
    renamed_old = False
    try:
        if dst_dir.exists():
            retry_io(
                f"rename old dir {rel}",
                lambda: dst_dir.rename(backup_old),
                work_dir=work_dir,
                trace_stage="apply:file",
            )
            renamed_old = True

        retry_io(
            f"deploy dir {rel}",
            lambda: shutil.copytree(staged_dir, dst_dir),
            work_dir=work_dir,
            trace_stage="apply:file",
        )
    except Exception:
        if dst_dir.exists():
            retry_io(
                f"cleanup partial dir {rel}",
                lambda: shutil.rmtree(dst_dir),
                work_dir=work_dir,
                trace_stage="rollback:file",
            )
        if renamed_old and backup_old.exists():
            retry_io(
                f"restore old dir {rel}",
                lambda: backup_old.rename(dst_dir),
                work_dir=work_dir,
                trace_stage="rollback:file",
            )
        raise
    else:
        if renamed_old and backup_old.exists():
            retry_io(
                f"cleanup old dir backup {rel}",
                lambda: shutil.rmtree(backup_old),
                work_dir=work_dir,
                trace_stage="apply:file",
            )
    trace(work_dir, "apply:file", action="stage-dir:done", rel=rel)


def apply_whitelist_copy(extracted_root: Path, work_dir: Path, stage_root: Path) -> dict[str, Any]:
    updated_targets: list[str] = []
    skipped_count = 0

    for src in extracted_root.rglob("*"):
        if not src.is_file():
            continue
        rel = normalize_rel_path(src, extracted_root)
        if not is_allowed(rel) or is_forbidden(rel):
            skipped_count += 1

    backend_src = extracted_root / "backend.exe"
    backend_dst = work_dir / "backend.exe"
    if backend_src.exists() and backend_src.is_file() and is_allowed("backend.exe"):
        replace_backend_executable(backend_src, backend_dst, work_dir)
        updated_targets.append("backend.exe")

    for rel in ALLOWED_RESOURCE_DIRS:
        src_dir = extracted_root / rel
        if not src_dir.exists() or not src_dir.is_dir():
            continue
        dst_dir = work_dir / rel
        replace_directory_from_stage(src_dir, dst_dir, rel, stage_root, work_dir)
        updated_targets.append(rel)

    return {
        "updated_targets": updated_targets,
        "updated_count": len(updated_targets),
        "skipped_count": skipped_count,
    }


def restore_from_backup(backup_dir: Path, work_dir: Path) -> dict[str, Any]:
    restored: list[str] = []
    trace(work_dir, "rollback:start", backup_dir=str(backup_dir))

    backend_backup = backup_dir / "backend.exe"
    backend_dst = work_dir / "backend.exe"
    if backend_backup.exists() and backend_backup.is_file():
        apply_file_copy(backend_backup, backend_dst, "backend.exe", work_dir, "rollback:file")
        restored.append("backend.exe")
    elif backend_dst.exists():
        retry_io(
            "remove backend.exe added by failed update",
            lambda: backend_dst.unlink(),
            work_dir=work_dir,
            trace_stage="rollback:file",
        )

    for rel in ALLOWED_RESOURCE_DIRS:
        src_dir = backup_dir / rel
        dst_dir = work_dir / rel
        if not src_dir.exists() or not src_dir.is_dir():
            if dst_dir.exists():
                retry_io(
                    f"remove newly created dir {rel} during rollback",
                    lambda d=dst_dir: shutil.rmtree(d),
                    work_dir=work_dir,
                    trace_stage="rollback:file",
                )
            continue
        if dst_dir.exists():
            retry_io(
                f"clear target dir for rollback {rel}",
                lambda d=dst_dir: shutil.rmtree(d),
                work_dir=work_dir,
                trace_stage="rollback:file",
            )
        retry_io(
            f"restore dir {rel}",
            lambda s=src_dir, d=dst_dir: shutil.copytree(s, d),
            work_dir=work_dir,
            trace_stage="rollback:file",
        )
        trace(work_dir, "rollback:file", action="restore-dir:done", rel=rel)
        restored.append(rel)

    trace(work_dir, "rollback:done", restored_count=len(restored))
    return {"restored": restored, "restored_count": len(restored)}


def prepare_update(ctx: RuntimeContext, repo: str, latest_tag: str, zip_url: str, sha_url: str, temp_root: Path) -> PreparedUpdate:
    zip_path = temp_root / RELEASE_ZIP_NAME
    sha_path = temp_root / RELEASE_SHA256_NAME
    extract_path = temp_root / "extract"

    trace(ctx.work_dir, "update:prepare", phase="downloading", zip_url=zip_url, sha_url=sha_url)
    write_phase_status(ctx.work_dir, "downloading", "downloading release assets", "downloading", {"repo": repo, "tag": latest_tag})
    download_to(zip_url, zip_path)
    download_to(sha_url, sha_path)

    expected_sha = parse_sha256_file(sha_path)
    actual_sha = file_sha256(zip_path)
    if actual_sha != expected_sha:
        raise RuntimeError("sha256 verification failed")

    write_phase_status(ctx.work_dir, "extracting", "extracting update package", "extracting", {"repo": repo, "tag": latest_tag})
    with zipfile.ZipFile(zip_path) as archive:
        archive.extractall(extract_path)

    backup_dir = ctx.work_dir / "static" / "backups" / "updater" / datetime.now().strftime("%Y%m%d_%H%M%S")
    trace(ctx.work_dir, "backup:start", backup_dir=str(backup_dir))
    write_phase_status(
        ctx.work_dir,
        "backup",
        "creating backup",
        "backing_up",
        {"repo": repo, "tag": latest_tag, "backup_dir": str(backup_dir)},
    )
    backup_targets(ctx.work_dir, backup_dir)
    trace(ctx.work_dir, "backup:done", backup_dir=str(backup_dir))

    trace(ctx.work_dir, "update:prepare", phase="stopping_backend", backend_pid=ctx.backend_pid)
    write_phase_status(
        ctx.work_dir,
        "stopping",
        "stopping current backend process",
        "stopping_backend",
        {"repo": repo, "tag": latest_tag, "backend_pid": ctx.backend_pid},
    )
    stop_ok = stop_backend_process(ctx.backend_pid, work_dir=ctx.work_dir)
    if not stop_ok:
        raise RuntimeError("failed to stop backend process")
    if os.name == "nt":
        trace(ctx.work_dir, "backend:post-stop-wait", seconds=2.0)
        time.sleep(2.0)

    return PreparedUpdate(repo=repo, latest_tag=latest_tag, extract_path=extract_path, backup_dir=backup_dir)


def apply_update(ctx: RuntimeContext, prepared: PreparedUpdate) -> dict[str, Any]:
    trace(ctx.work_dir, "apply:start", extract_path=str(prepared.extract_path))
    write_phase_status(
        ctx.work_dir,
        "applying",
        "applying whitelist update",
        "applying",
        {"repo": prepared.repo, "tag": prepared.latest_tag},
    )
    with tempfile.TemporaryDirectory(prefix="famyliam-stage-", dir=str(ctx.work_dir)) as stage_dir:
        result = apply_whitelist_copy(prepared.extract_path, ctx.work_dir, Path(stage_dir))
    trace(ctx.work_dir, "apply:done", **result)

    restart_command = resolve_restart_command(ctx)
    trace(ctx.work_dir, "restart:spawn", command=restart_command)
    write_phase_status(
        ctx.work_dir,
        "restarting",
        "starting backend after applying update",
        "restarting",
        {"repo": prepared.repo, "tag": prepared.latest_tag, "restart_command": restart_command},
    )

    restart_started_at = time.time()
    new_pid = start_backend_detached(restart_command, ctx.work_dir)
    verify_ok, verify_message, verify_extra = verify_backend_restart(ctx, new_pid, restart_started_at)
    trace(ctx.work_dir, "restart:verify", ok=verify_ok, message=verify_message, **verify_extra)
    if not verify_ok:
        raise UpdateApplyError(f"backend restart verification failed: {verify_message}", new_backend_pid=new_pid)

    ctx.backend_pid = new_pid
    return {
        "new_backend_pid": new_pid,
        "restart_command": restart_command,
        "restart_verify": verify_message,
        **verify_extra,
        **result,
    }


def finalize_or_rollback(ctx: RuntimeContext, prepared: PreparedUpdate, apply_error: Optional[Exception], apply_extra: Optional[dict[str, Any]]) -> dict[str, Any]:
    if apply_error is None:
        extra = {
            "latest_tag": prepared.latest_tag,
            "repo": prepared.repo,
            **(apply_extra or {}),
        }
        write_phase_status(
            ctx.work_dir,
            "updated",
            "update finished and backend restarted",
            "completed",
            extra,
        )
        return {"state": "updated", "message": "update finished and backend restarted", "extra": extra}

    trace(ctx.work_dir, "rollback:start", reason=repr(apply_error))
    failed_new_pid = 0
    if isinstance(apply_error, UpdateApplyError):
        failed_new_pid = int(apply_error.new_backend_pid or 0)
    if failed_new_pid > 0:
        trace(ctx.work_dir, "rollback:stop-new-backend:start", failed_new_pid=failed_new_pid)
        stop_ok = stop_backend_process(failed_new_pid, work_dir=ctx.work_dir)
        trace(ctx.work_dir, "rollback:stop-new-backend:done", failed_new_pid=failed_new_pid, stop_ok=stop_ok)
        if not stop_ok:
            raise RuntimeError(f"rollback failed: unable to stop failed new backend pid={failed_new_pid}")
        if os.name == "nt":
            trace(ctx.work_dir, "rollback:post-stop-wait", seconds=2.0)
            time.sleep(2.0)

    write_phase_status(
        ctx.work_dir,
        "rollback",
        f"update failed, rolling back: {apply_error}",
        "rollback",
        {"repo": prepared.repo, "tag": prepared.latest_tag},
    )
    rollback_info = restore_from_backup(prepared.backup_dir, ctx.work_dir)

    restart_command = resolve_restart_command(ctx)
    old_pid = start_backend_detached(restart_command, ctx.work_dir)
    verify_ok, verify_message, verify_extra = verify_backend_restart(ctx, old_pid, time.time())
    if not verify_ok:
        raise RuntimeError(
            f"update failed and rollback restart also failed: {apply_error}; rollback verify: {verify_message}"
        )

    ctx.backend_pid = old_pid
    extra = {
        "repo": prepared.repo,
        "latest_tag": prepared.latest_tag,
        "rollback_reason": str(apply_error),
        "rollback_restart_verify": verify_message,
        "rolled_back_backend_pid": old_pid,
        **verify_extra,
        **rollback_info,
    }
    write_phase_status(ctx.work_dir, "rolled_back", "update failed and rollback recovered old version", "completed", extra)
    return {
        "state": "rolled_back",
        "message": "update failed and rollback recovered old version",
        "extra": extra,
    }


def run_update_once(ctx: RuntimeContext, repo: str) -> dict[str, Any]:
    local_version = (ctx.app_version or "").strip() or APP_VERSION
    trace(ctx.work_dir, "release:checking", repo=repo, local_version=local_version)
    write_phase_status(
        ctx.work_dir,
        "checking",
        "checking latest release",
        "checking",
        {"repo": repo, "local_version": local_version},
    )
    release = github_latest_release(repo)
    latest_tag = str(release.get("tag_name") or "")

    if not is_remote_newer(local_version, latest_tag):
        trace(ctx.work_dir, "release:no-update", repo=repo, local_version=local_version, latest_tag=latest_tag)
        extra = {"repo": repo, "local_version": local_version, "latest_tag": latest_tag}
        write_phase_status(ctx.work_dir, "idle", "already up-to-date", "completed", extra)
        return {
            "state": "idle",
            "message": "already up-to-date",
            "extra": extra,
        }

    zip_url = find_asset_url(release, RELEASE_ZIP_NAME)
    sha_url = find_asset_url(release, RELEASE_SHA256_NAME)
    if not zip_url or not sha_url:
        raise RuntimeError("release assets are incomplete")
    trace(ctx.work_dir, "release:update-found", repo=repo, latest_tag=latest_tag)

    with tempfile.TemporaryDirectory(prefix="famyliam-update-") as temp_dir:
        prepared = prepare_update(ctx, repo, latest_tag, zip_url, sha_url, Path(temp_dir))
        apply_error: Optional[Exception] = None
        apply_extra: Optional[dict[str, Any]] = None
        try:
            apply_extra = apply_update(ctx, prepared)
        except Exception as exc:
            apply_error = exc
        return finalize_or_rollback(ctx, prepared, apply_error, apply_extra)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Famyliam updater sidecar")
    parser.add_argument("--watch", action="store_true", help="run forever and check updates every interval")
    parser.add_argument("--interval-hours", type=float, default=24.0, help="watch mode check interval")
    parser.add_argument("--work-dir", default="", help="backend working directory")
    parser.add_argument("--backend-pid", type=int, default=0, help="current backend process pid")
    parser.add_argument("--port", type=int, default=5000, help="backend listen port")
    parser.add_argument("--backend-mode", choices=["auto", "exe", "python"], default="auto")
    parser.add_argument("--backend-executable", default="", help="path to backend executable")
    parser.add_argument("--backend-script", default="", help="path to backend.py")
    parser.add_argument("--python-executable", default="", help="path to python executable")
    parser.add_argument("--repo", default=GITHUB_REPO, help="github repository in owner/repo format")
    return parser


def main() -> int:
    args = build_parser().parse_args()
    work_dir = Path(args.work_dir).resolve() if args.work_dir else Path(__file__).resolve().parent
    trace(work_dir, "main:start", pid=os.getpid(), watch=bool(args.watch), repo=str(args.repo))

    ctx = RuntimeContext(
        work_dir=work_dir,
        port=int(args.port),
        backend_pid=int(args.backend_pid),
        backend_mode=str(args.backend_mode),
        backend_executable=Path(args.backend_executable).resolve() if args.backend_executable else None,
        backend_script=Path(args.backend_script).resolve() if args.backend_script else None,
        python_executable=Path(args.python_executable).resolve() if args.python_executable else None,
        app_version=APP_VERSION,
    )

    trace(work_dir, "lock:acquire", pid=os.getpid())
    instance_lock = acquire_single_instance_lock(work_dir)
    if instance_lock is None:
        trace(work_dir, "lock:exists", pid=os.getpid())
        return 0
    trace(work_dir, "lock:acquire:ok", pid=os.getpid())

    interval_seconds = max(60, int(float(args.interval_hours) * 3600))
    last_result_state = "idle"
    last_result_message = "not checked yet"
    last_result_extra: dict[str, Any] = {"repo": str(args.repo)}
    try:
        while True:
            try:
                sync_runtime_context(ctx)
                trace(
                    work_dir,
                    "runtime:loaded",
                    backend_pid=ctx.backend_pid,
                    port=ctx.port,
                    backend_mode=ctx.backend_mode,
                    app_version=ctx.app_version,
                )
                result = run_update_once(ctx, str(args.repo))
                last_result_state = str(result.get("state") or "idle")
                last_result_message = str(result.get("message") or "")
                last_result_extra = dict(result.get("extra") or {})
            except Exception as exc:
                print(repr(exc), flush=True)
                trace(work_dir, "error:exception", error=repr(exc))
                last_result_state = "error"
                last_result_message = f"update failed: {exc}"
                last_result_extra = {"repo": str(args.repo)}
                write_phase_status(work_dir, last_result_state, last_result_message, "failed", last_result_extra)

            if not args.watch:
                break
            sleep_payload = {
                **last_result_extra,
                "repo": str(args.repo),
                "loop_state": "sleeping",
                "interval_seconds": interval_seconds,
                "last_result_state": last_result_state,
                "last_result_message": last_result_message,
            }
            write_phase_status(work_dir, last_result_state, last_result_message, "sleeping", sleep_payload)
            trace(work_dir, "loop:sleeping", interval_seconds=interval_seconds, last_result_state=last_result_state)
            time.sleep(interval_seconds)
    finally:
        trace(work_dir, "lock:release", pid=os.getpid())
        release_single_instance_lock(instance_lock)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
