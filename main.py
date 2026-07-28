import os
import time
from threading import Thread
from flask import Flask
import requests

# 1️⃣ سيرفر الويب للمحافظة على مجانية Render
app = Flask(__name__)


@app.route('/')
def health_check():
  return 'Bot is Alive!', 200


def start_server():
  port = int(os.environ.get('PORT', 10000))
  app.run(host='0.0.0.0', port=port, debug=False, use_reloader=False)


# 2️⃣ إعدادات التليجرام
TELEGRAM_BOT_TOKEN = '8596265665:AAEdjiNIHoA6D-oFmr_iCsaBbomwcdhqgp0'
CHAT_ID = '1015963752'

# ذاكرة لتخزين العملات المسجلة لمنع تكرار التنبيه
alerted_tokens = set()


def send_telegram_alert(message):
  url = f'https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage'
  payload = {'chat_id': CHAT_ID, 'text': message, 'parse_mode': 'Markdown'}
  try:
    requests.post(url, json=payload, timeout=10)
  except Exception as e:
    print(f'❌ خطأ تليجرام: {e}')


def check_token(token_address):
  # التأكد من أن العملة لم يُرسل عنها تنبيه سابقاً
  if token_address in alerted_tokens:
    return

  print(f'\n🔍 جاري فحص العملة: {token_address}')
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
      data = res.json()
      pairs = data.get('pairs', [])
      if pairs:
        pair = pairs[0]
        mcap = pair.get('fdv', 0) or pair.get('marketCap', 0)
        liq = pair.get('liquidity', {}).get('usd', 0)
        symbol = pair.get('baseToken', {}).get('symbol', 'UNKNOWN')

        msg = (
            f'🔥 *صيدة جديدة!*\n\n'
            f'🪙 *العملة:* `{symbol}`\n'
            f'📊 *القيمة السوقية:* ${mcap:,.2f}\n'
            f'💧 *السيولة:* ${liq:,.2f}\n'
            f'📄 *العقد:* `{token_address}`\n\n'
            f'🌐 [DexScreener Link]({pair.get("url")})'
        )
        send_telegram_alert(msg)
        alerted_tokens.add(token_address)  # حفظ العملة في الذاكرة
        print(f'✅ تم إرسال إشعار العملة {symbol} لمرة واحدة فقط!')
  except Exception as e:
    print(f'❌ خطأ فحص: {e}')


# 3️⃣ تشغيل البوت
if __name__ == '__main__':
  # تشغيل خادم Flask في الخلفية
  server_thread = Thread(target=start_server)
  server_thread.daemon = True
  server_thread.start()

  # قائمة العقود المراد رصدها
  tokens = ['4vXNhA6ncbx8usZ14CfxkYeQKdaQYgrLfJXNywcVpump']

  while True:
    for t in tokens:
      check_token(t)
      time.sleep(10)
    time.sleep(60)
