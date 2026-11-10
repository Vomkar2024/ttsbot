"""Runtime DAVE (Discord Audio & Video E2EE) compatibility shim and diagnostics.

`discord.py==2.7.1` targets `davey 0.1.x` (`<0.2.0`) and does not call
`DaveSession.prepare_transition()` or `DaveSession.execute_transition()`, which
became mandatory in `davey>=0.2.0` to activate MLS keys (`DaveSession.ready`).
Without those calls, `VoiceConnectionState.can_encrypt` remains `False` and
`VoiceClient._get_voice_packet()` silently transmits unencrypted Opus frames that
Discord clients discard on DAVE-enforced voice channels.

This module provides:
1. Dependency version inspection and live `VoiceConnectionState` diagnostics.
2. An idempotent runtime compatibility bridge (`ensure_dave_compat`) that adapts
   `discord.py`'s `DiscordVoiceWebSocket` and `VoiceConnectionState` when paired
   with `davey>=0.2.0`, while remaining a no-op on `davey 0.1.x` or newer
   `discord.py` releases that natively call `prepare_transition`/`execute_transition`.
"""

import inspect
import logging
import struct
import sys
from importlib import metadata as importlib_metadata
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

_PATCH_MARKER = "_dave_02_compat_patched"


def _parse_version_tuple(version_str: Optional[str]) -> Tuple[int, ...]:
    if not version_str:
        return ()
    parts = []
    for token in str(version_str).split("."):
        digits = ""
        for ch in token:
            if ch.isdigit():
                digits += ch
            else:
                break
        if digits:
            parts.append(int(digits))
        else:
            break
    return tuple(parts)


def _safe_dist_version(dist_name: str, module_obj: Optional[Any] = None) -> Optional[str]:
    if module_obj is not None:
        ver = getattr(module_obj, "__version__", None)
        if ver:
            return str(ver)
    try:
        return importlib_metadata.version(dist_name)
    except Exception:
        return None


def _get_davey_module() -> Optional[Any]:
    try:
        import davey  # type: ignore

        return davey
    except Exception:
        return None


def _get_nacl_module() -> Optional[Any]:
    try:
        import nacl  # type: ignore

        return nacl
    except Exception:
        return None


def _has_davey_02_api(davey_mod: Optional[Any]) -> bool:
    if davey_mod is None:
        return False
    session_cls = getattr(davey_mod, "DaveSession", None)
    if session_cls is None:
        return False
    return callable(getattr(session_cls, "prepare_transition", None)) and callable(
        getattr(session_cls, "execute_transition", None)
    )


def _discord_handles_dave_transitions_natively(
    voice_ws_cls: Any,
    voice_state_cls: Any,
) -> bool:
    try:
        ws_src = inspect.getsource(voice_ws_cls.received_binary_message)
        state_src = inspect.getsource(voice_state_cls._execute_transition)
        return "prepare_transition" in ws_src and "execute_transition" in state_src
    except (TypeError, OSError):
        return False


def ensure_opus_loaded() -> bool:
    """Ensures libopus native library is loaded (including Linux libopus.so.0 fallback)."""
    try:
        import discord.opus

        if discord.opus.is_loaded():
            return True

        try:
            if discord.opus._load_default():
                return True
        except Exception:
            pass

        if sys.platform != "win32":
            for lib_name in (
                "libopus.so.0",
                "libopus.so",
                "opus",
                "/usr/lib/x86_64-linux-gnu/libopus.so.0",
                "/usr/lib/aarch64-linux-gnu/libopus.so.0",
                "/usr/local/lib/libopus.so.0",
            ):
                try:
                    discord.opus.load_opus(lib_name)
                    if discord.opus.is_loaded():
                        logger.info("[Opus] Loaded native libopus via '%s'.", lib_name)
                        return True
                except Exception:
                    continue

        return bool(discord.opus.is_loaded())
    except Exception as err:
        logger.debug("[Opus] Failed checking/loading libopus: %s", err)
        return False


def get_voice_dependency_versions() -> Dict[str, Any]:
    """Returns installed versions and capability flags for Discord voice dependencies."""
    import discord
    import discord.gateway
    import discord.voice_client
    import discord.voice_state

    davey_mod = _get_davey_module()
    nacl_mod = _get_nacl_module()

    opus_loaded: Optional[bool] = None
    try:
        import discord.opus

        opus_loaded = bool(discord.opus.is_loaded())
    except Exception:
        opus_loaded = None

    return {
        "discord_py": _safe_dist_version("discord.py", discord),
        "davey": _safe_dist_version("davey", davey_mod),
        "pynacl": _safe_dist_version("PyNaCl", nacl_mod),
        "has_dave": bool(getattr(discord.voice_client, "has_dave", False)),
        "has_nacl": bool(getattr(discord.voice_client, "has_nacl", False)),
        "opus_loaded": opus_loaded,
        "davey_02_api": _has_davey_02_api(davey_mod),
        "compat_patched": bool(
            getattr(discord.gateway.DiscordVoiceWebSocket, _PATCH_MARKER, False)
            and getattr(discord.voice_state.VoiceConnectionState, _PATCH_MARKER, False)
        ),
    }


def ensure_dave_compat(
    *,
    voice_ws_cls: Optional[Any] = None,
    voice_state_cls: Optional[Any] = None,
    davey_mod: Optional[Any] = None,
) -> Dict[str, Any]:
    """Ensures `discord.py` properly drives `davey >= 0.2.0` transition lifecycle.

    Returns a summary dict with `applied`, `already_patched`, `davey_02_detected`,
    and `native_support` booleans.
    """
    if davey_mod is None:
        davey_mod = _get_davey_module()

    if voice_ws_cls is None:
        import discord.gateway

        voice_ws_cls = discord.gateway.DiscordVoiceWebSocket

    if voice_state_cls is None:
        import discord.voice_state

        voice_state_cls = discord.voice_state.VoiceConnectionState

    davey_02_detected = _has_davey_02_api(davey_mod)
    already_patched = bool(
        getattr(voice_ws_cls, _PATCH_MARKER, False)
        and getattr(voice_state_cls, _PATCH_MARKER, False)
    )

    if already_patched:
        return {
            "applied": False,
            "already_patched": True,
            "davey_02_detected": davey_02_detected,
            "native_support": False,
        }

    if not davey_02_detected:
        return {
            "applied": False,
            "already_patched": False,
            "davey_02_detected": False,
            "native_support": False,
        }

    if _discord_handles_dave_transitions_natively(voice_ws_cls, voice_state_cls):
        return {
            "applied": False,
            "already_patched": False,
            "davey_02_detected": True,
            "native_support": True,
        }

    orig_execute_transition = voice_state_cls._execute_transition
    orig_received_message = voice_ws_cls.received_message
    orig_received_binary_message = voice_ws_cls.received_binary_message

    async def _compat_execute_transition(self: Any, transition_id: int) -> None:
        had_pending = transition_id in getattr(self, "dave_pending_transitions", {})
        await orig_execute_transition(self, transition_id)
        if had_pending and getattr(self, "dave_session", None) is not None:
            execute_fn = getattr(self.dave_session, "execute_transition", None)
            if callable(execute_fn):
                executed = execute_fn(transition_id)
                logger.debug(
                    "[DAVE Compat] Executed transition id=%s (applied=%s, ready=%s, can_encrypt=%s)",
                    transition_id,
                    executed,
                    getattr(self.dave_session, "ready", None),
                    getattr(self, "can_encrypt", None),
                )

    async def _compat_received_message(self: Any, msg: Dict[str, Any]) -> None:
        op = msg.get("op") if isinstance(msg, dict) else None
        data = msg.get("d") if isinstance(msg, dict) else None
        prepare_op = getattr(self, "DAVE_PREPARE_TRANSITION", None)
        state = getattr(self, "_connection", None)
        if (
            op is not None
            and prepare_op is not None
            and op == prepare_op
            and isinstance(data, dict)
            and state is not None
            and getattr(state, "dave_session", None) is not None
        ):
            prepare_fn = getattr(state.dave_session, "prepare_transition", None)
            if callable(prepare_fn) and "transition_id" in data and "protocol_version" in data:
                prepare_fn(data["transition_id"], data["protocol_version"])
                logger.debug(
                    "[DAVE Compat] Prepared op-21 transition id=%s version=%s",
                    data["transition_id"],
                    data["protocol_version"],
                )
        await orig_received_message(self, msg)

    async def _compat_received_binary_message(self: Any, msg: bytes) -> None:
        if len(msg) < 3:
            await orig_received_binary_message(self, msg)
            return

        op = msg[2]
        announce_op = getattr(self, "MLS_ANNOUNCE_COMMIT_TRANSITION", 29)
        welcome_op = getattr(self, "MLS_WELCOME", 30)

        if op not in (announce_op, welcome_op):
            await orig_received_binary_message(self, msg)
            return

        self.seq_ack = struct.unpack_from(">H", msg, 0)[0]
        state = getattr(self, "_connection", None)
        if state is None or getattr(state, "dave_session", None) is None:
            return

        transition_id = struct.unpack_from(">H", msg, 3)[0]
        payload = msg[5:]
        try:
            if op == announce_op:
                should_prepare = state.dave_session.process_commit(payload)
                if should_prepare is False:
                    logger.debug(
                        "[DAVE Compat] Ignored MLS commit for transition id=%d (no transition to prepare).",
                        transition_id,
                    )
                    return
                state.dave_session.prepare_transition(transition_id, state.dave_protocol_version)
                if transition_id == 0:
                    state.dave_session.execute_transition(0)
                else:
                    state.dave_pending_transitions[transition_id] = state.dave_protocol_version
                    await self.send_transition_ready(transition_id)
                logger.debug(
                    "[DAVE Compat] Processed MLS commit transition id=%d (ready=%s, can_encrypt=%s).",
                    transition_id,
                    getattr(state.dave_session, "ready", None),
                    getattr(state, "can_encrypt", None),
                )
            else:
                state.dave_session.process_welcome(payload)
                state.dave_session.prepare_transition(transition_id, state.dave_protocol_version)
                if transition_id == 0:
                    state.dave_session.execute_transition(0)
                else:
                    state.dave_pending_transitions[transition_id] = state.dave_protocol_version
                    await self.send_transition_ready(transition_id)
                logger.debug(
                    "[DAVE Compat] Processed MLS welcome transition id=%d (ready=%s, can_encrypt=%s).",
                    transition_id,
                    getattr(state.dave_session, "ready", None),
                    getattr(state, "can_encrypt", None),
                )
        except Exception as err:
            logger.debug(
                "[DAVE Compat] Invalid MLS %s for transition id=%d (%s); requesting recovery.",
                "commit" if op == announce_op else "welcome",
                transition_id,
                err,
            )
            await state._recover_from_invalid_commit(transition_id)

    voice_state_cls._execute_transition = _compat_execute_transition
    voice_ws_cls.received_message = _compat_received_message
    voice_ws_cls.received_binary_message = _compat_received_binary_message
    setattr(voice_state_cls, _PATCH_MARKER, True)
    setattr(voice_ws_cls, _PATCH_MARKER, True)

    davey_ver = _safe_dist_version("davey", davey_mod) or "unknown"
    logger.warning(
        "[DAVE Compat] Detected davey %s (>=0.2.0) with discord.py lacking native prepare_transition/"
        "execute_transition hooks. Activated runtime DAVE transition compatibility bridge "
        "(recommended pin: davey>=0.1.7,<0.2.0).",
        davey_ver,
    )

    return {
        "applied": True,
        "already_patched": False,
        "davey_02_detected": True,
        "native_support": False,
    }


def inspect_voice_connection_state(voice_client: Optional[Any]) -> Optional[Dict[str, Any]]:
    """Safely extracts DAVE/transport diagnostic fields from a `discord.VoiceClient`.

    Returns `None` when the object has no concrete `_connection` state (e.g. unit test mocks).
    """
    if voice_client is None:
        return None

    conn = getattr(voice_client, "_connection", None)
    if conn is None:
        return None

    raw_proto = getattr(conn, "dave_protocol_version", None)
    if not isinstance(raw_proto, int) or isinstance(raw_proto, bool):
        return None

    dave_session = getattr(conn, "dave_session", None)
    raw_can_encrypt = getattr(conn, "can_encrypt", None)
    can_encrypt = raw_can_encrypt if isinstance(raw_can_encrypt, bool) else None

    raw_ready = getattr(dave_session, "ready", None) if dave_session is not None else None
    dave_ready = raw_ready if isinstance(raw_ready, bool) else None

    raw_session_can_encrypt = (
        getattr(dave_session, "can_encrypt", None) if dave_session is not None else None
    )
    session_can_encrypt = (
        raw_session_can_encrypt if isinstance(raw_session_can_encrypt, bool) else None
    )

    raw_mode = getattr(conn, "mode", None)
    mode = raw_mode if isinstance(raw_mode, str) else None

    status_obj = getattr(dave_session, "status", None) if dave_session is not None else None
    status_name = getattr(status_obj, "name", None)
    if status_name is None and status_obj is not None and not callable(status_obj):
        status_name = str(status_obj)

    return {
        "mode": mode,
        "dave_protocol_version": raw_proto,
        "dave_session_present": dave_session is not None,
        "dave_ready": dave_ready,
        "dave_session_can_encrypt": session_can_encrypt,
        "can_encrypt": can_encrypt,
        "dave_status": status_name,
    }


def check_dependency_warnings(versions: Optional[Dict[str, Any]] = None) -> List[str]:
    """Returns human-readable compatibility warnings for installed voice dependencies."""
    info = versions or get_voice_dependency_versions()
    warnings_list = []

    dpy_ver = info.get("discord_py")
    dpy_tuple = _parse_version_tuple(dpy_ver)
    if dpy_tuple and dpy_tuple < (2, 7, 0):
        warnings_list.append(
            f"discord.py {dpy_ver} is older than 2.7.0 and lacks Discord DAVE voice E2EE support; "
            "upgrade to discord.py[voice]>=2.7.0,<2.8."
        )

    nacl_ver = info.get("pynacl")
    nacl_tuple = _parse_version_tuple(nacl_ver)
    if dpy_tuple and dpy_tuple < (2, 8, 0) and nacl_tuple and nacl_tuple >= (1, 6, 0):
        warnings_list.append(
            f"PyNaCl {nacl_ver} exceeds discord.py {dpy_ver}'s declared requirement (PyNaCl<1.6,>=1.5.0). "
            "Pin PyNaCl>=1.5.0,<1.6 to avoid transport encryption regressions."
        )

    return warnings_list


__all__ = [
    "ensure_dave_compat",
    "ensure_opus_loaded",
    "get_voice_dependency_versions",
    "inspect_voice_connection_state",
    "check_dependency_warnings",
]
