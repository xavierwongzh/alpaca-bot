"""
Static ticker -> sector map for evaluation breakdowns.

Offline and dependency-free (no yfinance lookups on the hot path). Covers the
watchlist + catalyst names; anything unknown returns "Unknown". Extend freely.
"""
from __future__ import annotations

SECTOR_MAP: dict[str, str] = {
    # Mega-cap tech / internet
    "AAPL": "Technology", "MSFT": "Technology", "GOOGL": "Communication",
    "AMZN": "Consumer Disc", "META": "Communication", "NFLX": "Communication",
    "DIS": "Communication",
    # Semiconductors
    "NVDA": "Semiconductors", "AMD": "Semiconductors", "AVGO": "Semiconductors",
    "MU": "Semiconductors", "INTC": "Semiconductors", "TSM": "Semiconductors",
    "ARM": "Semiconductors", "MRVL": "Semiconductors", "QCOM": "Semiconductors",
    "SMCI": "Semiconductors", "SMH": "Semiconductors",
    # AI infra / software
    "PLTR": "Software", "DELL": "Technology", "ANET": "Technology",
    "CRM": "Software", "NOW": "Software", "SNOW": "Software",
    "ORCL": "Software", "ADBE": "Software",
    # Fintech / crypto-adjacent
    "COIN": "Financials", "HOOD": "Financials", "PYPL": "Financials",
    "SOFI": "Financials", "AFRM": "Financials", "MSTR": "Financials",
    "JPM": "Financials", "XLF": "Financials",
    # EV / auto
    "TSLA": "Consumer Disc", "RIVN": "Consumer Disc", "LCID": "Consumer Disc",
    "NIO": "Consumer Disc", "NKE": "Consumer Disc",
    # Quantum / space
    "IONQ": "Technology", "RGTI": "Technology", "QBTS": "Technology",
    "RKLB": "Industrials", "ASTS": "Communication", "BA": "Industrials",
    # Clean energy / materials / energy
    "ENPH": "Energy", "FSLR": "Energy", "PLUG": "Energy", "MP": "Materials",
    "FCX": "Materials", "XOM": "Energy", "XLE": "Energy",
    # Consumer / retail
    "HIMS": "Healthcare", "RDDT": "Communication", "DKNG": "Consumer Disc",
    "UBER": "Technology", "ABNB": "Consumer Disc", "GME": "Consumer Disc",
    # Health
    "LLY": "Healthcare", "MRNA": "Healthcare", "PFE": "Healthcare",
    "UNH": "Healthcare", "XLV": "Healthcare",
    # Broad ETFs
    "SPY": "Index ETF", "QQQ": "Index ETF", "IWM": "Index ETF",
}


def get_sector(ticker: str) -> str:
    return SECTOR_MAP.get((ticker or "").upper(), "Unknown")
