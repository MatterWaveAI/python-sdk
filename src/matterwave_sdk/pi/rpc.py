"""Synchronous and asynchronous drivers for `pi --mode rpc` over JSONL stdio."""

from __future__ import annotations

import asyncio
import contextlib
import json
import os
import subprocess
from collections.abc import AsyncIterator, Iterator, Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from types import TracebackType
from typing import Any

from ..agent.confine import DenyList, PathRefused, confine
from ..errors import MatterWaveError
from .config import build_pi_env

_WRITE_TOOLS = frozenset(
    {
        "write",
        "edit",
        "write_file",
        "edit_file",
        "create_file",
        "delete_file",
        "patch",
    }
)
_PATH_KEYS = ("path", "file_path", "filepath", "target_path", "destination")


@dataclass(frozen=True)
class PiEvent:
    """One parsed JSONL event emitted by `pi --mode rpc`."""

    type: str
    delta: str | None = None
    tool_name: str | None = None
    tool_call_id: str | None = None
    args: Any = None
    result: Any = None
    is_error: bool = False
    raw: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> PiEvent:
        raw = dict(data)
        event_type = str(raw.get("type", ""))

        delta: str | None = None
        top_delta = raw.get("delta")
        if isinstance(top_delta, str):
            delta = top_delta
        else:
            nested = raw.get("assistantMessageEvent")
            if isinstance(nested, Mapping):
                nested_delta = nested.get("delta")
                if isinstance(nested_delta, str):
                    delta = nested_delta

        tool_name: str | None = None
        for key in ("tool_name", "toolName"):
            val = raw.get(key)
            if isinstance(val, str) and val:
                tool_name = val
                break
        if tool_name is None:
            nested = raw.get("assistantMessageEvent")
            if isinstance(nested, Mapping):
                nested_name = nested.get("toolName") or nested.get("tool_name")
                if isinstance(nested_name, str) and nested_name:
                    tool_name = nested_name

        tool_call_id: str | None = None
        for key in ("tool_call_id", "toolCallId"):
            val = raw.get(key)
            if isinstance(val, str) and val:
                tool_call_id = val
                break
        if tool_call_id is None:
            nested = raw.get("assistantMessageEvent")
            if isinstance(nested, Mapping) and nested.get("type") == "toolcall_start":
                nested_id = nested.get("id")
                if isinstance(nested_id, str) and nested_id:
                    tool_call_id = nested_id

        args = raw.get("args")
        result = raw.get("result") if "result" in raw else raw.get("partialResult")
        is_error = bool(
            raw.get("is_error")
            or raw.get("isError")
            or event_type == "error"
            or (event_type == "response" and raw.get("success") is False)
        )

        return cls(
            type=event_type,
            delta=delta,
            tool_name=tool_name,
            tool_call_id=tool_call_id,
            args=args,
            result=result,
            is_error=is_error,
            raw=raw,
        )


def _check_confinement(
    event: PiEvent,
    workspace_root: Path | None,
    deny_list: DenyList | None,
    *,
    enforce_confinement: bool,
    approved: bool,
) -> None:
    if not enforce_confinement:
        return
    if workspace_root is None and deny_list is None:
        return
    if not isinstance(event.args, Mapping):
        return

    tool = (event.tool_name or "").lower()
    is_write = tool in _WRITE_TOOLS

    for key in _PATH_KEYS:
        path_val = event.args.get(key)
        if not isinstance(path_val, str) or not path_val:
            continue
        if workspace_root is not None:
            confine(
                workspace_root,
                path_val,
                deny=deny_list,
                write=is_write,
                approved=approved,
            )
        elif deny_list is not None and deny_list.matches(path_val):
            raise PathRefused("denied", path_val)


def _build_argv(
    command: Sequence[str] | None,
    cli_path: str,
    provider: str | None,
    model: str | None,
    extra_args: Sequence[str] | None,
) -> list[str]:
    if command is not None:
        return [str(part) for part in command]
    argv = [cli_path, "--mode", "rpc"]
    if provider:
        argv.extend(["--provider", provider])
    if model:
        argv.extend(["--model", model])
    if extra_args:
        argv.extend(str(a) for a in extra_args)
    return argv


def _build_process_env(
    client: Any,
    env: Mapping[str, str] | None,
    preset_id: str | None,
    temporary: bool,
) -> dict[str, str]:
    merged = dict(os.environ)
    if client is not None:
        merged.update(
            build_pi_env(
                client,
                preset_id=preset_id,
                temporary=temporary,
            )
        )
    if env is not None:
        for k, v in env.items():
            merged[str(k)] = str(v)
    return merged


class PiRpcSession:
    """Synchronous driver for `pi --mode rpc` over JSONL `stdin`/`stdout`."""

    def __init__(
        self,
        *,
        command: Sequence[str] | None = None,
        cli_path: str = "pi",
        cwd: str | os.PathLike[str] | None = None,
        workspace_root: str | os.PathLike[str] | None = None,
        deny_list: DenyList | None = None,
        enforce_confinement: bool = True,
        approved: bool = False,
        env: Mapping[str, str] | None = None,
        client: Any = None,
        preset_id: str | None = None,
        temporary: bool = False,
        provider: str | None = None,
        model: str | None = None,
        extra_args: Sequence[str] | None = None,
    ) -> None:
        self._argv = _build_argv(command, cli_path, provider, model, extra_args)
        self._cwd = Path(cwd) if cwd is not None else None
        root_source = workspace_root if workspace_root is not None else cwd
        self.workspace_root: Path | None = Path(root_source) if root_source is not None else None
        self.deny_list: DenyList | None = deny_list
        self.enforce_confinement = enforce_confinement
        self.approved = approved
        self._env = _build_process_env(client, env, preset_id, temporary)
        self._proc: subprocess.Popen[str] | None = None
        self._req_counter = 0
        self._streaming = False

    def start(self) -> PiRpcSession:
        """Spawn the `pi --mode rpc` subprocess if not already running."""
        if self._proc is not None and self._proc.poll() is None:
            return self
        self._proc = subprocess.Popen(  # noqa: S603
            self._argv,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            bufsize=1,
            cwd=str(self._cwd) if self._cwd is not None else None,
            env=self._env,
        )
        return self

    def __enter__(self) -> PiRpcSession:
        return self.start()

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        self.close()

    def _next_id(self) -> str:
        self._req_counter += 1
        return f"req_{self._req_counter}"

    def _write_json(self, payload: Mapping[str, Any]) -> None:
        proc = self.start()._proc
        if proc is None or proc.stdin is None:
            raise MatterWaveError("Pi RPC process stdin is not available.")
        line = json.dumps(dict(payload), separators=(",", ":")) + "\n"
        try:
            proc.stdin.write(line)
            proc.stdin.flush()
        except BrokenPipeError as exc:
            raise MatterWaveError("Pi RPC process closed stdin unexpectedly.") from exc

    def _read_json_line(self) -> dict[str, Any] | None:
        proc = self._proc
        if proc is None or proc.stdout is None:
            raise MatterWaveError("Pi RPC process stdout is not available.")
        while True:
            raw_line = proc.stdout.readline()
            if not raw_line:
                return None
            stripped = raw_line.strip()
            if not stripped:
                continue
            try:
                parsed = json.loads(stripped)
            except json.JSONDecodeError:
                continue
            if isinstance(parsed, dict):
                return parsed

    def send_command(self, cmd: Mapping[str, Any]) -> dict[str, Any]:
        """Send one RPC command object and return the parsed JSON response."""
        payload = dict(cmd)
        req_id = str(payload.setdefault("id", self._next_id()))
        cmd_type = str(payload.get("type", ""))
        self._write_json(payload)

        while True:
            msg = self._read_json_line()
            if msg is None:
                raise MatterWaveError(
                    f"Pi RPC process exited before responding to command {cmd_type!r}."
                )
            msg_type = msg.get("type")
            if msg_type == "response":
                msg_id = msg.get("id")
                msg_cmd = msg.get("command")
                if msg_id in (None, req_id) or msg_cmd == cmd_type:
                    if msg.get("success") is False:
                        raise MatterWaveError(
                            str(msg.get("error") or f"Pi RPC command {cmd_type!r} failed")
                        )
                    return msg
            elif msg_type == "error":
                raise MatterWaveError(str(msg.get("error") or msg.get("message") or "Pi RPC error"))

    def prompt(self, message: str) -> Iterator[PiEvent]:
        """Send a prompt command and yield `PiEvent` objects until completion."""
        payload = {"id": self._next_id(), "type": "prompt", "message": message}
        self._write_json(payload)
        self._streaming = True
        try:
            while True:
                msg = self._read_json_line()
                if msg is None:
                    break
                msg_type = msg.get("type")
                if msg_type == "response" and msg.get("command") in ("prompt", "abort"):
                    if msg.get("success") is False:
                        raise MatterWaveError(
                            str(msg.get("error") or "Pi RPC prompt command failed")
                        )
                    if msg.get("command") == "prompt":
                        data = msg.get("data")
                        if isinstance(data, Mapping) and data.get("disposition") == "handled":
                            break
                    continue

                event = PiEvent.from_dict(msg)
                if event.type in ("tool_execution_start", "tool_call", "message_update"):
                    try:
                        _check_confinement(
                            event,
                            self.workspace_root,
                            self.deny_list,
                            enforce_confinement=self.enforce_confinement,
                            approved=self.approved,
                        )
                    except PathRefused:
                        self._send_abort_nowait()
                        raise

                yield event

                if event.type in ("agent_end", "error"):
                    break
                if event.type == "turn_end" and not msg.get("toolResults"):
                    break
        finally:
            self._streaming = False

    def _send_abort_nowait(self) -> None:
        try:
            self._write_json({"id": self._next_id(), "type": "abort"})
        except Exception:
            return

    def abort(self) -> dict[str, Any]:
        """Abort the active agent operation."""
        if self._streaming:
            req_id = self._next_id()
            self._write_json({"id": req_id, "type": "abort"})
            return {"id": req_id, "type": "response", "command": "abort", "success": True}
        return self.send_command({"type": "abort"})

    def set_model(self, provider: str, model_id: str) -> dict[str, Any]:
        """Switch the active provider and model in the Pi session."""
        resp = self.send_command({"type": "set_model", "provider": provider, "modelId": model_id})
        data = resp.get("data")
        return dict(data) if isinstance(data, Mapping) else resp

    def get_state(self) -> dict[str, Any]:
        """Query the current Pi RPC session state."""
        resp = self.send_command({"type": "get_state"})
        data = resp.get("data")
        return dict(data) if isinstance(data, Mapping) else resp

    def steer(
        self,
        message: str,
        *,
        images: Sequence[Any] | None = None,
    ) -> dict[str, Any]:
        """Send a `steer` command to interrupt or steer the agent mid-run."""
        cmd: dict[str, Any] = {"type": "steer", "message": message}
        if images is not None:
            cmd["images"] = list(images)
        if self._streaming:
            req_id = self._next_id()
            cmd["id"] = req_id
            self._write_json(cmd)
            return {
                "id": req_id,
                "type": "response",
                "command": "steer",
                "success": True,
                "disposition": "queued",
            }
        resp = self.send_command(cmd)
        data = resp.get("data")
        return {**resp, **dict(data)} if isinstance(data, Mapping) else resp

    def follow_up(
        self,
        message: str,
        *,
        images: Sequence[Any] | None = None,
    ) -> dict[str, Any]:
        """Queue a `follow_up` message after the current agent run finishes."""
        cmd: dict[str, Any] = {"type": "follow_up", "message": message}
        if images is not None:
            cmd["images"] = list(images)
        if self._streaming:
            req_id = self._next_id()
            cmd["id"] = req_id
            self._write_json(cmd)
            return {
                "id": req_id,
                "type": "response",
                "command": "follow_up",
                "success": True,
                "disposition": "queued",
            }
        resp = self.send_command(cmd)
        data = resp.get("data")
        return {**resp, **dict(data)} if isinstance(data, Mapping) else resp

    def new_session(self, parent_session: str | None = None) -> dict[str, Any]:
        """Start a new session (`new_session`), optionally referencing a `parent_session`."""
        cmd: dict[str, Any] = {"type": "new_session"}
        if parent_session is not None:
            cmd["parentSession"] = parent_session
        resp = self.send_command(cmd)
        data = resp.get("data")
        return {**resp, **dict(data)} if isinstance(data, Mapping) else resp

    def get_messages(self) -> dict[str, Any]:
        """Retrieve all messages in the current session (`get_messages`)."""
        resp = self.send_command({"type": "get_messages"})
        data = resp.get("data")
        return {**resp, **dict(data)} if isinstance(data, Mapping) else resp

    def compact(self, custom_instructions: str | None = None) -> dict[str, Any]:
        """Compact the session context (`compact`)."""
        cmd: dict[str, Any] = {"type": "compact"}
        if custom_instructions is not None:
            cmd["customInstructions"] = custom_instructions
        resp = self.send_command(cmd)
        data = resp.get("data")
        return {**resp, **dict(data)} if isinstance(data, Mapping) else resp

    def close(self) -> None:
        """Terminate the underlying Pi RPC subprocess and close its streams."""
        proc = self._proc
        self._proc = None
        if proc is None:
            return
        for stream in (proc.stdin, proc.stdout, proc.stderr):
            if stream is not None:
                with contextlib.suppress(Exception):
                    stream.close()
        if proc.poll() is None:
            proc.terminate()
            try:
                proc.wait(timeout=2.0)
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.wait(timeout=2.0)


class AsyncPiRpcSession:
    """Asynchronous driver for `pi --mode rpc` using `asyncio.create_subprocess_exec`."""

    def __init__(
        self,
        *,
        command: Sequence[str] | None = None,
        cli_path: str = "pi",
        cwd: str | os.PathLike[str] | None = None,
        workspace_root: str | os.PathLike[str] | None = None,
        deny_list: DenyList | None = None,
        enforce_confinement: bool = True,
        approved: bool = False,
        env: Mapping[str, str] | None = None,
        client: Any = None,
        preset_id: str | None = None,
        temporary: bool = False,
        provider: str | None = None,
        model: str | None = None,
        extra_args: Sequence[str] | None = None,
    ) -> None:
        self._argv = _build_argv(command, cli_path, provider, model, extra_args)
        self._cwd = Path(cwd) if cwd is not None else None
        root_source = workspace_root if workspace_root is not None else cwd
        self.workspace_root: Path | None = Path(root_source) if root_source is not None else None
        self.deny_list: DenyList | None = deny_list
        self.enforce_confinement = enforce_confinement
        self.approved = approved
        self._env = _build_process_env(client, env, preset_id, temporary)
        self._proc: asyncio.subprocess.Process | None = None
        self._req_counter = 0
        self._streaming = False

    async def start(self) -> AsyncPiRpcSession:
        """Spawn the `pi --mode rpc` subprocess asynchronously if not already running."""
        if self._proc is not None and self._proc.returncode is None:
            return self
        self._proc = await asyncio.create_subprocess_exec(
            self._argv[0],
            *self._argv[1:],
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            cwd=str(self._cwd) if self._cwd is not None else None,
            env=self._env,
        )
        return self

    async def __aenter__(self) -> AsyncPiRpcSession:
        return await self.start()

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        await self.aclose()

    def _next_id(self) -> str:
        self._req_counter += 1
        return f"req_{self._req_counter}"

    async def _write_json(self, payload: Mapping[str, Any]) -> None:
        await self.start()
        proc = self._proc
        if proc is None or proc.stdin is None:
            raise MatterWaveError("Pi RPC process stdin is not available.")
        data = (json.dumps(dict(payload), separators=(",", ":")) + "\n").encode("utf-8")
        try:
            proc.stdin.write(data)
            await proc.stdin.drain()
        except (BrokenPipeError, ConnectionResetError) as exc:
            raise MatterWaveError("Pi RPC process closed stdin unexpectedly.") from exc

    async def _read_json_line(self) -> dict[str, Any] | None:
        proc = self._proc
        if proc is None or proc.stdout is None:
            raise MatterWaveError("Pi RPC process stdout is not available.")
        while True:
            raw_bytes = await proc.stdout.readline()
            if not raw_bytes:
                return None
            stripped = raw_bytes.decode("utf-8", errors="replace").strip()
            if not stripped:
                continue
            try:
                parsed = json.loads(stripped)
            except json.JSONDecodeError:
                continue
            if isinstance(parsed, dict):
                return parsed

    async def send_command(self, cmd: Mapping[str, Any]) -> dict[str, Any]:
        """Send one RPC command object and await its parsed JSON response."""
        payload = dict(cmd)
        req_id = str(payload.setdefault("id", self._next_id()))
        cmd_type = str(payload.get("type", ""))
        await self._write_json(payload)

        while True:
            msg = await self._read_json_line()
            if msg is None:
                raise MatterWaveError(
                    f"Pi RPC process exited before responding to command {cmd_type!r}."
                )
            msg_type = msg.get("type")
            if msg_type == "response":
                msg_id = msg.get("id")
                msg_cmd = msg.get("command")
                if msg_id in (None, req_id) or msg_cmd == cmd_type:
                    if msg.get("success") is False:
                        raise MatterWaveError(
                            str(msg.get("error") or f"Pi RPC command {cmd_type!r} failed")
                        )
                    return msg
            elif msg_type == "error":
                raise MatterWaveError(str(msg.get("error") or msg.get("message") or "Pi RPC error"))

    async def prompt(self, message: str) -> AsyncIterator[PiEvent]:
        """Send a prompt command and asynchronously yield `PiEvent` objects until completion."""
        payload = {"id": self._next_id(), "type": "prompt", "message": message}
        await self._write_json(payload)
        self._streaming = True
        try:
            while True:
                msg = await self._read_json_line()
                if msg is None:
                    break
                msg_type = msg.get("type")
                if msg_type == "response" and msg.get("command") in ("prompt", "abort"):
                    if msg.get("success") is False:
                        raise MatterWaveError(
                            str(msg.get("error") or "Pi RPC prompt command failed")
                        )
                    if msg.get("command") == "prompt":
                        data = msg.get("data")
                        if isinstance(data, Mapping) and data.get("disposition") == "handled":
                            break
                    continue

                event = PiEvent.from_dict(msg)
                if event.type in ("tool_execution_start", "tool_call", "message_update"):
                    try:
                        _check_confinement(
                            event,
                            self.workspace_root,
                            self.deny_list,
                            enforce_confinement=self.enforce_confinement,
                            approved=self.approved,
                        )
                    except PathRefused:
                        await self._send_abort_nowait()
                        raise

                yield event

                if event.type in ("agent_end", "error"):
                    break
                if event.type == "turn_end" and not msg.get("toolResults"):
                    break
        finally:
            self._streaming = False

    async def _send_abort_nowait(self) -> None:
        try:
            await self._write_json({"id": self._next_id(), "type": "abort"})
        except Exception:
            return

    async def abort(self) -> dict[str, Any]:
        """Abort the active agent operation."""
        if self._streaming:
            req_id = self._next_id()
            await self._write_json({"id": req_id, "type": "abort"})
            return {"id": req_id, "type": "response", "command": "abort", "success": True}
        return await self.send_command({"type": "abort"})

    async def set_model(self, provider: str, model_id: str) -> dict[str, Any]:
        """Switch the active provider and model in the Pi session."""
        resp = await self.send_command(
            {"type": "set_model", "provider": provider, "modelId": model_id}
        )
        data = resp.get("data")
        return dict(data) if isinstance(data, Mapping) else resp

    async def get_state(self) -> dict[str, Any]:
        """Query the current Pi RPC session state."""
        resp = await self.send_command({"type": "get_state"})
        data = resp.get("data")
        return dict(data) if isinstance(data, Mapping) else resp

    async def steer(
        self,
        message: str,
        *,
        images: Sequence[Any] | None = None,
    ) -> dict[str, Any]:
        """Send a `steer` command to interrupt or steer the agent mid-run."""
        cmd: dict[str, Any] = {"type": "steer", "message": message}
        if images is not None:
            cmd["images"] = list(images)
        if self._streaming:
            req_id = self._next_id()
            cmd["id"] = req_id
            await self._write_json(cmd)
            return {
                "id": req_id,
                "type": "response",
                "command": "steer",
                "success": True,
                "disposition": "queued",
            }
        resp = await self.send_command(cmd)
        data = resp.get("data")
        return {**resp, **dict(data)} if isinstance(data, Mapping) else resp

    async def follow_up(
        self,
        message: str,
        *,
        images: Sequence[Any] | None = None,
    ) -> dict[str, Any]:
        """Queue a `follow_up` message after the current agent run finishes."""
        cmd: dict[str, Any] = {"type": "follow_up", "message": message}
        if images is not None:
            cmd["images"] = list(images)
        if self._streaming:
            req_id = self._next_id()
            cmd["id"] = req_id
            await self._write_json(cmd)
            return {
                "id": req_id,
                "type": "response",
                "command": "follow_up",
                "success": True,
                "disposition": "queued",
            }
        resp = await self.send_command(cmd)
        data = resp.get("data")
        return {**resp, **dict(data)} if isinstance(data, Mapping) else resp

    async def new_session(self, parent_session: str | None = None) -> dict[str, Any]:
        """Start a new session (`new_session`), optionally referencing a `parent_session`."""
        cmd: dict[str, Any] = {"type": "new_session"}
        if parent_session is not None:
            cmd["parentSession"] = parent_session
        resp = await self.send_command(cmd)
        data = resp.get("data")
        return {**resp, **dict(data)} if isinstance(data, Mapping) else resp

    async def get_messages(self) -> dict[str, Any]:
        """Retrieve all messages in the current session (`get_messages`)."""
        resp = await self.send_command({"type": "get_messages"})
        data = resp.get("data")
        return {**resp, **dict(data)} if isinstance(data, Mapping) else resp

    async def compact(self, custom_instructions: str | None = None) -> dict[str, Any]:
        """Compact the session context (`compact`)."""
        cmd: dict[str, Any] = {"type": "compact"}
        if custom_instructions is not None:
            cmd["customInstructions"] = custom_instructions
        resp = await self.send_command(cmd)
        data = resp.get("data")
        return {**resp, **dict(data)} if isinstance(data, Mapping) else resp

    async def aclose(self) -> None:
        """Terminate the underlying Pi RPC subprocess and close its streams."""
        proc = self._proc
        self._proc = None
        if proc is None:
            return
        if proc.stdin is not None:
            with contextlib.suppress(Exception):
                proc.stdin.close()
        if proc.returncode is None:
            proc.terminate()
            try:
                await asyncio.wait_for(proc.wait(), timeout=2.0)
            except asyncio.TimeoutError:
                proc.kill()
                await proc.wait()
