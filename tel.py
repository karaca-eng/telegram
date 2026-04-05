import streamlit as st
import pandas as pd
import numpy as np
import requests
import time
import threading
from datetime import datetime

# --- CONFIGURATION ---
TELEGRAM_TOKEN = "8650328750:AAGQ-3NlYmpD_Gn5ONUFc59aQYv3UmS2l18"
TELEGRAM_CHAT_ID = "-1003576447874"

# Strateji Ayarları
DROP_THRESHOLD = 5.0  # %5 Derin Düşüş
FLAT_BARS = 32  # 8 Saatlik yataylık
FLAT_THRESHOLD = 5.0  # %5 Yataylık bandı
OFFLINE_THRESHOLD = 60


class MSBRadar:
    def __init__(self):
        self.signals = []
        self.lock = threading.RLock()
        self.headers = {'User-Agent': 'Mozilla/5.0'}
        self.last_heartbeat = time.time()
        self.total_scanned = 0
        self.is_currently_offline = False
        self.send_telegram_msg(
            "🛡️ <b>MSB Pro v5 Aktif!</b>\nSinyal hiyerarşisi düzeltildi: ULTRA sinyalleri artık engellenmeyecek.")

    def send_telegram_msg(self, text):
        try:
            url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
            payload = {"chat_id": TELEGRAM_CHAT_ID, "text": text, "parse_mode": "HTML"}
            requests.post(url, data=payload, timeout=5)
        except:
            pass

    def get_pairs(self):
        try:
            res = requests.get("https://fapi.binance.com/fapi/v1/exchangeInfo").json()
            return [s['symbol'] for s in res['symbols'] if s['quoteAsset'] == 'USDT' and s['status'] == 'TRADING']
        except:
            return []

    def get_daily_red_line(self, symbol):
        try:
            url = f"https://fapi.binance.com/fapi/v1/klines?symbol={symbol}&interval=1d&limit=2"
            res = requests.get(url, headers=self.headers, timeout=3).json()
            return float(res[0][2])
        except:
            return 0.0

    def calculate_macd(self, prices):
        df = pd.Series(prices)
        exp1 = df.ewm(span=12, adjust=False).mean()
        exp2 = df.ewm(span=26, adjust=False).mean()
        return (exp1 - exp2).values

    def analyze_symbol(self, symbol):
        try:
            url = f"https://fapi.binance.com/fapi/v1/klines?symbol={symbol}&interval=15m&limit=200"
            res = requests.get(url, headers=self.headers, timeout=3).json()
            if not isinstance(res, list) or len(res) < 50: return None

            closes = np.array([float(m[4]) for m in res])
            highs = np.array([float(m[2]) for m in res])
            lows = np.array([float(m[3]) for m in res])
            volumes = np.array([float(m[5]) for m in res])
            current_p = closes[-1]

            # 1. DERİN DÜŞÜŞ
            max_48h = np.max(highs)
            drop_rate = (max_48h - current_p) / max_48h * 100
            if drop_rate < DROP_THRESHOLD: return None

            # 2. YATAYLIK VE SARI ÇİZGİ
            h_flat = np.max(highs[-FLAT_BARS:])
            l_flat = np.min(lows[-FLAT_BARS:])
            price_range = (h_flat - l_flat) / current_p * 100
            if price_range > FLAT_THRESHOLD: return None
            yellow_line = h_flat

            # 3. HACİM ONAYI
            avg_vol = np.mean(volumes[-21:-1])
            vol_mult = volumes[-1] / avg_vol if avg_vol > 0 else 0

            # 4. MACD ONAYI
            macd = self.calculate_macd(closes)
            macd_now = macd[-1]
            macd_prev = macd[-2]

            # 5. KIRMIZI ÇİZGİ
            red_line = self.get_daily_red_line(symbol)

            # --- SİNYAL KARAR (Hiyerarşi Güncellendi) ---
            # ULTRA olması için: Fiyat sarıyı kırmalı VE (MACD 0 üstünde olmalı VEYA MACD yukarı yönlü çok güçlü olmalı)
            if current_p > yellow_line and (macd_now > 0 or macd_now > macd_prev):
                status = "💎 ULTRA MSB"
            elif current_p > (yellow_line * 0.996):
                status = "⏳ PRE-MSB"
            else:
                return None

            return {
                "Symbol": symbol.replace("USDT", ""),
                "Price": current_p,
                "Drop": round(drop_rate, 1),
                "Range": round(price_range, 1),
                "VolStatus": round(vol_mult, 1),
                "Target": red_line,
                "Status": status
            }
        except:
            pass
        return None

    def scanner_loop(self):
        while True:
            pairs = self.get_pairs()
            for symbol in pairs:
                self.last_heartbeat = time.time()
                res = self.analyze_symbol(symbol)
                if res:
                    self.add_signal(res)
                self.total_scanned += 1
                time.sleep(0.3)

    def add_signal(self, res):
        with self.lock:
            # SİNYAL HİYERARŞİSİ VE TEKRAR ENGELLEME
            can_send = True
            for s in self.signals[:20]:
                if s['Symbol'] == res['Symbol']:
                    time_diff = (datetime.now() - s['raw_time']).seconds
                    # Eğer son 15 dk içinde zaten ULTRA atılmışsa, bir daha atma
                    if s['Status'] == "💎 ULTRA MSB" and time_diff < 900:
                        can_send = False
                    # Eğer son 15 dk içinde PRE atılmışsa ama YENİ sinyal ULTRA ise, GÖNDER (Yükseltme)
                    elif s['Status'] == "⏳ PRE-MSB" and res['Status'] == "💎 ULTRA MSB":
                        can_send = True
                    # Diğer durumlarda (PRE -> PRE gibi) 15 dk bekle
                    elif time_diff < 900:
                        can_send = False

            if not can_send:
                return

            res['raw_time'] = datetime.now()
            res['Time'] = res['raw_time'].strftime("%H:%M:%S")
            self.signals.insert(0, res)

            # Telegram Sinyali
            msg = (f"🚨 <b>{res['Status']}</b>\n\n"
                   f"💎 Coin: #{res['Symbol']}\n"
                   f"💰 Fiyat: {res['Price']}\n"
                   f"📉 48s Düşüş: -%{res['Drop']}\n"
                   f"📏 Yataylık: %{res['Range']}\n"
                   f"📊 Hacim Gücü: {res['VolStatus']}x\n"
                   f"🎯 <b>Hedef (Kırmızı): {res['Target']}</b>\n"
                   f"🔍 <a href='https://www.tradingview.com/chart/?symbol=BINANCE:{res['Symbol']}USDT.P'>Grafiği Aç</a>")
            self.send_telegram_msg(msg)

            if len(self.signals) > 100: self.signals.pop()


# UI Kısmı v4 ile aynı...
st.set_page_config(layout="wide", page_title="MSB v5 Pro")


@st.cache_resource
def get_radar():
    radar_obj = MSBRadar()
    threading.Thread(target=radar_obj.scanner_loop, daemon=True).start()
    return radar_obj


radar = get_radar()
h1, h2 = st.columns([3, 1])
h1.title("🛡️ MSB v5 Intelligence Radar")
h2.markdown(f"<div style='text-align:right; margin-top:20px; color:#00ff88; font-weight:bold;'>● SYSTEM LIVE</div>",
            unsafe_allow_html=True)
s1, s2, s3 = st.columns(3)
s1.metric("Toplam Tarama", radar.total_scanned)
s2.metric("Aktif Sinyaller", len(radar.signals))
s3.metric("Son Veri Akışı", f"{int(time.time() - radar.last_heartbeat)}s önce")
st.divider()
if not radar.signals:
    st.info("Piyasa taranıyor... ULTRA sinyalleri için kırılım bekleniyor.")
else:
    for sig in radar.signals:
        color = "#00ff88" if "ULTRA" in sig['Status'] else "#f1c40f"
        with st.container():
            st.markdown(f"""
            <div style="background-color: #1e2127; padding: 15px; border-radius: 10px; border-left: 5px solid {color}; margin-bottom: 10px; display: flex; justify-content: space-between; align-items: center;">
                <div>
                    <b style="font-size: 1.3rem; color: #f1c40f;">#{sig['Symbol']}</b> 
                    <span style="color: {color}; margin-left:10px;">{sig['Status']}</span>
                </div>
                <div style="text-align: center;">
                    <small style="color: #bdc3c7;">Fiyat</small><br><b>{sig['Price']}</b>
                </div>
                <div style="text-align: center;">
                    <small style="color: #bdc3c7;">Hacim</small><br><b style="color:#00ff88;">{sig['VolStatus']}x</b>
                </div>
                <div style="text-align: center;">
                    <small style="color: #bdc3c7;">Hedef (Kırmızı)</small><br><b style="color:#ff4b4b;">{sig['Target']}</b>
                </div>
                <div>
                    <a href="https://www.tradingview.com/chart/?symbol=BINANCE:{sig['Symbol']}USDT.P" target="_blank" style="color: #3498db; text-decoration: none; font-weight: bold;">GRAFİK ↗</a>
                </div>
            </div>
            """, unsafe_allow_html=True)
time.sleep(4)
st.rerun()
