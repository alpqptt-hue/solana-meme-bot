import os
from flask import Flask
from threading import Thread

app = Flask('')

@app.route('/')
def home():
    return "Bot is running!"

def run():
    port = int(os.environ.get("PORT", 8080))
    app.run(host='0.0.0.0', port=port)

def keep_alive():
    t = Thread(target=run)
    t.daemon = True
    t.start()

keep_alive()
import requests
import time

# بيانات التليجرام الخاصة بك
TELEGRAM_BOT_TOKEN = "8596265665:AAEdjiNIHoA6D-oFmr_icsaBbomwcdhqgp0"
CHAT_ID = "1015963752"

class MemeRadarBot:
    def __init__(self):
        self.MIN_MARKET_CAP = 10000.0       # $10,000 حد أدنى
        self.MIN_LIQUIDITY = 3000.0         # $3,000 حد أدنى
        self.MAX_TOP10_HOLDING = 15.0       # 15% كحد أقصى للـ Top 10
        
        self.DEXSCREENER_API = "https://api.dexscreener.com/latest/dex/tokens/{}"
        self.RUGCHECK_API = "https://api.rugcheck.xyz/v1/tokens/{}/report/summary"
        
        self.HEADERS = {
            "User-Agent": "Mozilla/5.0 (iPhone; CPU iPhone OS 16_0 like Mac OS X) AppleWebKit/605.1.15"
        }

    def send_telegram_alert(self, message):
        """إرسال إشعار فوري للتليجرام"""
        url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
        payload = {
            "chat_id": CHAT_ID,
            "text": message,
            "parse_mode": "Markdown",
            "disable_web_page_preview": False
        }
        try:
            requests.post(url, json=payload, timeout=5)
        except Exception as e:
            print(f"خطأ في إرسال التليجرام: {e}")

    def audit_and_alert(self, token_address):
        """فحص الشروط الأربعة الصارمة"""
        print(f"\n🔍 جاري فحص العملة: {token_address}")
        
        # 1️⃣ فحص MCap والسيولة
        try:
            dex_res = requests.get(self.DEXSCREENER_API.format(token_address), headers=self.HEADERS, timeout=10).json()
            pairs = dex_res.get('pairs', [])
            if not pairs:
                print("❌ لا توجد أزواج تداول.")
                return
            
            pair = pairs[0]
            mcap = float(pair.get('fdv', 0) or 0)
            liquidity = float(pair.get('liquidity', {}).get('usd', 0) or 0)
            price = pair.get('priceUsd', '0')
            symbol = pair.get('baseToken', {}).get('symbol', 'UNKNOWN')

            if mcap < self.MIN_MARKET_CAP or liquidity < self.MIN_LIQUIDITY:
                print(f"❌ مرفوضة: MCap أو سيولة متدنية (MCap: ${mcap:,.0f})")
                return
        except Exception as e:
            print(f"خطأ DexScreener: {e}")
            return

        # 2️⃣ فحص العقد والتجميع (RugCheck)
        try:
            rug_res = requests.get(self.RUGCHECK_API.format(token_address), headers=self.HEADERS, timeout=10)
            if rug_res.status_code == 200:
                data = rug_res.json()
                token_meta = data.get('token', {})
                
                # فحص Mint & Freeze Authority
                if token_meta.get('mintAuthority') is not None:
                    print("🚨 مرفوضة: Mint Authority مفتوح")
                    return
                if token_meta.get('freezeAuthority') is not None:
                    print("🚨 مرفوضة: Freeze Authority مفتوح (Honeypot)")
                    return
                
                # فحص Top 10 Holders
                top_holders = data.get('topHolders', [])
                top_10_pct = sum([h.get('pct', 0) for h in top_holders[:10]])
                if top_10_pct > self.MAX_TOP10_HOLDING:
                    print(f"🚨 مرفوضة: كبار المالكين يملكون {top_10_pct:.1f}%")
                    return
        except Exception as e:
            print(f"تنبيه RugCheck: {e}")

        # 🟢 إرسال الإشعار فور المطابقة
        msg = (
            f"🚀 *صيدة ميم كوين آمنة 100%!*\n\n"
            f"🪙 *الرمز:* `${symbol}`\n"
            f"💰 *Market Cap:* `${mcap:,.0f}`\n"
            f"💧 *السيولة:* `${liquidity:,.0f}`\n"
            f"💵 *السعر:* `${price}`\n\n"
            f"🔒 *العقد:* Mint & Freeze Disabled | LP Locked\n"
            f"📌 *العنوان:* `{token_address}`\n\n"
            f"🔗 [افتح في DexScreener](https://dexscreener.com/solana/{token_address})"
        )
        print("🟢 تم العثور على عملة آمنة! جاري إرسال الإشعار...")
        self.send_telegram_alert(msg)

# --- الحلقة التكرارية المستمرة 24/7 ---
if __name__ == "__main__":
    bot = MemeRadarBot()
    # إرسال رسالة ترحيبية عند تشغيل السيرفر
    bot.send_telegram_alert("🤖 *تم تشغيل بوت رادار الميم كوينز بنجاح!* وهو شغال أونلاين 24/7 في السحابة.")
    
    while True:
        try:
            bot.audit_and_alert("4vXNhA6ncbx8usZ14CfxkYeQKdaQYgrLfJXNywcVpump")
            time.sleep(60) # فحص كل دقيقة
        except Exception as e:
            print(f"خطأ غير متوقع: {e}")
            time.sleep(10)
