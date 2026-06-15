"""The 'soul' of an agent — a structured character sheet.

Replaces the old free-text `persona` string with a structured identity:
a display name, an avatar emoji, a role, a tone, guiding values, and speech
quirks. It renders two ways:

  * render_prompt() — a system-prompt block injected into every turn so the
    agent actually *behaves* in character.
  * render_card()   — a human-readable card shown by the /soul command.

This is the foundation the rest of the "Soul" epic builds on:
  * the mood system (issue #2) shifts tone on top of this sheet
  * persona display (#4) uses `emoji` + `display_name` in message prefixes
  * proactive commentary (#5) speaks in this voice

Backward compatible: an agent saved with the old `persona` string is migrated
into `notes` on load, and a bare agent with nothing set renders no block at
all (the plain Claude Code persona).
"""

from dataclasses import dataclass, field


@dataclass
class Soul:
    display_name: str = ""      # what the agent calls itself, e.g. "Alfred"
    emoji: str = ""             # avatar shown in message prefixes, e.g. "🎩"
    role: str = ""              # "your devoted butler and engineering aide"
    tone: str = ""              # "dry wit, impeccably polite, economical"
    values: list = field(default_factory=list)   # guiding principles
    quirks: list = field(default_factory=list)    # speech / behaviour quirks
    notes: str = ""             # free-form extras (old persona lands here)

    # Fields the /soul editor can set, in display order.
    EDITABLE = ("display_name", "emoji", "role", "tone", "notes")
    LIST_FIELDS = ("values", "quirks")

    def is_set(self) -> bool:
        return any([self.display_name, self.emoji, self.role, self.tone,
                    self.values, self.quirks, self.notes])

    # -- persistence --------------------------------------------------------- #
    def to_dict(self) -> dict:
        return {"display_name": self.display_name, "emoji": self.emoji,
                "role": self.role, "tone": self.tone, "values": list(self.values),
                "quirks": list(self.quirks), "notes": self.notes}

    @classmethod
    def from_dict(cls, d: dict | None) -> "Soul":
        d = d or {}
        return cls(
            display_name=d.get("display_name", ""),
            emoji=d.get("emoji", ""),
            role=d.get("role", ""),
            tone=d.get("tone", ""),
            values=list(d.get("values", [])),
            quirks=list(d.get("quirks", [])),
            notes=d.get("notes", ""),
        )

    # -- rendering ----------------------------------------------------------- #
    def render_prompt(self, mood: str = "") -> str:
        """A system-prompt block. `mood` (optional) is a one-line tone nudge
        layered on top by the mood system; empty for now."""
        if not self.is_set():
            return ""
        who = self.display_name or "this assistant"
        lines = [f"YOUR CHARACTER — you are {who}."
                 + (f" {self.emoji}" if self.emoji else "")]
        if self.role:
            lines.append(f"Role: {self.role}")
        if self.tone:
            lines.append(f"Tone: {self.tone}")
        if self.values:
            lines.append("You hold to: " + "; ".join(self.values) + ".")
        if self.quirks:
            lines.append("Speech quirks: " + "; ".join(self.quirks) + ".")
        if self.notes:
            lines.append(self.notes)
        if mood:
            lines.append(f"Right now: {mood}")
        lines.append("Stay in character, but never let it get in the way of "
                     "being correct, clear, and genuinely useful.")
        return "\n".join(lines)

    def render_card(self) -> str:
        """Human-readable card for /soul (Telegram-friendly)."""
        if not self.is_set():
            return ("🎭 no character set — this agent uses the plain assistant "
                    "voice.\nUse /soul preset alfred, or /soul set role <text>.")
        head = (self.emoji + " " if self.emoji else "") + \
               (self.display_name or "(unnamed)")
        out = [f"🎭 {head}"]
        if self.role:
            out.append(f"• role: {self.role}")
        if self.tone:
            out.append(f"• tone: {self.tone}")
        if self.values:
            out.append("• values: " + ", ".join(self.values))
        if self.quirks:
            out.append("• quirks: " + ", ".join(self.quirks))
        if self.notes:
            out.append(f"• notes: {self.notes}")
        return "\n".join(out)


# Ready-made characters. `alfred` is the bridge's namesake default.
PRESETS: dict[str, Soul] = {
    "alfred": Soul(
        display_name="Alfred",
        emoji="🎩",
        role="a devoted butler and engineering aide — calm, capable, "
             "always a step ahead",
        tone="dry wit, impeccably polite, economical with words",
        values=["discretion", "competence over flattery",
                "anticipate the need before it is voiced"],
        quirks=["addresses the user as 'sir' sparingly, never fawning",
                "understates rather than oversells"],
    ),
    "plain": Soul(),
}
