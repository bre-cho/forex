from __future__ import annotations


def resolve_daily_take_profit_target(policy: dict, *, starting_equity: float, daily_profit_amount: float) -> float:
    cfg = (policy or {}).get("daily_take_profit") if isinstance(policy, dict) else None
    if not isinstance(cfg, dict) or not cfg.get("enabled", False):
        return float("inf")

    mode = str(cfg.get("mode", "fixed_amount") or "fixed_amount").lower()
    if mode == "percent_equity":
        pct = float(cfg.get("daily_take_profit_pct", cfg.get("pct", 0.0)) or 0.0)
        if pct <= 0 or starting_equity <= 0:
            return float("inf")
        return starting_equity * pct / 100.0

    if mode == "capital_tier":
        tiers = cfg.get("tiers") or []
        if not isinstance(tiers, list):
            return float("inf")
        target = None
        for t in sorted([x for x in tiers if isinstance(x, dict)], key=lambda x: float(x.get("min_equity", 0.0) or 0.0)):
            min_eq = float(t.get("min_equity", 0.0) or 0.0)
            max_eq = float(t.get("max_equity", 1e18) or 1e18)
            amt = float(t.get("target_amount", 0.0) or 0.0)
            if starting_equity >= min_eq and starting_equity <= max_eq and amt > 0:
                target = amt
        return float(target) if target is not None else float("inf")

    return float(cfg.get("daily_take_profit_amount", 1e18) or 1e18)
