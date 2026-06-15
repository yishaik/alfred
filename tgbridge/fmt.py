"""Text formatting: markdown -> Telegram HTML, splitting, tool summaries.

The renderer targets Telegram's HTML subset and aims for a "rich & decorative"
look: language-tagged code blocks (Telegram colorizes them), markdown tables
rendered as aligned monospace, expandable blockquotes for long quotes, spoilers
and underline, plus underlined headers and horizontal-rule dividers.
"""

import html
import json
import re

from .config import TG_MAX

SEP = "──────────────"  # decorative divider (used by session footers too)

_FENCE_RE = re.compile(r"```(\w*)\n?(.*?)```", re.DOTALL)
_INLINE_CODE_RE = re.compile(r"`([^`\n]+)`")
_BOLD_RE = re.compile(r"\*\*(.+?)\*\*")
# __bold__: double underscore, not glued to surrounding word chars.
_BOLD_US_RE = re.compile(r"(?<![\w_])__(\S(?:.*?\S)?)__(?![\w_])")
_STRIKE_RE = re.compile(r"~~(.+?)~~")
# ||spoiler||
_SPOILER_RE = re.compile(r"\|\|(.+?)\|\|", re.DOTALL)
# *italic*: no space just inside the markers (so "a * b *" stays literal) and
# not glued to a word char or another * (which would be bold).
_ITALIC_RE = re.compile(r"(?<![\w*])\*(?!\s)([^*\n]+?)(?<!\s)\*(?![\w*])")
# _italic_: single underscore, word-boundaried so snake_case is left alone.
_ITALIC_US_RE = re.compile(r"(?<![\w_])_(?!\s)([^_\n]+?)(?<!\s)_(?![\w_])")
_LINK_RE = re.compile(r"\[([^\]]+)\]\((https?://[^)\s]+)\)")
# Headers: capture level so we can style H1/H2 with an underline.
_HEADER_RE = re.compile(r"^(#{1,6})\s+(.+?)\s*#*$", re.MULTILINE)
_BULLET_RE = re.compile(r"^(\s*)[-*+]\s+", re.MULTILINE)
# Horizontal rule: a line of only ---, ***, ___ (3+). Run AFTER escaping and
# table extraction, BEFORE bold/italic so the markers aren't half-eaten.
_HR_RE = re.compile(r"^[ \t]*([-*_])\1{2,}[ \t]*$", re.MULTILINE)
# Runs of consecutive "> " quoted lines (after html.escape turns > into &gt;).
_QUOTE_RE = re.compile(r"(?:^&gt;[ ]?.*(?:\n|$))+", re.MULTILINE)

# A markdown table: a header row with pipes, a |---|:--:|--- separator, then
# zero or more body rows. Leading/trailing pipes optional.
_TABLE_RE = re.compile(
    r"(?:^[ \t]*\|?.*\|.*\n)"          # header (has a pipe)
    r"(?:^[ \t]*\|?[ \t:|-]*-[ \t:|-]*\n)"  # separator (dashes + optional :|)
    r"(?:^[ \t]*\|?.*\|.*\n?)*",       # body rows
    re.MULTILINE)

_EXPAND_QUOTE_LINES = 4   # quotes longer than this collapse behind a tap
_EXPAND_QUOTE_CHARS = 320


def _split_row(line: str) -> list[str]:
    line = line.strip()
    if line.startswith("|"):
        line = line[1:]
    if line.endswith("|"):
        line = line[:-1]
    return [c.strip() for c in line.split("|")]


def _render_table(block: str) -> str:
    rows = [ln for ln in block.splitlines() if ln.strip()]
    if len(rows) < 2:
        return block
    header = _split_row(rows[0])
    aligns = []
    for spec in _split_row(rows[1]):
        s = spec.strip()
        if s.startswith(":") and s.endswith(":"):
            aligns.append("c")
        elif s.endswith(":"):
            aligns.append("r")
        else:
            aligns.append("l")
    body = [_split_row(r) for r in rows[2:]]
    ncol = max([len(header)] + [len(r) for r in body] + [len(aligns)])

    def pad(cells):
        return cells + [""] * (ncol - len(cells))
    header = pad(header)
    body = [pad(r) for r in body]
    aligns = (aligns + ["l"] * ncol)[:ncol]
    widths = [len(header[i]) for i in range(ncol)]
    for r in body:
        for i in range(ncol):
            widths[i] = max(widths[i], len(r[i]))

    def fmt_cell(text, i):
        w = widths[i]
        if aligns[i] == "r":
            return text.rjust(w)
        if aligns[i] == "c":
            return text.center(w)
        return text.ljust(w)

    def fmt_line(cells):
        return " │ ".join(fmt_cell(cells[i], i) for i in range(ncol))

    sep = "─┼─".join("─" * widths[i] for i in range(ncol))
    lines = [fmt_line(header), sep] + [fmt_line(r) for r in body]
    return "<pre>" + html.escape("\n".join(lines)) + "</pre>"


def md_to_html(text: str) -> str:
    """Best-effort markdown -> Telegram HTML. Raises nothing; on weird input the
    output may just look plain. Callers must fall back to plain text if Telegram
    rejects the entities."""
    placeholders: list[str] = []

    def stash(content: str) -> str:
        placeholders.append(content)
        return f"\x00{len(placeholders) - 1}\x00"

    # Pull code out first so we never style inside it. Language-tagged fences
    # become <pre><code class="language-xx"> so Telegram syntax-highlights them.
    def _fence(m: re.Match) -> str:
        lang = (m.group(1) or "").strip().lower()
        code = html.escape(m.group(2).rstrip())
        if lang:
            return stash(f'<pre><code class="language-{lang}">{code}</code></pre>')
        return stash(f"<pre>{code}</pre>")
    text = _FENCE_RE.sub(_fence, text)

    # Tables -> aligned monospace (before inline code so cell pipes survive).
    text = _TABLE_RE.sub(lambda m: stash(_render_table(m.group(0))), text)

    text = _INLINE_CODE_RE.sub(
        lambda m: stash(f"<code>{html.escape(m.group(1))}</code>"), text)

    text = html.escape(text)

    text = _HR_RE.sub(SEP, text)
    text = _LINK_RE.sub(r'<a href="\2">\1</a>', text)

    def _header(m: re.Match) -> str:
        level, body = len(m.group(1)), m.group(2)
        if level <= 2:                      # H1/H2: bold + underline
            return f"<b><u>{body}</u></b>"
        return f"<b>{body}</b>"             # H3+: bold
    text = _HEADER_RE.sub(_header, text)

    # Bold before italic so ** / __ win over their single-char counterparts.
    text = _BOLD_RE.sub(r"<b>\1</b>", text)
    text = _BOLD_US_RE.sub(r"<b>\1</b>", text)
    text = _STRIKE_RE.sub(r"<s>\1</s>", text)
    text = _SPOILER_RE.sub(r"<tg-spoiler>\1</tg-spoiler>", text)
    text = _ITALIC_RE.sub(r"<i>\1</i>", text)
    text = _ITALIC_US_RE.sub(r"<i>\1</i>", text)
    text = _BULLET_RE.sub(r"\1• ", text)
    text = _QUOTE_RE.sub(_blockquote, text)

    def unstash(m: re.Match) -> str:
        return placeholders[int(m.group(1))]

    return re.sub(r"\x00(\d+)\x00", unstash, text)


def _blockquote(m: re.Match) -> str:
    lines = m.group(0).rstrip("\n").split("\n")
    inner = "\n".join(re.sub(r"^&gt;[ ]?", "", ln) for ln in lines)
    # Long quotes collapse behind a tap so the chat stays tidy.
    if len(lines) > _EXPAND_QUOTE_LINES or len(inner) > _EXPAND_QUOTE_CHARS:
        return f"<blockquote expandable>{inner}</blockquote>\n"
    return f"<blockquote>{inner}</blockquote>\n"


def split_msg(text: str, limit: int = TG_MAX) -> list[str]:
    """Split long text on line boundaries where possible."""
    out = []
    while len(text) > limit:
        cut = text.rfind("\n", 0, limit)
        if cut <= 0:
            cut = limit
        out.append(text[:cut])
        text = text[cut:].lstrip("\n")
    if text:
        out.append(text)
    return out


# A distinct glyph per tool so the activity feed reads at a glance.
_TOOL_ICONS = {
    "Read": "📖", "Edit": "✏️", "MultiEdit": "✏️", "Write": "📝",
    "NotebookEdit": "📓", "Bash": "💻", "PowerShell": "💻",
    "Glob": "🔍", "Grep": "🔍", "WebFetch": "🌐", "WebSearch": "🌐",
    "Task": "🧠", "Agent": "🧠", "Skill": "⚡", "TodoWrite": "📋",
    "BashOutput": "📤", "KillShell": "🛑",
}

_TODO_MARK = {"completed": "☑", "in_progress": "🔄", "pending": "☐"}


def tool_icon(name: str) -> str:
    return _TOOL_ICONS.get(name, "🔧")


def summarize_tool(name: str, inp: dict) -> str:
    try:
        if name in ("Bash", "PowerShell"):
            return (inp.get("command") or "")[:400]
        if name in ("Read", "Edit", "Write", "NotebookEdit"):
            return inp.get("file_path") or inp.get("notebook_path") or ""
        if name in ("Glob", "Grep"):
            return inp.get("pattern", "")
        if name == "WebFetch":
            return inp.get("url", "")
        if name == "WebSearch":
            return inp.get("query", "")
        if name in ("Task", "Agent"):
            return inp.get("description", "")
        if name == "Skill":
            return inp.get("command") or inp.get("skill") or ""
        if name == "TodoWrite":
            return _todo_checklist(inp.get("todos", []))
        return json.dumps(inp, ensure_ascii=False)[:300]
    except Exception:
        return ""


def _todo_checklist(todos: list, limit: int = 14) -> str:
    """Render a TodoWrite payload as a checklist on its own lines (markdown:
    the caller re-renders it through md_to_html, so emit ** not <b>)."""
    if not todos:
        return ""
    lines = []
    for t in todos[:limit]:
        mark = _TODO_MARK.get(t.get("status", "pending"), "☐")
        content = (t.get("content") or "").strip()[:70]
        if t.get("status") == "in_progress":
            content = f"**{content}**"
        lines.append(f"{mark} {content}")
    if len(todos) > limit:
        lines.append(f"… +{len(todos) - limit} more")
    # leading newline so it sits under the "📋 TodoWrite" label
    return "\n" + "\n".join(lines)


def format_error(content: str, max_chars: int = 1500) -> str:
    """Tool-error text (markdown) as a colorized traceback or a collapsing
    quote — the caller re-renders it through md_to_html."""
    content = (content or "").strip()
    if len(content) > max_chars:
        content = content[:max_chars] + "\n… (truncated)"
    if "Traceback (most recent call last)" in content:
        return "⚠️ **tool error**\n```python\n" + content + "\n```"
    lines = content.splitlines() or [content]
    return "⚠️ **tool error**\n" + "\n".join("> " + ln for ln in lines)


def fmt_duration(seconds: float) -> str:
    if seconds < 90:
        return f"{seconds:.1f}s"
    m, s = divmod(int(seconds), 60)
    if m < 90:
        return f"{m}m{s:02d}s"
    h, m = divmod(m, 60)
    return f"{h}h{m:02d}m"
