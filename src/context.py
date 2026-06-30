"""
Layer 3: Context, news, and LLM market summary.

  - Macro read: VIX level + recent trend via yfinance.
  - Headlines: recent news per ticker via yfinance.
  - One batched OpenAI call (cheap model) for a short sentiment/technicals summary.

Exactly one OpenAI call is made here, to control cost.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Optional

from config import ModelConfig, Secrets, UniverseConfig
from src.logger import get_logger

log = get_logger()


@dataclass
class MacroContext:
    vix: Optional[float] = None
    vix_change_5d: Optional[float] = None
    regime: str = "unknown"        # calm | normal | elevated | stressed

    def as_dict(self) -> dict[str, Any]:
        return {"vix": self.vix, "vix_change_5d": self.vix_change_5d, "regime": self.regime}


def _vix_regime(vix: Optional[float]) -> str:
    if vix is None:
        return "unknown"
    if vix < 14:
        return "calm"
    if vix < 20:
        return "normal"
    if vix < 28:
        return "elevated"
    return "stressed"


def get_macro_context() -> MacroContext:
    """Pull VIX level and 5-day change via yfinance. Degrades gracefully."""
    try:
        import yfinance as yf

        hist = yf.Ticker("^VIX").history(period="1mo")
        if hist.empty:
            return MacroContext()
        last = float(hist["Close"].iloc[-1])
        ref = float(hist["Close"].iloc[-6]) if len(hist) >= 6 else last
        change = (last - ref) / ref if ref else None
        return MacroContext(vix=round(last, 2),
                            vix_change_5d=round(change, 4) if change is not None else None,
                            regime=_vix_regime(last))
    except Exception as e:  # noqa: BLE001
        log.warning("VIX/macro fetch failed: %s", e)
        return MacroContext()


def get_headlines(tickers: list[str], max_per_ticker: int = 3) -> dict[str, list[str]]:
    """Recent headlines per ticker via yfinance. Degrades gracefully to {}."""
    out: dict[str, list[str]] = {}
    try:
        import yfinance as yf
    except Exception as e:  # noqa: BLE001
        log.warning("yfinance unavailable for headlines: %s", e)
        return out

    for t in tickers:
        try:
            news = yf.Ticker(t).news or []
            titles: list[str] = []
            for item in news[:max_per_ticker]:
                # yfinance shapes vary across versions; handle both.
                title = item.get("title") or item.get("content", {}).get("title")
                if title:
                    titles.append(title.strip())
            if titles:
                out[t] = titles
        except Exception as e:  # noqa: BLE001
            log.debug("News fetch failed for %s: %s", t, e)
    return out


def summarize_market(
    secrets: Secrets,
    model_cfg: ModelConfig,
    macro: MacroContext,
    headlines: dict[str, list[str]],
    technicals_by_ticker: dict[str, dict[str, Any]],
    cost_cfg: Optional["CostConfig"] = None,
) -> tuple[str, Optional["Usage"]]:
    """
    One batched OpenAI call (cheap model) -> short prose summary of sentiment
    and technicals. Returns (summary, usage); degrades to (stub, None) on failure.
    """
    try:
        from openai import OpenAI
        from src.cost import extract_usage

        client = OpenAI(api_key=secrets.openai_api_key)
        payload = {
            "macro": macro.as_dict(),
            "headlines": headlines,
            "technicals": technicals_by_ticker,
        }
        resp = client.chat.completions.create(
            model=model_cfg.summary_model,
            temperature=model_cfg.temperature,
            messages=[
                {
                    "role": "system",
                    "content": (
                        "You are a concise market analyst. Given macro data, "
                        "headlines, and technicals, write a 4-6 sentence summary "
                        "of overall market sentiment and notable single-name "
                        "setups. No preamble, no bullet lists, just the summary."
                    ),
                },
                {"role": "user", "content": str(payload)},
            ],
        )
        usage = extract_usage(resp, "summary", model_cfg.summary_model, cost_cfg) if cost_cfg else None
        return resp.choices[0].message.content.strip(), usage
    except Exception as e:  # noqa: BLE001
        log.warning("OpenAI market summary failed: %s", e)
        return (
            f"[summary unavailable] Macro regime: {macro.regime} "
            f"(VIX={macro.vix}). Proceeding on technicals + flow signals only."
        ), None
