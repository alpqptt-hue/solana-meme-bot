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
  return 'Solana Sniper Meme Bot is Running 24/7!', 200


def start_server():
  port = int(os.environ.get('PORT', 10000))
  app.run(host='0.0.0.0', port=port, debug=False, use_reloader=False)


# 2️⃣ إعدادات التليجرام (بوت الميم كوينز)
TELEGRAM_BOT_TOKEN = '8596265665:AAEdjiNIHoA6D-oFmr_iCsaBbomwcdhqgp0'
CHAT_ID = '1015963752'

# 3️⃣ إدارة المحفظة الوهمية والأداء
USD_TO_SAR = 3.75
INITIAL_BALANCE_SAR = 1000.0
INITIAL_BALANCE_USD = INITIAL_BALANCE_SAR / USD_TO_SAR

balance_usd = INITIAL_BALANCE_USD
active_trades = {}
trade_history = []
MAX_CONCURRENT_TRADES = 3


def send_telegram_alert(message):
  url = f'https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage'
  payload = {
      'chat_id': CHAT_ID,
      'text': message,
      'parse_mode': 'Markdown',
      'disable_web_page_preview': True,
  }
  try:
    requests.post(url, json=payload, timeout=10)
  except Exception as e:
    print(f'❌ خطأ تليجرام: {e}')


def scan_solana_meme_coins():
  """سكان مخصص لرصد أحدث الميم كوينز الناشئة فور إطلاقها على شبكة سولانا"""
  meme_opportunities = []
  try:
    # نقطة النهاية المباشرة لأحدث الأزواج المضافة حديثاً في DexScreener
    url = 'https://api.dexscreener.com/latest/dex/tokens/solana'
    res = requests.get(url, timeout=10)

    # إذا لم توفر هذه نقطة النهاية نتائج مباشرة، نتحول للبحث الشامل عن سولانا لجلب أحدث الأطراف
    if res.status_code != 200 or not res.json().get('pairs'):
      url = 'https://api.dexscreener.com/latest/dex/search?q=solana'
      res = requests.get(url, timeout=10)

    if res.status_code == 200:
      data = res.json()
      pairs = data.get('pairs', []) if isinstance(data, dict) else data

      for pair in pairs:
        if pair.get('chainId') != 'solana':
          continue

        token_info = pair.get('baseToken', {})
        symbol = str(token_info.get('symbol', 'UNKNOWN')).upper()
        name = str(token_info.get('name', '')).upper()

        # حجب العملات الكبرى والوهمية
        excluded_tokens = ['SOL', 'WSOL', 'USDC', 'USDT', 'WBTC', 'ETH']
        if symbol in excluded_tokens or 'SOLANA' in name:
          continue

        volume_24h = float(pair.get('volume', {}).get('h24', 0) or 0)
        market_cap = float(pair.get('marketCap', pair.get('fdv', 0)) or 0)
        price = float(pair.get('priceUsd', 0) or 0)

        # 🎯 شروط مرنة لاصطياد العملات فور نزولها في بداياتها (قيمة سوقية مبكرة جداً)
        if 5000 <= market_cap <= 120000 and price > 0:
          meme_opportunities.append(
              {'symbol': symbol, 'price': price, 'market_cap': market_cap}
          )
  except Exception as e:
    print(f'❌ خطأ جلب الميم كوينز الحديثة: {e}')
  return meme_opportunities


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

    tp_price = price * 1.08  # هدف 8% لانطلاقات البداية القوية
    sl_price = price * 0.97  # وقف خسارة 3%

    active_trades[symbol] = {
        'entry_price': price,
        'tokens': tokens,
        'invested_usd': trade_amount_usd,
        'tp': tp_price,
        'sl': sl_price,
    }

    msg = (
        f'🚨 *قنص ميم كوين فور نزولها الجديد!*\n'
        f'-----------------------------------\n'
        f'🪙 *العملة:* `${symbol}`\n'
        f'📊 *القيمة السوقية لحظة الولادة (MC):* `${market_cap:,.0f}`\n'
        f'💵 *سعر القنص:* `${price:,.8f}`\n'
        f'💰 *المبلغ المستثمر:* `${trade_amount_usd:.2f}` ({trade_amount_usd * USD_TO_SAR:.1f} ريال)\n'
        f'🎯 *الهدف (TP):* `${tp_price:,.8f}` (+8.0%)\n'
        f'🛑 *وقف الخسارة (SL):* `${sl_price:,.8f}` (-3.0%)\n'
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
              f'🚀 *تم تحقيق هدف القنص بنجاح! (+8%)*\n'
              f'🪙 *العملة:* `${symbol}`\n'
              f'💰 *الربح:* `+${pnl_usd:.2f}` (`+{pnl_usd * USD_TO_SAR:.1f}` ريال)\n'
              f'💼 *الرصيد الجديد:* `${balance_usd:.2f}` ({balance_usd * USD_TO_SAR:.1f} ريال)'
          )
          send_telegram_alert(msg)
          trade_history.append({'symbol': symbol, 'pnl_usd': pnl_usd, 'win': True})
          del active_trades[symbol]

        elif current_price <= trade['sl']:
          return_usd = trade['tokens'] * current_price
          pnl_usd = return_usd - trade['invested_usd']
          balance_usd += return_usd

          msg = (
              f'🛑 *ضرب وقف الخسارة للميم! (-3%)*\n'
              f'🪙 *العملة:* `${symbol}`\n'
              f'📉 *الخسارة:* `${pnl_usd:.2f}` (`{pnl_usd * USD_TO_SAR:.1f}` ريال)\n'
              f'💼 *الرصيد الجديد:* `${balance_usd:.2f}` ({balance_usd * USD_TO_SAR:.1f} ريال)'
          )
          send_telegram_alert(msg)
          trade_history.append(
              {'symbol': symbol, 'pnl_usd': pnl_usd, 'win': False}
          )
          del active_trades[symbol]
    except Exception as e:
      print(f'❌ خطأ: {e}')


def send_hourly_report():
  """تقرير ساعي دقيق ومحسوب لبوت القنص"""
  unrealized_usd = 0.0
  for symbol, trade in active_trades.items():
    try:
      url = f'https://api.dexscreener.com/latest/dex/search?q={symbol}'
      res = requests.get(url, timeout=5)
      if res.status_code == 200:
        pairs = res.json().get('pairs', [])
        if pairs:
          unrealized_usd += trade['tokens'] * float(
              pairs[0].get('priceUsd', 0) or 0
          )
        else:
          unrealized_usd += trade['invested_usd']
      else:
        unrealized_usd += trade['invested_usd']
    except:
      unrealized_usd += trade['invested_usd']

  total_equity_usd = balance_usd + unrealized_usd
  total_equity_sar = total_equity_usd * USD_TO_SAR

  pnl_sar = total_equity_sar - INITIAL_BALANCE_SAR
  pnl_pct = (pnl_sar / INITIAL_BALANCE_SAR) * 100

  wins = sum(1 for t in trade_history if t['win'])
  total_closed = len(trade_history)
  win_rate = (wins / total_closed * 100) if total_closed > 0 else 0.0

  report = (
      f'📊 *التقرير الساعي لبوت قنص الميم كوين*\n'
      f'-----------------------------------\n'
      f'💰 *رأس المال الحالي:* {total_equity_sar:.2f} ريال (${total_equity_usd:.2f})\n'
      f'📈 *صافي الأرباح/الخسائر:* {pnl_sar:+.2f} ريال ({pnl_pct:+.2f}%)\n'
      f'🔄 *الصفقات المفتوحة حالياً:* {len(active_trades)}/{MAX_CONCURRENT_TRADES}\n'
      f'✅ *إجمالي الصفقات المغلقة:* {total_closed} (ناجحة: {wins} | خاسرة: {total_closed - wins})\n'
      f'🎯 *نسبة النجاح:* {win_rate:.1f}%\n'
      f'-----------------------------------\n'
      f'🟢 *رادار القنص اللحظي للإطلاقات الجديدة يعمل 24/7*'
  )
  send_telegram_alert(report)


if __name__ == 'main' or __name__ == '__main__':
  server_thread = Thread(target=start_server)
  server_thread.daemon = True
  server_thread.start()

  welcome_msg = (
      '🚀 *تم تشغيل بوت قنص الميم كوين (Latest Tokens Sniper) بنجاح!*\n'
      '💰 *رأس المال المبدئي:* 1,000 ريال سعودي\n'
      '⏰ سيقنص العملات فور ولادتها ويراسلك بكل جديد.'
  )
  send_telegram_alert(welcome_msg)

  last_report_time = time.time()

  while True:
    try:
      check_and_execute_meme_trades()
      update_meme_trades()

      if time.time() - last_report_time >= 3600:
        send_hourly_report()
        last_report_time = time.time()

    except Exception as e:
      print(f'⚠️ خطأ رئيسي: {e}')

    time.sleep(15)
