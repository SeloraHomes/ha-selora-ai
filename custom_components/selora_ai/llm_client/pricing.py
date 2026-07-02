"""Token → USD cost estimation for LLM calls.

The pricing table (``LLM_PRICING_USD_PER_MTOK``) lives in ``const.py`` as
plain data; the estimation logic lives here.
"""

from __future__ import annotations

from ..const import LLM_PRICING_USD_PER_MTOK


def estimate_llm_cost_usd(
    provider: str,
    model: str,
    input_tokens: int,
    output_tokens: int,
    overrides: dict[str, dict[str, tuple[float, float] | list[float]]] | None = None,
) -> float:
    """Return an approximate USD cost for one call, or 0 when unknown.

    ``overrides`` (optional) lets the user supply custom $/MTok rates per
    provider/model — typically loaded from the config entry's options.
    Override shape mirrors ``LLM_PRICING_USD_PER_MTOK``; values may be
    tuples or 2-element lists (lists survive a JSON round-trip).
    """
    pricing: tuple[float, float] | list[float] | None = None
    if overrides:
        override_entry = overrides.get(provider, {}).get(model)
        if override_entry is not None:
            pricing = override_entry
    if pricing is None:
        pricing = LLM_PRICING_USD_PER_MTOK.get(provider, {}).get(model)
    # Selora Cloud bills in prepaid credits (volume-discounted packs), not
    # per-token USD, so there is no meaningful token→USD rate to apply here.
    # Token and call counts are still recorded; cost stays unset.
    if not pricing or len(pricing) < 2:
        return 0.0
    in_price = float(pricing[0])
    out_price = float(pricing[1])
    return (input_tokens * in_price + output_tokens * out_price) / 1_000_000.0
