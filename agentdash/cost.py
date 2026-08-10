"""
cost.py -- costing, from a published rate table or from the harness itself.

GitHub Copilot bills in AI credits; 1 credit = $0.01. Rates are transcribed from
the published table and are NEVER inferred. A model with no usable rate yields
None -- "no published rate" -- which is not the same as $0.00 and is rendered
differently. Partial rates are honoured and flagged so the UI can say the figure
is a floor rather than a total.

TWO BASES, NEVER BLENDED.

Not every harness needs a rate table. pi knows what its provider charged and
reports `pi.llm.cost.*_usd` per call; that figure is better than anything this
module could derive, so it is carried through as-is (basis
"harness_reported"). Copilot reports no cost at all, so it is priced from
rates.json (basis "published_rates").

The two must not cross, and the danger is concrete rather than theoretical:
rates.json prices `gpt-5.6-luna`, and pi also ran `gpt-5.6-luna` -- through a
different provider, at a different price, on a plan GitHub does not bill. Left
unguarded, every such pi turn would silently acquire GitHub's credit price and
the total would look entirely reasonable. `rates.json` therefore declares
`applies_to`, and a table is only consulted for a harness it names.
"""

import json
import sys

_TOK_FIELDS = [
    ("fresh", "credits_per_1m_input"),
    ("cache_read", "credits_per_1m_cache_read"),
    ("cache_creation", "credits_per_1m_cache_creation"),
    ("output", "credits_per_1m_output"),
]


def load_rates(path="rates.json"):
    try:
        return json.load(open(path))
    except FileNotFoundError:
        print(f"WARNING: {path} not found -- costs will show as 'no published rate'.",
              file=sys.stderr)
        return {"credit_usd": None, "models": {}}
    except json.JSONDecodeError as e:
        raise SystemExit(f"FATAL: {path} is not valid JSON: {e}")


def resolve_tier(entry, input_tokens):
    """Pick the context-size tier, or the flat entry if untiered.

    Grok 4.5 and GPT-5.6 Luna are priced in two bands split at 200K input
    tokens. The first tier whose max_input_tokens covers the turn wins; a null
    bound means no upper limit.
    """
    tiers = entry.get("tiers")
    if not tiers:
        return entry
    n = input_tokens or 0
    for t in tiers:
        lim = t.get("max_input_tokens")
        if lim is None or n <= lim:
            return t
    return tiers[-1]


def rates_apply_to(rates, harness):
    """May this rate table price this harness?

    A table that names `applies_to` is scoped to those harnesses and no others.
    A table without the key is treated as applying, so a hand-written rates.json
    from before the field existed keeps working unchanged.
    """
    scope = rates.get("applies_to")
    if scope is None:
        return True
    return harness in scope


def turn_credits(turn, rates):
    """(credits, status) where status is 'ok' | 'partial' | 'no_rate'."""
    entry = (rates.get("models") or {}).get(turn.get("model"))
    if not entry:
        return None, "no_rate"
    m = resolve_tier(entry, turn.get("input"))
    credits, used, missing = 0.0, 0, 0

    per_req = m.get("credits_per_request")
    if per_req is not None:
        credits += per_req
        used += 1

    for tkey, rkey in _TOK_FIELDS:
        rate = m.get(rkey)
        val = turn.get(tkey)
        if rate is None:
            if val:                       # only "missing" if there were tokens to bill
                missing += 1
            continue
        credits += (val or 0) / 1_000_000 * rate
        used += 1

    if used == 0:
        return None, "no_rate"
    return credits, ("partial" if missing else "ok")


def cost_block(turns, rates, harness=None):
    """Session-level roll-up plus a per-model breakdown.

    `usd` is the one comparable number across harnesses, and `basis` says where
    it came from -- read them together. Rate-table pricing wins when available
    because it is the billing the user is actually charged; the harness's own
    figure is used when no table covers this harness, and is carried alongside
    either way so the two can be cross-checked rather than conflated.
    """
    table_applies = rates_apply_to(rates, harness)
    usd_per = rates.get("credit_usd")
    per_model, total, status = {}, 0.0, "ok"
    priced = unpriced = 0
    harness_total, harness_turns = 0.0, 0

    for t in turns:
        c, st = (turn_credits(t, rates) if table_applies else (None, "out_of_scope"))
        mo = t.get("model") or "(unknown)"
        row = per_model.setdefault(mo, {"model": mo, "turns": 0, "credits": None,
                                        "input": 0, "output": 0, "status": st,
                                        "reported_usd": None})
        row["turns"] += 1
        row["input"] += t.get("input") or 0
        row["output"] += t.get("output") or 0

        rep = t.get("reported_usd")
        if rep is not None:
            row["reported_usd"] = (row["reported_usd"] or 0) + rep
            harness_total += rep
            harness_turns += 1

        if c is None:
            row["status"] = st
            unpriced += 1
            status = "partial"
            continue
        row["credits"] = (row["credits"] or 0) + c
        if st == "partial" and row["status"] == "ok":
            row["status"] = "partial"
        total += c
        priced += 1

    for row in per_model.values():
        row["usd"] = (row["reported_usd"] if not priced and row["reported_usd"] is not None
                      else None if (row["credits"] is None or usd_per is None)
                      else row["credits"] * usd_per)

    if priced == 0:
        status = "harness_reported" if harness_turns else (
            "out_of_scope" if not table_applies else "no_rate")

    rate_usd = (total * usd_per) if (priced and usd_per is not None) else None
    basis = ("published_rates" if rate_usd is not None
             else "harness_reported" if harness_turns else None)
    return {
        "credits": total if priced else None,
        "usd": rate_usd if rate_usd is not None else (
            harness_total if harness_turns else None),
        "basis": basis,
        "credit_usd": usd_per,
        "status": status,
        "priced_turns": priced,
        "unpriced_turns": unpriced,
        # The harness's own figure, always carried when reported -- so a
        # rate-priced harness that also self-reports can be cross-checked
        # instead of one number quietly replacing the other.
        "harness_usd": harness_total if harness_turns else None,
        "harness_priced_turns": harness_turns,
        "rates_in_scope": table_applies,
        "by_model": sorted(per_model.values(),
                           key=lambda r: -((r["credits"] or 0) or (r["reported_usd"] or 0))),
        "rates_source": rates.get("source"),
        "rates_retrieved": rates.get("retrieved"),
    }


def schema_waste_cost(unused_per_turn, defs_turns, turns, rates):
    """Bound the cost of never-invoked tool schemas.

    Reported as a RANGE, not a point: the schema is resident every turn, but
    only the first pays fresh-input rates and the rest is cached, and that cache
    split cannot be attributed to the schema portion specifically.
    """
    waste_tok = (unused_per_turn or 0) * (defs_turns or 0)
    if not waste_tok:
        return None
    lo_rates, hi_rates = [], []
    max_in = max((t.get("input") or 0) for t in turns) if turns else 0
    for mo in {t.get("model") for t in turns if t.get("model")}:
        entry = (rates.get("models") or {}).get(mo)
        if not entry:
            continue
        tier = resolve_tier(entry, max_in)
        cr, fr = tier.get("credits_per_1m_cache_read"), tier.get("credits_per_1m_input")
        if cr is not None:
            lo_rates.append(cr)
        if fr is not None:
            hi_rates.append(fr)
    if not (lo_rates and hi_rates):
        return None
    usd_per = rates.get("credit_usd")
    lo_c = waste_tok / 1_000_000 * min(lo_rates)
    hi_c = waste_tok / 1_000_000 * max(hi_rates)
    return {
        "tokens": waste_tok, "credits_low": lo_c, "credits_high": hi_c,
        "usd_low": None if usd_per is None else lo_c * usd_per,
        "usd_high": None if usd_per is None else hi_c * usd_per,
    }
