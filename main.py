import os
import time
from datetime import datetime
from threading import Thread
from flask import Flask
import requests

# 1️⃣ خادم Web لـ Render
app = Flask(__name__)


@app.route('/')
def health_check():
  return 'Paper Trading Bot is Alive!', 200


def start_server():
  port = int(os.environ.get('PORT', 10000))
  app.run(host='0.0.0.0', port=port, debug=False, use_reloader=False)


# 2️⃣ الإعدادات وبيانات التليجرام
TELEGRAM_BOT_TOKEN = '8596265665:AAEdjiNIHoA6D-oFmr_iCsaBbomwcdhqgp0'
CHAT_ID = '1015963752'

# المحفظة الوهمية (1000 ريال سعودي = ~266.67 دولار)
USD_TO_SAR = 3.75
initial_balance_sar = 1000.0
balance_usd = initial_balance_sar / USD_TO_SAR

# ذاكرة الصفقات والمراقبة
active_trades = {}  # الصفقات الحالية
trade_history = []  # سجل الصفقات المغلقة
alerted_tokens = set()

# شروط الفلترة
MIN_LIQUIDITY_USD = 2000.0
MIN_MARKET_CAP_USD = 5000.0


def send_telegram_alert(message):
  url = f'https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage'
  payload = {'chat_id': CHAT_ID, 'text': message, 'parse_mode': 'Markdown'}
  try:
    requests.post(url, json=payload, timeout=10)
  except Exception as e:
    print(f'❌ خطأ تليجرام: {e}')


def fetch_token_data(token_address):
  """جلب بيانات العملة من DexScreener"""
  try:
    url = f'https://api.dexscreener.com/latest/dex/tokens/{token_address}'
    res = requests.get(
        url,
        headers={
            'User-Agent': (
                'Mozilla/5.0 (iPhone; CPU iPhone OS 16_0 like Mac OS X)'
            )
        },
        timeout=10,
    )
    if res.status_code == 200:
      pairs = res.json().get('pairs', [])
      if pairs:
        return pairs[0]
  except Exception as e:
    print(f'❌ خطأ جلب البيانات: {e}')
  return None


def execute_paper_buy(token_address, pair_data):
  """محاكاة عملية الشراء"""
  global balance_usd
  symbol = pair_data.get('baseToken', {}).get('symbol', 'UNKNOWN')
  price_usd = float(pair_data.get('priceUsd', 0) or 0)

  if price_usd <= 0:
    return

  # تحديد حجم الصفقة (10% من الرصيد الحالي)
  trade_amount_usd = balance_usd * 0.10
  if trade_amount_usd < 5:  # حد أدنى للصفقة
    return

  balance_usd -= trade_amount_usd
  tokens_bought = trade_amount_usd / price_usd

  active_trades[token_address] = {
      'symbol': symbol,
      'entry_price': price_usd,
      'tokens': tokens_bought,
      'invested_usd': trade_amount_usd,
      'entry_time': datetime.now(),
  }

  msg = (
      f'🛒 *صفقة شراء وهمية جديدة!*\n\n'
      f'🪙 *العملة:* `{symbol}`\n'
      f'💵 *مبلغ الدخول:* ${trade_amount_usd:.2f} ({trade_amount_usd * USD_TO_SAR:.1f} ريال)\n'
      f'📈 *سعر الدخول:* ${price_usd:.8f}\n'
      f'💼 *الرصيد المتبقي:* ${balance_usd:.2f} ({balance_usd * USD_TO_SAR:.1f} ريال)'
  )
  send_telegram_alert(msg)


def update_and_check_trades():
  """متابعة الصفقات المفتوحة وإغلاقها عند جني الربح أو وقف الخسارة"""
  global balance_usd
  for token_address, trade in list(active_trades.items()):
    pair_data = fetch_token_data(token_address)
    if not pair_data:
      continue

    current_price = float(pair_data.get('priceUsd', 0) or 0)
    if current_price <= 0:
      continue

    entry_price = trade['entry_price']
    price_change_pct = ((current_price - entry_price) / entry_price) * 100

    # شرط جني الأرباح (+50%) أو وقف الخسارة (-20%)
    if price_change_pct >= 50.0 or price_change_pct <= -20.0:
      return_usd = trade['tokens'] * current_price
      pnl_usd = return_usd - trade['invested_usd']
      balance_usd += return_usd

      status = '🎉 جني أرباح (+50%)' if price_change_pct >= 50 else '🛑 وقف خسارة (-20%)'

      msg = (
          f'{status}\n\n'
          f'🪙 *العملة:* `{trade["symbol"]}`\n'
          f'📊 *نسبة التغير:* `{price_change_pct:+.2f}%`\n'
          f'💰 *الربح/الخسارة:* ${pnl_usd:+.2f} ({pnl_usd * USD_TO_SAR:+.1f} ريال)\n'
          f'💼 *رصيد المحفظة الجديد:* ${balance_usd:.2f} ({balance_usd * USD_TO_SAR:.1f} ريال)'
      )
      send_telegram_alert(msg)

      trade_history.append({
          'symbol': trade['symbol'],
          'pnl_usd': pnl_usd,
          'change_pct': price_change_pct,
      })
      del active_trades[token_address]


def send_hourly_report():
  """إرسال التقرير الساعي"""
  total_equity_usd = balance_usd + sum(
      t['invested_usd'] for t in active_trades.values()
  )
  total_equity_sar = total_equity_usd * USD_TO_SAR
  profit_loss_sar = total_equity_sar - initial_balance_sar
  profit_loss_pct = (
      (total_equity_sar - initial_balance_sar) / initial_balance_sar
  ) * 100

  report = (
      f'📊 *التقرير الساعي للمحفظة الوهمية*\n'
      f'-----------------------------------\n'
      f'💰 *رأس المال الحالي:* {total_equity_sar:.2f} ريال (${total_equity_usd:.2f})\n'
      f'📈 *إجمالي الأرباح/الخسائر:* {profit_loss_sar:+.2f} ريال ({profit_loss_pct:+.2f}%)\n'
      f'🔄 *الصفقات المفتوحة حالياً:* {len(active_trades)}\n'
      f'✅ *الصفقات المغلقة إجمالاً:* {len(trade_history)}\n'
      f'-----------------------------------\n'
      f'⚙️ *البوت يعمل ويصيد 24/7*'
  )
  send_telegram_alert(report)


def check_and_trade(token_address):
  if token_address in alerted_tokens:
    return

  pair_data = fetch_token_data(token_address)
  if not pair_data:
    return

  mcap = pair_data.get('fdv', 0) or pair_data.get('marketCap', 0)
  liq = pair_data.get('liquidity', {}).get('usd', 0)

  # تطبيق الشروط الأربعة
  if mcap >= MIN_MARKET_CAP_USD and liq >= MIN_LIQUIDITY_USD:
    alerted_tokens.add(token_address)
    execute_paper_buy(token_address, pair_data)


# 3️⃣ الحلقة الرئيسية والدورة التكرارية
if __name__ == '__main__':
  server_thread = Thread(target=start_server)
  server_thread.daemon = True
  server_thread.start()

  welcome = (
      '🚀 *تم تشغيل بوت التداول الوهمي بنجاح!*\n'
      '💰 *رأس المال المبدئي:* 1,000 ريال سعودي ($266.67 USD)\n'
      '⏰ سيصلك تقرير ساعي بحالة المحفظة والصفقات.'
  )
  send_telegram_alert(welcome)

  tokens_to_watch = ['4vXNhA6ncbx8usZ14CfxkYeQKdaQYgrLfJXNywcVpump']
  last_report_time = time.time()

  while True:
    # 1. فحص الفرص والشراء
    for token in tokens_to_watch:
      check_and_trade(token)
      time.sleep(5)

    # 2. تحديث ومتابعة الصفقات القائمة
    update_and_check_trades()

    # 3. إرسال التقرير الساعي كل 3600 ثانية (ساعة)
    if time.time() - last_report_time >= 3600:
      send_hourly_report()
      last_report_time = time.time()

    time.sleep(30)
