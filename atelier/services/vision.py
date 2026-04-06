"""One-shot vision calls via the `claude` CLI.

Used to auto-generate text descriptions of uploaded images so that the
coaching prompt can reference them cheaply for ancestor nodes instead of
passing raw image bytes on every call.
"""

import asyncio
import logging
from pathlib import Path

from atelier.services.claude_cli import find_claude_cli

logger = logging.getLogger(__name__)

DESCRIBE_PROMPT = """\
Read the image at {path} and describe this Midjourney output in about 100 words.

Cover, in order:
- Subject & composition
- Lighting & color palette
- Medium / rendering style (photo, illustration, painting, 3D render, etc.)
- Mood & atmosphere
- Any visible artifacts or failure modes (melted faces, bad hands, plastic skin, over-stylization, etc.)

Be specific and grounded. Prefer concrete observations over vague adjectives.
Respond with just the description — no preamble, no headers, no bullet labels.
"""


async def describe_image(
    image_path: Path,
    timeout: float = 120.0,
    claude_cli: str | None = None,
) -> str | None:
    """Generate a text description of an image via `claude -p`.

    `claude_cli` should be the absolute path to the `claude` binary
    (resolved at server startup). If not provided, falls back to a fresh
    PATH lookup.

    Returns the description string, or None on failure. Failures are
    logged but not raised — description is a best-effort enrichment and
    must never block image upload from succeeding.
    """
    if not image_path.exists():
        logger.warning("describe_image: file does not exist: %s", image_path)
        return None

    binary = claude_cli or find_claude_cli()
    if not binary:
        logger.error("describe_image: `claude` CLI not found in PATH")
        return None

    prompt = DESCRIBE_PROMPT.format(path=image_path)

    try:
        proc = await asyncio.create_subprocess_exec(
            binary,
            "-p",
            prompt,
            "--max-turns",
            "3",
            "--add-dir",
            str(image_path.parent),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
    except FileNotFoundError:
        logger.error("describe_image: `claude` CLI not found at %s", binary)
        return None

    try:
        stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=timeout)
    except asyncio.TimeoutError:
        proc.kill()
        logger.error("describe_image: timed out after %.0fs for %s", timeout, image_path)
        return None

    if proc.returncode != 0:
        err = stderr.decode().strip() if stderr else "(no stderr)"
        logger.error(
            "describe_image: claude exited %d for %s: %s",
            proc.returncode,
            image_path,
            err,
        )
        return None

    text = stdout.decode().strip()
    if not text:
        logger.warning("describe_image: empty output for %s", image_path)
        return None
    return text
