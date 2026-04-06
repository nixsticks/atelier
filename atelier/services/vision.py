"""One-shot vision calls via the `claude` CLI.

Used to auto-generate text descriptions of uploaded images so that the
coaching prompt can reference them cheaply for ancestor nodes instead of
passing raw image bytes on every call.
"""

import asyncio
from pathlib import Path

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


async def describe_image(image_path: Path, timeout: float = 90.0) -> str | None:
    """Generate a text description of an image via `claude -p`.

    Returns the description string, or None on failure. Failures are
    intentionally swallowed — description is a best-effort enrichment and
    must never block image upload from succeeding.
    """
    if not image_path.exists():
        return None

    prompt = DESCRIBE_PROMPT.format(path=image_path)

    try:
        proc = await asyncio.create_subprocess_exec(
            "claude",
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
        return None

    try:
        stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=timeout)
    except asyncio.TimeoutError:
        proc.kill()
        return None

    if proc.returncode != 0:
        return None

    text = stdout.decode().strip()
    return text or None
