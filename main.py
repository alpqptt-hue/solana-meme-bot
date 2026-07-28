import os
import time
from threading import Thread
from flask import Flask
import requests

# 1️⃣ إنشاء سيرفر Flask خفيف للمحافظة على المجانية في Render
app = Flask(__name__)


@app.route('/')
def health_check():
  return 'Bot is Alive!', 200


def start_server():
  port = int(os.environ.get('PORT', 10000))
  # تشغيل السيرفر بدون debug لتفادي الحظر
  app.run(host='0.0.0.0', port=port, debug=False, use_reloader=False)


# 2️⃣ بيانات التليجرام
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


# 3️⃣ نقطة الانطلاق الرئيسية
if __name__ == '__main__':
  # تشغيل السيرفر في Thread جانبي
  server_thread = Thread(target=start_server)
  server_thread.daemon = True
  server_thread.start()

  # إرسال رسالة الترحيب فوراً للتليجرام
  welcome = (
      '🤖 *تم تشغيل بوت رادار الميم كوينز بنجاح!*\nالبوت شغال مجاناً 24/7 على'
      ' Render.'
  )
  send_telegram_alert(welcome)
  print(welcome)

  # حلقة فحص العملات
  tokens = ['4vXNhA6ncbx8usZ14CfxkYeQKdaQYgrLfJXNywcVpump']

  while True:
    for token in tokens:
      check_token(token)
      time.sleep(10)
    print('\n😴 انتظار الدورة القادمة (60 ثانية)...')
    time.sleep(60)
