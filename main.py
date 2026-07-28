   import os
import time
from threading import Thread
from flask import Flask
import requests

# 1️⃣ خادم Flask يعمل في Thread منفصل تماماً
app = Flask('')


@app.route('/')
def home():
  return 'Bot is running 24/7!'


def run_flask():
  port = int(os.environ.get('PORT', 10000))
  app.run(host='0.0.0.0', port=port)


# تشغيل السيرفر في الخلفية
flask_thread = Thread(target=run_flask)
flask_thread.daemon = True
flask_thread.start()

# 2️⃣ إعدادات التليجرام
TELEGRAM_BOT_TOKEN = '8596265665:AAEdjiNIHoA6D-oFmr_icsaBbomwcdhqqp0'
CHAT_ID = '1015963752'


def send_telegram_alert(message):
  url = f'https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage'
  payload = {'chat_id': CHAT_ID, 'text': message, 'parse_mode': 'Markdown'}
  try:
    requests.post(url, json=payload, timeout=10)
  except Exception as e:
    print(f'❌ خطأ تليجرام: {e}')


def check_token(token_address):
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
        print(f'✅ تم إرسال إشعار العملة {symbol} إلى التليجرام!')
  except Exception as e:
    print(f'❌ خطأ فحص: {e}')


# 3️⃣ الحلقة الرئيسية للبوت
if __name__ == '__main__':
  welcome = (
      '🤖 *تم تشغيل بوت رادار الميم كوينز بنجاح!*\nالبوت شغال الآن أونلاين 24/7'
      ' في السحابة.'
  )
  send_telegram_alert(welcome)
  print(welcome)

  tokens = ['4vXNhA6ncbx8usZ14CfxkYeQKdaQYgrLfJXNywcVpump']

  while True:
    for t in tokens:
      check_token(t)
      time.sleep(10)
    print('\n😴 انتظار الدورة القادمة (60 ثانية)...')
    time.sleep(60)
