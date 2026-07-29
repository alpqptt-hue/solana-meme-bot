from datetime import datetime
import os
from threading import Thread
import time
from flask import Flask
import requests

# 1️⃣ خادم Flask لإبقاء الخدمة شغالة 24/7 على Render
app = Flask(__name__)


@app.route('/')
def health_check():
  return 'Solana Meme Radar Bot (Smart Strategy) is Running 24/7!', 200


def start_server():
  port = int(os.environ.get('PORT', 10000))
  app.run(host='0.0.0.0', port=port, debug=False, use_reloader=False)


# 2️⃣ إعدادات التليجرام (بوت الميم كوينز)
TELEGRAM_BOT_TOKEN = '8596265665:AAEdjiNIHoA6D-oFmr_iCsaBbomwcdhqgp0'
CHAT_ID = '1015963752'

# 3️⃣ إدارة المحفظة الوهمية ونطاق الصفقات الناجحة
USD_TO_SAR = 3.75
INITIAL_BALANCE_SAR = 1000.0
INITIAL_BALANCE_USD = INITIAL_BALANCE_SAR / USD_TO_SAR

balance_usd = INITIAL_BALANCE_USD
active_trades = {}
MAX_CONCURRENT_TRADES = 3


def send_telegram_alert(message, target_chat_id=None):
  destination_id = target_chat_id if target_chat_id else CHAT_ID
  url = f'https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage'
  payload = {
      'chat_id': destination_id,
      'text': message,
      'parse_mode': 'Markdown',
      'disable_web_page_preview': True,
  }
  try:
    requests.post(url, json=payload, timeout=10)
  except Exception as e:
    print(f'❌ خطأ تليجرام: {e}')


def scan_solana_meme_coins():
  """رصد الميم كوينز بناءً على استراتيجية الصفقات الناجحة (نطاق القيمة السوقية المبكرة)"""
  try:
    url = 'https://api.dexscreener.com/latest/dex/search?q=solana'
    res = requests.get(url, timeout=10)
    if res.status_code == 200:
      pairs = res.json().get('pairs', [])
      meme_opportunities = []

      for pair in pairs[:50]:
        if pair.get('chainId') != 'solana':
          continue

        token_info = pair.get('baseToken', {})
        symbol = str(token_info.get('symbol', 'UNKNOWN')).upper()
        name = str(token_info.get('name', '')).upper()

        # حجب العملات الكبرى
        excluded_tokens = ['SOL', 'WSOL', 'USDC', 'USDT', 'WBTC', 'ETH']
        if symbol in excluded_tokens or 'SOLANA' in name:
          continue

        volume_24h = float(pair.get('volume', {}).get('h24', 0) or 0)
        liquidity_usd = float(pair.get('liquidity', {}).get('usd', 0) or 0)
        market_cap = float(
            pair.get('marketCap', pair.get('fdv', 0)) or 0
        )  # القيمة السوقية
        price = float(pair.get('priceUsd', 0) or 0)

        # 🎯 الشروط المستوحاة من الصفقات الناجحة: القيمة السوقية بين $30K و $70K
        if 30000 <= market_cap <= 70000 and volume_24h >= 40000:
          meme_opportunities.append(
              {'symbol': symbol, 'price': price, 'market_cap': market_cap}
          )
      return meme_opportunities
  except Exception as e:
    print(f'❌ خطأ جلب الميم كوينز: {e}')
  return []


def check_and_execute_meme_trades():
  global balance_usd

  if len(active_trades) >= MAX_CONCURRENT_TRADES:
    return

  opportunities = scan_solana_meme_coins()

  for opp in opportunities:
    symbol = opp['symbol']
    price = opp['price']
    market_cap = opp['market_cap']

    if price <= 0 or symbol in active_trades:
      continue

    trade_amount_usd = balance_usd * 0.20
    if trade_amount_usd < 5:
      continue

    balance_usd -= trade_amount_usd
    tokens = trade_amount_usd / price

    tp_price = price * 1.05  # هدف 5% كبداية آمنة وممتازة
    sl_price = price * 0.98  # وقف خسارة 2%

    active_trades[symbol] = {
        'entry_price': price,
        'tokens': tokens,
        'invested_usd': trade_amount_usd,
        'tp': tp_price,
        'sl': sl_price,
    }

    msg = (
        f'🔥 *صفقة ميم كوين استراتيجية جديدة (استراتيجية الصفقات الناجحة)*\n'
        f'-----------------------------------\n'
        f'🪙 *العملة:* `${symbol}`\n'
        f'📊 *القيمة السوقية لحظة الدخول (MC):* `${market_cap:,.0f}`\n'
        f'💵 *سعر الشراء:* `${price:,.6f}`\n'
        f'💰 *المبلغ المستثمر:* `${trade_amount_usd:.2f}` ({trade_amount_usd * USD_TO_SAR:.1f} ريال)\n'
        f'🎯 *الهدف (TP):* `${tp_price:,.6f}` (+5.0%)\n'
        f'🛑 *وقف الخسارة (SL):* `${sl_price:,.6f}` (-2.0%)\n'
        f'💼 *الرصيد المتبقي:* `${balance_usd:.2f}` ({balance_usd * USD_TO_SAR:.1f} ريال)'
    )
    send_telegram_alert(msg)

    if len(active_trades) >= MAX_CONCURRENT_TRADES:
      break


def update_meme_trades():
  global balance_usd

  for symbol, trade in list(active_trades.items()):
    try:
      url = f'https://api.dexscreener.com/latest/dex/search?q={symbol}'
      res = requests.get(url, timeout=5)
      if res.status_code == 200:
        pairs = res.json().get('pairs', [])
        if not pairs:
          continue
        current_price = float(pairs[0].get('priceUsd', 0) or 0)
        if current_price <= 0:
          continue

        if current_price >= trade['tp']:
          return_usd = trade['tokens'] * current_price
          pnl_usd = return_usd - trade['invested_usd']
          balance_usd += return_usd

          msg = (
              f'🚀 *تم تحقيق الهدف بنجاح! (+5%)*\n'
              f'🪙 *العملة:* `${symbol}`\n'
              f'💰 *الربح:* `+${pnl_usd:.2f}` (`+{pnl_usd * USD_TO_SAR:.1f}` ريال)\n'
              f'💼 *الرصيد الجديد:* `${balance_usd:.2f}` ({balance_usd * USD_TO_SAR:.1f} ريال)'
          )
          send_telegram_alert(msg)
          del active_trades[symbol]

        elif current_price <= trade['sl']:
          return_usd = trade['tokens'] * current_price
          pnl_usd = return_usd - trade['invested_usd']
          balance_usd += return_usd

          msg = (
              f'🛑 *ضرب وقف الخسارة! (-2%)*\n'
              f'🪙 *العملة:* `${symbol}`\n'
              f'📉 *الخسارة:* `${pnl_usd:.2f}` (`{pnl_usd * USD_TO_SAR:.1f}` ريال)\n'
              f'💼 *الرصيد الجديد:* `${balance_usd:.2f}` ({balance_usd * USD_TO_SAR:.1f} ريال)'
          )
          send_telegram_alert(msg)
          del active_trades[symbol]
    except Exception as e:
      print(f'❌ خطأ: {e}')


if __name__ == 'main' or __name__ == '__main__':
  # 1. تشغيل سيرفر Flask
  server_thread = Thread(target=start_server)
  server_thread.daemon = True
  server_thread.start()

  # 2. الحلقة الرئيسية لرصد الميم كوينز
  while True:
    try:
      check_and_execute_meme_trades()
      update_meme_trades()
    except Exception as e:
      print(f'⚠️ خطأ رئيسي: {e}')
    time.sleep(15)
