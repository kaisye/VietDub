from __future__ import annotations

import base64
import hashlib
import json
import os
import re
import shlex
import socket
import subprocess
import tempfile
import threading
import time
import webbrowser
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlsplit, urlunsplit

import httpx

from .runtime_settings import get_runtime_settings, update_runtime_settings
from .storage import ensure_storage


SESSION_NAME = os.getenv("AETHER_COLAB_SESSION_NAME", "aether-omnivoice").strip() or "aether-omnivoice"
WSL_DISTRO = os.getenv("AETHER_COLAB_WSL_DISTRO", "").strip()
PREFERRED_WSL_DISTROS = ("Ubuntu-24.04", "Ubuntu")
COLAB_GPU = os.getenv("AETHER_COLAB_GPU", "T4").strip() or "T4"
COLAB_BIN = os.getenv("AETHER_COLAB_BIN", "/root/.local/bin/colab").strip() or "/root/.local/bin/colab"
# google-colab-cli 0.6.0 calls jupyter_kernel_client.KernelClient but declares
# the dependency unpinned. jupyter-kernel-client 1.0.0 renamed that class to
# JupyterKernelClient and dropped the old name, so an unconstrained install now
# resolves to 1.x and every `colab exec` dies with "module
# 'jupyter_kernel_client' has no attribute 'KernelClient'". 0.9 is the
# floor because colab-cli also reads the top-level JupyterSubprotocol added there.
KERNEL_CLIENT_PIN = "jupyter-kernel-client>=0.9,<1"

READY_PREFIX = "AETHER_OMNIVOICE_READY="
STATE_PREFIX = "AETHER_OMNIVOICE_STATE="

# colab-cli credential layout inside WSL. The live token is what every `colab`
# command reads; we archive a copy per Google account under ACCOUNTS_DIR so a
# previously used account can be restored without a fresh browser login.
COLAB_CONFIG_DIR = "/root/.config/colab-cli"
LIVE_TOKEN_PATH = f"{COLAB_CONFIG_DIR}/token.json"
ACCOUNTS_DIR = "/root/.config/colab-cli-accounts"
GOOGLE_USERINFO_URL = "https://www.googleapis.com/oauth2/v3/userinfo"

_process_lock = threading.Lock()
_active_process: subprocess.Popen[str] | None = None
_active_thread: threading.Thread | None = None
_stop_requested = threading.Event()
_oauth_bridge_thread: threading.Thread | None = None
_oauth_bridge_stop: threading.Event | None = None


def _env_int(name: str, default: int, *, minimum: int, maximum: int) -> int:
    try:
        value = int(str(os.getenv(name, "")).strip())
    except (TypeError, ValueError):
        return default
    return max(minimum, min(maximum, value))


def _exec_timeout_seconds() -> int:
    # The bootstrap cell runs for the whole session (see the heartbeat loop), so
    # the exec timeout must cover Colab's full runtime lifetime rather than the
    # old hard 1-hour cap that killed the live cell and let the VM go idle.
    # Default ~12h (Colab free-tier ceiling); override with the env var.
    return _env_int(
        "AETHER_COLAB_EXEC_TIMEOUT_SECONDS", 43200, minimum=600, maximum=86400
    )


def _heartbeat_interval_seconds() -> int:
    return _env_int(
        "AETHER_COLAB_HEARTBEAT_SECONDS", 240, minimum=30, maximum=540
    )


def start_colab_runtime() -> dict[str, Any]:
    environment = _environment_status()
    if not environment["wsl_available"] or not environment["distro_available"]:
        state = _default_state()
        state.update(environment)
        state["state"] = "setup_required"
        state["error"] = "An Ubuntu WSL distribution is required for google-colab-cli."
        _write_state(state)
        return get_colab_runtime_status()
    if not environment["cli_installed"]:
        state = _read_state()
        state.update(environment)
        state["state"] = "setup_required"
        state["error"] = "google-colab-cli is not installed in the configured WSL distribution."
        state["updated_at"] = time.time()
        _write_state(state)
        return get_colab_runtime_status()

    global _active_thread
    with _process_lock:
        launcher_running = bool(_active_thread and _active_thread.is_alive())
    if launcher_running:
        # A live launcher usually means a healthy in-progress/ready session, so
        # leave it alone. But when the Colab VM disconnects the `colab exec` stream
        # can hang open: the launcher thread then never exits, and a plain relaunch
        # would no-op forever — stranding the runtime in "disconnected" with no way
        # to recover except a manual Stop. Detect a dead-but-stuck session by health
        # and tear it down first so both the UI Launch button and the in-job auto-
        # recovery can actually restart it.
        existing_url = str(_read_state().get("api_url") or "").strip().rstrip("/")
        if existing_url and _remote_health(existing_url)["reachable"]:
            return get_colab_runtime_status()
        stop_colab_runtime()

    with _process_lock:
        if _active_thread and _active_thread.is_alive():
            return get_colab_runtime_status()
        _write_state(
            {
                **_default_state(),
                **environment,
                "state": "starting",
                "quota_message": f"Requesting a Colab {COLAB_GPU} GPU runtime.",
                "updated_at": time.time(),
            }
        )
        _stop_requested.clear()
        _active_thread = threading.Thread(
            target=_launch_worker,
            daemon=True,
            name="aether-colab-cli-launcher",
        )
        _active_thread.start()
    return get_colab_runtime_status()


def install_colab_cli() -> dict[str, Any]:
    environment = _environment_status()
    if not environment["distro_available"]:
        state = {**_default_state(), **environment}
        state["state"] = "setup_required"
        state["error"] = "Install Ubuntu WSL first using the setup command shown below."
        _write_state(state)
        return get_colab_runtime_status()

    global _active_thread
    with _process_lock:
        if _active_thread and _active_thread.is_alive():
            return get_colab_runtime_status()
        state = {**_read_state(), **environment}
        state["state"] = "installing"
        state["error"] = None
        state["updated_at"] = time.time()
        _write_state(state)
        _active_thread = threading.Thread(
            target=_install_worker,
            daemon=True,
            name="aether-colab-cli-installer",
        )
        _active_thread.start()
    return get_colab_runtime_status()


def switch_colab_account() -> dict[str, Any]:
    # Archive the account in use first so adding a new one never discards it —
    # the user can switch straight back without logging in again.
    _capture_active_account()
    stop_colab_runtime()
    # Delete the saved OAuth token so the next launch prompts for a new account.
    # Also remove sessions.json so the status check returns "no session" — this
    # forces _launch_worker to run `colab new` (which opens the auth URL) rather
    # than skipping straight to `colab exec` where auth URLs are not captured.
    result = _run_wsl(
        "rm -f "
        "/root/.config/colab-cli/token.json "
        "/root/.config/colab-cli/sessions.json "
        "/root/.config/colab-cli/sessions.json.lock",
        timeout=20,
    )
    if result.returncode != 0:
        state = {**_default_state(), **_environment_status()}
        state["state"] = "error"
        state["error"] = "Unable to clear the saved Google account: " + _failure_message(result.stdout)
        state["updated_at"] = time.time()
        _write_state(state)
        return get_colab_runtime_status()
    if not _release_oauth_callback_port():
        state = {**_default_state(), **_environment_status()}
        state["state"] = "error"
        state["error"] = "Unable to release the Google OAuth callback port 8200 in WSL."
        state["updated_at"] = time.time()
        _write_state(state)
        return get_colab_runtime_status()
    return start_colab_runtime()


def submit_colab_auth_code(code: str) -> dict[str, Any]:
    # Hand the authorization code the user copied from Google to the live
    # `colab new` prompt waiting on stdin (the >= 0.6 copy-paste login flow).
    cleaned = (code or "").strip()
    with _process_lock:
        process = _active_process
    if not cleaned:
        _update_state(error="Enter the authorization code Google showed after you signed in.")
        return get_colab_runtime_status()
    if process is None or process.poll() is not None or process.stdin is None:
        _update_state(
            needs_auth_code=False,
            error="The Google sign-in prompt is no longer active. Launch again to retry.",
        )
        return get_colab_runtime_status()
    try:
        process.stdin.write(cleaned + "\n")
        process.stdin.flush()
    except (OSError, ValueError) as exc:
        _update_state(
            needs_auth_code=False,
            error=f"Could not submit the authorization code: {exc}",
        )
        return get_colab_runtime_status()
    _update_state(
        state="waiting_for_gpu",
        authorization_url="",
        needs_auth_code=False,
        quota_message="Verifying the authorization code and requesting a GPU…",
        error=None,
    )
    return get_colab_runtime_status()


def stop_colab_runtime() -> dict[str, Any]:
    global _active_process, _active_thread
    _stop_requested.set()
    with _process_lock:
        process = _active_process
        _active_process = None
        launcher = _active_thread
    if process and process.poll() is None:
        process.terminate()
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            process.kill()
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                pass
    if _environment_status()["cli_installed"] and _has_colab_token():
        _run_colab(["stop", "-s", SESSION_NAME], timeout=60)
    _release_oauth_callback_port()

    # The launcher owns the long-running `colab exec` stream. Starting a new
    # account while this thread is still registered makes start_colab_runtime()
    # return early, so the OAuth flow never opens and the old bootstrap may keep
    # port 8008 occupied. Let the terminated process unwind the worker first.
    if launcher and launcher is not threading.current_thread():
        launcher.join(timeout=10)
    with _process_lock:
        if _active_thread is launcher and (launcher is None or not launcher.is_alive()):
            _active_thread = None

    prior = _read_state()
    state = {**_default_state(), **_environment_status()}
    state["state"] = "stopped"
    # Keep the signed-in account visible in the header while stopped — the saved
    # token is still on disk, so the connection is paused, not signed out.
    for key in ("account_email", "account_name", "account_picture", "account_hint"):
        if prior.get(key):
            state[key] = prior[key]
    state["updated_at"] = time.time()
    _write_state(state)
    return get_colab_runtime_status()


def get_colab_runtime_status() -> dict[str, Any]:
    state = _read_state()
    persisted_state = dict(state)
    should_refresh_environment = state["state"] in {"stopped", "setup_required"} or (
        state["state"] not in {"installing", "ready", "loading", "starting", "disconnected"}
        and (not state.get("distro_available") or not state.get("cli_installed"))
    )
    if should_refresh_environment:
        state.update(_environment_status())
        if not state["distro_available"] or not state["cli_installed"]:
            state["state"] = "setup_required"
            state["authorization_url"] = ""
            state["api_url"] = ""
            state["error"] = (
                "An Ubuntu WSL distribution is required for google-colab-cli."
                if not state["distro_available"]
                else "google-colab-cli is not installed in the configured WSL distribution."
            )
        elif state["state"] == "setup_required":
            state["state"] = "stopped"
            state["error"] = None

    api_url = str(state.get("api_url") or "").strip().rstrip("/")
    if api_url:
        health = _remote_health(api_url)
        state["reachable"] = health["reachable"]
        if health["reachable"]:
            remote_state = str(health.get("status") or "ready")
            state["state"] = "loading" if remote_state in {"loading", "not_loaded"} else remote_state
            state["device"] = health.get("device") or state.get("device") or ""
            state["model"] = health.get("model")
            state["error"] = health.get("error")
        elif state.get("state") in {"ready", "loading", "starting"}:
            state["state"] = "disconnected"
            state["error"] = health.get("error")
    else:
        state["reachable"] = False

    # The pasted-code prompt only applies while the login is actually waiting.
    if state.get("state") != "waiting_for_login":
        state["needs_auth_code"] = False

    started_at = state.get("session_started_at")
    state["session_age_seconds"] = max(0, int(time.time() - float(started_at))) if started_at else None
    if any(
        state.get(key) != persisted_state.get(key)
        for key in ("state", "reachable", "device", "model", "error", "needs_auth_code")
    ):
        state["updated_at"] = time.time()
        _write_state(state)
    return state


def _launch_worker() -> None:
    try:
        # If the stored Colab URL is already reachable, skip re-bootstrapping —
        # re-running the bootstrap would pkill the live OmniVoice process.
        current_state = _read_state()
        existing_url = str(current_state.get("api_url") or "").strip().rstrip("/")
        if existing_url and _remote_health(existing_url)["reachable"]:
            _update_state(state="ready", error=None)
            return

        # Every CLI command that needs credentials can start a localhost OAuth
        # callback server. Calling `colab status` without a token therefore hangs
        # on port 8200 and the following `colab new` crashes with Errno 98. Skip
        # status entirely for a fresh/switched account and enter the interactive
        # login flow directly.
        has_token = _has_colab_token()
        if has_token:
            status = _run_colab(["status", "-s", SESSION_NAME], timeout=45)
            session_exists = status.returncode == 0 and f"[{SESSION_NAME}]" in status.stdout
        else:
            session_exists = False
        if not session_exists:
            if not _release_oauth_callback_port():
                raise RuntimeError("Google OAuth callback port 8200 is still occupied in WSL.")
            _start_oauth_loopback_bridge()
            result = _run_colab_interactive(["new", "-s", SESSION_NAME, "--gpu", COLAB_GPU])
            if result != 0:
                raise RuntimeError(_failure_message(_read_log()))

        _update_state(
            state="starting",
            quota_state="available",
            quota_message=f"Colab assigned the requested {COLAB_GPU} runtime.",
            session_started_at=time.time(),
            error=None,
        )
        # Capture (and archive) the signed-in account: email, display name and
        # avatar for the UI, plus a saved token so it can be restored later.
        # Fall back to `colab whoami` for just the email if the profile fetch
        # fails (e.g. offline).
        if not _capture_active_account():
            account = _run_colab(["whoami"], timeout=60)
            account_match = re.search(r"Email:\s+([^\s]+)", account.stdout)
            if account_match:
                _update_state(account_hint=account_match.group(1))

        bootstrap_path = _write_bootstrap_script()
        exec_arguments = [
            "exec", "-s", SESSION_NAME,
            "-f", _windows_to_wsl_path(bootstrap_path),
            "--timeout", str(_exec_timeout_seconds()),
        ]
        output: list[str] = []
        result = _run_colab_streaming(
            exec_arguments, parse_bootstrap=True, sink=output
        )
        if _stop_requested.is_set():
            return
        if result != 0 and _broken_kernel_client("".join(output)):
            # Copies installed before KERNEL_CLIENT_PIN existed resolved to
            # jupyter-kernel-client 1.x. The UI only offers the install button
            # while the CLI is missing, so repair here rather than leaving the
            # user with a permanently broken install and no way to redo it.
            _update_state(
                state="installing",
                quota_message="Repairing google-colab-cli dependencies.",
                error=None,
            )
            if not _repair_colab_cli():
                raise RuntimeError(_failure_message(_read_log()))
            _update_state(state="starting", error=None)
            output = []
            result = _run_colab_streaming(
                exec_arguments, parse_bootstrap=True, sink=output
            )
            if _stop_requested.is_set():
                return
        # The bootstrap cell runs for the whole session, so exec returning means
        # the session ended (timeout/disconnect). Only treat that as an error if
        # the runtime is genuinely unreachable — a tunnel that is still healthy
        # must not be flipped to "error" just because the exec stream closed.
        existing_url = str(_read_state().get("api_url") or "").strip().rstrip("/")
        if existing_url and _remote_health(existing_url)["reachable"]:
            _update_state(state="ready", error=None)
            return
        if result != 0:
            raise RuntimeError(_failure_message(_read_log()))
        if _read_state().get("state") != "ready":
            raise RuntimeError("OmniVoice bootstrap finished without returning a public tunnel URL.")
    except Exception as exc:
        if _stop_requested.is_set():
            return
        message = str(exc)
        _update_state(
            state="error",
            quota_state=_quota_state_from_error(message),
            quota_message=_quota_message_from_error(message),
            error=message,
        )


def _install_command() -> str:
    return (
        "set -e; "
        "export PATH=\"$HOME/.local/bin:$PATH\"; "
        "if ! command -v uv >/dev/null 2>&1; then "
        "curl -LsSf https://astral.sh/uv/install.sh | sh; "
        "fi; "
        "export PATH=\"$HOME/.local/bin:$PATH\"; "
        "uv tool install --python 3.12 --force "
        f"--with {shlex.quote(KERNEL_CLIENT_PIN)} google-colab-cli; "
        "colab version"
    )


def _repair_colab_cli() -> bool:
    """Reinstall the CLI with the pin applied. True when it succeeds."""
    result = _run_wsl(_install_command(), timeout=900)
    _append_log(result.stdout)
    return result.returncode == 0


def _install_worker() -> None:
    _clear_log()
    result = _run_wsl(_install_command(), timeout=900)
    _append_log(result.stdout)
    if result.returncode == 0:
        state = {**_read_state(), **_environment_status()}
        state["state"] = "stopped"
        state["error"] = None
        state["updated_at"] = time.time()
        _write_state(state)
    else:
        _update_state(state="setup_required", error=_failure_message(result.stdout))


def _run_colab_interactive(arguments: list[str]) -> int:
    command = _colab_shell_command(arguments)
    _clear_log()
    # google-colab-cli >= 0.6 authorizes through a copy-paste authorization code
    # (redirect_uri=.../applicationdefaultauthcode.html) and blocks on a stdin
    # prompt for that code instead of redirecting to localhost:8200. A PIPE keeps
    # the prompt alive so submit_colab_auth_code() can feed the code the user
    # copies from Google; DEVNULL would EOF the prompt immediately ("Aborted.").
    process = subprocess.Popen(
        _wsl_args(command),
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        encoding="utf-8",
        errors="replace",
        bufsize=1,
        creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
    )
    global _active_process
    with _process_lock:
        _active_process = process
    opened_authorization_url = ""
    try:
        if process.stdout:
            for line in process.stdout:
                _append_log(line)
                auth_url = _authorization_url(line)
                if auth_url:
                    console_login = _is_console_login_url(auth_url)
                    _update_state(
                        state="waiting_for_login",
                        authorization_url=auth_url,
                        needs_auth_code=console_login,
                        quota_message=(
                            "Sign in with Google, then copy the authorization code it "
                            "shows and paste it back into Aether to continue."
                            if console_login
                            else "Sign in with Google. The browser will return to "
                            "localhost:8200 and Aether will continue automatically."
                        ),
                        error=None,
                    )
                    if auth_url != opened_authorization_url:
                        _open_authorization_url(auth_url)
                        opened_authorization_url = auth_url
                elif "Creating session" in line:
                    _update_state(state="waiting_for_gpu", error=None)
        return process.wait()
    finally:
        with _process_lock:
            if _active_process is process:
                _active_process = None


def _run_colab_streaming(
    arguments: list[str],
    *,
    parse_bootstrap: bool = False,
    sink: list[str] | None = None,
) -> int:
    command = _colab_shell_command(arguments)
    process = subprocess.Popen(
        _wsl_args(command),
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        encoding="utf-8",
        errors="replace",
        bufsize=1,
        creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
    )
    global _active_process
    with _process_lock:
        _active_process = process
    try:
        if process.stdout:
            for line in process.stdout:
                _append_log(line)
                if sink is not None:
                    sink.append(line)
                auth_url = _authorization_url(line)
                if auth_url:
                    _update_state(
                        state="waiting_for_login",
                        authorization_url=auth_url,
                        quota_message=(
                            "Sign in with Google. The browser will return to localhost:8200 "
                            "and Aether will continue automatically."
                        ),
                        error=None,
                    )
                elif parse_bootstrap:
                    _consume_bootstrap_marker(line)
        return process.wait()
    finally:
        with _process_lock:
            if _active_process is process:
                _active_process = None


def _consume_bootstrap_marker(line: str) -> None:
    for prefix in (STATE_PREFIX, READY_PREFIX):
        marker_index = line.find(prefix)
        if marker_index < 0:
            continue
        payload_text = line[marker_index + len(prefix) :].strip()
        try:
            payload = json.loads(payload_text)
        except json.JSONDecodeError:
            return
        if prefix == READY_PREFIX:
            payload["state"] = "ready"
            payload["quota_state"] = "available"
            payload["quota_message"] = (
                "A free GPU runtime is assigned. Google does not expose remaining quota as a number."
            )
        _update_state(**payload)
        api_url = str(payload.get("api_url") or "").strip().rstrip("/")
        if api_url:
            update_runtime_settings(
                {
                    "tts_provider": "omnivoice",
                    "omnivoice_runtime": "colab",
                    "omnivoice_device": "remote",
                    "omnivoice_api_url": api_url,
                }
            )


def _run_colab(arguments: list[str], *, timeout: int) -> subprocess.CompletedProcess[str]:
    return _run_wsl(_colab_shell_command(arguments), timeout=timeout)


def _open_authorization_url(url: str) -> bool:
    try:
        return bool(webbrowser.open(url, new=2, autoraise=True))
    except (OSError, webbrowser.Error):
        return False


def _has_colab_token() -> bool:
    result = _run_wsl("test -s /root/.config/colab-cli/token.json", timeout=10)
    return result.returncode == 0


# --- Saved Google accounts ---------------------------------------------------
# Switching account used to delete the saved OAuth token, forcing a full browser
# login every time. We instead archive each account's token.json under
# ACCOUNTS_DIR keyed by a slug of its email. Restoring a saved token lets
# colab-cli refresh silently (the refresh_token never expires for these scopes),
# so switching back to a known account skips the browser entirely.


_WSL_READ_SENTINEL = "AETHER_B64"


def _wsl_read_text(path: str) -> str | None:
    """Read a file from WSL as text (base64 transport avoids encoding surprises).

    The payload is wrapped in sentinels so any login-shell/MOTD noise a `bash -lc`
    might print can't corrupt the decoded bytes.
    """
    command = (
        f"test -s {shlex.quote(path)} || exit 9; "
        f"printf '<{_WSL_READ_SENTINEL}>'; "
        f"base64 -w0 {shlex.quote(path)} 2>/dev/null; "
        f"printf '</{_WSL_READ_SENTINEL}>'"
    )
    result = _run_wsl(command, timeout=15)
    if result.returncode != 0:
        return None
    match = re.search(
        rf"<{_WSL_READ_SENTINEL}>(.*)</{_WSL_READ_SENTINEL}>", result.stdout, re.S
    )
    data = (match.group(1).strip() if match else "")
    if not data:
        return None
    try:
        return base64.b64decode(data).decode("utf-8")
    except (ValueError, UnicodeDecodeError):
        return None


def _wsl_write_text(path: str, content: str) -> bool:
    encoded = base64.b64encode(content.encode("utf-8")).decode("ascii")
    directory = path.rsplit("/", 1)[0]
    command = (
        f"mkdir -p {shlex.quote(directory)} && "
        f"printf %s {shlex.quote(encoded)} | base64 -d > {shlex.quote(path)}"
    )
    return _run_wsl(command, timeout=15).returncode == 0


def _account_slug(value: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", (value or "").lower()).strip("-")
    return slug or "account"


def _token_key(token: dict[str, Any]) -> str:
    raw = str(token.get("refresh_token") or "")
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16] if raw else ""


def _fetch_account_identity(token: dict[str, Any]) -> dict[str, str] | None:
    """Refresh the access token and read the Google profile (email/name/avatar).

    Uses the stored refresh_token — no browser, no interaction. The userinfo
    ``picture`` is a public googleusercontent URL the UI can load directly.
    """
    refresh_token = token.get("refresh_token")
    client_id = token.get("client_id")
    client_secret = token.get("client_secret")
    token_uri = token.get("token_uri") or "https://oauth2.googleapis.com/token"
    if not (refresh_token and client_id and client_secret):
        return None
    try:
        token_response = httpx.post(
            token_uri,
            data={
                "client_id": client_id,
                "client_secret": client_secret,
                "refresh_token": refresh_token,
                "grant_type": "refresh_token",
            },
            timeout=15,
        )
        token_response.raise_for_status()
        access_token = str(token_response.json().get("access_token") or "")
        if not access_token:
            return None
        userinfo_response = httpx.get(
            GOOGLE_USERINFO_URL,
            headers={"Authorization": f"Bearer {access_token}"},
            timeout=15,
        )
        userinfo_response.raise_for_status()
        info = userinfo_response.json()
    except (httpx.HTTPError, ValueError):
        return None
    email = str(info.get("email") or "").strip()
    if not email:
        return None
    return {
        "email": email,
        "name": str(info.get("name") or ""),
        "picture": str(info.get("picture") or ""),
    }


def _read_account_profiles() -> list[dict[str, Any]]:
    listing = _run_wsl(f"ls -1 {shlex.quote(ACCOUNTS_DIR)} 2>/dev/null", timeout=15)
    if listing.returncode != 0:
        return []
    profiles: list[dict[str, Any]] = []
    for slug in [line.strip() for line in listing.stdout.splitlines() if line.strip()]:
        meta_text = _wsl_read_text(f"{ACCOUNTS_DIR}/{slug}/meta.json")
        if not meta_text:
            continue
        try:
            meta = json.loads(meta_text)
        except json.JSONDecodeError:
            continue
        if isinstance(meta, dict):
            meta.setdefault("slug", slug)
            profiles.append(meta)
    return profiles


def _write_account_profile(slug: str, token_text: str, meta: dict[str, Any]) -> None:
    base = f"{ACCOUNTS_DIR}/{slug}"
    _wsl_write_text(f"{base}/token.json", token_text)
    _wsl_write_text(f"{base}/meta.json", json.dumps(meta, indent=2))


def _set_active_account_state(meta: dict[str, Any]) -> None:
    _update_state(
        account_email=meta.get("email", ""),
        account_name=meta.get("name", ""),
        account_picture=meta.get("picture", ""),
        account_hint=meta.get("email", ""),
    )


def _live_token() -> dict[str, Any] | None:
    token_text = _wsl_read_text(LIVE_TOKEN_PATH)
    if not token_text:
        return None
    try:
        token = json.loads(token_text)
    except json.JSONDecodeError:
        return None
    return token if isinstance(token, dict) else None


def _capture_active_account() -> dict[str, Any] | None:
    """Archive the currently signed-in account so it can be restored later.

    Reuses the cached profile (no network) when the live token was already
    archived; otherwise fetches the identity once and writes the profile. Also
    mirrors the active account's email/name/avatar into the runtime state.
    """
    token_text = _wsl_read_text(LIVE_TOKEN_PATH)
    if not token_text:
        return None
    try:
        token = json.loads(token_text)
    except json.JSONDecodeError:
        return None
    key = _token_key(token)
    if key:
        for profile in _read_account_profiles():
            if profile.get("token_key") == key and profile.get("picture"):
                _set_active_account_state(profile)
                return profile
    identity = _fetch_account_identity(token)
    if not identity:
        return None
    slug = _account_slug(identity["email"])
    meta = {
        "slug": slug,
        "email": identity["email"],
        "name": identity["name"],
        "picture": identity["picture"],
        "token_key": key,
        "added_at": time.time(),
        "last_used_at": time.time(),
    }
    _write_account_profile(slug, token_text, meta)
    _set_active_account_state(meta)
    return meta


def list_colab_accounts() -> dict[str, Any]:
    # Make sure the account in use right now is archived and listed, even if it
    # predates this feature or was added before we started capturing.
    active = _capture_active_account()
    active_key = active.get("token_key") if active else None
    if active_key is None:
        live = _live_token()
        active_key = _token_key(live) if live else None
    profiles = sorted(
        _read_account_profiles(),
        key=lambda meta: meta.get("last_used_at") or meta.get("added_at") or 0,
        reverse=True,
    )
    accounts = [
        {
            "slug": profile.get("slug", ""),
            "email": profile.get("email", ""),
            "name": profile.get("name", ""),
            "picture": profile.get("picture", ""),
            "active": bool(active_key and profile.get("token_key") == active_key),
            "last_used_at": profile.get("last_used_at"),
        }
        for profile in profiles
    ]
    return {"accounts": accounts, "active_email": (active or {}).get("email", "")}


def switch_to_saved_colab_account(slug: str) -> dict[str, Any]:
    safe_slug = _account_slug(slug)
    base = f"{ACCOUNTS_DIR}/{safe_slug}"
    token_text = _wsl_read_text(f"{base}/token.json")
    if not token_text:
        state = {**_default_state(), **_environment_status()}
        state["state"] = "error"
        state["error"] = "That saved Google account is no longer available. Add it again."
        state["updated_at"] = time.time()
        _write_state(state)
        return get_colab_runtime_status()

    # Preserve whatever account is live now before we overwrite the token.
    _capture_active_account()
    stop_colab_runtime()

    # Restore the saved token and drop the session list so the launcher runs
    # `colab new` under this account. A valid refresh token means colab-cli
    # authenticates silently — no browser, no pasted code.
    if not _wsl_write_text(LIVE_TOKEN_PATH, token_text):
        state = {**_default_state(), **_environment_status()}
        state["state"] = "error"
        state["error"] = "Unable to restore the saved Google account token in WSL."
        state["updated_at"] = time.time()
        _write_state(state)
        return get_colab_runtime_status()
    _run_wsl(
        "rm -f "
        f"{COLAB_CONFIG_DIR}/sessions.json "
        f"{COLAB_CONFIG_DIR}/sessions.json.lock",
        timeout=20,
    )
    if not _release_oauth_callback_port():
        state = {**_default_state(), **_environment_status()}
        state["state"] = "error"
        state["error"] = "Unable to release the Google OAuth callback port 8200 in WSL."
        state["updated_at"] = time.time()
        _write_state(state)
        return get_colab_runtime_status()

    meta_text = _wsl_read_text(f"{base}/meta.json")
    if meta_text:
        try:
            meta = json.loads(meta_text)
        except json.JSONDecodeError:
            meta = {}
        if isinstance(meta, dict):
            meta["slug"] = safe_slug
            meta["last_used_at"] = time.time()
            _write_account_profile(safe_slug, token_text, meta)
            _set_active_account_state(meta)
    return start_colab_runtime()


def remove_colab_account(slug: str) -> dict[str, Any]:
    safe_slug = _account_slug(slug)
    _run_wsl(f"rm -rf {shlex.quote(ACCOUNTS_DIR + '/' + safe_slug)}", timeout=20)
    return list_colab_accounts()


def _release_oauth_callback_port() -> bool:
    # Killing wsl.exe does not necessarily kill the Linux child it launched.
    # Clean up the callback listener left by an interrupted `colab new/status`
    # and wait until Linux confirms the fixed OAuth port is free.
    _stop_oauth_loopback_bridge()
    command = (
        "pkill -9 -f '[c]olab (new|status)' >/dev/null 2>&1 || true; "
        "fuser -k -9 8200/tcp >/dev/null 2>&1 || true; "
        "sleep 1; "
        "if ss -ltn 2>/dev/null | grep -q ':8200 '; then exit 1; fi; "
        "exit 0"
    )
    return _run_wsl(command, timeout=15).returncode == 0


def _start_oauth_loopback_bridge() -> None:
    """Forward Windows IPv6 localhost callbacks to WSL's IPv4 listener.

    Google redirects to ``http://localhost:8200``. On affected Windows/WSL
    installations localhost resolves to ``::1``, while WSL localhost forwarding
    exposes the CLI callback only at ``127.0.0.1``. A short-lived byte proxy keeps
    the original Host header and requires no administrator portproxy rules.
    """
    global _oauth_bridge_thread, _oauth_bridge_stop
    if os.name != "nt":
        return
    _stop_oauth_loopback_bridge()
    stop_event = threading.Event()
    _oauth_bridge_stop = stop_event
    _oauth_bridge_thread = threading.Thread(
        target=_oauth_loopback_bridge_worker,
        args=(stop_event,),
        daemon=True,
        name="aether-colab-oauth-loopback",
    )
    _oauth_bridge_thread.start()


def _stop_oauth_loopback_bridge() -> None:
    global _oauth_bridge_thread, _oauth_bridge_stop
    if _oauth_bridge_stop is not None:
        _oauth_bridge_stop.set()
    thread = _oauth_bridge_thread
    if thread and thread is not threading.current_thread():
        thread.join(timeout=1.5)
    _oauth_bridge_thread = None
    _oauth_bridge_stop = None


def _oauth_loopback_bridge_worker(stop_event: threading.Event) -> None:
    try:
        with socket.socket(socket.AF_INET6, socket.SOCK_STREAM) as server:
            server.setsockopt(socket.IPPROTO_IPV6, socket.IPV6_V6ONLY, 1)
            server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            server.bind(("::1", 8200))
            server.listen(2)
            server.settimeout(0.5)
            deadline = time.time() + 600
            while not stop_event.is_set() and time.time() < deadline:
                try:
                    client, _address = server.accept()
                except socket.timeout:
                    continue
                with client:
                    client.settimeout(10)
                    target = _connect_oauth_ipv4_target()
                    if target is None:
                        continue
                    with target:
                        _relay_oauth_http(client, target)
                    return
    except OSError as exc:
        _append_log(f"[oauth] Unable to bridge localhost:8200: {exc}")


def _connect_oauth_ipv4_target() -> socket.socket | None:
    for _ in range(20):
        try:
            return socket.create_connection(("127.0.0.1", 8200), timeout=1)
        except OSError:
            time.sleep(0.1)
    return None


def _relay_oauth_http(client: socket.socket, target: socket.socket) -> None:
    request = bytearray()
    while b"\r\n\r\n" not in request and len(request) < 65536:
        chunk = client.recv(8192)
        if not chunk:
            return
        request.extend(chunk)
    target.sendall(request)
    while True:
        chunk = target.recv(8192)
        if not chunk:
            return
        client.sendall(chunk)


def _colab_shell_command(arguments: list[str]) -> str:
    quoted = " ".join(shlex.quote(str(value)) for value in arguments)
    return f"export PATH=\"$HOME/.local/bin:$PATH\"; {shlex.quote(COLAB_BIN)} {quoted}"


def _run_wsl(command: str, *, timeout: int) -> subprocess.CompletedProcess[str]:
    try:
        completed = subprocess.run(
            _wsl_args(command),
            capture_output=True,
            timeout=timeout,
            creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        return subprocess.CompletedProcess(_wsl_args(command), 1, "", str(exc))
    stdout = _decode_output(completed.stdout)
    stderr = _decode_output(completed.stderr)
    return subprocess.CompletedProcess(completed.args, completed.returncode, stdout + stderr, "")


def _wsl_args(command: str) -> list[str]:
    return ["wsl.exe", "-d", _effective_wsl_distro(), "-u", "root", "--", "bash", "-lc", command]


def _environment_status() -> dict[str, Any]:
    distros = _wsl_distributions()
    selected_distro = _effective_wsl_distro(distros)
    distro_available = selected_distro.casefold() in {item.casefold() for item in distros}
    cli_installed = False
    cli_version = ""
    if distro_available:
        result = _run_wsl(
            f"test -x {shlex.quote(COLAB_BIN)} && {shlex.quote(COLAB_BIN)} version",
            timeout=30,
        )
        cli_installed = result.returncode == 0
        if cli_installed:
            cli_version = result.stdout.strip().splitlines()[-1] if result.stdout.strip() else ""
    return {
        "wsl_available": bool(distros),
        "distro_available": distro_available,
        "cli_installed": cli_installed,
        "cli_version": cli_version,
        "wsl_distro": selected_distro,
        "session_name": SESSION_NAME,
        "setup_command": "powershell -ExecutionPolicy Bypass -File scripts\\install-aether-colab-cli.ps1",
        "log_path": str(_log_path()),
    }


def _effective_wsl_distro(distros: list[str] | None = None) -> str:
    available = distros if distros is not None else _wsl_distributions()
    by_name = {item.casefold(): item for item in available}
    if WSL_DISTRO:
        return by_name.get(WSL_DISTRO.casefold(), WSL_DISTRO)
    for preferred in PREFERRED_WSL_DISTROS:
        if preferred.casefold() in by_name:
            return by_name[preferred.casefold()]
    for distro in available:
        if distro.casefold().startswith("ubuntu"):
            return distro
    return PREFERRED_WSL_DISTROS[0]


def _wsl_distributions() -> list[str]:
    try:
        result = subprocess.run(
            ["wsl.exe", "--list", "--quiet"],
            capture_output=True,
            timeout=15,
            creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
        )
    except (OSError, subprocess.TimeoutExpired):
        return []
    text = _decode_output(result.stdout)
    return [line.strip().replace("\x00", "") for line in text.splitlines() if line.strip().replace("\x00", "")]


def _decode_output(value: bytes | str | None) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    if b"\x00" in value:
        return value.decode("utf-16-le", errors="replace")
    return value.decode("utf-8", errors="replace")


def _write_bootstrap_script() -> Path:
    assets_dir = Path(__file__).resolve().parent.parent / "assets"
    files: dict[str, str] = {}
    for name in ("omnivoice_service.py",):
        path = assets_dir / name
        if path.exists():
            files[name] = base64.b64encode(path.read_bytes()).decode("ascii")
    for name in ("Voice_Ref.WAV", "voice_scripts.txt", "Instruction.txt"):
        path = assets_dir / "defaults" / name
        if path.exists():
            files[name] = base64.b64encode(path.read_bytes()).decode("ascii")

    encoded_files = json.dumps(files)
    heartbeat_interval = _heartbeat_interval_seconds()
    # When an ngrok authtoken is configured, the bootstrap prefers ngrok over the
    # zero-config Cloudflare quick tunnel. ngrok keeps a persistent authenticated
    # session (and, with a free reserved domain, a URL that never rotates), which
    # is far more stable for long renders than trycloudflare.com. Without a token
    # we keep the cloudflared fallback so the tool still works out of the box.
    runtime_settings = get_runtime_settings()
    ngrok_authtoken = runtime_settings.ngrok_authtoken
    ngrok_domain = runtime_settings.ngrok_domain
    script = f"""
import base64
import importlib.util
import json
import os
import re
import subprocess
import sys
import time
from pathlib import Path

import requests
import torch

print({STATE_PREFIX!r} + json.dumps({{"state": "installing", "error": None}}), flush=True)
requirements = [
    ("omnivoice", "omnivoice"),
    ("fastapi", "fastapi"),
    ("uvicorn", "uvicorn"),
    ("requests", "requests"),
    ("soundfile", "soundfile"),
    ("multipart", "python-multipart"),
]
missing = [package for module, package in requirements if importlib.util.find_spec(module) is None]
if missing:
    subprocess.check_call([sys.executable, "-m", "pip", "install", "-q", *missing])

# Best-effort: hf_transfer is a Rust-based parallel downloader that markedly
# speeds the HuggingFace model download on a cold VM. It is optional — if it
# cannot be installed we fall back to the standard downloader rather than failing.
if importlib.util.find_spec("hf_transfer") is None:
    subprocess.run([sys.executable, "-m", "pip", "install", "-q", "hf_transfer"], check=False)
hf_transfer_ready = importlib.util.find_spec("hf_transfer") is not None

service_dir = Path("/content/aether-omnivoice")
service_dir.mkdir(parents=True, exist_ok=True)
for name, encoded in {encoded_files}.items():
    (service_dir / name).write_bytes(base64.b64decode(encoded))

print({STATE_PREFIX!r} + json.dumps({{"state": "loading", "error": None}}), flush=True)
env = os.environ.copy()
env.update({{
    "OMNIVOICE_DEVICE_MAP": "cuda:0",
    "OMNIVOICE_HOST": "127.0.0.1",
    "OMNIVOICE_PORT": "8008",
    "OMNIVOICE_OUTPUT_DIR": "/content/omnivoice_outputs",
    "OMNIVOICE_LOAD_ON_STARTUP": "1",
    "OMNIVOICE_LOAD_ASR": "0",
    "OMNIVOICE_DEFAULT_REF_AUDIO_PATH": str(service_dir / "Voice_Ref.WAV"),
    "OMNIVOICE_DEFAULT_REF_TEXT_PATH": str(service_dir / "voice_scripts.txt"),
    "OMNIVOICE_DEFAULT_INSTRUCTION_PATH": str(service_dir / "Instruction.txt"),
    "HF_HUB_ENABLE_HF_TRANSFER": "1" if hf_transfer_ready else "0",
}})

def _local_health():
    try:
        return requests.get("http://127.0.0.1:8008/health", timeout=3).json()
    except (requests.RequestException, ValueError):
        return {{}}

def _port_is_open():
    import socket
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.settimeout(0.5)
        return sock.connect_ex(("127.0.0.1", 8008)) == 0

# Re-running the Colab bootstrap must not start a second Uvicorn instance. A
# healthy service already has the model in GPU memory, so reuse it. If a stale
# or unrelated process owns the port, stop both the known service command and
# the actual port owner, then wait until the kernel releases the socket.
health = _local_health()
reuse_service = health.get("status") in {{"loading", "ready"}}
if reuse_service:
    print("[service] Reusing the OmniVoice server already running on port 8008.", flush=True)
else:
    subprocess.run("pkill -f omnivoice_service.py || true", shell=True, check=False)
    subprocess.run("fuser -k 8008/tcp >/dev/null 2>&1 || true", shell=True, check=False)
    for _ in range(30):
        if not _port_is_open():
            break
        time.sleep(0.5)
    if _port_is_open():
        raise RuntimeError(
            "Port 8008 is still occupied after stopping the old OmniVoice service. "
            "Restart the Colab runtime, then run OmniVoice once."
        )

subprocess.run("pkill -f cloudflared || true", shell=True, check=False)
if not reuse_service:
    service_log = open("/content/omnivoice_service.log", "w")
    subprocess.Popen(
        [sys.executable, str(service_dir / "omnivoice_service.py")],
        cwd=str(service_dir),
        env=env,
        stdout=service_log,
        stderr=subprocess.STDOUT,
        start_new_session=True,
    )

for _ in range(240):
    try:
        health = requests.get("http://127.0.0.1:8008/health", timeout=5).json()
        if health.get("status") == "ready":
            break
        if health.get("status") == "error":
            raise RuntimeError(health.get("model_error") or "OmniVoice model failed to load")
    except requests.RequestException:
        pass
    time.sleep(5)
else:
    raise RuntimeError("OmniVoice did not become ready. See /content/omnivoice_service.log")

_ngrok_authtoken = {ngrok_authtoken!r}
_ngrok_domain = {ngrok_domain!r}

def _verify_tunnel(url):
    # The public URL must actually reach the OmniVoice /health endpoint before we
    # trust it. The ngrok-skip-browser-warning header bypasses ngrok-free's HTML
    # interstitial (harmless for cloudflared, which ignores unknown headers).
    try:
        return requests.get(
            url + "/health",
            timeout=10,
            headers={{"ngrok-skip-browser-warning": "true"}},
        ).ok
    except requests.RequestException:
        return False

def _ensure_cloudflared():
    cloudflared = Path("/content/cloudflared")
    if not cloudflared.exists():
        response = requests.get(
            "https://github.com/cloudflare/cloudflared/releases/latest/download/cloudflared-linux-amd64",
            timeout=120,
        )
        response.raise_for_status()
        cloudflared.write_bytes(response.content)
        cloudflared.chmod(0o755)
    return cloudflared

def _start_cloudflared():
    # Start cloudflared and return (process, public_url). Uses http2 for stability.
    cloudflared = _ensure_cloudflared()
    subprocess.run("pkill -f cloudflared || true", shell=True, check=False)
    time.sleep(2)
    log_handle = open("/content/cloudflared.log", "w")
    proc = subprocess.Popen(
        [
            str(cloudflared), "tunnel",
            "--url", "http://127.0.0.1:8008",
            "--no-autoupdate",          # prevent self-update restarts
            "--protocol", "http2",      # http2 is more reliable than QUIC on Colab VMs
        ],
        stdout=log_handle,
        stderr=subprocess.STDOUT,
        start_new_session=True,
    )
    for _ in range(60):
        text = Path("/content/cloudflared.log").read_text(errors="ignore")
        match = re.search(r"https://[a-zA-Z0-9.-]+\\.trycloudflare\\.com", text)
        if match and _verify_tunnel(match.group(0)):
            return proc, match.group(0)
        time.sleep(1)
    return proc, ""

def _ensure_ngrok():
    ngrok = Path("/content/ngrok")
    if not ngrok.exists():
        import io
        import tarfile
        response = requests.get(
            "https://bin.equinox.io/c/bNyj1mQVY4c/ngrok-v3-stable-linux-amd64.tgz",
            timeout=120,
        )
        response.raise_for_status()
        with tarfile.open(fileobj=io.BytesIO(response.content), mode="r:gz") as tar:
            tar.extract("ngrok", "/content")
        ngrok.chmod(0o755)
    return ngrok

def _start_ngrok():
    # Start an authenticated ngrok tunnel and return (process, public_url). With a
    # reserved domain the URL is stable across restarts; without one ngrok still
    # keeps a single persistent URL for the whole session (unlike trycloudflare).
    ngrok = _ensure_ngrok()
    subprocess.run("pkill -f ngrok || true", shell=True, check=False)
    time.sleep(2)
    subprocess.run(
        [str(ngrok), "config", "add-authtoken", _ngrok_authtoken],
        check=False, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )
    args = [str(ngrok), "http", "8008", "--log", "/content/ngrok.log", "--log-format", "logfmt"]
    if _ngrok_domain:
        args += ["--domain", _ngrok_domain]
    log_handle = open("/content/ngrok_stdout.log", "w")
    proc = subprocess.Popen(args, stdout=log_handle, stderr=subprocess.STDOUT, start_new_session=True)
    # ngrok exposes its assigned public URL on a local inspection API.
    for _ in range(60):
        try:
            data = requests.get("http://127.0.0.1:4040/api/tunnels", timeout=5).json()
            for tunnel in data.get("tunnels", []):
                url = str(tunnel.get("public_url") or "")
                if url.startswith("https://") and _verify_tunnel(url):
                    return proc, url
        except (requests.RequestException, ValueError):
            pass
        time.sleep(1)
    return proc, ""

def _start_tunnel():
    # Prefer ngrok when an authtoken is present; fall back to cloudflared if ngrok
    # cannot produce a reachable URL so the session is never left without a tunnel.
    if _ngrok_authtoken:
        proc, url = _start_ngrok()
        if url:
            print("[tunnel] ngrok tunnel is live: {{}}".format(url), flush=True)
            return proc, url
        print("[tunnel] ngrok did not come up — falling back to cloudflared.", flush=True)
    return _start_cloudflared()

_tunnel_proc = [None]  # mutable container so watchdog can replace

_tunnel_proc[0], public_url = _start_tunnel()
if not public_url:
    raise RuntimeError(
        "No tunnel exposed a reachable OmniVoice health endpoint. "
        "See /content/ngrok.log or /content/cloudflared.log."
    )

# Mutable single-element list so the watchdog can track the current tunnel URL
# after restarts. A plain closure string variable cannot be reassigned from
# inside the nested function.
_current_url = [public_url]

def _tunnel_watchdog():
    # Monitor the active tunnel; restart if dead and emit a new READY marker with
    # the updated URL. Works for both ngrok and cloudflared via _start_tunnel().
    consecutive_fails = 0
    while True:
        time.sleep(60)
        proc = _tunnel_proc[0]
        needs_restart = proc is None or proc.poll() is not None
        if not needs_restart:
            try:
                r = requests.get(
                    _current_url[0] + "/health",
                    timeout=12,
                    headers={{"ngrok-skip-browser-warning": "true"}},
                )
                if r.status_code == 200:
                    consecutive_fails = 0
                    continue
                if r.status_code == 530:
                    # 530 = Cloudflare edge reports the origin is unregistered:
                    # the tunnel is definitively dead (not merely busy), so a
                    # render polling this URL is already getting hard failures.
                    # Restart immediately instead of waiting out the tolerance.
                    print("[watchdog] HTTP 530 origin unregistered — tunnel is dead.", flush=True)
                    needs_restart = True
                elif r.status_code >= 500:
                    consecutive_fails += 1
                else:
                    consecutive_fails = 0
            except Exception:
                consecutive_fails += 1
            # For ambiguous failures (timeouts, 5xx from the service) wait out 4
            # consecutive failures (~4 min) so a busy long synthesis job is not
            # mistaken for a dead tunnel.
            needs_restart = needs_restart or consecutive_fails >= 4
        if needs_restart:
            print("[watchdog] tunnel dead — restarting...", flush=True)
            new_proc, new_url = _start_tunnel()
            _tunnel_proc[0] = new_proc
            consecutive_fails = 0
            if new_url and new_url != _current_url[0]:
                print(f"[watchdog] New tunnel URL: {{new_url}}", flush=True)
                _current_url[0] = new_url  # update so next iteration checks the new URL
                # Emit a fresh READY marker so the backend picks up the new URL
                props = torch.cuda.get_device_properties(0)
                print({READY_PREFIX!r} + json.dumps({{
                    "api_url": new_url,
                    "device": "cuda:0",
                    "gpu_name": torch.cuda.get_device_name(0),
                    "gpu_memory_gb": round(props.total_memory / 1024**3, 1),
                    "session_started_at": time.time(),
                    "error": None,
                }}), flush=True)
            elif not new_url:
                print("[watchdog] Tunnel restart failed — will retry next cycle.", flush=True)

import threading as _threading
# The watchdog runs in the background; the main thread blocks forever in the
# heartbeat loop below, so a daemon thread is fine (the process never exits on
# its own).
_threading.Thread(target=_tunnel_watchdog, daemon=True).start()

props = torch.cuda.get_device_properties(0)
print({READY_PREFIX!r} + json.dumps({{
    "api_url": public_url,
    "device": "cuda:0",
    "gpu_name": torch.cuda.get_device_name(0),
    "gpu_memory_gb": round(props.total_memory / 1024**3, 1),
    "session_started_at": time.time(),
    "error": None,
}}), flush=True)

# Keep this cell running for the entire session by emitting a heartbeat line on
# a fixed interval. This is the crux of why the web notebook survives for hours
# while a fire-and-forget script dies: Colab reclaims a runtime once it goes
# idle, and idle is measured by *kernel activity* (cell execution / streamed
# output) — NOT by internal HTTP traffic. A long-running cell that periodically
# prints (like a training loop) continuously resets that idle timer. Pinging
# 127.0.0.1 from inside the VM does nothing for this; flushing stdout over the
# exec channel does. Blocking here also keeps `colab exec` connected so the
# watchdog's fresh-URL READY markers keep reaching the backend in real time.
_heartbeat_interval = {heartbeat_interval}
while True:
    time.sleep(_heartbeat_interval)
    proc = _tunnel_proc[0]
    tunnel_up = proc is not None and proc.poll() is None
    try:
        local_ok = requests.get("http://127.0.0.1:8008/health", timeout=10).ok
    except Exception:
        local_ok = False
    print(
        "[heartbeat] ts={{}} tunnel={{}} service={{}} url={{}}".format(
            int(time.time()),
            "up" if tunnel_up else "down",
            "ok" if local_ok else "down",
            _current_url[0],
        ),
        flush=True,
    )
"""
    # IMPORTANT: write the generated bootstrap OUTSIDE the source tree. In dev the
    # API runs under `uvicorn --reload`, whose watcher reloads on any *.py change
    # below the repo. Emitting this .py into storage/runtime/ (inside the repo)
    # triggered a reload that killed the launcher thread and its live `colab exec`
    # mid-bootstrap — leaving the session stuck "starting" forever. The system temp
    # dir is on C:, so WSL can still read it via /mnt/c for `colab exec -f`.
    runtime_dir = Path(tempfile.gettempdir()) / "aether-omnivoice"
    runtime_dir.mkdir(parents=True, exist_ok=True)
    path = runtime_dir / "aether_omnivoice_colab_bootstrap.py"
    path.write_text(script.strip() + "\n", encoding="utf-8")
    return path.resolve()


def _windows_to_wsl_path(path: Path) -> str:
    value = str(path.resolve())
    match = re.match(r"^([A-Za-z]):\\(.*)$", value)
    if not match:
        return value.replace("\\", "/")
    drive, tail = match.groups()
    return f"/mnt/{drive.lower()}/{tail.replace(chr(92), '/')}"


def _authorization_url(line: str) -> str:
    match = re.search(r"https://accounts\.google\.com/[^\s]+", line)
    if not match:
        return ""
    candidate = match.group(0).rstrip(":,.;")
    parsed = urlsplit(candidate)
    if parsed.scheme != "https" or parsed.netloc != "accounts.google.com":
        return ""
    access_type = parse_qs(parsed.query).get("access_type", [])
    if access_type and access_type != ["offline"]:
        return ""
    return urlunsplit(parsed)


def _is_console_login_url(url: str) -> bool:
    # The copy-paste login flow redirects to applicationdefaultauthcode.html (or
    # tags token_usage=remote) and then waits for the user to paste a code, rather
    # than completing automatically via the localhost:8200 redirect.
    query = parse_qs(urlsplit(url).query)
    redirect_uri = " ".join(query.get("redirect_uri", []))
    return "applicationdefaultauthcode" in redirect_uri or query.get("token_usage") == ["remote"]


def _remote_health(api_url: str) -> dict[str, Any]:
    try:
        response = httpx.get(
            f"{api_url}/health",
            timeout=4,
            headers={"ngrok-skip-browser-warning": "true"},
        )
        if response.status_code == 530 or _is_cloudflare_tunnel_error(response):
            return {
                "reachable": False,
                "stale_tunnel": True,
                "error": (
                    "Cloudflare tunnel expired or disconnected (HTTP 530). "
                    "Use Retry to create a fresh tunnel."
                ),
            }
        response.raise_for_status()
        payload = response.json()
        return {
            "reachable": True,
            "status": payload.get("status"),
            "device": payload.get("device"),
            "model": payload.get("model"),
            "error": payload.get("model_error"),
        }
    except (httpx.HTTPError, ValueError) as exc:
        return {"reachable": False, "error": str(exc)}


def _is_cloudflare_tunnel_error(response: httpx.Response) -> bool:
    content_type = response.headers.get("content-type", "").lower()
    if "text/html" not in content_type:
        return False
    lowered = response.text.lower()
    return "cloudflare tunnel error" in lowered or "trycloudflare.com | cloudflare" in lowered


def _quota_state_from_error(message: str) -> str:
    return "exhausted" if re.search(r"quota|entitlement|rejected accelerator|resource", message, re.I) else "unknown"


def _quota_message_from_error(message: str) -> str:
    if _quota_state_from_error(message) == "exhausted":
        return "This Google account could not obtain the requested GPU. Switch account or wait for quota recovery."
    return "The Colab CLI could not start OmniVoice. Review the runtime log for details."


_KERNEL_CLIENT_ERROR = re.compile(
    r"module ['\"]?jupyter_kernel_client['\"]? has no attribute ['\"]?KernelClient",
    re.IGNORECASE,
)


def _broken_kernel_client(output: str) -> bool:
    return bool(_KERNEL_CLIENT_ERROR.search(output))


def _failure_message(output: str) -> str:
    lines = [line.strip() for line in output.splitlines() if line.strip()]
    last = lines[-1] if lines else "Colab CLI command failed."
    if _broken_kernel_client(output):
        return (
            "google-colab-cli was installed against jupyter-kernel-client 1.x, "
            "which removed the KernelClient class it calls. Reinstalling the "
            f"Colab CLI pins it to {KERNEL_CLIENT_PIN}."
        )
    if "invalid_grant" in last.lower() or "invalidgranterror" in last.lower():
        return (
            "The Google authorization code was invalid or expired. "
            "Launch again and paste a fresh code right after signing in."
        )
    return last


def _state_path() -> Path:
    path = ensure_storage() / "runtime" / "omnivoice-colab.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    return path


def _log_path() -> Path:
    path = ensure_storage() / "runtime" / "omnivoice-colab-cli.log"
    path.parent.mkdir(parents=True, exist_ok=True)
    return path


def _read_state() -> dict[str, Any]:
    path = _state_path()
    if not path.exists():
        return _default_state()
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return _default_state()
    state = {**_default_state(), **(payload if isinstance(payload, dict) else {})}
    authorization_url = str(state.get("authorization_url") or "")
    state["authorization_url"] = _authorization_url(authorization_url)
    return state


def _write_state(payload: dict[str, Any]) -> None:
    _state_path().write_text(json.dumps(payload, indent=2), encoding="utf-8")


def _update_state(**updates: Any) -> None:
    state = _read_state()
    state.update({key: value for key, value in updates.items() if value is not None or key == "error"})
    state["updated_at"] = time.time()
    _write_state(state)


def _clear_log() -> None:
    _log_path().write_text("", encoding="utf-8")


_LOG_MAX_BYTES = 1_000_000


def _append_log(text: str) -> None:
    if not text:
        return
    path = _log_path()
    # The bootstrap cell streams a heartbeat for the whole session (up to ~12h),
    # so cap the log and keep only the most recent tail instead of growing it
    # without bound.
    try:
        if path.exists() and path.stat().st_size > _LOG_MAX_BYTES:
            tail = path.read_bytes()[-(_LOG_MAX_BYTES // 2):]
            path.write_bytes(tail)
    except OSError:
        pass
    with path.open("a", encoding="utf-8") as file:
        file.write(text if text.endswith("\n") else text + "\n")


def _read_log() -> str:
    try:
        return _log_path().read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""


def _default_state() -> dict[str, Any]:
    return {
        "state": "stopped",
        "reachable": False,
        "api_url": "",
        "device": "",
        "gpu_name": "",
        "gpu_memory_gb": None,
        "quota_state": "unknown",
        "quota_message": "Google does not expose a numeric free-tier quota API.",
        "account_hint": "",
        "account_email": "",
        "account_name": "",
        "account_picture": "",
        "authorization_url": "",
        "needs_auth_code": False,
        "session_started_at": None,
        "session_age_seconds": None,
        "updated_at": None,
        "error": None,
        "model": None,
        "wsl_available": False,
        "distro_available": False,
        "cli_installed": False,
        "cli_version": "",
        # Keep state reads pure and fast. Environment refreshes replace this
        # placeholder with the detected distro; calling `wsl --list` here made
        # every poll and state update block for up to 15 seconds.
        "wsl_distro": WSL_DISTRO or PREFERRED_WSL_DISTROS[0],
        "session_name": SESSION_NAME,
        "setup_command": "powershell -ExecutionPolicy Bypass -File scripts\\install-aether-colab-cli.ps1",
        "log_path": str(_log_path()),
    }
