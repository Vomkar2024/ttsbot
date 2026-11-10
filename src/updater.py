"""Pre-start / on-demand GitHub repository updater for hosting panels (bot-hosting.net)."""

from dataclasses import dataclass
import hashlib
import os
from pathlib import Path
import shutil
import subprocess
import sys
from typing import Mapping, Optional

try:
    from dotenv import load_dotenv
except ImportError:  # pragma: no cover
    load_dotenv = None  # type: ignore


@dataclass
class UpdateResult:
    attempted: bool = False
    updated: bool = False
    pip_ran: bool = False
    pip_succeeded: bool = False
    before_commit: str = ""
    after_commit: str = ""
    skipped_reason: Optional[str] = None
    error: Optional[str] = None


def _is_truthy(value: Optional[str], default: bool = False) -> bool:
    if value is None:
        return default
    cleaned = str(value).strip().strip('"').strip("'").lower()
    if not cleaned:
        return default
    return cleaned not in ("false", "0", "no", "off")


def _file_sha256(path: Path) -> str:
    if not path.is_file():
        return ""
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _run_subprocess(
    cmd: list[str],
    *,
    cwd: Path,
    timeout: int = 60,
) -> subprocess.CompletedProcess:
    return subprocess.run(
        cmd,
        cwd=str(cwd),
        capture_output=True,
        text=True,
        timeout=timeout,
        check=False,
    )


def run_startup_update(
    repo_dir: Optional[Path] = None,
    *,
    branch: str = "main",
    install_deps: bool = True,
    force_pip: bool = False,
    timeout_sec: int = 60,
) -> UpdateResult:
    """Pulls fast-forward updates from origin/<branch> and reinstalls requirements if changed."""
    root = Path(repo_dir) if repo_dir is not None else Path(__file__).resolve().parent.parent
    result = UpdateResult(attempted=True)

    if not (root / ".git").exists():
        result.attempted = False
        result.skipped_reason = "not_git_repo"
        print(
            "[Updater] Skipping git pull: '.git' folder not found in project root. "
            "Re-clone from GitHub once in the hosting console to enable 'git pull' updates.",
            flush=True,
        )
        return result

    git_bin = shutil.which("git")
    if not git_bin:
        result.attempted = False
        result.skipped_reason = "git_missing"
        print(
            "[Updater] Skipping git pull: 'git' binary is not installed in the container.",
            file=sys.stderr,
            flush=True,
        )
        return result

    req_file = root / "requirements.txt"
    req_hash_before = _file_sha256(req_file)

    try:
        rev_before_proc = _run_subprocess([git_bin, "rev-parse", "--short", "HEAD"], cwd=root, timeout=15)
        result.before_commit = rev_before_proc.stdout.strip() if rev_before_proc.returncode == 0 else ""

        print(
            f"[Updater] Fetching latest code from origin/{branch} (current commit: {result.before_commit or 'unknown'})...",
            flush=True,
        )
        fetch_proc = _run_subprocess([git_bin, "fetch", "origin", branch], cwd=root, timeout=timeout_sec)
        if fetch_proc.returncode != 0:
            err_detail = (fetch_proc.stderr or fetch_proc.stdout).strip()
            result.error = f"git fetch failed (exit={fetch_proc.returncode}): {err_detail}"
            print(f"[Updater] WARNING: {result.error}", file=sys.stderr, flush=True)
            return result

        pull_proc = _run_subprocess(
            [git_bin, "pull", "--ff-only", "origin", branch],
            cwd=root,
            timeout=timeout_sec,
        )
        if pull_proc.returncode != 0:
            err_detail = (pull_proc.stderr or pull_proc.stdout).strip()
            result.error = f"git pull --ff-only failed (exit={pull_proc.returncode}): {err_detail}"
            print(
                f"[Updater] WARNING: {result.error}. Starting bot with existing files.",
                file=sys.stderr,
                flush=True,
            )
            return result

        rev_after_proc = _run_subprocess([git_bin, "rev-parse", "--short", "HEAD"], cwd=root, timeout=15)
        result.after_commit = rev_after_proc.stdout.strip() if rev_after_proc.returncode == 0 else ""
        result.updated = bool(
            result.before_commit and result.after_commit and result.before_commit != result.after_commit
        )

        if result.updated:
            print(
                f"[Updater] Updated code from {result.before_commit} -> {result.after_commit}.",
                flush=True,
            )
        else:
            print(
                f"[Updater] Repository already up to date ({result.after_commit or result.before_commit or 'HEAD'}).",
                flush=True,
            )
    except subprocess.TimeoutExpired as exc:
        result.error = f"Git command timed out after {timeout_sec}s: {exc}"
        print(f"[Updater] WARNING: {result.error}", file=sys.stderr, flush=True)
        return result
    except Exception as exc:
        result.error = f"Git update raised unexpected error: {exc}"
        print(f"[Updater] WARNING: {result.error}", file=sys.stderr, flush=True)
        return result

    req_hash_after = _file_sha256(req_file)
    deps_changed = req_hash_before != req_hash_after

    if install_deps and req_file.is_file() and (deps_changed or force_pip):
        reason = "requirements.txt changed" if deps_changed else "forced install requested"
        print(f"[Updater] Reinstalling Python dependencies ({reason})...", flush=True)
        result.pip_ran = True
        try:
            pip_proc = _run_subprocess(
                [sys.executable, "-m", "pip", "install", "--no-cache-dir", "-r", str(req_file)],
                cwd=root,
                timeout=max(timeout_sec, 180),
            )
            if pip_proc.returncode == 0:
                result.pip_succeeded = True
                print("[Updater] Python dependencies installed cleanly.", flush=True)
            else:
                err_detail = (pip_proc.stderr or pip_proc.stdout).strip()
                result.error = f"pip install failed (exit={pip_proc.returncode}): {err_detail[-500:]}"
                print(f"[Updater] WARNING: {result.error}", file=sys.stderr, flush=True)
        except Exception as exc:
            result.error = f"pip install exception: {exc}"
            print(f"[Updater] WARNING: {result.error}", file=sys.stderr, flush=True)

    return result


def maybe_update_on_startup(
    *,
    force: bool = False,
    force_pip: bool = False,
    env: Optional[Mapping[str, str]] = None,
    repo_dir: Optional[Path] = None,
) -> UpdateResult:
    """Runs `run_startup_update` if AUTO_GIT_PULL=true or `force=True`."""
    if load_dotenv is not None and env is None:
        load_dotenv()

    source = os.environ if env is None else env
    enabled = force or _is_truthy(source.get("AUTO_GIT_PULL"), default=False)
    if not enabled:
        return UpdateResult(attempted=False, skipped_reason="disabled")

    branch = (source.get("AUTO_GIT_PULL_BRANCH") or "main").strip() or "main"
    install_deps = _is_truthy(source.get("AUTO_GIT_PULL_PIP"), default=True)
    raw_timeout = source.get("AUTO_GIT_PULL_TIMEOUT_SEC") or "60"
    try:
        timeout_sec = max(5, int(str(raw_timeout).strip()))
    except ValueError:
        timeout_sec = 60

    return run_startup_update(
        repo_dir=repo_dir,
        branch=branch,
        install_deps=install_deps,
        force_pip=force_pip,
        timeout_sec=timeout_sec,
    )


__all__ = ["UpdateResult", "run_startup_update", "maybe_update_on_startup"]

