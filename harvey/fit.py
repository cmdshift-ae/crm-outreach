"""Your company, and why a prospect fits it.

The company profile is skills/product_knowledge.md: training writes it, the
Writer and Handler read it before every email, and the dashboard's Settings
lets the owner edit it. A "fit" is Claude's short read of one prospect
business against that profile: what they do, why they might need you, and
the angle to open with. It's cached per company (settings key
FIT_PREFIX + company id), shown on each Outbox draft and given to the
Writer, and cleared whenever the profile changes.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone

from harvey.untrusted import UNTRUSTED_RULE, fence

FIT_PREFIX = "company_fit:"
PROFILE_SKILL = "product_knowledge"
PROFILE_MAX_CHARS = 20000
FIT_SCORES = ("strong", "possible", "weak")


def starter_profile(config) -> str:
    """A profile to start from when training never wrote one."""
    p = getattr(config, "product", None)
    persona = getattr(config, "persona", None)
    name = getattr(persona, "company", "") or getattr(p, "name", "") or "Your company"
    benefits = "\n".join(f"- {b}" for b in (getattr(p, "key_benefits", None) or [])) or "- "
    return f"""# {name}

## What we do
{getattr(p, "description", "") or "One or two plain sentences: what you make or do, and for whom."}

## Services
- 

## Mission
Why the company exists, in a sentence or two.

## Who we help
The kinds of businesses that get the most from you, and the problems they have when they need you.

## Key benefits
{benefits}

## Proof
Projects, clients you may name, results with numbers.

## Not a fit
Businesses you don't want Harvey to contact, and why.

## Pricing
{getattr(p, "pricing", "") or "How you price, if you're happy for Harvey to say."}
"""


def prospect_facts(context: dict) -> str:
    rows = [("Company", context.get("company")), ("Website", context.get("website")),
            ("What they say about themselves", context.get("description")),
            ("Contact", " · ".join(x for x in (context.get("contact"), context.get("contact_title")) if x)),
            ("Research notes", context.get("notes"))]
    lines = [f"- {k}: {v}" for k, v in rows if v]
    for k, v in (context.get("signals") or {}).items():
        lines.append(f"- {k}: {v}")
    return "\n".join(lines)


def build_fit_prompt(profile: str, context: dict) -> str:
    return f"""You help a small company decide whether a prospect business is worth emailing,
and how to approach them.

OUR COMPANY (written by its owner; this is who we are and what we sell):
{profile.strip()[:PROFILE_MAX_CHARS]}

THE PROSPECT (gathered automatically from their website and the web):
{fence(prospect_facts(context), "prospect research")}

{UNTRUSTED_RULE}

Write, in plain English, for the owner reading it in a review queue:
- summary: what this business does, in one or two sentences. Only what the facts say.
- fit: why they might need what we sell, in two or three sentences. Name the specific
  service of ours that fits and the reason in their business. If the facts are thin,
  say what is unknown instead of guessing.
- angle: the single best opening for a first email, in one line.
- score: "strong", "possible" or "weak".

Never invent facts about the prospect (projects, size, clients, numbers).

Return ONLY JSON: {{"summary": "...", "fit": "...", "angle": "...", "score": "..."}}"""


def parse_fit(result) -> dict | None:
    if not isinstance(result, dict):
        return None
    out = {k: " ".join(str(result.get(k) or "").split())[:700] for k in ("summary", "fit", "angle")}
    if not out["summary"] or not out["fit"]:
        return None
    score = str(result.get("score") or "").strip().lower()
    out["score"] = score if score in FIT_SCORES else "possible"
    out["at"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
    return out


async def cached_fit(state, company_key: str) -> dict | None:
    if not company_key:
        return None
    raw = await state.get_setting(FIT_PREFIX + company_key)
    try:
        fit = json.loads(raw) if raw else None
    except (json.JSONDecodeError, TypeError):
        return None
    return fit if isinstance(fit, dict) else None


async def save_fit(state, company_key: str, fit: dict):
    await state.set_setting(FIT_PREFIX + company_key, json.dumps(fit))


async def clear_fits(state) -> int:
    """The profile changed: every fit was judged against the old one."""
    async with state._connect() as db:
        cursor = await db.execute("DELETE FROM settings WHERE substr(key, 1, ?) = ?",
                                  (len(FIT_PREFIX), FIT_PREFIX))
        await db.commit()
        return cursor.rowcount


def fit_lines(fit: dict | None) -> list[str]:
    """A fit as Writer fact lines, labelled as Harvey's own read."""
    if not fit:
        return []
    return [f"- Harvey's own read of why they might need us (an analysis, not a fact to quote): "
            f"{fit.get('fit', '')} Suggested angle: {fit.get('angle', '')}"]
