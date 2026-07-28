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
  return 'Meme Radar Auto Scanner is Running!', 200


def start_server():
  port = int(os.environ.get('PORT', 10000))
  app.run(host='0.0.0.0', port=port, debug=False, use_reloader=False)


# 2️⃣ إعدادات التليجرام
TELEGRAM_BOT_TOKEN = '8596265665:AAEdjiNIHoA6D-oFmr_iCsaBbomwcdhqgp0'
CHAT_ID = '1015963752'

# المحفظة الوهمية (1000 ريال = 266.67 دولار)
USD_TO_SAR = 3.75
initial_balance_sar = 1000.0
balance_usd = initial_balance_sar / USD_TO_SAR

active_trades = {}
trade_history = []
alerted_tokens = set()

# شروط الفلترة المتقدمة (مستوحاة من البوت المحترف)
MIN_MARKET_CAP_USD = 10000.0  # القيمة السوقية لا تقل عن 10 آلاف $
MIN_LIQUIDITY_USD = 3000.0  # السيولة لا تقل عن 3 آلاف $


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


def scan_solana_trending():
  """جلب أحدث عملات سولانا النشطة تلقائياً من DexScreener"""
  try:
    url = 'https://api.dexscreener.com/latest/dex/search?q=solana'
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
      # تصفية أزواج شبكة سولانا فقط
      sol_pairs = [
          p for p in pairs if p.get('chainId') == 'solana' and p.get('priceUsd')
      ]
      return sol_pairs
  except Exception as e:
    print(f'❌ خطأ مسح السوق: {e}')
  return []


def execute_paper_buy(pair_data):
  """محاكاة الشراء للعملة المكتشفة"""
  global balance_usd
  token_address = pair_data.get('baseToken', {}).get('address')
  symbol = pair_data.get('baseToken', {}).get('symbol', 'UNKNOWN')
  price_usd = float(pair_data.get('priceUsd', 0) or 0)
  mcap = pair_data.get('fdv', 0) or pair_data.get('marketCap', 0)
  vol_24h = pair_data.get('volume', {}).get('h24', 0)
  dex_url = pair_data.get('url', '')

  if price_usd <= 0 or not token_address:
    return

  # مبلغ دخول الصفقة (10% من المحفظة)
  trade_amount_usd = balance_usd * 0.10
  if trade_amount_usd < 5:
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
      f'🚀 *صيدة جديدة واردة من الماسح التلقائي!*\n\n'
      f'🪙 *العملة:* `${symbol}`\n'
      f'📊 *القيمة السوقية:* ${mcap:,.0f}\n'
      f'🔥 *الفوليوم (24h):* ${vol_24h:,.0f}\n'
      f'💵 *مبلغ الشراء الوهمي:* ${trade_amount_usd:.2f} ({trade_amount_usd * USD_TO_SAR:.1f} ريال)\n'
      f'📄 *العقد:* `{token_address}`\n\n'
      f'🌐 [DexScreener Link]({dex_url})'
  )
  send_telegram_alert(msg)


def update_and_check_trades():
  """متابعة الصفقات القائمة وإغلاقها"""
  global balance_usd
  for token_address, trade in list(active_trades.items()):
    try:
      url = f'https://api.dexscreener.com/latest/dex/tokens/{token_address}'
      res = requests.get(url, timeout=10)
      if res.status_code == 200:
        pairs = res.json().get('pairs', [])
        if pairs:
          current_price = float(pairs[0].get('priceUsd', 0) or 0)
          if current_price <= 0:
            continue

          entry_price = trade['entry_price']
          price_change_pct = (
              (current_price - entry_price) / entry_price
          ) * 100

          # جني أرباح (+40%) أو وقف خسارة (-15%)
          if price_change_pct >= 40.0 or price_change_pct <= -15.0:
            return_usd = trade['tokens'] * current_price
            pnl_usd = return_usd - trade['invested_usd']
            balance_usd += return_usd

            status = (
                '🎉 جني أرباح (+40%)'
                if price_change_pct >= 40
                else '🛑 وقف خسارة (-15%)'
            )

            msg = (
                f'{status}\n\n'
                f'🪙 *العملة:* `${trade["symbol"]}`\n'
                f'📊 *نسبة التغير:* `{price_change_pct:+.2f}%`\n'
                f'💰 *الربح/الخسارة:* ${pnl_usd:+.2f} ({pnl_usd * USD_TO_SAR:+.1f} ريال)\n'
                f'💼 *رصيد المحفظة:* ${balance_usd:.2f} ({balance_usd * USD_TO_SAR:.1f} ريال)'
            )
            send_telegram_alert(msg)

            trade_history.append({
                'symbol': trade['symbol'],
                'pnl_usd': pnl_usd,
                'change_pct': price_change_pct,
            })
            del active_trades[token_address]
    except Exception as e:
      print(f'❌ خطأ تحديث صفقة: {e}')


def send_hourly_report():
  """التقرير الساعي"""
  total_equity_usd = balance_usd + sum(
      t['invested_usd'] for t in active_trades.values()
  )
  total_equity_sar = total_equity_usd * USD_TO_SAR
  profit_loss_sar = total_equity_sar - initial_balance_sar
  profit_loss_pct = (
      (total_equity_sar - initial_balance_sar) / initial_balance_sar
  ) * 100

  report = (
      f'📊 *التقرير الساعي للماسح التلقائي*\n'
      f'-----------------------------------\n'
      f'💰 *إجمالي المحفظة:* {total_equity_sar:.2f} ريال (${total_equity_usd:.2f})\n'
      f'📈 *الأرباح/الخسائر:* {profit_loss_sar:+.2f} ريال ({profit_loss_pct:+.2f}%)\n'
      f'🔄 *الصفقات المفتوحة:* {len(active_trades)}\n'
      f'✅ *الصفقات المغلقة:* {len(trade_history)}\n'
      f'-----------------------------------\n'
      f'📡 *الماسح يعمل 24/7 ويصيد من التريندات*'
  )
  send_telegram_alert(report)


if __name__ == '__main__':
  server_thread = Thread(target=start_server)
  server_thread.daemon = True
  server_thread.start()

  welcome = (
      '🔥 *تم تشغيل ماسح الميم كوينز التلقائي المطور!*\n'
      'البوت يبحث الآن تلقائياً عن أفضل عملات Solana النشطة مع الفوليوم العالي.'
  )
  send_telegram_alert(welcome)

  last_report_time = time.time()

  while True:
    # 1. مسح تريندات سولانا تلقائياً
    trending_pairs = scan_solana_trending()

    for pair in trending_pairs:
      token_addr = pair.get('baseToken', {}).get('address')
      mcap = pair.get('fdv', 0) or pair.get('marketCap', 0)
      liq = pair.get('liquidity', {}).get('usd', 0)
      vol_24h = pair.get('volume', {}).get('h24', 0)

      # فحص الفلاتر:
      # 1. لم يتنبه عنها سابقاً
      # 2. القيمة السوقية والسيولة فوق الحد الأدنى
      # 3. حجم التداول عالي جداً (الفوليوم أعلى من القيمة السوقية أو قريب منها)
      if (
          token_addr
          and token_addr not in alerted_tokens
          and mcap >= MIN_MARKET_CAP_USD
          and liq >= MIN_LIQUIDITY_USD
          and vol_24h > (mcap * 0.5)
      ):  # الفوليوم أكثر من نصف الماركيت كاب
        alerted_tokens.add(token_addr)
        execute_paper_buy(pair)
        time.sleep(3)

    # 2. متابعة الصفقات القائمة
    update_and_check_trades()

    # 3. التقرير الساعي
    if time.time() - last_report_time >= 3600:
      send_hourly_report()
      last_report_time = time.time()

    time.sleep(30)
