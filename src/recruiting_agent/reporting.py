"""Human-readable rendering of the JSON blobs the pipeline records.

Every pipeline stage persists its outcome as a JSON object (``AgentAction.result_json``,
``Run.stats_json``, ``AgentEvent.payload_json``). The dashboard used to print those
blobs verbatim. This module turns them into labelled facts instead, without dropping
anything: keys we know about get a curated label, unit, and ordering, and keys we do
not know about still render with a humanised label rather than disappearing.

It lives at the package root because both readers need the same vocabulary: the
dashboard renders these facts as HTML, and the coordinator writes them into the
candidate's search journal, which an agent later reads back as context.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

#: Curated presentation for the keys the pipeline actually writes.
#: ``key -> (label, unit, tone)``. ``tone`` drives colour: "" is neutral,
#: "positive" reads as progress, "negative" as something needing attention.
FACT_SPECS: dict[str, tuple[str, str, str]] = {
    # ingest
    "new": ("New roles", "count", "positive"),
    "updated": ("Updated", "count", ""),
    "deactivated": ("Closed", "count", ""),
    "unchanged": ("Unchanged", "count", ""),
    "companies": ("Companies", "count", ""),
    "errors": ("Source errors", "count", "negative"),
    "fetched": ("Fetched", "count", ""),
    # prefilter + scoring
    "prefiltered": ("Screened", "count", ""),
    "plausible": ("Passed screen", "count", "positive"),
    "rejected": ("Screened out", "count", ""),
    "scored": ("Evaluated", "count", "positive"),
    "evaluated": ("Evaluated", "count", "positive"),
    "score_errors": ("Failed evaluations", "count", "negative"),
    "skipped": ("Skipped", "count", ""),
    # discovery + scouting
    "proposed": ("Companies proposed", "count", "positive"),
    "companies_scouted": ("Sites scouted", "count", ""),
    "roles_found": ("Roles found", "count", "positive"),
    "candidates": ("Candidates", "count", ""),
    # memory
    "events": ("Events folded in", "count", ""),
    "last_event_id": ("Through event", "id", ""),
    "revisions": ("Revisions proposed", "count", ""),
    # cost accounting
    "cost_usd": ("Cost", "money", ""),
    "llm_calls": ("Model calls", "count", ""),
    "input_tokens": ("Input tokens", "count", ""),
    "output_tokens": ("Output tokens", "count", ""),
    "cached_tokens": ("Cached tokens", "count", "positive"),
    # misc
    "status": ("Status", "text", ""),
    "reason": ("Reason", "text", ""),
    "summary": ("Summary", "text", ""),
    "trigger": ("Trigger", "text", ""),
    "duration_seconds": ("Duration", "duration", ""),
}

#: Stable display order; anything unlisted sorts after these, alphabetically.
FACT_ORDER = list(FACT_SPECS)

#: Keys that describe *how* the run was configured rather than what it produced.
#: They are still shown, just after the outcome facts.
CONTEXT_KEYS = {"trigger", "status", "reason", "summary"}


@dataclass(frozen=True)
class Fact:
    label: str
    value: str
    tone: str = ""
    raw: Any = None

    @property
    def is_zero(self) -> bool:
        return self.raw in (0, 0.0)


def humanize_key(key: str) -> str:
    """`score_errors` -> `Score errors`, `cost_usd` -> `Cost usd`."""
    words = str(key).replace("-", " ").replace("_", " ").strip()
    return words[:1].upper() + words[1:] if words else str(key)


def humanize_enum(value: Any) -> str:
    """`onboarding_activation` -> `Onboarding activation`."""
    return humanize_key(getattr(value, "value", value))


def format_count(value: Any) -> str:
    try:
        return f"{int(value):,}"
    except (TypeError, ValueError):
        return str(value)


def format_money(value: Any) -> str:
    try:
        amount = float(value)
    except (TypeError, ValueError):
        return str(value)
    if amount == 0:
        return "$0.00"
    if amount < 0.01:
        return f"${amount:.4f}"
    return f"${amount:,.2f}"


def format_duration(seconds: Any) -> str:
    """Compact, always two components at most: `48s`, `3m 07s`, `2h 14m`."""
    try:
        total = max(0, int(float(seconds)))
    except (TypeError, ValueError):
        return str(seconds)
    if total < 60:
        return f"{total}s"
    minutes, secs = divmod(total, 60)
    if minutes < 60:
        return f"{minutes}m {secs:02d}s"
    hours, minutes = divmod(minutes, 60)
    return f"{hours}h {minutes:02d}m"


def elapsed_seconds(start: datetime | None, end: datetime | None = None) -> int:
    """Seconds between two timestamps, tolerating naive/aware mismatches."""
    if start is None:
        return 0
    finish = end or datetime.now(timezone.utc)
    if start.tzinfo is None and finish.tzinfo is not None:
        finish = finish.replace(tzinfo=None)
    elif start.tzinfo is not None and finish.tzinfo is None:
        finish = finish.replace(tzinfo=timezone.utc)
    return max(0, int((finish - start).total_seconds()))


def format_scalar(value: Any) -> str:
    """Last-resort formatting for a value we have no spec for."""
    if value is None or value == "":
        return "—"
    if isinstance(value, bool):
        return "Yes" if value else "No"
    if isinstance(value, int):
        return f"{value:,}"
    if isinstance(value, float):
        return f"{value:,.2f}" if abs(value) >= 0.01 else f"{value:.4f}"
    if isinstance(value, (list, tuple)):
        if not value:
            return "None"
        if all(isinstance(item, (str, int, float, bool)) for item in value):
            head = ", ".join(str(item) for item in value[:4])
            return head + (f" +{len(value) - 4} more" if len(value) > 4 else "")
        return f"{len(value):,} items"
    if isinstance(value, dict):
        if not value:
            return "None"
        return ", ".join(f"{humanize_key(k)} {format_scalar(v)}" for k, v in list(value.items())[:3])
    text = str(value)
    return humanize_key(text) if text.islower() and "_" in text else text


def format_fact(key: str, value: Any) -> Fact:
    label, unit, tone = FACT_SPECS.get(key, (humanize_key(key), "auto", ""))
    if unit == "count":
        text = format_count(value)
    elif unit == "money":
        text = format_money(value)
    elif unit == "duration":
        text = format_duration(value)
    elif unit == "id":
        text = f"#{value}"
    elif unit == "text":
        text = format_scalar(value)
        tone = ""
    else:
        text = format_scalar(value)
    # A tone only earns its colour when the number is actually non-zero.
    if tone and value in (0, 0.0, None, "", False):
        tone = ""
    return Fact(label=label, value=text, tone=tone, raw=value)


def parse_payload(raw: str | None) -> dict[str, Any]:
    """Decode a stored JSON blob into a dict, never raising."""
    if not raw:
        return {}
    if isinstance(raw, dict):
        return raw
    try:
        value = json.loads(raw)
    except (TypeError, ValueError):
        return {"result": raw}
    if isinstance(value, dict):
        return value
    if value in (None, [], ""):
        return {}
    return {"result": value}


def payload_facts(payload: dict[str, Any], *, skip: set[str] | None = None) -> list[Fact]:
    """Every key in the payload as an ordered, labelled fact.

    A nested dict of scalars is flattened one level and its label qualified with the
    parent key, so ``{"totals": {"scored": 25}}`` reads as "Totals · Evaluated 25"
    rather than losing either half. Deeper nesting falls back to ``format_scalar``.
    """
    skip = skip or set()
    entries: list[tuple[str, str, Any]] = []  # (sort key, label prefix, value)
    for key, value in payload.items():
        if key in skip:
            continue
        scalar_dict = (
            isinstance(value, dict)
            and value
            and all(not isinstance(inner, (dict, list)) for inner in value.values())
        )
        if scalar_dict:
            for inner_key, inner_value in value.items():
                entries.append((inner_key, humanize_key(key), inner_value))
        else:
            entries.append((key, "", value))

    def sort_key(entry: tuple[str, str, Any]) -> tuple[int, int, str]:
        key = entry[0]
        context = 1 if key in CONTEXT_KEYS else 0
        try:
            return (context, FACT_ORDER.index(key), key)
        except ValueError:
            return (context, len(FACT_ORDER), key)

    facts = []
    for key, prefix, value in sorted(entries, key=sort_key):
        fact = format_fact(key, value)
        if prefix:
            fact = Fact(label=f"{prefix} · {fact.label}", value=fact.value, tone=fact.tone, raw=fact.raw)
        facts.append(fact)
    return facts


def outcome_facts(payload: dict[str, Any], *, limit: int = 6) -> list[Fact]:
    """The facts worth putting on a summary row: non-zero outcomes first."""
    facts = payload_facts(payload)
    ranked = [fact for fact in facts if not fact.is_zero] or facts
    return ranked[:limit]


#: The pipeline stages a coordinator cycle walks through, in order.
#: ``kind -> (title, what it does, what a finished one produced)``
ACTION_KINDS: dict[str, tuple[str, str]] = {
    "probe": ("Verify sources", "Check which career systems can be trusted."),
    "ingest": ("Collect openings", "Pull current roles from approved companies."),
    "match": ("Evaluate roles", "Apply the active search policy and evidence rubric."),
    "scout": ("Scout gaps", "Inspect unsupported career sites for missing coverage."),
    "discover": ("Propose companies", "Look for companies the search universe is missing."),
    "consolidate_memory": ("Update memory", "Turn durable observations into a reviewable revision."),
    "digest": ("Prepare briefing", "Summarize what changed and what needs attention."),
}


def action_title(kind: str) -> tuple[str, str]:
    return ACTION_KINDS.get(kind, (humanize_key(kind), "A recorded agent action."))


def summarize_action(
    kind: str,
    status: str,
    payload: dict,
    error: str | None = None,
    context: dict | None = None,
) -> str:
    """One sentence describing what an action did — never a JSON fragment.

    ``context`` carries cross-action facts the payload alone cannot supply, such as
    how many roles the preceding ingest collected, so a running stage can say what
    it is working through instead of restating its own description.
    """
    title, detail = action_title(kind)
    context = context or {}
    if status == "running":
        collected = int(context.get("collected_roles") or 0)
        if kind == "match" and collected:
            return f"Evaluating {format_count(collected)} newly collected roles"
        return f"{detail.rstrip('.')} now"
    if status == "failed":
        return error or "The action stopped before completing."
    if status == "skipped":
        return payload.get("reason") or "Not due this cycle."
    if not payload:
        return detail
    if kind == "ingest":
        parts = [f"{format_count(payload.get('new', 0))} new"]
        if payload.get("updated"):
            parts.append(f"{format_count(payload['updated'])} updated")
        if payload.get("deactivated"):
            parts.append(f"{format_count(payload['deactivated'])} closed")
        summary = ", ".join(parts) + f" across {format_count(payload.get('companies', 0))} companies"
        if payload.get("errors"):
            count = int(payload["errors"])
            summary += f" · {format_count(count)} source error{'s' if count != 1 else ''}"
        return summary
    if kind == "match":
        scored = payload.get("scored") or payload.get("evaluated") or 0
        screened = payload.get("prefiltered") or 0
        if scored or screened:
            summary = f"{format_count(scored)} roles evaluated"
            if screened:
                summary += f" of {format_count(screened)} screened"
            if payload.get("cost_usd"):
                summary += f" · {format_money(payload['cost_usd'])}"
            return summary
        return "Nothing new to evaluate"
    if kind == "probe":
        recorded = payload.get("status")
        return humanize_key(recorded) if recorded else "Career sources verified and ready for collection"
    if kind == "discover":
        proposed = payload.get("proposed") or payload.get("candidates") or 0
        return f"{format_count(proposed)} companies proposed for your approval" if proposed else "No new companies proposed"
    if kind == "scout":
        found = payload.get("roles_found") or 0
        sites = payload.get("companies_scouted") or 0
        if sites or found:
            return f"{format_count(found)} roles found across {format_count(sites)} unsupported sites"
        return "No coverage gaps found"
    if kind == "consolidate_memory":
        events = payload.get("events") or 0
        return f"{format_count(events)} events folded into the search journal" if events else "Nothing new to record"
    facts = outcome_facts(payload, limit=3)
    if facts:
        return " · ".join(f"{fact.value} {fact.label.lower()}" for fact in facts)
    return detail


# ---------------------------------------------------------------------------
# Markdown
# ---------------------------------------------------------------------------
# Job descriptions come from third-party ATS HTML by way of `markdownify`, so the
# renderer is configured with `html=False`: raw HTML in the source is escaped and
# shown as text rather than passed through. That makes the output safe by
# construction and avoids depending on a separate sanitiser. markdown-it also
# refuses `javascript:` and other unsafe link schemes by default.
_MARKDOWN = None


def render_markdown(text: str | None) -> str:
    """Markdown to HTML, with any embedded raw HTML neutralised."""
    global _MARKDOWN
    if not text:
        return ""
    if _MARKDOWN is None:
        from markdown_it import MarkdownIt

        _MARKDOWN = MarkdownIt("commonmark", {"html": False, "linkify": False})
    return _MARKDOWN.render(text)
