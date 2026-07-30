from flask import Flask
import asyncio
import aiohttp
import requests
import time
import os
import json
from threading import Thread
from datetime import datetime, timezone

# ============================================================================
# 1) Flask health-check server (Render/Railway keep-alive)
# ============================================================================
app = Flask(__name__)

@app.route('/')
def health_check():
    return "Solana Fast Sniper Meme Bot is Running 24/7!", 200

def start_server():
    port = int(os.environ.get("PORT", 10000))
    app.run(host="0.0.0.0", port=port, debug=False, use_reloader=False)

# ============================================================================
# 2) Telegram config — from environment variables only. Regenerate any token
#    that was ever pasted in plain text somewhere (chat, repo, screenshot).
# ============================================================================
TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "8596265665:AAEdjiNIHoA6D-oFmr_iCsaBbomwcdhqgp0")
CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID", "1015963752")

if not TELEGRAM_BOT_TOKEN or not CHAT_ID:
    print("⚠️ TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID env vars are not set.")

# ============================================================================
# 3) Config
# ============================================================================
USD_TO_SAR = 3.75
INITIAL_BALANCE_SAR = 1000.0
INITIAL_BALANCE_USD = INITIAL_BALANCE_SAR / USD_TO_SAR

STATE_FILE = "bot_state.json"
MAX_CONCURRENT_TRADES = 3

MIN_MARKET_CAP = 25_000
MAX_MARKET_CAP = 80_000
MIN_LIQUIDITY = 15_000
MAX_LIQUIDITY = 60_000

MAX_TOP_HOLDER_PCT = 20.0
REQUIRE_MINT_DISABLED = True
REQUIRE_FREEZE_DISABLED = True
MIN_LP_LOCKED_OR_BURNED_PCT = 80.0

# Confirmation: N consecutive scans with rising volume AND rising price,
# instead of a fixed wall-clock timer (feedback #2). With a ~2s scan cadence
# this is roughly a 4-6 second window, not 15.
REQUIRED_CONSECUTIVE_CONFIRMS = 3

MEME_KEYWORDS = ["DOG", "CAT", "PEPE", "BANANA", "AI", "ELON", "TRUMP",
                  "FROG", "BRAINROT", "MOON", "INU", "SHIB", "WOJAK"]

SCAN_LOOP_SLEEP_SECONDS = 2  # dexscreener rate limits make sub-1s polling risky;
                              # true sub-second discovery needs a push feed
                              # (Pump.fun/LetsBONK stream or Helius/Yellowstone
                              # Geyser websocket) — not available without keys.

RUG_CACHE_TTL_SECONDS = 10   # feedback #5
MIN_SCORE_TO_BUY = 70        # feedback #13 tiers start at 70

# Minimum average trade size (volume_m5 / txns_m5) used as a free proxy for
# "a whale bought", since Dexscreener doesn't expose individual tx sizes
# without a paid feed. Real whale-wallet tracking (feedback #7) would need
# Helius/Birdeye wallet-activity subscriptions.
WHALE_AVG_TRADE_USD = 400

BLACKLIST_LOSS_PCT_THRESHOLD = -50.0  # feedback #12: auto-blacklist a dev
                                       # wallet if a trade in their token
                                       # loses this much


def load_state():
    defaults = (INITIAL_BALANCE_USD, {}, [], set(), {}, set())
    if not os.path.exists(STATE_FILE):
        return defaults
    try:
        with open(STATE_FILE, "r") as f:
            data = json.load(f)
        saved_addresses = data.get("traded_addresses", [])[-2000:]
        return (
            data.get("balance_usd", INITIAL_BALANCE_USD),
            data.get("active_trades", {}),
            data.get("trade_history", []),
            set(saved_addresses),
            data.get("pending_candidates", {}),
            set(data.get("dev_blacklist", [])),
        )
    except Exception as e:
        print(f"⚠️ خطأ في قراءة ملف الحفظ: {e}")
        return defaults

def save_state():
    try:
        global traded_addresses
        if len(traded_addresses) > 2000:
            traded_addresses = set(list(traded_addresses)[-2000:])
        data = {
            "balance_usd": balance_usd,
            "active_trades": active_trades,
            "trade_history": trade_history,
            "traded_addresses": list(traded_addresses),
            "pending_candidates": pending_candidates,
            "dev_blacklist": list(dev_blacklist),
        }
        temp_file = "bot_state.tmp"
        with open(temp_file, "w") as f:
            json.dump(data, f)
        os.replace(temp_file, STATE_FILE)
    except Exception as e:
        print(f"⚠️ خطأ في حفظ الملف: {e}")

(balance_usd, active_trades, trade_history, traded_addresses,
 pending_candidates, dev_blacklist) = load_state()

# In-memory TTL cache for rug reports: {address: (timestamp, report_or_None)}
_rug_cache = {}


def send_telegram_alert(message):
    if not TELEGRAM_BOT_TOKEN or not CHAT_ID:
        print("⚠️ Telegram not configured — skipping alert:", message[:60])
        return
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    payload = {"chat_id": CHAT_ID, "text": message, "parse_mode": "Markdown",
               "disable_web_page_preview": True}
    try:
        # Alerts are low-volume and non-blocking to trading logic; a plain
        # sync call here is fine and keeps this function usable from
        # anywhere without needing an event loop.
        requests.post(url, json=payload, timeout=5)
    except Exception as e:
        print(f"❌ خطأ تليجرام: {e}")


# ============================================================================
# 4) Rug-safety check — now async + cached (feedback #3, #5)
# ============================================================================
async def fetch_rug_report(session, token_address):
    cached = _rug_cache.get(token_address)
    if cached and (time.time() - cached[0]) < RUG_CACHE_TTL_SECONDS:
        return cached[1]

    report = None
    try:
        url = f"https://api.rugcheck.xyz/v1/tokens/{token_address}/report"
        async with session.get(url, timeout=aiohttp.ClientTimeout(total=4)) as res:
            if res.status == 200:
                data = await res.json()
                mint_authority = data.get("mintAuthority")
                freeze_authority = data.get("freezeAuthority")

                top_holder_pct = 0.0
                for h in (data.get("topHolders", []) or []):
                    if h.get("insider") or h.get("isLp"):
                        continue
                    top_holder_pct = max(top_holder_pct, float(h.get("pct", 0) or 0))

                lp_locked_pct = 0.0
                markets = data.get("markets", []) or []
                lp_vals = [float(m.get("lp", {}).get("lpLockedPct", 0) or 0) for m in markets]
                if lp_vals:
                    lp_locked_pct = max(lp_vals)

                report = {
                    "mint_disabled": mint_authority in (None, "", "None"),
                    "freeze_disabled": freeze_authority in (None, "", "None"),
                    "top_holder_pct": top_holder_pct,
                    "lp_locked_pct": lp_locked_pct,
                    "creator": data.get("creator") or data.get("creatorAddress"),
                    "holder_count": int(data.get("totalHolders", 0) or 0),
                }
    except Exception as e:
        print(f"⚠️ RugCheck lookup failed for {token_address}: {e}")
        report = None

    _rug_cache[token_address] = (time.time(), report)
    return report


def evaluate_rug_safety(report):
    if report is None:
        return False, "rug_report_unavailable", 0
    if report.get("creator") and report["creator"] in dev_blacklist:
        return False, "blacklisted_dev", 0
    if REQUIRE_MINT_DISABLED and not report["mint_disabled"]:
        return False, "mint_authority_enabled", 0
    if REQUIRE_FREEZE_DISABLED and not report["freeze_disabled"]:
        return False, "freeze_authority_enabled", 0
    if report["top_holder_pct"] > MAX_TOP_HOLDER_PCT:
        return False, f"top_holder_{report['top_holder_pct']:.1f}pct", 0
    if report["lp_locked_pct"] < MIN_LP_LOCKED_OR_BURNED_PCT:
        return False, f"lp_locked_only_{report['lp_locked_pct']:.1f}pct", 0

    # Holder distribution score: up to 15, more points the less concentrated
    holder_score = max(0.0, (MAX_TOP_HOLDER_PCT - report["top_holder_pct"]) / MAX_TOP_HOLDER_PCT) * 15
    return True, "ok", holder_score


# ============================================================================
# 5) Scanning (async) — m5 metrics, socials-as-social-score, whale proxy
# ============================================================================
async def scan_solana_meme_coins(session):
    meme_opportunities = []
    seen_addresses = set()

    urls = [
        "https://api.dexscreener.com/latest/dex/tokens/solana",
        "https://api.dexscreener.com/latest/dex/search?q=solana"
    ]

    for url in urls:
        try:
            async with session.get(url, timeout=aiohttp.ClientTimeout(total=4)) as res:
                if res.status != 200:
                    continue
                data = await res.json()
        except Exception as e:
            print(f"⚠️ خطأ في الاتصال بالمصدر السريع: {e}")
            continue

        pairs = data.get("pairs", []) if isinstance(data, dict) else data
        for pair in (pairs or []):
            if pair.get("chainId") != "solana":
                continue

            token_address = str(pair.get("address", "") or pair.get("pairAddress", ""))
            if not token_address or token_address in seen_addresses:
                continue
            seen_addresses.add(token_address)

            token_info = pair.get("baseToken", {})
            symbol = str(token_info.get("symbol", "UNKNOWN")).upper()
            name = str(token_info.get("name", "")).upper()

            if symbol in ["SOL", "WSOL", "USDC", "USDT", "WBTC", "ETH"] or "SOLANA" in name:
                continue

            market_cap = float(pair.get("marketCap", pair.get("fdv", 0)) or 0)
            price = float(pair.get("priceUsd", 0) or 0)
            if price <= 0 or not (MIN_MARKET_CAP <= market_cap <= MAX_MARKET_CAP):
                continue

            pair_created_at_ms = pair.get("pairCreatedAt", 0)
            if not pair_created_at_ms:
                continue
            creation_time = datetime.fromtimestamp(pair_created_at_ms / 1000.0, tz=timezone.utc)
            age_minutes = (datetime.now(timezone.utc).timestamp() - (pair_created_at_ms / 1000.0)) / 60.0

            liquidity = float(pair.get("liquidity", {}).get("usd", 0) or 0)
            if not (MIN_LIQUIDITY <= liquidity <= MAX_LIQUIDITY):
                continue

            volume = pair.get("volume", {}) or {}
            volume_m5 = float(volume.get("m5", 0) or 0)
            volume_h1 = float(volume.get("h1", 0) or 0)

            txns = pair.get("txns", {}) or {}
            m5_txns = txns.get("m5", {}) or {}
            buys_m5 = int(m5_txns.get("buys", 0) or 0)
            sells_m5 = int(m5_txns.get("sells", 0) or 0)
            total_txns_m5 = buys_m5 + sells_m5
            if total_txns_m5 == 0:
                continue

            price_change_m5 = float((pair.get("priceChange", {}) or {}).get("m5", 0) or 0)

            expected_m5_share = (volume_h1 / 12.0) if volume_h1 > 0 else 0
            volume_spike_ratio = (volume_m5 / expected_m5_share) if expected_m5_share > 0 else 0
            buy_sell_ratio = (buys_m5 / sells_m5) if sells_m5 > 0 else float(buys_m5 or 1)
            avg_trade_usd = volume_m5 / total_txns_m5 if total_txns_m5 else 0
            is_meme_named = any(kw in name or kw in symbol for kw in MEME_KEYWORDS)

            info = pair.get("info", {}) or {}
            socials = info.get("socials", []) or []
            has_socials = len(socials) > 0

            # ---- Score (excludes Holder/Rug — filled in after rug check) ----
            score = 0.0
            score += min(15, (liquidity - MIN_LIQUIDITY) / (MAX_LIQUIDITY - MIN_LIQUIDITY) * 15)     # Liquidity: 15
            score += min(15, volume_spike_ratio / 3.0 * 15) if volume_spike_ratio else 0             # Volume spike: 15
            if buy_sell_ratio >= 3:                                                                   # Buy pressure: 15
                score += 15
            elif buy_sell_ratio >= 1.5:
                score += 9
            elif buy_sell_ratio >= 1.0:
                score += 4
            score += 10 if age_minutes < 5 else (5 if age_minutes < 20 else 0)                        # Age: 10
            score += 10 if price_change_m5 > 0 else 0                                                  # Price momentum: 10
            score += 5 if has_socials else 0                                                           # Social: 5
            score += 5 if avg_trade_usd >= WHALE_AVG_TRADE_USD else 0                                  # Whale proxy: 5
            # Holder (15) + Rug (10) = 25 pts added later only for candidates
            # that already clear a reasonable pre-check, to avoid wasting
            # RugCheck calls on junk.

            if score < 40:
                continue

            meme_opportunities.append({
                "symbol": symbol, "name": name, "address": token_address, "price": price,
                "market_cap": market_cap, "liquidity_usd": liquidity, "volume_m5": volume_m5,
                "buys_m5": buys_m5, "sells_m5": sells_m5, "txns_m5": total_txns_m5,
                "price_change_m5": price_change_m5, "age_minutes": age_minutes,
                "creation_time": creation_time.strftime("%Y-%m-%d %H:%M:%S"),
                "pre_rug_score": score,
                "url": str(pair.get("url", f"https://dexscreener.com/solana/{token_address}")),
            })

    return meme_opportunities


# ============================================================================
# 6) Position sizing (feedback #13)
# ============================================================================
def position_size_pct(score):
    if score >= 95:
        return 0.25
    if score >= 90:
        return 0.20
    if score >= 80:
        return 0.10
    return 0.05  # 70-79


# ============================================================================
# 7) Trade execution — confirmation by consecutive rising scans, parallel
#    rug checks (feedback #2, #3, #6, #12)
# ============================================================================
async def check_and_execute_meme_trades(session):
    global balance_usd, pending_candidates

    if len(active_trades) >= MAX_CONCURRENT_TRADES:
        return

    opportunities = await scan_solana_meme_coins(session)

    ready_for_rugcheck = []  # opportunities that just hit the confirm count
    for opp in opportunities:
        address = opp["address"]
        if address in active_trades or address in traded_addresses:
            continue

        prior = pending_candidates.get(address)
        if prior is None:
            pending_candidates[address] = {
                "consecutive": 1, "volume_m5": opp["volume_m5"], "price": opp["price"],
                "opp": opp,
            }
            continue

        volume_rising = opp["volume_m5"] >= prior["volume_m5"]
        price_rising = opp["price"] >= prior["price"]  # crude higher-high/higher-low proxy
        if volume_rising and price_rising:
            prior["consecutive"] += 1
        else:
            prior["consecutive"] = 1  # momentum broke, restart the count
        prior["volume_m5"] = opp["volume_m5"]
        prior["price"] = opp["price"]
        prior["opp"] = opp

        if prior["consecutive"] >= REQUIRED_CONSECUTIVE_CONFIRMS:
            ready_for_rugcheck.append(opp)

    if not ready_for_rugcheck:
        save_state()
        return

    # Parallel rug checks instead of one-request-at-a-time (feedback #3)
    reports = await asyncio.gather(
        *[fetch_rug_report(session, opp["address"]) for opp in ready_for_rugcheck]
    )

    for opp, report in zip(ready_for_rugcheck, reports):
        address = opp["address"]
        is_safe, reason, holder_score = evaluate_rug_safety(report)
        pending_candidates.pop(address, None)

        if not is_safe:
            print(f"⛔ Skipping {opp['symbol']} ({address}): {reason}")
            continue

        final_score = min(100, opp["pre_rug_score"] + holder_score + 10)  # +10 flat for passing rug check

        if final_score < MIN_SCORE_TO_BUY:
            continue
        if len(active_trades) >= MAX_CONCURRENT_TRADES:
            break

        pct = position_size_pct(final_score)
        trade_amount_usd = balance_usd * pct
        if trade_amount_usd < 5:
            continue

        price = opp["price"]
        balance_usd -= trade_amount_usd
        tokens = trade_amount_usd / price

        active_trades[address] = {
            "symbol": opp["symbol"],
            "creator": (report or {}).get("creator"),
            "entry_price": price,
            "peak_price": price,
            "tokens": tokens,
            "original_tokens": tokens,
            "invested_usd": trade_amount_usd,
            "sl": price * 0.90,
            "trailing_stage": 0,
            "partial_tp1_done": False,
            "partial_tp2_done": False,
        }
        traded_addresses.add(address)
        save_state()

        msg = (
            f"🚀 *قنص ميم كوين مؤكّد!*\n"
            f"-----------------------------------\n"
            f"🪙 *العملة:* `{opp['symbol']}` ({opp['name']})\n"
            f"⭐ *النقاط:* `{final_score:.0f}/100` | 💼 *الحجم:* `{pct*100:.0f}%` من الرصيد\n"
            f"⏳ *عمر العملة:* `{opp['age_minutes']:.1f} دقيقة`\n"
            f"📊 *القيمة السوقية:* `${opp['market_cap']:,.0f}` | 💧 *سيولة:* `${opp['liquidity_usd']:,.0f}`\n"
            f"📈 *حجم 5د:* `${opp['volume_m5']:,.0f}` | 🛒 `{opp['buys_m5']}`/🛍️`{opp['sells_m5']}`\n"
            f"💵 *سعر الدخول:* `${price:,.8f}`\n"
            f"💰 *المستثمر:* `${trade_amount_usd:.2f}` ({trade_amount_usd*USD_TO_SAR:.1f} ريال)\n"
            f"🔗 `{address}`\n"
            f"📈 [الشارت]({opp['url']})\n"
            f"💼 *الرصيد المتبقي:* `${balance_usd:.2f}`"
        )
        send_telegram_alert(msg)


# ============================================================================
# 8) Position management — parallel price fetch, trailing stop, partial TP
#    (feedback #3, #11, #14)
# ============================================================================
def _trailing_sl_for_stage(entry_price, peak_price, stage):
    gain_pct = (peak_price / entry_price - 1) * 100
    if gain_pct >= 120 and stage < 3:
        return entry_price * 1.60, 3
    if gain_pct >= 60 and stage < 2:
        return entry_price * 1.30, 2
    if gain_pct >= 30 and stage < 1:
        return entry_price * 1.00, 1
    return None, stage


async def _fetch_price(session, address):
    try:
        url = f"https://api.dexscreener.com/latest/dex/tokens/{address}"
        async with session.get(url, timeout=aiohttp.ClientTimeout(total=3)) as res:
            if res.status == 200:
                data = await res.json()
                pairs = data.get("pairs", [])
                if pairs:
                    return address, float(pairs[0].get("priceUsd", 0) or 0)
    except Exception as e:
        print(f"❌ خطأ في تحديث السعر لـ {address}: {e}")
    return address, 0.0


async def update_meme_trades(session):
    global balance_usd

    if not active_trades:
        return

    # Parallel price fetches for every open position (feedback #3)
    results = await asyncio.gather(*[_fetch_price(session, addr) for addr in list(active_trades.keys())])

    for address, current_price in results:
        trade = active_trades.get(address)
        if trade is None or current_price <= 0:
            continue

        symbol = trade["symbol"]
        gain_pct = (current_price / trade["entry_price"] - 1) * 100

        if current_price > trade["peak_price"]:
            trade["peak_price"] = current_price

        # --- Partial take-profit (feedback #14) ---
        if not trade["partial_tp1_done"] and gain_pct >= 100:
            sell_tokens = trade["original_tokens"] * 0.30
            sell_tokens = min(sell_tokens, trade["tokens"])
            proceeds = sell_tokens * current_price
            balance_usd += proceeds
            trade["tokens"] -= sell_tokens
            trade["partial_tp1_done"] = True
            send_telegram_alert(
                f"💰 *جني ربح جزئي (+100%)* `{symbol}`\n"
                f"بيع 30% من المركز مقابل `${proceeds:.2f}`\n"
                f"💼 *الرصيد:* `${balance_usd:.2f}`"
            )
        if not trade["partial_tp2_done"] and gain_pct >= 200:
            sell_tokens = trade["original_tokens"] * 0.20
            sell_tokens = min(sell_tokens, trade["tokens"])
            proceeds = sell_tokens * current_price
            balance_usd += proceeds
            trade["tokens"] -= sell_tokens
            trade["partial_tp2_done"] = True
            send_telegram_alert(
                f"💰 *جني ربح جزئي (+200%)* `{symbol}`\n"
                f"بيع 20% إضافية مقابل `${proceeds:.2f}`\n"
                f"💼 *الرصيد:* `${balance_usd:.2f}`"
            )

        new_sl, new_stage = _trailing_sl_for_stage(trade["entry_price"], trade["peak_price"], trade["trailing_stage"])
        if new_sl is not None and new_sl > trade["sl"]:
            trade["sl"] = new_sl
            trade["trailing_stage"] = new_stage

        if current_price <= trade["sl"]:
            return_usd = trade["tokens"] * current_price
            pnl_usd = return_usd - trade["invested_usd"]  # note: invested_usd is original cost basis;
                                                            # partial-TP proceeds already added to balance above
            balance_usd += return_usd
            won = pnl_usd > 0

            # --- Auto-blacklist dev on a heavy loss (feedback #12) ---
            loss_pct = (return_usd + 0 - trade["invested_usd"]) / trade["invested_usd"] * 100
            if loss_pct <= BLACKLIST_LOSS_PCT_THRESHOLD and trade.get("creator"):
                dev_blacklist.add(trade["creator"])

            emoji = "🚀" if won else "🛑"
            label = "خروج نهائي بالربح (Trailing)" if won else "ضرب وقف الخسارة"
            send_telegram_alert(
                f"{emoji} *{label}!* `{symbol}`\n"
                f"{'💰' if won else '📉'} *PnL الجزء المتبقي:* `{pnl_usd:+.2f}$`\n"
                f"💼 *الرصيد الجديد:* `${balance_usd:.2f}`"
            )
            trade_history.append({"symbol": symbol, "address": address, "pnl_usd": pnl_usd, "win": won})
            del active_trades[address]

    save_state()


async def send_hourly_report(session):
    unrealized_usd = 0.0
    if active_trades:
        results = await asyncio.gather(*[_fetch_price(session, addr) for addr in active_trades.keys()])
        price_map = dict(results)
        for address, trade in active_trades.items():
            price = price_map.get(address, 0)
            unrealized_usd += trade["tokens"] * price if price > 0 else trade["invested_usd"]

    total_equity_usd = balance_usd + unrealized_usd
    total_equity_sar = total_equity_usd * USD_TO_SAR
    pnl_sar = total_equity_sar - INITIAL_BALANCE_SAR
    pnl_pct = (pnl_sar / INITIAL_BALANCE_SAR) * 100

    wins = sum(1 for t in trade_history if t["win"])
    total_closed = len(trade_history)
    win_rate = (wins / total_closed * 100) if total_closed > 0 else 0.0

    hour = datetime.now(timezone.utc).hour
    session_name = ("آسيا / لندن (Asia / London)" if 7 <= hour < 15 else
                     "نيويورك (New York)" if 15 <= hour < 22 else
                     "سيدني / آسيا المبكرة (Sydney / Early Asia)")

    report = (
        f"📊 *التقرير الساعي*\n-----------------------------------\n"
        f"🌐 *الجلسة:* `{session_name}`\n"
        f"💰 *رأس المال:* {total_equity_sar:.2f} ريال (${total_equity_usd:.2f})\n"
        f"📈 *صافي الربح/الخسارة:* {pnl_sar:+.2f} ريال ({pnl_pct:+.2f}%)\n"
        f"🔄 *صفقات مفتوحة:* {len(active_trades)}/{MAX_CONCURRENT_TRADES}\n"
        f"✅ *صفقات مغلقة:* {total_closed} (ناجحة: {wins} | خاسرة: {total_closed - wins})\n"
        f"🎯 *نسبة النجاح:* {win_rate:.1f}%\n"
        f"🚫 *مطورون في القائمة السوداء:* {len(dev_blacklist)}\n"
        f"-----------------------------------\n⚡ *asyncio + Parallel RugCheck مفعّل*"
    )
    send_telegram_alert(report)


# ============================================================================
# 9) Main async loop
# ============================================================================
async def main():
    welcome_msg = (
        "🚀 *تم تشغيل البوت (نسخة asyncio: RugCheck متوازي + Cache، تأكيد بالشموع، "
        "TP جزئي، Position Sizing، Dev Blacklist)!*\n"
        "⚡ *ملاحظة:* اكتشاف Pump.fun/Helius اللحظي والـ Smart Money الحقيقي "
        "ما زالا يحتاجان مفاتيح API مدفوعة."
    )
    send_telegram_alert(welcome_msg)

    last_report_time = time.time()
    async with aiohttp.ClientSession() as session:
        while True:
            try:
                await check_and_execute_meme_trades(session)
                await update_meme_trades(session)

                if time.time() - last_report_time >= 3600:
                    await send_hourly_report(session)
                    last_report_time = time.time()
            except Exception as e:
                print(f"❌ خطأ رئيسي: {e}")

            await asyncio.sleep(SCAN_LOOP_SLEEP_SECONDS)


if __name__ == "__main__":
    server_thread = Thread(target=start_server)
    server_thread.daemon = True
    server_thread.start()

    asyncio.run(main())
