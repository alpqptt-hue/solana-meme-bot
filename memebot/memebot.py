"""
=====================================================================
 MEMECOIN SCANNER / SCORING / ALERT BOT — ALL-IN-ONE FILE
=====================================================================
Implements the framework from the strategy doc:
  Liquidity + Smart Money + Volume Confirmation + Narrative
scored 0-100, with hard safety gates (liquidity, honeypot/rug checks,
holder concentration) that must ALL pass before a token is even scored.

WHAT THIS BOT DOES:
  - Scans/monitors tokens (via DexScreener, free & keyless) for
    liquidity, volume spikes, and price breakouts.
  - Checks honeypot / rug-pull risk (honeypot.is for EVM chains,
    RugCheck.xyz for Solana) before ever scoring a token.
  - Optionally tracks a watchlist of known "smart money" wallets
    (via Etherscan-family explorer APIs for EVM chains) to see if
    they've recently bought a token -- this needs YOU to supply a
    wallet watchlist and (free) explorer API key; see SETTINGS.
  - Scores every candidate 0-100 and only alerts when the score
    clears your threshold.
  - Simulates a paper position (TP ladder + stop) so you can see
    hypothetical P&L without risking real funds.

WHAT THIS BOT DELIBERATELY DOES NOT DO:
  - It does NOT hold a wallet private key and does NOT execute real
    on-chain swaps. On-chain trades are irreversible and this class
    of token is high-rug-risk -- automatic execution here would turn
    any bug into an instant, unrecoverable loss. You get an instant
    Telegram alert instead and pull the trigger yourself in your own
    wallet app. If you later want a real execution layer, that's a
    separate, carefully-reviewed addition -- not something to bolt on
    silently.

--- RUN LOCALLY ---
    pip install -r requirements.txt
    python3 memebot.py --once        # one scan cycle, prints results
    python3 memebot.py               # continuous scanning loop

--- RUN ON RENDER (free web service) ---
Start command:
    gunicorn -w 1 -k gthread --threads 4 -b 0.0.0.0:$PORT memebot:app
This file defines a module-level `app` (Flask) for gunicorn, and starts
the scan loop in a background thread automatically when RENDER is set.

--- EDIT YOUR SETTINGS BELOW ---
=====================================================================
"""

from __future__ import annotations

import os
import time
import json
import sqlite3
import argparse
import threading
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Optional, List, Dict, Any, Tuple

import requests


# =====================================================================
# ===== EDIT YOUR SETTINGS HERE =======================================
# =====================================================================

class Settings:
    # ---- what to scan ----
    # Chains supported out of the box: "solana", "ethereum", "bsc", "base"
    CHAINS = ["solana"]

    # Option A: give exact token addresses to watch (safest, most predictable)
    WATCHLIST_TOKENS: Dict[str, List[str]] = {
        # "solana": ["TokenMintAddressHere..."],
        # "ethereum": ["0xTokenAddressHere..."],
    }

    # Option B: auto-discover trending/boosted tokens from DexScreener
    # (noisier, more false positives -- the hard gates below filter most junk)
    AUTO_DISCOVER_TRENDING = True
    DISCOVERY_SEARCH_TERMS = ["solana", "pump"]   # free-text DexScreener search seeds

    # ---- hard safety gates (a token failing ANY of these is skipped entirely) ----
    MIN_LIQUIDITY_USD = 15_000
    MIN_VOLUME_H24_USD = 30_000
    MAX_TOP10_HOLDER_PCT = 40.0        # skip if top 10 wallets hold more than this %
    MIN_PAIR_AGE_HOURS = 24            # doc's advice: don't ape in minute 1, wait 24-72h
    MAX_PAIR_AGE_HOURS = 24 * 14       # ignore old, already-played-out tokens
    BLOCK_IF_HONEYPOT_OR_HIGH_RISK = True

    # ---- scoring weights (mirrors the doc's 100-point framework) ----
    # Components without a free reliable data source default to 0 and are
    # clearly flagged "not configured" in the alert -- wire in a paid feed
    # or your own signal if you want to use them.
    WEIGHTS = {
        "smart_wallet_buys": 30,   # needs SMART_WALLETS configured below
        "volume_spike": 20,
        "liquidity_health": 15,
        "holder_growth": 10,       # not free-available -> 0 unless you plug a source
        "breakout_momentum": 10,
        "narrative_momentum": 10,  # not free-available -> 0 unless you plug a source
        "distribution_health": 5,
    }
    # Because 2 of the 7 components (20 pts) are usually unconfigured on free
    # data, a realistic default threshold is lower than the doc's "90" ideal.
    # Raise this back toward 85-90 once smart-wallet tracking + a holder-growth
    # or social feed are wired in.
    ENTRY_SCORE_THRESHOLD = 60.0

    # ---- smart wallet tracking (EVM chains; needs a free explorer API key) ----
    # Get free keys at etherscan.io/apis, bscscan.com/apis, basescan.org/apis
    ETHERSCAN_API_KEY = os.getenv("ETHERSCAN_API_KEY", "")
    BSCSCAN_API_KEY = os.getenv("BSCSCAN_API_KEY", "")
    BASESCAN_API_KEY = os.getenv("BASESCAN_API_KEY", "")
    SMART_WALLETS: Dict[str, List[str]] = {
        # "ethereum": ["0xWalletAddress1", "0xWalletAddress2"],
        # "bsc": ["0xWalletAddress3"],
        # Solana smart-wallet tracking needs a paid indexer (GMGN/Birdeye) for
        # reliable results -- left as an extension point, see track_smart_wallets_solana().
    }
    SMART_WALLET_LOOKBACK_HOURS = 6

    # ---- take-profit ladder + stop loss (paper simulation) ----
    TP_LADDER = [
        {"gain_pct": 50, "sell_fraction": 0.25},
        {"gain_pct": 100, "sell_fraction": 0.25},
        {"gain_pct": 200, "sell_fraction": 0.25},
        # remaining 25% rides with a trailing stop
    ]
    STOP_LOSS_PCT = 25.0            # approximate -- true structure-based stop needs
                                     # OHLC candle data (paid feed); this is a % fallback
    TRAILING_STOP_PCT = 20.0        # trails once in profit past the last TP level

    # ---- virtual portfolio (paper trading with a starting balance) ----
    STARTING_BALANCE_SAR = 1000.0
    # SAR is pegged to USD at ~3.75 -- crypto prices/quotes are all in USD,
    # so this converts your SAR balance internally. Update if the peg ever
    # changes; it hasn't in decades but don't take my word for it blindly.
    USD_SAR_RATE = 3.75
    ALLOCATION_PCT_PER_TRADE = 15.0   # % of CURRENT balance invested per new position
    MAX_OPEN_PAPER_POSITIONS = 5

    # ---- notifications ----
    TELEGRAM_ENABLED = True
    TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
    TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "")

    # ---- loop ----
    SCAN_INTERVAL_SECONDS = 120
    DB_PATH = "memebot.db"


S = Settings()


# =====================================================================
# ===== DEXSCREENER CLIENT (free, no API key) ==========================
# =====================================================================

DEXSCREENER_BASE = "https://api.dexscreener.com"


def _get_json(url: str, timeout: int = 15) -> Optional[dict]:
    try:
        r = requests.get(url, timeout=timeout, headers={"User-Agent": "memebot/1.0"})
        if r.status_code == 200:
            return r.json()
        print(f"[dexscreener] HTTP {r.status_code} for {url}")
    except Exception as e:  # noqa: BLE001
        print(f"[dexscreener] request failed: {e}")
    return None


def ds_search(query: str) -> List[dict]:
    data = _get_json(f"{DEXSCREENER_BASE}/latest/dex/search?q={requests.utils.quote(query)}")
    return (data or {}).get("pairs") or []


def ds_token_pairs(chain: str, token_address: str) -> List[dict]:
    data = _get_json(f"{DEXSCREENER_BASE}/latest/dex/tokens/{token_address}")
    pairs = (data or {}).get("pairs") or []
    return [p for p in pairs if p.get("chainId") == chain]


def discover_candidates() -> List[dict]:
    """Returns a de-duplicated list of DexScreener pair objects to evaluate this cycle."""
    pairs: Dict[str, dict] = {}

    for chain, addresses in S.WATCHLIST_TOKENS.items():
        for addr in addresses:
            for p in ds_token_pairs(chain, addr):
                pairs[p.get("pairAddress", addr)] = p

    if S.AUTO_DISCOVER_TRENDING:
        for term in S.DISCOVERY_SEARCH_TERMS:
            for p in ds_search(term):
                if p.get("chainId") in S.CHAINS:
                    pairs[p.get("pairAddress")] = p

    return list(pairs.values())


# =====================================================================
# ===== HONEYPOT / RUG CHECKS ==========================================
# =====================================================================

def check_honeypot_evm(token_address: str, chain_id: int = 1) -> dict:
    """
    honeypot.is free public API -- simulates a buy+sell to detect honeypots
    and reports buy/sell tax. chain_id: 1=ethereum, 56=bsc, 8453=base.
    Returns {"is_honeypot": bool, "buy_tax": float, "sell_tax": float, "ok": bool}
    """
    url = f"https://api.honeypot.is/v2/IsHoneypot?address={token_address}&chainID={chain_id}"
    data = _get_json(url)
    if not data:
        return {"is_honeypot": None, "buy_tax": None, "sell_tax": None, "ok": False, "reason": "check failed"}
    honeypot_result = data.get("honeypotResult", {}) or {}
    simulation = data.get("simulationResult", {}) or {}
    is_hp = honeypot_result.get("isHoneypot", False)
    return {
        "is_honeypot": is_hp,
        "buy_tax": simulation.get("buyTax"),
        "sell_tax": simulation.get("sellTax"),
        "ok": True,
    }


def check_rugcheck_solana(mint_address: str) -> dict:
    """
    RugCheck.xyz free public report for Solana tokens: mint/freeze authority,
    LP lock status, top holder concentration, and an overall risk score.
    Returns {"ok": bool, "risk_level": str, "top10_holder_pct": float, "flags": [...]}
    """
    url = f"https://api.rugcheck.xyz/v1/tokens/{mint_address}/report"
    data = _get_json(url)
    if not data:
        return {"ok": False, "risk_level": None, "top10_holder_pct": None, "flags": ["check failed"]}

    risks = data.get("risks") or []
    top_holders = data.get("topHolders") or []
    top10_pct = sum(h.get("pct", 0) for h in top_holders[:10]) if top_holders else None
    high_risk_flags = [r.get("name") for r in risks if r.get("level") in ("danger", "warn")]

    return {
        "ok": True,
        "risk_level": data.get("score_normalised") or data.get("score"),
        "top10_holder_pct": top10_pct,
        "flags": high_risk_flags,
    }


def passes_safety_gates(chain: str, token_address: str, pair: dict) -> Tuple[bool, List[str]]:
    """Phase 1 of the doc: filter the coin before anything else matters."""
    fails: List[str] = []

    liquidity_usd = (pair.get("liquidity") or {}).get("usd", 0) or 0
    if liquidity_usd < S.MIN_LIQUIDITY_USD:
        fails.append(f"liquidity ${liquidity_usd:,.0f} < ${S.MIN_LIQUIDITY_USD:,.0f}")

    vol_h24 = (pair.get("volume") or {}).get("h24", 0) or 0
    if vol_h24 < S.MIN_VOLUME_H24_USD:
        fails.append(f"24h volume ${vol_h24:,.0f} < ${S.MIN_VOLUME_H24_USD:,.0f}")

    created_at_ms = pair.get("pairCreatedAt")
    if created_at_ms:
        age_hours = (datetime.utcnow() - datetime.utcfromtimestamp(created_at_ms / 1000)).total_seconds() / 3600
        if age_hours < S.MIN_PAIR_AGE_HOURS:
            fails.append(f"pair age {age_hours:.1f}h < min {S.MIN_PAIR_AGE_HOURS}h (too new, doc says wait 24-72h)")
        if age_hours > S.MAX_PAIR_AGE_HOURS:
            fails.append(f"pair age {age_hours:.1f}h > max {S.MAX_PAIR_AGE_HOURS}h (stale)")

    if S.BLOCK_IF_HONEYPOT_OR_HIGH_RISK:
        chain_ids = {"ethereum": 1, "bsc": 56, "base": 8453}
        if chain in chain_ids:
            hp = check_honeypot_evm(token_address, chain_ids[chain])
            if hp.get("ok") and hp.get("is_honeypot"):
                fails.append("HONEYPOT detected (honeypot.is)")
            if hp.get("ok") and (hp.get("sell_tax") or 0) > 15:
                fails.append(f"sell tax {hp.get('sell_tax')}% too high")
        elif chain == "solana":
            rc = check_rugcheck_solana(token_address)
            if rc.get("ok"):
                if rc.get("top10_holder_pct") is not None and rc["top10_holder_pct"] > S.MAX_TOP10_HOLDER_PCT:
                    fails.append(f"top10 holders {rc['top10_holder_pct']:.1f}% > max {S.MAX_TOP10_HOLDER_PCT}%")
                if rc.get("flags"):
                    fails.append(f"RugCheck flags: {', '.join(rc['flags'][:3])}")

    return (len(fails) == 0), fails


# =====================================================================
# ===== SMART WALLET TRACKING (EVM via explorer APIs) ===================
# =====================================================================

_EXPLORER_APIS = {
    "ethereum": ("https://api.etherscan.io/api", "ETHERSCAN_API_KEY"),
    "bsc": ("https://api.bscscan.com/api", "BSCSCAN_API_KEY"),
    "base": ("https://api.basescan.org/api", "BASESCAN_API_KEY"),
}


def track_smart_wallets_evm(chain: str, token_address: str) -> dict:
    """Checks whether any wallet in S.SMART_WALLETS[chain] has bought this
    token in the last SMART_WALLET_LOOKBACK_HOURS. Needs a free explorer API key."""
    wallets = S.SMART_WALLETS.get(chain, [])
    if not wallets or chain not in _EXPLORER_APIS:
        return {"configured": False, "buyers": []}

    base_url, key_attr = _EXPLORER_APIS[chain]
    api_key = getattr(S, key_attr)
    if not api_key:
        return {"configured": False, "buyers": []}

    cutoff = datetime.utcnow() - timedelta(hours=S.SMART_WALLET_LOOKBACK_HOURS)
    buyers = []
    for wallet in wallets:
        url = (f"{base_url}?module=account&action=tokentx&contractaddress={token_address}"
               f"&address={wallet}&sort=desc&apikey={api_key}")
        data = _get_json(url)
        if not data or data.get("status") != "1":
            continue
        for tx in data.get("result", [])[:20]:
            try:
                ts = datetime.utcfromtimestamp(int(tx["timeStamp"]))
            except Exception:
                continue
            if ts < cutoff:
                continue
            # a "buy" = tokens moving TO the tracked wallet
            if tx.get("to", "").lower() == wallet.lower():
                buyers.append(wallet)
                break

    return {"configured": True, "buyers": list(set(buyers))}


def track_smart_wallets_solana(token_address: str) -> dict:
    """
    Placeholder extension point. Reliable Solana smart-money tracking
    (GMGN-style) needs a paid indexer -- free RPC/Solscan calls are too
    limited for this to be trustworthy. Wire in your provider of choice
    here; until then this always returns "not configured" so the scoring
    function correctly shows 0/30 for this component instead of guessing.
    """
    return {"configured": False, "buyers": []}


# =====================================================================
# ===== SCORING ENGINE =================================================
# =====================================================================

@dataclass
class ScoreBreakdown:
    total: float
    components: Dict[str, float]
    reasons: List[str]
    signal: str  # "ALERT" / "WAIT"


def score_pair(chain: str, token_address: str, pair: dict) -> ScoreBreakdown:
    w = S.WEIGHTS
    reasons: List[str] = []
    components: Dict[str, float] = {}

    # --- liquidity health ---
    liquidity_usd = (pair.get("liquidity") or {}).get("usd", 0) or 0
    fdv = pair.get("fdv") or pair.get("marketCap") or 0
    liq_pts = 0.0
    if liquidity_usd >= S.MIN_LIQUIDITY_USD:
        ratio = (liquidity_usd / fdv) if fdv else 0
        if ratio >= 0.03:  # at least 3% of FDV sitting in liquidity = healthier
            liq_pts = w["liquidity_health"]
            reasons.append(f"liquidity ${liquidity_usd:,.0f} healthy vs FDV")
        else:
            liq_pts = w["liquidity_health"] * 0.5
            reasons.append(f"liquidity ${liquidity_usd:,.0f} present but thin vs FDV")
    components["liquidity_health"] = liq_pts

    # --- volume spike (current h1 pace vs 24h average pace) ---
    vol = pair.get("volume") or {}
    vol_h1 = vol.get("h1", 0) or 0
    vol_h24 = vol.get("h24", 0) or 0
    avg_hourly = vol_h24 / 24 if vol_h24 else 0
    vol_pts = 0.0
    if avg_hourly > 0:
        spike_ratio = vol_h1 / avg_hourly
        if spike_ratio >= 3:
            vol_pts = w["volume_spike"]
            reasons.append(f"volume spike {spike_ratio:.1f}x hourly average")
        elif spike_ratio >= 1.5:
            vol_pts = w["volume_spike"] * 0.5
            reasons.append(f"volume elevated {spike_ratio:.1f}x hourly average")
        else:
            reasons.append(f"volume normal ({spike_ratio:.1f}x average)")
    components["volume_spike"] = vol_pts

    # --- breakout momentum (proxy: recent price change fields from DexScreener) ---
    change = pair.get("priceChange") or {}
    m5, h1, h6 = change.get("m5", 0) or 0, change.get("h1", 0) or 0, change.get("h6", 0) or 0
    breakout_pts = 0.0
    if h1 > 5 and m5 > 0 and vol_pts > 0:
        breakout_pts = w["breakout_momentum"]
        reasons.append(f"breakout: +{h1:.1f}% (1h) confirmed by volume")
    elif h1 > 5:
        breakout_pts = w["breakout_momentum"] * 0.4
        reasons.append(f"price up +{h1:.1f}% (1h) but WITHOUT volume confirmation (possible fake breakout)")
    else:
        reasons.append(f"no clear breakout (1h change {h1:.1f}%)")
    components["breakout_momentum"] = breakout_pts

    # --- distribution health (from RugCheck for solana; skipped/0 for EVM here) ---
    dist_pts = 0.0
    if chain == "solana":
        rc = check_rugcheck_solana(token_address)
        if rc.get("ok") and rc.get("top10_holder_pct") is not None:
            if rc["top10_holder_pct"] <= 25:
                dist_pts = w["distribution_health"]
                reasons.append(f"top10 holders {rc['top10_holder_pct']:.1f}% (healthy distribution)")
            elif rc["top10_holder_pct"] <= S.MAX_TOP10_HOLDER_PCT:
                dist_pts = w["distribution_health"] * 0.5
                reasons.append(f"top10 holders {rc['top10_holder_pct']:.1f}% (moderate concentration)")
    components["distribution_health"] = dist_pts

    # --- smart wallet buys ---
    smart_pts = 0.0
    if chain in _EXPLORER_APIS:
        sw = track_smart_wallets_evm(chain, token_address)
    elif chain == "solana":
        sw = track_smart_wallets_solana(token_address)
    else:
        sw = {"configured": False, "buyers": []}

    if not sw["configured"]:
        reasons.append("smart-wallet tracking not configured (0/{} pts)".format(w["smart_wallet_buys"]))
    elif sw["buyers"]:
        smart_pts = w["smart_wallet_buys"]
        reasons.append(f"{len(sw['buyers'])} tracked smart wallet(s) bought recently")
    else:
        reasons.append("no tracked smart wallets bought recently")
    components["smart_wallet_buys"] = smart_pts

    # --- holder growth & narrative momentum: no free reliable source wired in ---
    components["holder_growth"] = 0.0
    reasons.append(f"holder-growth tracking not configured (0/{w['holder_growth']} pts)")
    components["narrative_momentum"] = 0.0
    reasons.append(f"narrative/social tracking not configured (0/{w['narrative_momentum']} pts)")

    total = sum(components.values())
    signal = "ALERT" if total >= S.ENTRY_SCORE_THRESHOLD else "WAIT"

    return ScoreBreakdown(total=total, components=components, reasons=reasons, signal=signal)


# =====================================================================
# ===== PAPER POSITION SIMULATION ======================================
# =====================================================================

@dataclass
class PaperPosition:
    chain: str
    token_address: str
    symbol: str
    entry_price: float
    qty: float
    cost_sar: float                    # SAR actually "spent" opening this position
    opened_at: datetime = field(default_factory=datetime.utcnow)
    tp_hit: List[float] = field(default_factory=list)
    remaining_fraction: float = 1.0
    peak_price: float = 0.0
    closed: bool = False

    def __post_init__(self):
        self.peak_price = self.entry_price

    def current_value_sar(self, current_price: float) -> float:
        return self.qty * self.remaining_fraction * current_price * S.USD_SAR_RATE


class PaperAccount:
    """
    A real running virtual balance, starting at S.STARTING_BALANCE_SAR.
    Every open/partial-close/close moves actual SAR between `cash_sar`
    and the position -- exactly like a real account, so equity() always
    reflects "what would my balance actually be right now."
    """

    def __init__(self, starting_balance_sar: float = None):
        self.starting_balance_sar = starting_balance_sar or S.STARTING_BALANCE_SAR
        self.cash_sar = self.starting_balance_sar
        self.positions: Dict[str, PaperPosition] = {}
        self.closed_trades: List[dict] = []  # each: {symbol, pnl_sar, pnl_pct, reason, opened_at, closed_at}

    def can_open(self) -> bool:
        return (len(self.positions) < S.MAX_OPEN_PAPER_POSITIONS
                and self.cash_sar > self.starting_balance_sar * 0.05)  # keep a small floor

    def open(self, chain: str, token_address: str, symbol: str, price_usd: float) -> Optional[PaperPosition]:
        if price_usd <= 0:
            return None
        allocation_sar = min(self.cash_sar, self.equity() * (S.ALLOCATION_PCT_PER_TRADE / 100))
        if allocation_sar <= 0:
            return None

        allocation_usd = allocation_sar / S.USD_SAR_RATE
        qty = allocation_usd / price_usd

        self.cash_sar -= allocation_sar
        pos = PaperPosition(chain=chain, token_address=token_address, symbol=symbol,
                             entry_price=price_usd, qty=qty, cost_sar=allocation_sar)
        self.positions[token_address] = pos
        return pos

    def _sell_fraction(self, pos: PaperPosition, fraction: float, current_price: float, reason: str) -> dict:
        proceeds_sar = pos.qty * fraction * current_price * S.USD_SAR_RATE
        cost_basis_sar = pos.cost_sar * fraction
        pnl_sar = proceeds_sar - cost_basis_sar
        self.cash_sar += proceeds_sar

        record = {
            "symbol": pos.symbol, "chain": pos.chain, "token_address": pos.token_address,
            "reason": reason, "fraction": fraction, "pnl_sar": round(pnl_sar, 2),
            "pnl_pct": round((current_price - pos.entry_price) / pos.entry_price * 100, 2),
            "opened_at": pos.opened_at.isoformat(), "closed_at": datetime.utcnow().isoformat(),
        }
        self.closed_trades.append(record)
        return record

    def update(self, token_address: str, current_price: float) -> List[dict]:
        """Returns a list of realized events (partial TP / stop / trailing stop) this tick."""
        pos = self.positions.get(token_address)
        if not pos or pos.closed or current_price <= 0:
            return []

        events = []
        pos.peak_price = max(pos.peak_price, current_price)
        gain_pct = (current_price - pos.entry_price) / pos.entry_price * 100

        for level in S.TP_LADDER:
            if level["gain_pct"] not in pos.tp_hit and gain_pct >= level["gain_pct"]:
                pos.tp_hit.append(level["gain_pct"])
                record = self._sell_fraction(pos, level["sell_fraction"], current_price,
                                              reason=f"partial_tp_{level['gain_pct']}%")
                pos.remaining_fraction -= level["sell_fraction"]
                events.append(record)

        if not pos.tp_hit and gain_pct <= -S.STOP_LOSS_PCT:
            record = self._sell_fraction(pos, pos.remaining_fraction, current_price, reason="stop_loss")
            pos.remaining_fraction = 0
            pos.closed = True
            events.append(record)
        elif pos.tp_hit and pos.remaining_fraction > 0:
            drawdown_from_peak = (pos.peak_price - current_price) / pos.peak_price * 100
            if drawdown_from_peak >= S.TRAILING_STOP_PCT:
                record = self._sell_fraction(pos, pos.remaining_fraction, current_price, reason="trailing_stop")
                pos.remaining_fraction = 0
                pos.closed = True
                events.append(record)

        if pos.remaining_fraction <= 0.001:
            pos.closed = True

        return events

    def close(self, token_address: str):
        self.positions.pop(token_address, None)

    def equity(self, prices: Dict[str, float] = None) -> float:
        """Cash + current market value of every open position. Pass a
        {token_address: current_price_usd} dict for live valuation; if
        omitted, open positions are valued at their entry price."""
        prices = prices or {}
        total = self.cash_sar
        for addr, pos in self.positions.items():
            price = prices.get(addr, pos.entry_price)
            total += pos.current_value_sar(price)
        return total

    def stats(self, prices: Dict[str, float] = None) -> dict:
        equity_now = self.equity(prices)
        total_return_pct = (equity_now - self.starting_balance_sar) / self.starting_balance_sar * 100
        wins = [t for t in self.closed_trades if t["pnl_sar"] > 0]
        losses = [t for t in self.closed_trades if t["pnl_sar"] <= 0]
        win_rate = (len(wins) / len(self.closed_trades) * 100) if self.closed_trades else None

        return {
            "starting_balance_sar": round(self.starting_balance_sar, 2),
            "current_equity_sar": round(equity_now, 2),
            "cash_sar": round(self.cash_sar, 2),
            "total_return_pct": round(total_return_pct, 2),
            "open_positions": len(self.positions),
            "closed_trades": len(self.closed_trades),
            "win_rate_pct": round(win_rate, 1) if win_rate is not None else None,
            "total_realized_pnl_sar": round(sum(t["pnl_sar"] for t in self.closed_trades), 2),
            "best_trade_sar": round(max((t["pnl_sar"] for t in self.closed_trades), default=0), 2),
            "worst_trade_sar": round(min((t["pnl_sar"] for t in self.closed_trades), default=0), 2),
        }


# =====================================================================
# ===== NOTIFIER ========================================================
# =====================================================================

def notify(message: str):
    print(f"[notify] {message}")
    if S.TELEGRAM_ENABLED and S.TELEGRAM_BOT_TOKEN and S.TELEGRAM_CHAT_ID:
        try:
            url = f"https://api.telegram.org/bot{S.TELEGRAM_BOT_TOKEN}/sendMessage"
            requests.post(url, json={"chat_id": S.TELEGRAM_CHAT_ID, "text": message}, timeout=10)
        except Exception as e:  # noqa: BLE001
            print(f"[notify] telegram failed: {e}")


# =====================================================================
# ===== TRADE / SCAN LOGGER (SQLite) ====================================
# =====================================================================

SCHEMA = """
CREATE TABLE IF NOT EXISTS alerts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    chain TEXT, token_address TEXT, symbol TEXT, score REAL,
    reasons TEXT, price REAL, timestamp TEXT
);
CREATE TABLE IF NOT EXISTS paper_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    chain TEXT, token_address TEXT, symbol TEXT, event_type TEXT,
    gain_pct REAL, price REAL, timestamp TEXT
);
"""


class Logger:
    def __init__(self, db_path: str = None):
        self.db_path = db_path or S.DB_PATH
        with self._conn() as c:
            c.executescript(SCHEMA)

    def _conn(self):
        return sqlite3.connect(self.db_path)

    def log_alert(self, chain, token_address, symbol, score, reasons, price):
        with self._conn() as c:
            c.execute(
                "INSERT INTO alerts (chain, token_address, symbol, score, reasons, price, timestamp) "
                "VALUES (?, ?, ?, ?, ?, ?, ?)",
                (chain, token_address, symbol, score, json.dumps(reasons, ensure_ascii=False), price,
                 datetime.utcnow().isoformat()),
            )

    def log_paper_event(self, chain, token_address, symbol, event_type, gain_pct, price):
        with self._conn() as c:
            c.execute(
                "INSERT INTO paper_events (chain, token_address, symbol, event_type, gain_pct, price, timestamp) "
                "VALUES (?, ?, ?, ?, ?, ?, ?)",
                (chain, token_address, symbol, event_type, gain_pct, price, datetime.utcnow().isoformat()),
            )


# =====================================================================
# ===== BOT STATUS (for the web endpoint) ==============================
# =====================================================================

class BotStatus:
    def __init__(self):
        self._lock = threading.Lock()
        self.started_at = datetime.utcnow().isoformat()
        self.last_scan_at = None
        self.last_scan_ok = None
        self.last_error = None
        self.candidates_last_scan = 0
        self.alerts_last_scan = 0
        self.portfolio: dict = {}

    def update(self, **kwargs):
        with self._lock:
            for k, v in kwargs.items():
                setattr(self, k, v)

    def snapshot(self) -> dict:
        with self._lock:
            return {
                "started_at": self.started_at, "last_scan_at": self.last_scan_at,
                "last_scan_ok": self.last_scan_ok, "last_error": self.last_error,
                "candidates_last_scan": self.candidates_last_scan,
                "alerts_last_scan": self.alerts_last_scan,
                "portfolio": self.portfolio,
                "server_time_utc": datetime.utcnow().isoformat(),
            }


status = BotStatus()


# =====================================================================
# ===== MAIN SCAN LOOP ==================================================
# =====================================================================

def format_portfolio_summary(account: "PaperAccount", prices: Dict[str, float] = None) -> str:
    s = account.stats(prices)
    lines = [
        "📊 ملخص المحفظة الوهمية",
        f"الرصيد الابتدائي: {s['starting_balance_sar']:.2f} ريال",
        f"القيمة الحالية: {s['current_equity_sar']:.2f} ريال ({s['total_return_pct']:+.2f}%)",
        f"نقدي متاح: {s['cash_sar']:.2f} ريال | صفقات مفتوحة: {s['open_positions']}",
        f"صفقات مغلقة: {s['closed_trades']}"
        + (f" | نسبة الفوز: {s['win_rate_pct']:.1f}%" if s['win_rate_pct'] is not None else ""),
        f"أفضل صفقة: {s['best_trade_sar']:+.2f} ريال | أسوأ صفقة: {s['worst_trade_sar']:+.2f} ريال",
    ]
    return "\n".join(lines)


def run_scan_cycle(account: "PaperAccount", logger: Logger):
    live_prices: Dict[str, float] = {}

    # 1) manage existing paper positions with fresh prices
    for token_address, pos in list(account.positions.items()):
        pairs = ds_token_pairs(pos.chain, token_address)
        if not pairs:
            continue
        price = float(pairs[0].get("priceUsd", 0) or 0)
        if price <= 0:
            continue
        live_prices[token_address] = price

        for event in account.update(token_address, price):
            msg = (f"[PAPER] {event['symbol']} ({event['chain']}) {event['reason']} "
                   f"at {event['pnl_pct']:+.1f}% -> pnl {event['pnl_sar']:+.2f} ريال "
                   f"| رصيد نقدي الآن: {account.cash_sar:.2f} ريال")
            notify(msg)
            logger.log_paper_event(pos.chain, token_address, pos.symbol, event["reason"],
                                    event["pnl_pct"], price)
        if pos.closed:
            account.close(token_address)

    # 2) discover + evaluate new candidates
    candidates = discover_candidates()
    alerts_count = 0

    for pair in candidates:
        chain = pair.get("chainId")
        token_address = (pair.get("baseToken") or {}).get("address")
        symbol = (pair.get("baseToken") or {}).get("symbol", "?")
        if not chain or not token_address or chain not in S.CHAINS:
            continue
        if token_address in account.positions:
            continue  # already tracking a paper position on this one

        ok, fail_reasons = passes_safety_gates(chain, token_address, pair)
        if not ok:
            continue  # silently skip -- logging every rejected token would be very noisy

        score = score_pair(chain, token_address, pair)
        price = float(pair.get("priceUsd", 0) or 0)
        if price > 0:
            live_prices[token_address] = price

        print(f"[{chain}] {symbol} score={score.total:.1f} signal={score.signal} -- "
              f"{' | '.join(score.reasons[:4])}")

        if score.signal == "ALERT":
            alerts_count += 1
            msg = (f"🚨 ALERT {symbol} ({chain}) score={score.total:.1f}/100\n"
                   f"price: ${price:.8f}\n"
                   f"{token_address}\n"
                   f"reasons: {' | '.join(score.reasons)}")
            notify(msg)
            logger.log_alert(chain, token_address, symbol, score.total, score.reasons, price)

            if account.can_open() and price > 0:
                pos = account.open(chain, token_address, symbol, price)
                if pos:
                    notify(f"[PAPER] فتح صفقة وهمية {symbol}: {pos.cost_sar:.2f} ريال @ ${price:.8f} "
                           f"| نقدي متبقي: {account.cash_sar:.2f} ريال")

    return len(candidates), alerts_count, live_prices


def run_bot(once: bool = False, bot_status: BotStatus = None):
    account = PaperAccount()
    logger = Logger()

    summary_every_n_cycles = max(1, int(3600 / S.SCAN_INTERVAL_SECONDS))  # roughly hourly
    cycle_count = 0

    print(f"[memebot] starting -- chains={S.CHAINS} threshold={S.ENTRY_SCORE_THRESHOLD} "
          f"starting_balance={S.STARTING_BALANCE_SAR} SAR")
    notify(f"🚀 البوت بدأ الشغل. رصيد وهمي ابتدائي: {S.STARTING_BALANCE_SAR:.2f} ريال")

    while True:
        ok, err = True, None
        n_candidates, n_alerts, live_prices = 0, 0, {}
        try:
            n_candidates, n_alerts, live_prices = run_scan_cycle(account, logger)
        except Exception as e:  # noqa: BLE001
            ok, err = False, str(e)
            print(f"[memebot] scan cycle error: {e}")

        portfolio_stats = account.stats(live_prices)

        if bot_status:
            bot_status.update(
                last_scan_at=datetime.utcnow().isoformat(), last_scan_ok=ok, last_error=err,
                candidates_last_scan=n_candidates, alerts_last_scan=n_alerts,
                portfolio=portfolio_stats,
            )

        cycle_count += 1
        if cycle_count % summary_every_n_cycles == 0:
            notify(format_portfolio_summary(account, live_prices))

        if once:
            break
        time.sleep(S.SCAN_INTERVAL_SECONDS)


# =====================================================================
# ===== FLASK APP (Render + uptime pinging) =============================
# =====================================================================

from flask import Flask, jsonify  # noqa: E402

app = Flask(__name__)


@app.route("/")
def root():
    return jsonify({"service": "memecoin-scanner-bot", **status.snapshot()})


@app.route("/health")
def health():
    return jsonify({"status": "ok", **status.snapshot()}), 200


@app.route("/portfolio")
def portfolio():
    return jsonify(status.portfolio), 200


def _start_background_bot():
    t = threading.Thread(target=run_bot, kwargs={"once": False, "bot_status": status}, daemon=True)
    t.start()
    return t


if os.getenv("RENDER") or os.getenv("START_BOT_ON_IMPORT") == "1":
    _start_background_bot()


# =====================================================================
# ===== CLI ENTRYPOINT ==================================================
# =====================================================================

def main():
    parser = argparse.ArgumentParser(description="Memecoin scanner/scoring/alert bot (single file)")
    parser.add_argument("--once", action="store_true", help="Run a single scan cycle then exit")
    parser.add_argument("--serve", action="store_true", help="Also start the Flask health server on $PORT")
    args = parser.parse_args()

    if args.serve:
        _start_background_bot()
        port = int(os.getenv("PORT", "10000"))
        app.run(host="0.0.0.0", port=port)
    else:
        run_bot(once=args.once, bot_status=status)


if __name__ == "__main__":
    main()
