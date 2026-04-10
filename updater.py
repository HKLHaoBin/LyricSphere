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
from typing import Any, Optional

import requests

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
FORBIDDEN_PREFIXES = ("static/songs/", "static/backups/")


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


def acquire_single_instance_lock(work_dir: Path) -> bool:
    lock_path = work_dir / LOCK_FILE_NAME
    current_pid = os.getpid()
    if lock_path.exists():
        try:
            existing_pid = int(lock_path.read_text(encoding="utf-8").strip())
        except Exception:
            existing_pid = -1
        if existing_pid > 0 and existing_pid != current_pid and is_pid_running(existing_pid):
            return False
    lock_path.write_text(str(current_pid), encoding="utf-8")
    return True


def release_single_instance_lock(work_dir: Path) -> None:
    lock_path = work_dir / LOCK_FILE_NAME
    if not lock_path.exists():
        return
    try:
        content = lock_path.read_text(encoding="utf-8").strip()
        if int(content) == os.getpid():
            lock_path.unlink(missing_ok=True)
    except Exception:
        pass


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


def apply_whitelist_copy(extracted_root: Path, work_dir: Path) -> dict[str, Any]:
    copied: list[str] = []
    skipped: list[str] = []

    for src in extracted_root.rglob("*"):
        if not src.is_file():
            continue
        rel = normalize_rel_path(src, extracted_root)
        if is_forbidden(rel):
            skipped.append(rel)
            continue
        if not is_allowed(rel):
            skipped.append(rel)
            continue

        dst = work_dir / rel
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, dst)
        copied.append(rel)

    return {
        "copied_count": len(copied),
        "skipped_count": len(skipped),
    }


def run_update_once(ctx: RuntimeContext, repo: str) -> dict[str, Any]:
    local_version = (ctx.app_version or "").strip() or APP_VERSION
    trace(ctx.work_dir, "release:checking", repo=repo, local_version=local_version)
    write_status(ctx.work_dir, "checking", "checking latest release", {"repo": repo, "local_version": local_version})
    release = github_latest_release(repo)
    latest_tag = str(release.get("tag_name") or "")

    if not is_remote_newer(local_version, latest_tag):
        trace(ctx.work_dir, "release:no-update", repo=repo, local_version=local_version, latest_tag=latest_tag)
        write_status(
            ctx.work_dir,
            "idle",
            "already up-to-date",
            {"repo": repo, "local_version": local_version, "latest_tag": latest_tag},
        )
        return {
            "state": "idle",
            "message": "already up-to-date",
            "extra": {"repo": repo, "local_version": local_version, "latest_tag": latest_tag},
        }

    zip_url = find_asset_url(release, RELEASE_ZIP_NAME)
    sha_url = find_asset_url(release, RELEASE_SHA256_NAME)
    if not zip_url or not sha_url:
        raise RuntimeError("release assets are incomplete")

    trace(ctx.work_dir, "release:update-found", repo=repo, latest_tag=latest_tag)

    with tempfile.TemporaryDirectory(prefix="famyliam-update-") as temp_dir:
        temp_root = Path(temp_dir)
        zip_path = temp_root / RELEASE_ZIP_NAME
        sha_path = temp_root / RELEASE_SHA256_NAME
        extract_path = temp_root / "extract"

        trace(ctx.work_dir, "download:start", zip_url=zip_url, sha_url=sha_url)
        write_status(ctx.work_dir, "downloading", "downloading release assets", {"repo": repo, "tag": latest_tag})
        download_to(zip_url, zip_path)
        trace(ctx.work_dir, "download:zip-done", zip_path=str(zip_path))
        download_to(sha_url, sha_path)
        trace(ctx.work_dir, "download:sha-done", sha_path=str(sha_path))

        expected_sha = parse_sha256_file(sha_path)
        actual_sha = file_sha256(zip_path)
        if actual_sha != expected_sha:
            raise RuntimeError("sha256 verification failed")
        trace(ctx.work_dir, "verify:sha256-ok", sha256=actual_sha)

        write_status(ctx.work_dir, "extracting", "extracting update package", {"repo": repo, "tag": latest_tag})
        with zipfile.ZipFile(zip_path) as archive:
            archive.extractall(extract_path)
        trace(ctx.work_dir, "extract:done", extract_path=str(extract_path))

        backup_dir = ctx.work_dir / "static" / "backups" / "updater" / datetime.now().strftime("%Y%m%d_%H%M%S")
        write_status(ctx.work_dir, "backup", "creating backup", {"repo": repo, "backup_dir": str(backup_dir)})
        backup_targets(ctx.work_dir, backup_dir)
        trace(ctx.work_dir, "backup:done", backup_dir=str(backup_dir))

        trace(ctx.work_dir, "backend:stopping", backend_pid=ctx.backend_pid)
        write_status(ctx.work_dir, "stopping", "stopping current backend process", {"repo": repo, "backend_pid": ctx.backend_pid})
        stop_ok = stop_backend_process(ctx.backend_pid, work_dir=ctx.work_dir)
        trace(ctx.work_dir, "backend:stopped", backend_pid=ctx.backend_pid, stop_ok=stop_ok)
        if not stop_ok:
            raise RuntimeError("failed to stop backend process")

        trace(ctx.work_dir, "apply:start", extract_path=str(extract_path))
        write_status(ctx.work_dir, "applying", "applying whitelist update", {"repo": repo, "tag": latest_tag})
        result = apply_whitelist_copy(extract_path, ctx.work_dir)
        trace(ctx.work_dir, "apply:done", **result)

        restart_command = resolve_restart_command(ctx)
        trace(ctx.work_dir, "backend:restart-command", command=restart_command)
        restart_started_at = time.time()
        new_pid = start_backend_detached(restart_command, ctx.work_dir)
        trace(ctx.work_dir, "backend:restart-spawned", new_backend_pid=new_pid)
        ctx.backend_pid = new_pid

        verify_ok, verify_message, verify_extra = verify_backend_restart(ctx, new_pid, restart_started_at)
        if not verify_ok:
            raise RuntimeError(f"backend restart verification failed: {verify_message}")

        trace(ctx.work_dir, "backend:restart-verified", verify_message=verify_message, **verify_extra)
        write_status(
            ctx.work_dir,
            "updated",
            "update finished and backend restarted",
            {
                "latest_tag": latest_tag,
                "repo": repo,
                "new_backend_pid": new_pid,
                "restart_command": restart_command,
                "restart_verify": verify_message,
                **verify_extra,
                **result,
            },
        )
        return {
            "state": "updated",
            "message": "update finished and backend restarted",
            "extra": {
                "latest_tag": latest_tag,
                "repo": repo,
                "new_backend_pid": new_pid,
                "restart_command": restart_command,
                "restart_verify": verify_message,
                **verify_extra,
                **result,
            },
        }


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

    if not acquire_single_instance_lock(work_dir):
        trace(work_dir, "lock:exists", pid=os.getpid())
        return 0
    trace(work_dir, "lock:acquired", pid=os.getpid())

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
                write_status(work_dir, last_result_state, last_result_message, last_result_extra)

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
            write_status(work_dir, last_result_state, last_result_message, sleep_payload)
            trace(work_dir, "loop:sleeping", interval_seconds=interval_seconds, last_result_state=last_result_state)
            time.sleep(interval_seconds)
    finally:
        trace(work_dir, "lock:released", pid=os.getpid())
        release_single_instance_lock(work_dir)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
