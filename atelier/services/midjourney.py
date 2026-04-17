"""Midjourney generation via a long-lived Discord self-bot client.

One MidjourneyService instance lives in `app.state.midjourney` for the
life of the FastAPI app. Generations are serialized via an internal lock
— atelier is single-user, low-volume, and serialization sidesteps any
correlation/cross-wiring issues that come with multiple in-flight
generations on a shared Discord connection.

Architectural mirror of `CoachingService`: a service object that owns
its own external connection, exposes an async streaming interface, and
yields events the router can translate into SSE.

Important caveat: self-botting violates Discord ToS. The user is
expected to run this against a dedicated account on a private personal
server. See nixsticks/atelier#2 for context.
"""

from __future__ import annotations

import asyncio
import logging
import re
from collections.abc import AsyncGenerator
from dataclasses import dataclass
from typing import Any, Literal

logger = logging.getLogger(__name__)

# Public, stable Midjourney bot user ID.
MIDJOURNEY_BOT_ID = 936929561302675456

# Markers MJ embeds in message content while a generation is in flight.
PROGRESS_PERCENT = re.compile(r"\((\d+)%\)")
WAITING_MARKERS = re.compile(r"\((Waiting to start|paused|stopped)\)", re.I)

# Embed title/description fragments MJ uses for synchronous failure
# modes (banned word, invalid param, queue full, etc.).
ERROR_KEYWORDS = (
    "error",
    "invalid",
    "banned",
    "blocked",
    "action needed",
    "rejected",
    "queue full",
    "subscription",
)

# Plain-content phrases MJ uses when a job dies mid-flight (their backend
# crashed, the model refused, etc.). These show up as the message content
# of the in-progress reply, not as embeds, so the embed-title check above
# won't catch them.
JOB_FAILURE_PHRASES = (
    "something went wrong",
    "encountered an error",
    "our team has been notified",
    "job failed",
    "image cannot be shown",
    "image was filtered",
)

EventType = Literal["queued", "progress", "done", "error"]


def _find_component_button(message: Any, label: str) -> Any | None:
    """Locate a button by label in a message's component rows.

    Match by `.label` first (MJ uses stable "U1".."U4" / "V1".."V4"
    labels — verified in the spike). Fall back to a `custom_id`
    substring match so a future MJ change that drops labels but keeps
    the `MJ::JOB::upsample::N::<uuid>` pattern still works.
    """
    target_upper = label.upper()
    rows = getattr(message, "components", None) or []
    for row in rows:
        for child in getattr(row, "children", None) or []:
            if (getattr(child, "label", "") or "").strip().upper() == target_upper:
                return child
    for row in rows:
        for child in getattr(row, "children", None) or []:
            cid = (getattr(child, "custom_id", "") or "").upper()
            if cid and f"::{target_upper[1:]}::" in cid and "UPSAMPLE" in cid and target_upper.startswith("U"):
                return child
            if cid and f"::{target_upper[1:]}::" in cid and "VARIATION" in cid and target_upper.startswith("V"):
                return child
    return None


@dataclass
class GenerationEvent:
    type: EventType
    progress: int | None = None
    image_url: str | None = None
    message: str | None = None
    # Populated on the terminal `done` event so callers can persist a
    # handle for follow-up actions (pressing U1–U4 / V1–V4 on the
    # original grid message to spawn upscales/variations).
    discord_message_id: str | None = None
    discord_channel_id: str | None = None


class MidjourneyService:
    def __init__(
        self,
        token: str,
        channel_id: int,
        guild_id: int,
        timeout: float = 300,
    ) -> None:
        self._token = token
        self._channel_id = channel_id
        self._guild_id = guild_id
        self._timeout = timeout

        # Lazy import — discord.py-self is an optional dep so atelier
        # still installs and runs without it.
        try:
            import discord  # type: ignore
        except ImportError as e:
            raise RuntimeError(
                "discord.py-self is required for Midjourney integration. "
                "Install with: pip install 'atelier[mj]'"
            ) from e
        self._discord = discord
        self._client = discord.Client()
        self._client.event(self.on_ready)
        self._client.event(self.on_message)
        self._client.event(self.on_message_edit)

        self._lock = asyncio.Lock()
        self._ready = asyncio.Event()
        self._runner: asyncio.Task[Any] | None = None
        self._channel: Any = None
        self._imagine_cmd: Any = None
        self._active_queue: asyncio.Queue[GenerationEvent] | None = None
        # When pressing a button on a grid message, MJ edits that grid
        # message to reflect the new button state (pressed quadrant goes
        # primary). Those edits share the grid's attachment + non-progress
        # content, which would otherwise look "final" to `_dispatch`.
        # `upscale()` sets this to the grid's id so we ignore those ghosts.
        self._ignore_message_id: int | None = None

    # ---- lifecycle ----

    async def start(self) -> None:
        """Connect to Discord and resolve the /imagine command.

        Called from FastAPI lifespan startup. Raises RuntimeError if the
        client can't reach the ready state in 30s — better to fail loud
        at boot than silently degrade and surprise the user mid-session.
        """
        self._runner = asyncio.create_task(self._client.start(self._token))
        try:
            await asyncio.wait_for(self._ready.wait(), timeout=30)
        except asyncio.TimeoutError:
            await self.stop()
            raise RuntimeError(
                "Midjourney Discord client failed to become ready in 30s"
            )

    async def stop(self) -> None:
        await self._client.close()
        if self._runner is not None:
            try:
                await self._runner
            except Exception:
                pass

    # ---- event handlers ----

    async def on_ready(self) -> None:
        logger.info("MJ Discord client logged in as %s", self._client.user)
        channel = self._client.get_channel(self._channel_id)
        if channel is None:
            logger.error(
                "MJ channel %s not found — is the bot installed in the right server?",
                self._channel_id,
            )
            return
        self._channel = channel

        try:
            cmds = [c async for c in channel.slash_commands(query="imagine")]
        except Exception:
            logger.exception("MJ slash command enumeration failed")
            return

        self._imagine_cmd = next(
            (
                c
                for c in cmds
                if c.name == "imagine"
                and getattr(c, "application_id", None) == MIDJOURNEY_BOT_ID
            ),
            None,
        )
        if self._imagine_cmd is None:
            logger.error(
                "MJ /imagine command not found in #%s", getattr(channel, "name", "?")
            )
            return

        logger.info(
            "MJ ready — channel=#%s, command resolved",
            getattr(channel, "name", "?"),
        )
        self._ready.set()

    async def on_message(self, message: Any) -> None:
        await self._dispatch(message, is_edit=False)

    async def on_message_edit(self, _before: Any, after: Any) -> None:
        await self._dispatch(after, is_edit=True)

    # ---- routing ----

    async def _dispatch(self, message: Any, is_edit: bool) -> None:
        # Drop quickly if no generation is active. Drop is fine — these
        # are leftover messages from prior generations or unrelated chat.
        if self._active_queue is None:
            return
        if self._channel is None or message.channel.id != self._channel.id:
            return
        if message.author.id != MIDJOURNEY_BOT_ID:
            return
        if not self._addressed_to_us(message):
            return
        # See note on `_ignore_message_id`: skip edits to the grid whose
        # button we pressed, otherwise the re-styled grid looks like a
        # finished upscale to the attachment/no-progress heuristic below.
        if (
            self._ignore_message_id is not None
            and message.id == self._ignore_message_id
        ):
            return

        content = message.content or ""

        # Per-message log so unfamiliar MJ formats are debuggable without
        # re-running the spike. Truncated; full content goes to DEBUG.
        logger.info(
            "MJ message %s: embeds=%d attachments=%d content=%r",
            "EDIT" if is_edit else "NEW",
            len(message.embeds),
            len(message.attachments),
            content[:160].replace("\n", " "),
        )

        # Synchronous failures (banned word, invalid param, queue full)
        # come as embeds with a recognizable title or description.
        for embed in message.embeds:
            title = (embed.title or "").lower()
            desc = (embed.description or "").lower()
            if any(word in title for word in ERROR_KEYWORDS) or any(
                word in desc for word in ERROR_KEYWORDS
            ):
                await self._active_queue.put(
                    GenerationEvent(
                        type="error",
                        message=f"{embed.title}: {embed.description or ''}".strip(": "),
                    )
                )
                return

        # Mid-flight job failures ("Sorry, something went wrong",
        # filtered images, etc.) show up as plain message content, not
        # embeds. MJ stops editing the message when this happens, so the
        # client otherwise hangs at the last seen progress percentage.
        content_lower = content.lower()
        if any(phrase in content_lower for phrase in JOB_FAILURE_PHRASES):
            await self._active_queue.put(
                GenerationEvent(
                    type="error",
                    message=content.strip() or "Midjourney job failed",
                )
            )
            return

        # Progress edits.
        if is_edit:
            m = PROGRESS_PERCENT.search(content)
            if m:
                await self._active_queue.put(
                    GenerationEvent(type="progress", progress=int(m.group(1)))
                )
                return

        # Final state: has attachments AND no in-flight markers.
        if message.attachments and not (
            PROGRESS_PERCENT.search(content) or WAITING_MARKERS.search(content)
        ):
            await self._active_queue.put(
                GenerationEvent(
                    type="done",
                    image_url=message.attachments[0].url,
                    discord_message_id=str(message.id),
                    discord_channel_id=str(message.channel.id),
                )
            )

    def _addressed_to_us(self, message: Any) -> bool:
        # Primary: MJ pings the requester in every reply.
        if self._client.user in message.mentions:
            return True
        # Fallback: ephemeral error embeds may not include a mention but
        # do carry interaction metadata pointing back at us.
        meta = getattr(message, "interaction_metadata", None) or getattr(
            message, "interaction", None
        )
        if meta is not None and getattr(meta, "user", None) is not None:
            return bool(meta.user.id == self._client.user.id)
        return False

    # ---- public API ----

    async def fetch_attachment_url(
        self, message_id: str, channel_id: str
    ) -> str:
        """Return the current Discord CDN URL for a stored MJ message.

        MJ CDN URLs have signed tokens that expire after a few hours —
        we can't persist them. This re-fetches the message so `--cref`
        / `--sref` references stay fresh each time the user iterates.

        Raises RuntimeError on any failure (channel/message not found,
        no attachments, client not ready) so the caller can surface it
        as an HTTP error.
        """
        if not self._ready.is_set():
            raise RuntimeError("Midjourney service not ready")
        try:
            channel = self._client.get_channel(int(channel_id))
            if channel is None:
                channel = await self._client.fetch_channel(int(channel_id))
            msg = await channel.fetch_message(int(message_id))
        except Exception as e:
            raise RuntimeError(
                f"couldn't fetch message {message_id}: {e}"
            ) from e
        if not msg.attachments:
            raise RuntimeError(f"message {message_id} has no attachments")
        return msg.attachments[0].url

    async def _press_grid_button(
        self,
        grid_message_id: str,
        grid_channel_id: str,
        button_label: str,
    ) -> AsyncGenerator[GenerationEvent, None]:
        """Shared implementation for upscale/variation.

        Fetches the grid message, locates the button by label, presses
        it, and streams events back through the same state machine as
        `generate()`. `_ignore_message_id` filters out edits to the
        grid message that happen because MJ restyles the pressed button.
        """
        if not self._ready.is_set():
            yield GenerationEvent(
                type="error",
                message="Midjourney service not ready (Discord client not connected)",
            )
            return
        if self._channel is None:
            yield GenerationEvent(
                type="error",
                message="Midjourney channel not available",
            )
            return

        async with self._lock:
            queue: asyncio.Queue[GenerationEvent] = asyncio.Queue()
            self._active_queue = queue
            self._ignore_message_id = int(grid_message_id)
            try:
                yield GenerationEvent(type="queued")

                try:
                    channel = self._client.get_channel(int(grid_channel_id))
                    if channel is None:
                        channel = await self._client.fetch_channel(int(grid_channel_id))
                    grid_msg = await channel.fetch_message(int(grid_message_id))
                except Exception as e:
                    logger.exception(
                        "failed to fetch MJ grid message %s", grid_message_id
                    )
                    yield GenerationEvent(
                        type="error",
                        message=f"couldn't fetch grid message {grid_message_id}: {e}",
                    )
                    return

                button = _find_component_button(grid_msg, button_label)
                if button is None:
                    yield GenerationEvent(
                        type="error",
                        message=(
                            f"{button_label} button not found on grid message "
                            f"{grid_message_id} (MJ may have expired the buttons)"
                        ),
                    )
                    return

                try:
                    await button.click()
                except Exception as e:
                    logger.exception(
                        "button.click() raised for %s", button_label
                    )
                    yield GenerationEvent(
                        type="error",
                        message=f"couldn't press {button_label}: {e}",
                    )
                    return

                while True:
                    try:
                        event = await asyncio.wait_for(
                            queue.get(), timeout=self._timeout
                        )
                    except asyncio.TimeoutError:
                        yield GenerationEvent(
                            type="error",
                            message=(
                                f"timed out after {int(self._timeout)}s "
                                f"waiting for Midjourney {button_label}"
                            ),
                        )
                        return
                    yield event
                    if event.type in ("done", "error"):
                        return
            finally:
                self._active_queue = None
                self._ignore_message_id = None

    async def upscale(
        self, grid_message_id: str, grid_channel_id: str, quadrant: int,
    ) -> AsyncGenerator[GenerationEvent, None]:
        """Press U{quadrant} on a prior grid and stream the upscale."""
        if quadrant not in (1, 2, 3, 4):
            yield GenerationEvent(
                type="error", message=f"quadrant must be 1..4, got {quadrant}"
            )
            return
        async for event in self._press_grid_button(
            grid_message_id, grid_channel_id, f"U{quadrant}"
        ):
            yield event

    async def variation(
        self, grid_message_id: str, grid_channel_id: str, quadrant: int,
    ) -> AsyncGenerator[GenerationEvent, None]:
        """Press V{quadrant} on a prior grid and stream the variation."""
        if quadrant not in (1, 2, 3, 4):
            yield GenerationEvent(
                type="error", message=f"quadrant must be 1..4, got {quadrant}"
            )
            return
        async for event in self._press_grid_button(
            grid_message_id, grid_channel_id, f"V{quadrant}"
        ):
            yield event

    async def generate(self, prompt: str) -> AsyncGenerator[GenerationEvent, None]:
        """Submit a /imagine prompt and stream events back.

        Serialized — concurrent callers block on the lock until any
        in-flight generation finishes. Always yields a terminal event
        (`done` or `error`) before returning.
        """
        if not self._ready.is_set():
            yield GenerationEvent(
                type="error",
                message="Midjourney service not ready (Discord client not connected)",
            )
            return
        if self._imagine_cmd is None or self._channel is None:
            yield GenerationEvent(
                type="error",
                message="Midjourney /imagine command not available in configured channel",
            )
            return

        async with self._lock:
            queue: asyncio.Queue[GenerationEvent] = asyncio.Queue()
            self._active_queue = queue
            try:
                yield GenerationEvent(type="queued")

                try:
                    await self._imagine_cmd(prompt=prompt, channel=self._channel)
                except Exception as e:
                    logger.exception("MJ /imagine invocation failed")
                    yield GenerationEvent(
                        type="error", message=f"slash command failed: {e}"
                    )
                    return

                while True:
                    try:
                        event = await asyncio.wait_for(
                            queue.get(), timeout=self._timeout
                        )
                    except asyncio.TimeoutError:
                        yield GenerationEvent(
                            type="error",
                            message=(
                                f"timed out after {int(self._timeout)}s "
                                "waiting for Midjourney"
                            ),
                        )
                        return
                    yield event
                    if event.type in ("done", "error"):
                        return
            finally:
                self._active_queue = None
