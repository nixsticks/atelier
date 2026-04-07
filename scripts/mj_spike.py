"""Midjourney Discord spike — throwaway script.

Goal: prove we can fire `/imagine` from Python and pull back the finished
grid URL. This script is intentionally NOT integrated with the atelier
package — it lives outside `atelier/` and has its own deps.

Usage:
    pip install discord.py-self python-dotenv
    # Put DISCORD_TOKEN, MJ_CHANNEL_ID, MJ_GUILD_ID in .env (or env vars)
    python scripts/mj_spike.py "a sphinx in the desert --ar 16:9"

What you'll need:
    DISCORD_TOKEN     — your personal Discord user token (NOT a bot token).
                        Grab from DevTools > Network > any request to
                        discord.com/api > Authorization header.
    MJ_CHANNEL_ID     — ID of the channel where you run /imagine
    MJ_GUILD_ID       — ID of the server containing that channel

Caveats:
    - Self-botting violates Discord TOS. Use a dedicated account.
    - The script embeds a short hex nonce in your prompt for correlation.
      Visible as `[#abcd]` in the MJ message — usually doesn't affect
      generation but is technically text in your prompt.
    - Detection of "done" is heuristic: no progress markers in content,
      and at least one image attachment present.
"""

from __future__ import annotations

import asyncio
import logging
import os
import re
import sys

import discord  # discord.py-self

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

# Verbose logging so we can actually see what discord.py-self is doing.
# Set MJ_SPIKE_DEBUG=1 for full DEBUG-level firehose; default is INFO.
_level = logging.DEBUG if os.environ.get("MJ_SPIKE_DEBUG") else logging.INFO
logging.basicConfig(
    level=_level,
    format="%(asctime)s.%(msecs)03d %(name)-22s %(levelname)-5s %(message)s",
    datefmt="%H:%M:%S",
)
# Quiet a couple of noisy ones unless we're in debug mode.
if not os.environ.get("MJ_SPIKE_DEBUG"):
    logging.getLogger("discord.gateway").setLevel(logging.WARNING)
    logging.getLogger("discord.client").setLevel(logging.WARNING)
log = logging.getLogger("spike")

# Midjourney bot user ID — public, stable
MIDJOURNEY_BOT_ID = 936929561302675456

# Heuristic markers MJ uses while a generation is in flight.
# Final messages have an image attachment AND no progress markers.
PROGRESS_MARKERS = re.compile(r"\((\d+%|Waiting to start|paused|stopped)\)", re.I)


async def run_spike(prompt: str) -> None:
    token = os.environ.get("DISCORD_TOKEN")
    channel_id = os.environ.get("MJ_CHANNEL_ID")
    guild_id = os.environ.get("MJ_GUILD_ID")

    missing = [k for k, v in {
        "DISCORD_TOKEN": token,
        "MJ_CHANNEL_ID": channel_id,
        "MJ_GUILD_ID": guild_id,
    }.items() if not v]
    if missing:
        sys.exit(f"Missing env vars: {', '.join(missing)}")

    # Correlation strategy: MJ pings the requesting user in every reply
    # message, so we match by `client.user in message.mentions`. This
    # leaves the prompt untouched (MJ's parser rejects unknown bracketed
    # tokens, and it auto-appends user default settings, both of which
    # break naive prompt-text matching).
    log.info("prompt=%s", prompt)

    client = discord.Client()
    done = asyncio.Event()
    result = {"url": None, "error": None}
    seen_messages: set[int] = set()

    def matches_us(message: discord.Message) -> bool:
        # Primary signal: MJ pings the requester in its reply.
        if client.user in message.mentions:
            return True
        # Fallback: some MJ messages (e.g. error embeds delivered as
        # ephemeral interaction responses) may not include a mention but
        # do carry interaction metadata pointing back at us.
        meta = getattr(message, "interaction_metadata", None) or getattr(
            message, "interaction", None
        )
        if meta is not None and getattr(meta, "user", None) is not None:
            return meta.user.id == client.user.id
        return False

    def is_final(message: discord.Message) -> bool:
        if not message.attachments:
            return False
        if PROGRESS_MARKERS.search(message.content):
            return False
        return True

    async def handle_message(message: discord.Message, is_edit: bool) -> None:
        # Log EVERY message in the channel we care about, even ones we
        # ultimately skip — this is the only way to debug a stuck spike.
        if message.channel.id != int(channel_id):
            return
        kind = "EDIT" if is_edit else "NEW "
        author = f"{message.author} (id={message.author.id})"
        snippet = (message.content or "")[:120].replace("\n", " ")
        log.info(
            "%s msg from %s | embeds=%d attachments=%d | %r",
            kind, author, len(message.embeds), len(message.attachments), snippet,
        )

        if message.author.id != MIDJOURNEY_BOT_ID:
            log.debug("  -> skip: not from MJ bot")
            return
        if not matches_us(message):
            log.info("  -> skip: not addressed to us (no mention/interaction)")
            return

        log.info("  -> MATCH: this is our generation")

        # Surface MJ errors. MJ delivers errors as embeds — log every embed
        # we see and treat anything that looks error-shaped as a hard failure.
        ERROR_KEYWORDS = (
            "error", "invalid", "banned", "blocked", "action needed",
            "rejected", "queue full", "subscription",
        )
        for embed in message.embeds:
            log.info(
                "  embed: title=%r desc=%r",
                embed.title, (embed.description or "")[:200],
            )
            title = (embed.title or "").lower()
            if any(word in title for word in ERROR_KEYWORDS):
                result["error"] = f"{embed.title}: {embed.description or ''}"
                done.set()
                return

        # Log progress on edits
        if is_edit:
            progress = PROGRESS_MARKERS.search(message.content)
            if progress:
                log.info("  progress: %s", progress.group(0))

        if is_final(message):
            result["url"] = message.attachments[0].url
            log.info("  FINAL: %s", result["url"])
            done.set()
        else:
            log.debug(
                "  not final yet: attachments=%d markers=%s",
                len(message.attachments),
                bool(PROGRESS_MARKERS.search(message.content)),
            )

    @client.event
    async def on_ready() -> None:
        log.info("logged in as %s (id=%s)", client.user, client.user.id)
        channel = client.get_channel(int(channel_id))
        if channel is None:
            result["error"] = f"channel {channel_id} not found (not in cache?)"
            done.set()
            return
        log.info("channel: #%s in %s", channel.name, channel.guild.name)

        # Find MJ's /imagine slash command in this channel.
        # Drop the application filter — match by name only, log everything we see.
        log.info("enumerating slash commands matching 'imagine'...")
        try:
            commands = [
                cmd async for cmd in channel.slash_commands(query="imagine")
            ]
        except Exception as e:
            log.exception("slash_commands() raised")
            result["error"] = f"failed to enumerate slash commands: {e}"
            done.set()
            return

        log.info("found %d candidate command(s):", len(commands))
        for c in commands:
            app_id = getattr(c, "application_id", None)
            log.info("  - name=%s application_id=%s from=%s", c.name, app_id, c)

        imagine = next(
            (c for c in commands
             if c.name == "imagine"
             and getattr(c, "application_id", None) == MIDJOURNEY_BOT_ID),
            None,
        )
        if imagine is None:
            # Fall back: any /imagine, even if not from MJ. Log loudly.
            imagine = next((c for c in commands if c.name == "imagine"), None)
            if imagine:
                log.warning("no MJ-owned /imagine found; falling back to %s", imagine)
        if imagine is None:
            result["error"] = "/imagine command not found in this channel"
            done.set()
            return

        log.info("firing /imagine ...")
        try:
            await imagine(prompt=prompt, channel=channel)
            log.info("/imagine call returned (interaction acknowledged)")
        except Exception as e:
            log.exception("imagine() raised")
            result["error"] = f"slash command invocation failed: {e}"
            done.set()
            return

    @client.event
    async def on_message(message: discord.Message) -> None:
        if message.id in seen_messages:
            return
        seen_messages.add(message.id)
        await handle_message(message, is_edit=False)

    @client.event
    async def on_message_edit(_before: discord.Message, after: discord.Message) -> None:
        await handle_message(after, is_edit=True)

    # Run the client until either we get a result or hit the timeout.
    # Heartbeat every 10s so the user can see we're still alive.
    async def heartbeat() -> None:
        i = 0
        while not done.is_set():
            await asyncio.sleep(10)
            i += 10
            if not done.is_set():
                log.info("...still waiting (%ds elapsed)", i)

    runner = asyncio.create_task(client.start(token))
    hb = asyncio.create_task(heartbeat())
    try:
        await asyncio.wait_for(done.wait(), timeout=300)
    except asyncio.TimeoutError:
        result["error"] = "timed out after 5min waiting for MJ"
    finally:
        hb.cancel()
        await client.close()
        try:
            await runner
        except Exception:
            pass

    if result["error"]:
        sys.exit(f"[spike] error: {result['error']}")
    log.info("DONE")
    log.info("grid url: %s", result["url"])


def main() -> None:
    if len(sys.argv) < 2:
        sys.exit('usage: python scripts/mj_spike.py "<prompt>"')
    prompt = " ".join(sys.argv[1:])
    asyncio.run(run_spike(prompt))


if __name__ == "__main__":
    main()
