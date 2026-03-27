"""Interpret pipeline results using Claude Agent SDK."""

import argparse
import asyncio
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

from claude_agent_sdk import query
from claude_agent_sdk.types import ClaudeAgentOptions, ResultMessage

DEFAULT_SYSTEM_PROMPT = (
    "You are a research assistant interpreting analysis results. "
    "Write a concise update for a Linear issue. Include: "
    "(1) what completed, (2) key quantitative results, "
    "(3) notable observations, (4) suggested next steps. "
    "Use markdown. Be specific, not vague."
)

TEXT_EXTENSIONS = {".csv", ".json", ".txt", ".tsv", ".log", ".yaml", ".yml"}
MAX_INLINE_SIZE = 50_000  # 50KB


def load_request(base_dir: Path, issue_id: str) -> dict:
    """Read .lr-interpret JSON request file."""
    request_path = base_dir / issue_id / ".lr-interpret"
    if not request_path.exists():
        raise FileNotFoundError(f"No interpretation request found at {request_path}")
    return json.loads(request_path.read_text())


def results_dir(base_dir: Path, issue_id: str, request: dict) -> Path:
    """Get the results directory from a request."""
    d = base_dir / issue_id / request["timestamp"]
    if not d.is_dir():
        raise FileNotFoundError(f"Results directory not found at {d}")
    return d


def get_agent_prompt(rdir: Path) -> str | None:
    """Read .lr-agent-prompt if present in results."""
    prompt_file = rdir / ".lr-agent-prompt"
    if prompt_file.exists():
        return prompt_file.read_text().strip()
    return None


def build_inline_context(rdir: Path, request: dict) -> str:
    """Assemble context with inlined file contents (for host mode)."""
    lines = [
        "CONTEXT:",
        f"- Issue: {request['issue_id']}",
        f"- Command: {request['command']}",
        f"- Exit code: {request['exit_code']}",
        f"- Duration: {request['duration']}",
        f"- Last 30 lines of output log:",
        request.get("log_tail", ""),
        "- Result files:",
    ]

    # List files
    try:
        for f in sorted(rdir.iterdir()):
            if f.is_file() and not f.name.startswith("."):
                size = f.stat().st_size
                lines.append(f"  {f.name} ({size} bytes)")
    except OSError:
        lines.append("  (none)")

    # Inline small text files
    for f in sorted(rdir.iterdir()):
        if f.is_file() and f.suffix in TEXT_EXTENSIONS and f.stat().st_size < MAX_INLINE_SIZE:
            lines.append(f"\n- Contents of {f.name}:")
            lines.append(f.read_text())

    # Custom instructions
    agent_prompt = get_agent_prompt(rdir)
    if agent_prompt:
        lines.append(f"\n- Custom instructions (.lr-agent-prompt):")
        lines.append(agent_prompt)

    return "\n".join(lines)


def build_prompt(rdir: Path, request: dict, guest: bool) -> str:
    """Build the user prompt for Claude."""
    if guest:
        return (
            f"Interpret the analysis results in {rdir}.\n\n"
            f"Metadata:\n"
            f"- Issue: {request['issue_id']}\n"
            f"- Command: {request['command']}\n"
            f"- Exit code: {request['exit_code']}\n"
            f"- Duration: {request['duration']}\n"
            f"- Last 30 lines of output log:\n{request.get('log_tail', '')}\n\n"
            f"Explore the result files and write your interpretation."
        )
    else:
        return build_inline_context(rdir, request)


async def run_interpret(
    prompt: str, system_prompt: str, guest: bool, cwd: Path | None = None, cli_path: str | None = None
) -> str:
    """Run Claude Agent SDK and return the interpretation text."""
    options = ClaudeAgentOptions(
        system_prompt=system_prompt,
        permission_mode="bypassPermissions",
        max_turns=10 if guest else 1,
        cwd=cwd,
        cli_path=cli_path,
    )

    result_text = None
    async for msg in query(prompt=prompt, options=options):
        if isinstance(msg, ResultMessage):
            if msg.is_error:
                raise RuntimeError(f"Claude returned an error: {msg.result}")
            result_text = msg.result

    if not result_text:
        raise RuntimeError("Claude returned empty interpretation")

    return result_text


def find_lr_update() -> str:
    """Find the lr-update script."""
    # Check relative to this package (sibling in project root)
    project_root = Path(__file__).parent.parent
    candidate = project_root / "lr-update"
    if candidate.is_file() and os.access(candidate, os.X_OK):
        return str(candidate)
    # Fall back to PATH
    found = shutil.which("lr-update")
    if found:
        return found
    raise FileNotFoundError("lr-update not found")


def post_to_linear(issue_id: str, interpretation: str) -> None:
    """Post interpretation to Linear via lr-update."""
    lr_update = find_lr_update()
    comment = f"**Agent Interpretation**\n\n{interpretation}"
    subprocess.run([lr_update, "comment", issue_id, comment], check=True)
    subprocess.run([lr_update, "status", issue_id, "Ready for Review"], check=True)


def cleanup_request(base_dir: Path, issue_id: str) -> None:
    """Remove .lr-interpret request file."""
    request_path = base_dir / issue_id / ".lr-interpret"
    request_path.unlink(missing_ok=True)


def main() -> None:
    parser = argparse.ArgumentParser(description="Interpret pipeline results using Claude")
    parser.add_argument("issue_id", help="Linear issue ID")
    parser.add_argument(
        "--host",
        action="store_true",
        help="Host mode: inline result contents, no tool access (default: guest mode)",
    )
    args = parser.parse_args()

    guest = not args.host
    base_dir = Path(os.environ.get("LR_RESULTS_MOUNT", "/mnt/lr-results"))

    request = load_request(base_dir, args.issue_id)
    rdir = results_dir(base_dir, args.issue_id, request)

    agent_prompt = get_agent_prompt(rdir)
    system_prompt = agent_prompt or DEFAULT_SYSTEM_PROMPT

    prompt = build_prompt(rdir, request, guest=guest)
    cwd = rdir if guest else None

    cli_path = os.environ.get("CLAUDE_CLI_PATH")

    print(f"Interpreting results for {args.issue_id} ({'guest' if guest else 'host'} mode)...")

    interpretation = asyncio.run(
        run_interpret(prompt, system_prompt, guest=guest, cwd=cwd, cli_path=cli_path)
    )

    post_to_linear(args.issue_id, interpretation)
    cleanup_request(base_dir, args.issue_id)

    print(f"Interpretation posted to {args.issue_id}")


if __name__ == "__main__":
    main()
