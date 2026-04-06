import asyncio
import json
from collections.abc import AsyncGenerator
from pathlib import Path

from sqlalchemy.ext.asyncio import AsyncSession

from atelier.config import Settings
from atelier.models import PromptNode
from atelier.services.tree import get_ancestors

COACHING_INSTRUCTIONS = """\
You are a Midjourney prompt engineering coach inside Atelier, a prompt workbench for concept art.

Your approach:
- Give specific, cause-and-effect suggestions — not rules. Say "X produces Y; if you want Z, try W."
- One change at a time when troubleshooting.
- Explain what each change will produce before suggesting it.
- Surface relevant Midjourney tips contextually — targeted, not a docs dump.
- The user does NOT like highly stylized, obviously-AI-looking art. Prefer grounded, natural aesthetics.

Your response format — follow this structure every time:

### What to change
One or two focused suggestions. Use a bullet per suggestion. For each, explain the cause and effect: what the change does and why.

### Why
Brief reasoning grounded in how Midjourney actually interprets the change (model behavior, parameter interaction, prompt weighting, etc.). Keep it to 2-3 sentences max.

### Try this
A complete, ready-to-use Midjourney prompt in a single code block. This must be a full prompt the user can copy-paste directly into Midjourney — not a fragment or diff.

```
the full refined prompt here with all parameters
```

Keep the whole response concise. No preamble, no filler. Go straight into the suggestions.
"""


class CoachingService:
    def __init__(
        self,
        settings: Settings,
        knowledge: dict[str, str],
        claude_cli: str | None = None,
    ):
        self.settings = settings
        self.knowledge = knowledge
        self.claude_cli = claude_cli

    def _build_prompt(
        self,
        node: PromptNode,
        ancestors: list[PromptNode],
        user_message: str,
        prior_messages: list,
        current_image_path: Path | None,
    ) -> str:
        parts: list[str] = []

        # Coaching instructions + MJ knowledge
        parts.append(COACHING_INSTRUCTIONS)
        parts.append("\n# Midjourney Knowledge Base\n")
        for name, content in self.knowledge.items():
            parts.append(f"## {name}\n\n{content}\n")
        parts.append("\n---\n")

        # Iteration context (ancestor prompts, root-first).
        # Ancestor images are represented by their cached text description
        # to keep the context cheap — we don't pass raw pixels for history.
        non_self = [a for a in ancestors if a.id != node.id]
        if non_self:
            parts.append("# Prompt iteration history (oldest first)\n")
            for a in non_self:
                parts.append(f"**Prompt:** `{a.prompt_text}`")
                if a.notes:
                    parts.append(f"**Notes:** {a.notes}")
                if a.image and a.image.description:
                    parts.append(
                        f"**Generated image:** {a.image.description}"
                    )
                if a.image and a.image.feedback:
                    parts.append(f"**Feedback on that result:** {a.image.feedback}")
                parts.append("")

        # Current prompt
        parts.append(f"# Current prompt\n\n`{node.prompt_text}`\n")
        if node.notes:
            parts.append(f"# User notes\n\n{node.notes}\n")

        # Current image — pass the file path and let the coach read it
        # directly so it can see the real pixels (not just a description).
        if current_image_path is not None:
            parts.append(
                "# Generated image for the current prompt\n\n"
                f"The image produced by this prompt is at: {current_image_path}\n"
                "Read the file so you can see it before responding.\n"
            )
            if node.image and node.image.description:
                parts.append(
                    f"Prior auto-description (for reference): {node.image.description}\n"
                )

        if node.image and node.image.feedback:
            parts.append(
                f"# Feedback on the current generated result\n\n{node.image.feedback}\n"
            )

        # Prior coaching conversation on this node
        if prior_messages:
            parts.append("# Prior coaching conversation\n")
            for msg in prior_messages:
                label = "User" if msg.role == "user" else "Coach"
                parts.append(f"**{label}:** {msg.content}\n")

        # Current user message
        parts.append(f"# Current request\n\n{user_message}")

        return "\n".join(parts)

    async def stream_response(
        self,
        db: AsyncSession,
        node: PromptNode,
        user_message: str,
    ) -> AsyncGenerator[str, None]:
        ancestors = await get_ancestors(db, node.id)

        # Exclude the message we just saved (it's the current user_message)
        prior = [
            m
            for m in node.coaching_messages
            if not (
                m.role == "user"
                and m.content == user_message
                and m == node.coaching_messages[-1]
            )
        ]

        # Resolve the current image path (if any) so the coach can read it.
        current_image_path: Path | None = None
        if node.image:
            candidate = (
                self.settings.image_dir
                / str(node.project_id)
                / node.image.filename
            )
            if candidate.exists():
                current_image_path = candidate

        prompt = self._build_prompt(
            node, ancestors, user_message, prior, current_image_path
        )

        # When we're handing the coach an image, it needs to use the Read
        # tool (one extra turn) and we need to grant filesystem access to
        # the image directory. Otherwise keep the existing single-turn
        # behavior so coaching stays fast.
        from atelier.services.claude_cli import find_claude_cli

        binary = self.claude_cli or find_claude_cli()
        if not binary:
            yield (
                "Error: `claude` CLI not found. Install Claude Code and make sure "
                "`claude` is on your PATH (or in `~/.local/bin`). If it is, "
                "restart the atelier server from a shell that has it on PATH."
            )
            return

        cli_args = [
            binary,
            "-p",
            "--output-format",
            "stream-json",
            "--verbose",
            "--include-partial-messages",
        ]
        if current_image_path is not None:
            cli_args += [
                "--max-turns", "3",
                "--add-dir", str(current_image_path.parent),
            ]
        else:
            cli_args += ["--max-turns", "1"]

        try:
            proc = await asyncio.create_subprocess_exec(
                *cli_args,
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
        except FileNotFoundError:
            yield f"Error: `claude` CLI not found at {binary}."
            return

        proc.stdin.write(prompt.encode())
        await proc.stdin.drain()
        proc.stdin.close()

        timed_out = False
        try:
            async for raw_line in proc.stdout:
                line = raw_line.decode().strip()
                if not line:
                    continue
                try:
                    data = json.loads(line)
                    if data.get("type") == "stream_event":
                        event = data.get("event", {})
                        if (
                            event.get("type") == "content_block_delta"
                            and event.get("delta", {}).get("type") == "text_delta"
                        ):
                            text = event["delta"].get("text", "")
                            if text:
                                yield text
                except json.JSONDecodeError:
                    continue
        except asyncio.CancelledError:
            proc.kill()
            raise

        try:
            rc = await asyncio.wait_for(proc.wait(), timeout=10)
        except asyncio.TimeoutError:
            proc.kill()
            rc = -1
            timed_out = True

        if timed_out:
            yield "\n\n[Coach timed out]"
        elif rc != 0:
            stderr = await proc.stderr.read()
            err_msg = stderr.decode().strip() if stderr else "Unknown error"
            # Strip noisy bun warnings
            err_lines = [
                l for l in err_msg.splitlines()
                if not l.startswith("warn:") and l.strip()
            ]
            clean_err = "\n".join(err_lines) if err_lines else err_msg
            yield f"\n\n[Coaching error: {clean_err}]"
