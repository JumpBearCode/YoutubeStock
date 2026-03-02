import json
import os
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class ClaudeResult:
    success: bool
    output: str | None = None
    duration_ms: int = 0
    session_id: str | None = None
    error: str | None = None


def run_claude_session(
    prompt: str,
    tools: str | None = None,
    allowed_tools: list[str] | None = None,
    model: str = "sonnet",
    log_path: str | None = None,
) -> ClaudeResult:
    """Launch a `claude -p` subprocess with stream-json output, streaming events to terminal in real-time."""

    cmd = [
        "claude", "-p",
        "--verbose",
        "--output-format", "stream-json",
        "--model", model,
    ]
    if tools is not None:
        cmd += ["--tools", tools]
    if allowed_tools:
        for tool in allowed_tools:
            cmd += ["--allowedTools", tool]

    log_file = None
    if log_path:
        Path(log_path).parent.mkdir(parents=True, exist_ok=True)
        log_file = open(log_path, "w", encoding="utf-8")

    try:
        # Remove CLAUDECODE env var to allow nested claude invocations
        env = {k: v for k, v in os.environ.items() if k != "CLAUDECODE"}

        proc = subprocess.Popen(
            cmd,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            env=env,
        )

        # Write prompt to stdin and close
        proc.stdin.write(prompt)
        proc.stdin.close()

        result_output = None
        duration_ms = 0
        session_id = None

        # Read stdout line by line in real-time
        for line in proc.stdout:
            line = line.rstrip("\n")
            if not line:
                continue

            # Save raw line to log
            if log_file:
                log_file.write(line + "\n")
                log_file.flush()

            # Try to parse as JSON event
            try:
                event = json.loads(line)
            except json.JSONDecodeError:
                print(f"  [claude] {line}", flush=True)
                continue

            _print_event(event)

            # Extract result
            if event.get("type") == "result":
                result_output = _extract_result_text(event)
                duration_ms = event.get("duration_ms", 0)
                session_id = event.get("session_id")

        proc.wait()

        if proc.returncode != 0 and result_output is None:
            return ClaudeResult(
                success=False,
                error=f"claude exited with code {proc.returncode}",
            )

        return ClaudeResult(
            success=True,
            output=result_output,
            duration_ms=duration_ms,
            session_id=session_id,
        )

    except FileNotFoundError:
        return ClaudeResult(
            success=False,
            error="'claude' CLI not found. Make sure Claude Code is installed and on PATH.",
        )
    except Exception as e:
        return ClaudeResult(success=False, error=str(e))
    finally:
        if log_file:
            log_file.close()


def _print_event(event: dict) -> None:
    """Print a stream-json event to the terminal in a readable format."""
    etype = event.get("type", "")

    if etype == "system":
        # Init message with model info
        model = event.get("model", "")
        print(f"  [claude] Session started (model: {model})", flush=True)

    elif etype == "assistant":
        # Assistant message — may contain text and/or tool_use blocks
        message = event.get("message", {})
        for block in message.get("content", []):
            if block.get("type") == "text":
                text = block.get("text", "")
                # Print first 200 chars of reasoning
                preview = text[:200] + ("..." if len(text) > 200 else "")
                for line in preview.split("\n"):
                    print(f"  [claude] {line}", flush=True)
            elif block.get("type") == "tool_use":
                tool_name = block.get("name", "?")
                tool_input = block.get("input", {})
                # Abbreviate input for display
                input_str = json.dumps(tool_input, ensure_ascii=False)
                if len(input_str) > 120:
                    input_str = input_str[:120] + "..."
                print(f"  [claude] Tool: {tool_name}({input_str})", flush=True)

    elif etype == "tool_result":
        # Tool result — show success/error
        content = event.get("content", "")
        is_error = event.get("is_error", False)
        status = "ERROR" if is_error else "OK"
        preview = str(content)[:150] + ("..." if len(str(content)) > 150 else "")
        print(f"  [claude] Tool result [{status}]: {preview}", flush=True)

    elif etype == "result":
        duration = event.get("duration_ms", 0)
        print(f"  [claude] Done ({duration / 1000:.1f}s)", flush=True)


def _extract_result_text(event: dict) -> str | None:
    """Extract the final text output from a result event."""
    # The result event has a "result" field with the final output
    result_text = event.get("result")
    if isinstance(result_text, str):
        return result_text

    # Fallback: try to get from subMessage content blocks
    sub = event.get("subMessage", {})
    for block in sub.get("content", []):
        if block.get("type") == "text":
            return block.get("text")

    return None
