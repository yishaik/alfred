"""Per-route Telegram delivery: one queue, one sender task.

Everything a session wants to show the user flows through here so ordering is
preserved: batched text lines, streaming drafts (one message edited in place),
files, question/permission keyboards. Sends are throttled per chat and HTML
formatting falls back to plain text if Telegram rejects the entities.
"""

import asyncio
import logging
import os
import re
import tempfile
import time

from telegram import InlineKeyboardMarkup
from telegram.error import BadRequest, NetworkError, RetryAfter, TimedOut

from . import metrics
from .config import (BATCH_WINDOW, EDIT_MIN_INTERVAL, FILE_THRESHOLD, IMG_EXTS,
                     SEND_MIN_INTERVAL, TG_MAX, TMP_DIR)
from .fmt import md_to_html, split_msg

log = logging.getLogger("bridge.outbox")

_DRAFT_CHUNK = 3800  # finalize the draft and roll a new one past this size

_URL_RE = re.compile(r"https?://[^\s<>)\"]+")


def _wants_preview(text: str) -> bool:
    """Show a link preview only for a short message built around a single URL,
    so a normal reply that happens to mention a link doesn't sprout a big card."""
    urls = _URL_RE.findall(text or "")
    return len(urls) == 1 and len(text) <= 400


class Outbox:
    def __init__(self, bot, chat_id: int, thread_id: int | None = None,
                 prefix_fn=None):
        self.bot = bot
        self.chat_id = chat_id
        self.thread_id = thread_id
        self.prefix_fn = prefix_fn or (lambda: "")
        self.queue: asyncio.Queue = asyncio.Queue()
        self._task: asyncio.Task | None = None
        self._last_send = 0.0
        # streaming draft state (only touched from the sender task)
        self._draft_id: int | None = None
        self._draft_buf = ""
        self._draft_sent = ""      # what's currently rendered in the draft msg
        self._last_edit = 0.0
        self.stream_seen = False   # session checks this to avoid double-printing
        self._page_store: dict[int, list[str]] = {}
        self._page_counter: int = 0
        # mute (#19): when True, every producer is a no-op so the session keeps
        # running and holding its context but sends nothing to this route.
        self.muted = False

    def start(self):
        if self._task is None or self._task.done():
            self._task = asyncio.create_task(self._run())

    async def stop(self, drain: bool = True):
        if not self._task:
            return
        if drain and not self._task.done():
            # let queued items go out (incl. the batch window flush) before
            # killing the sender — a stop must not eat pending messages
            deadline = time.monotonic() + 5.0
            while not self.queue.empty() and time.monotonic() < deadline:
                await asyncio.sleep(0.1)
            await asyncio.sleep(BATCH_WINDOW + 0.2)
        self._task.cancel()
        try:
            await self._task          # don't let the loop GC a pending task
        except (asyncio.CancelledError, Exception):
            pass
        self._task = None

    # -- producers (sync, callable from anywhere) --------------------------- #
    def emit(self, text: str):
        if text and not self.muted:
            self.queue.put_nowait(("text", text))

    def file(self, path: str):
        if not self.muted:
            self.queue.put_nowait(("file", path))

    def voice(self, path: str, as_voice: bool = True):
        """Send synthesized speech (temp file is deleted after sending)."""
        if self.muted:
            return
        self.queue.put_nowait(("vc", path, as_voice))

    def keyboard(self, text: str, markup: InlineKeyboardMarkup, on_sent=None):
        """Send text with an inline keyboard; on_sent(message) is awaited after."""
        if not self.muted:
            self.queue.put_nowait(("kb", text, markup, on_sent))

    def stream_delta(self, text: str):
        if self.muted:
            return
        self.stream_seen = True
        self.queue.put_nowait(("sd", text))

    def stream_close(self, final_clean_md: str | None, markup=None):
        """Finalize the current draft. final_clean_md replaces the draft tail
        (markers stripped); None keeps whatever streamed."""
        if not self.muted:
            self.queue.put_nowait(("sc", final_clean_md, markup))

    # -- sender loop --------------------------------------------------------- #
    async def _run(self):
        pending: list[str] = []
        while True:
            try:
                item = await asyncio.wait_for(
                    self.queue.get(), timeout=BATCH_WINDOW if pending else None)
            except asyncio.TimeoutError:
                await self._flush(pending)
                continue
            kind = item[0]
            if kind == "text":
                pending.append(item[1])
                if sum(len(x) for x in pending) > 3000:
                    await self._flush(pending)
            elif kind == "sd":
                await self._flush(pending)
                await self._on_delta(item[1])
            elif kind == "sc":
                await self._flush(pending)
                await self._on_close(item[1], item[2])
            else:
                await self._flush(pending)
                if kind == "file":
                    await self._send_file(item[1])
                elif kind == "vc":
                    await self._send_voice(item[1], item[2])
                elif kind == "kb":
                    msg = await self._safe_send(item[1][:TG_MAX],
                                                reply_markup=item[2], html=True)
                    if item[3] and msg:
                        try:
                            await item[3](msg)
                        except Exception:
                            log.exception("on_sent callback failed")

    async def _flush(self, pending: list[str]):
        if not pending:
            return
        text = "\n".join(t for t in pending if t)
        pending.clear()
        prefix = self.prefix_fn()
        if prefix:
            text = prefix + text
        if len(text) > FILE_THRESHOLD:
            await self._send_paginated(text)
            return
        disable_preview = not _wants_preview(text)
        for part in split_msg(text):
            await self._safe_send(part, html=True, disable_preview=disable_preview)

    async def _send_paginated(self, text: str):
        """Split large text into pages with Next buttons instead of sending as file."""
        page_size = 3500
        pages: list[str] = []
        remaining = text
        while remaining:
            if len(remaining) <= page_size:
                pages.append(remaining)
                break
            cut = remaining.rfind("\n", 0, page_size)
            if cut <= 0:
                cut = page_size
            pages.append(remaining[:cut])
            remaining = remaining[cut:].lstrip("\n")

        n = len(pages)
        self._page_counter += 1
        pid = self._page_counter
        self._page_store[pid] = pages
        # Keep at most 30 page sets
        while len(self._page_store) > 30:
            self._page_store.pop(min(self._page_store), None)

        nav = f"\n\n`[1/{n}]`" if n > 1 else ""
        first = pages[0] + nav
        if n > 1:
            from telegram import InlineKeyboardButton, InlineKeyboardMarkup
            tid = self.thread_id or 0
            kb = InlineKeyboardMarkup([[
                InlineKeyboardButton(
                    f"▶ More  2/{n}",
                    callback_data=f"pgc:{self.chat_id}:{tid}:{pid}:1")
            ]])
            await self._safe_send(first, reply_markup=kb, html=True)
        else:
            await self._safe_send(first, html=True)

    # -- streaming draft ------------------------------------------------------ #
    async def _on_delta(self, text: str):
        self._draft_buf += text
        if len(self._draft_buf) > _DRAFT_CHUNK:
            cut = self._draft_buf.rfind("\n", 0, _DRAFT_CHUNK) or _DRAFT_CHUNK
            if cut <= 0:
                cut = _DRAFT_CHUNK
            chunk, rest = self._draft_buf[:cut], self._draft_buf[cut:].lstrip("\n")
            await self._edit_draft(chunk, final=True)
            self._draft_id = None
            self._draft_buf = rest
            self._draft_sent = ""
        now = time.monotonic()
        if now - self._last_edit >= EDIT_MIN_INTERVAL and self._draft_buf.strip():
            await self._edit_draft(self._draft_buf + " ▌")

    async def _on_close(self, final_clean_md: str | None, markup):
        text = final_clean_md if final_clean_md is not None else self._draft_buf
        if not text.strip() and markup is not None:
            text = "➡️"
        if text.strip():
            await self._edit_draft(text, final=True, markup=markup)
        elif self._draft_id:
            # streamed only markers/whitespace: remove the placeholder
            try:
                await self.bot.delete_message(self.chat_id, self._draft_id)
            except Exception:
                pass
        self._draft_id = None
        self._draft_buf = ""
        self._draft_sent = ""

    async def _edit_draft(self, text: str, final: bool = False, markup=None):
        text = text[:TG_MAX]
        # only the finalized reply may show a link preview; live edits never do
        # (a preview that pops in/out mid-stream is jarring).
        disable_preview = not (final and _wants_preview(text))
        if self._draft_id is None:
            msg = await self._safe_send(text if not final else None,
                                        html_text=md_to_html(text) if final else None,
                                        reply_markup=markup,
                                        disable_preview=disable_preview)
            if msg:
                self._draft_id = msg.message_id
                self._draft_sent = text
            return
        if text == self._draft_sent and markup is None:
            return
        try:
            if final:
                try:
                    await self.bot.edit_message_text(
                        md_to_html(text), self.chat_id, self._draft_id,
                        parse_mode="HTML", disable_web_page_preview=disable_preview,
                        reply_markup=markup)
                except BadRequest:
                    await self.bot.edit_message_text(
                        text, self.chat_id, self._draft_id,
                        disable_web_page_preview=disable_preview, reply_markup=markup)
            else:
                await self.bot.edit_message_text(
                    text, self.chat_id, self._draft_id,
                    disable_web_page_preview=True)
            self._draft_sent = text
            self._last_edit = time.monotonic()
        except RetryAfter as e:
            self._last_edit = time.monotonic() + e.retry_after
        except BadRequest as e:
            if "not modified" not in str(e).lower():
                log.warning("draft edit failed: %s", e)
        except (TimedOut, NetworkError):
            pass

    # -- low-level sends ------------------------------------------------------ #
    async def _throttle(self):
        wait = SEND_MIN_INTERVAL - (time.monotonic() - self._last_send)
        if wait > 0:
            await asyncio.sleep(wait)
        self._last_send = time.monotonic()

    async def _safe_send(self, text: str | None, reply_markup=None,
                         html: bool = False, html_text: str | None = None,
                         disable_preview: bool = True):
        raw = text if text is not None else ""
        for attempt in range(4):
            await self._throttle()
            try:
                if html_text is not None or html:
                    try:
                        return await self.bot.send_message(
                            self.chat_id, html_text or md_to_html(raw),
                            parse_mode="HTML", reply_markup=reply_markup,
                            disable_web_page_preview=disable_preview,
                            message_thread_id=self.thread_id)
                    except BadRequest:
                        pass  # fall through to plain
                return await self.bot.send_message(
                    self.chat_id, raw, reply_markup=reply_markup,
                    disable_web_page_preview=disable_preview,
                    message_thread_id=self.thread_id)
            except RetryAfter as e:
                await asyncio.sleep(e.retry_after + 0.5)
            except (TimedOut, NetworkError):
                await asyncio.sleep(1 + attempt)
            except Exception:
                if attempt == 3:
                    break
                await asyncio.sleep(0.5)
        metrics.bump("outbox_drop")
        log.error("dropped message after retries: %.300s", raw)
        return None

    async def _send_as_file(self, text: str, name: str):
        try:
            fd, tmp = tempfile.mkstemp(suffix=".md", prefix="reply_",
                                       dir=str(TMP_DIR))
            with os.fdopen(fd, "w", encoding="utf-8") as fh:
                fh.write(text)
            with open(tmp, "rb") as fh:
                await self._throttle()
                await self.bot.send_document(
                    self.chat_id, fh, filename=name,
                    caption=text[:200].replace("\n", " "),
                    message_thread_id=self.thread_id)
            os.unlink(tmp)
        except Exception as e:
            log.error("send-as-file failed: %s", e)
            for part in split_msg(text):
                await self._safe_send(part)

    async def _send_voice(self, path: str, as_voice: bool):
        try:
            await self._throttle()
            with open(path, "rb") as fh:
                if as_voice:
                    await self.bot.send_voice(self.chat_id, fh,
                                              message_thread_id=self.thread_id)
                else:
                    await self.bot.send_audio(self.chat_id, fh, title="reply",
                                              message_thread_id=self.thread_id)
        except Exception as e:
            log.warning("voice send failed: %s", e)
        finally:
            try:
                os.unlink(path)
            except OSError:
                pass

    async def _send_file(self, path: str):
        path = path.strip().strip('"')
        if not os.path.isfile(path):
            self.emit(f"⚠️ can't send (not found): {path}")
            return
        ext = os.path.splitext(path)[1].lower()
        name = os.path.basename(path)
        for attempt in range(3):
            try:
                await self._throttle()
                with open(path, "rb") as fh:
                    if ext in IMG_EXTS:
                        await self.bot.send_photo(
                            self.chat_id, fh, caption=name,
                            message_thread_id=self.thread_id)
                    else:
                        await self.bot.send_document(
                            self.chat_id, fh, filename=name,
                            message_thread_id=self.thread_id)
                return
            except RetryAfter as e:
                await asyncio.sleep(e.retry_after + 0.5)
            except Exception as e:
                if attempt == 2:
                    self.emit(f"⚠️ failed to send {name}: {e}")
                    return
                await asyncio.sleep(1)
