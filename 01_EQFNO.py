import os
import time
import requests
import threading
import pandas as pd
import xml.etree.ElementTree as ET
import html
from datetime import datetime, timedelta
from urllib.parse import urlparse, parse_qs, quote
from kiteconnect import KiteConnect
from dotenv import load_dotenv, set_key

# 1. Load Environment Variables
load_dotenv()
API_KEY = os.getenv("ZERODHA_API_KEY")
API_SECRET = os.getenv("ZERODHA_API_SECRET")
TELEGRAM_BOT_TOKEN = os.getenv("01_TELEGRAM_BOT_TOKEN")

# The specific Admin Chat ID authorized to make changes
ADMIN_CHAT_ID = os.getenv("TELEGRAM_CHAT_IDS_01")

# Dynamically gather ALL Telegram Chat IDs from the .env file
TELEGRAM_CHAT_IDS = [
    value for key, value in os.environ.items() 
    if key.startswith("TELEGRAM_CHAT_IDS_") and value.strip()
]

# Initialize KiteConnect & Requests Session for Connection Pooling
kite = KiteConnect(api_key=API_KEY)
session = requests.Session()

# --- GLOBAL VARIABLES FOR TELEGRAM BOT CONTROL ---
mp_bull_threshold = 0.0
mp_bear_threshold = 0.0
awaiting_input_for = None  
last_update_id = 0

def authenticate():
    """Handles Zerodha auth, tests token validity, and auto-saves to .env."""
    env_path = ".env"
    access_token = os.getenv("ZERODHA_ACCESS_TOKEN")
    
    if access_token:
        kite.set_access_token(access_token)
        try:
            kite.margins() 
            print("✅ Valid Access Token found in .env. Proceeding silently...")
            return
        except Exception:
            print("⚠️ Saved token is expired (New Day). Requesting fresh login...")
            access_token = None 
            
    if not access_token:
        print("\nLogin to this URL to get your request token:", kite.login_url())
        redirected_url = input("Paste the ENTIRE redirected URL here: ").strip()
        
        try:
            parsed_url = urlparse(redirected_url)
            request_token = parse_qs(parsed_url.query)['request_token'][0]
            
            session_data = kite.generate_session(request_token, api_secret=API_SECRET)
            access_token = session_data["access_token"]
            
            set_key(env_path, "ZERODHA_ACCESS_TOKEN", access_token)
            kite.set_access_token(access_token)
            print("✅ Auth successful! New token securely saved to .env for today's scans.\n")
            
        except KeyError:
            print("Error: Could not find 'request_token' in the URL. Make sure you pasted the full redirect.")
            exit(1)
        except Exception as e:
            print(f"Authentication failed: {e}")
            exit(1)

def get_fno_instruments_and_symbols():
    """Fetches symbols and pre-groups options for O(1) instant lookup."""
    print("Fetching NFO instruments list...")
    instruments = kite.instruments("NFO")
    fno_symbols = set()
    options_map = {}
    indices = {"NIFTY", "BANKNIFTY", "FINNIFTY", "MIDCPNIFTY", "SENSEX", "BANKEX"}
    
    for ins in instruments:
        name = ins.get('name')
        if name and name not in indices:
            fno_symbols.add(name)
            if ins['instrument_type'] in ['CE', 'PE']:
                options_map.setdefault(name, []).append(ins)
                
    return options_map, sorted(list(fno_symbols))

def get_instrument_tokens(fno_symbols):
    """Maps trading symbols to their Zerodha instrument tokens."""
    nse_instruments = kite.instruments("NSE")
    token_lookup = {ins['tradingsymbol']: ins['instrument_token'] for ins in nse_instruments if ins['tradingsymbol'] in fno_symbols}
    return token_lookup

def get_previous_month_dates():
    """Calculates the exact start and end dates of the previous calendar month."""
    today = datetime.today()
    first_day_curr_month = today.replace(day=1)
    prev_month_end = first_day_curr_month - timedelta(days=1)
    prev_month_start = prev_month_end.replace(day=1)
    return prev_month_start.strftime('%Y-%m-%d'), prev_month_end.strftime('%Y-%m-%d')

def is_dump_fresh(filename="01_EQFNO_All.csv"):
    """Checks if the Equity Dump file was already created today."""
    if not os.path.exists(filename):
        return False
    return datetime.fromtimestamp(os.path.getmtime(filename)).date() == datetime.today().date()

def create_daily_dump(fno_symbols, token_lookup, nse_instruments):
    """Fetches 365-day history once per day to calculate 52W High/Low and creates static cache."""
    print("\n--- INITIATING DAILY CACHE DUMP ---")
    print("Fetching 365 days historical data. This will take ~1 minute...")
    
    pm_start_str, pm_end_str = get_previous_month_dates()
    pm_start_dt = datetime.strptime(pm_start_str, '%Y-%m-%d').date()
    pm_end_dt = datetime.strptime(pm_end_str, '%Y-%m-%d').date()
    
    fetch_start = (datetime.today() - timedelta(days=365)).strftime('%Y-%m-%d')
    fetch_end = datetime.today().strftime('%Y-%m-%d')
    today_date = datetime.today().date()
    
    try:
        current_data = kite.quote(nse_instruments)
    except Exception as e:
        print(f"Error fetching Quote data for Dump: {e}")
        exit(1)
        
    dump_records = []
    
    for i, symbol in enumerate(fno_symbols, 1):
        nse_key = f"NSE:{symbol}"
        prev_close = current_data.get(nse_key, {}).get('ohlc', {}).get('close', 0)
        token = token_lookup.get(symbol)
        
        pm_o, pm_h, pm_l, pm_c = 0.0, 0.0, 0.0, 0.0
        high_52, low_52 = 0.0, 0.0
        avg_vol_20 = 0.0
        hist_closes_str = ""
        
        if token:
            try:
                hist_data = kite.historical_data(token, fetch_start, fetch_end, "day")
                if hist_data:
                    high_52 = max(c['high'] for c in hist_data)
                    low_52 = min(c['low'] for c in hist_data)
                    
                    pm_candles = [d for d in hist_data if pm_start_dt <= d['date'].date() <= pm_end_dt]
                    if pm_candles:
                        pm_o = pm_candles[0]['open']
                        pm_h = max(c['high'] for c in pm_candles)
                        pm_l = min(c['low'] for c in pm_candles)
                        pm_c = pm_candles[-1]['close']
                    
                    completed_candles = [d for d in hist_data if d['date'].date() < today_date]
                    
                    closes_24 = [str(d['close']) for d in completed_candles[-24:]]
                    hist_closes_str = ",".join(closes_24)
                    
                    hist_vols = [d['volume'] for d in completed_candles[-20:]]
                    if len(hist_vols) >= 20:
                        avg_vol_20 = round(sum(hist_vols) / 20, 2)
                        
            except Exception as e:
                print(f"Error fetching history for {symbol}: {e}")
            
            time.sleep(0.35) 
            
        if i % 10 == 0:
            print(f"Dumped {i}/{len(fno_symbols)} stocks...")
            
        dump_records.append({
            "Symbol": symbol,
            "Previous_Close": prev_close,
            "PM_O": pm_o,
            "PM_H": pm_h,
            "PM_L": pm_l,
            "PM_C": pm_c,
            "High_52": high_52,
            "Low_52": low_52,
            "Hist_Closes_24": hist_closes_str,
            "Avg_Vol_20": avg_vol_20
        })
        
    pd.DataFrame(dump_records).to_csv("01_EQFNO_All.csv", mode='w', index=False)
    print("✅ 01_EQFNO_All.csv successfully created/updated for today.")

def get_google_news(symbol):
    """Fetches the top 2 recent news headlines for a stock using Google News RSS."""
    try:
        search_query = f"{symbol} stock NSE India when:2d"
        query_encoded = quote(search_query)
        url = f"https://news.google.com/rss/search?q={query_encoded}&hl=en-IN&gl=IN&ceid=IN:en"
        
        response = session.get(url, timeout=5)
        root = ET.fromstring(response.content)
        
        news_items = []
        for item in root.findall('./channel/item')[:2]:
            clean_title = item.find('title').text.rsplit(' - ', 1)[0] 
            safe_title = html.escape(clean_title) # Escape HTML entities for Telegram
            news_items.append(f"🔹 {safe_title}")
            
        return "\n".join(news_items) if news_items else "🔹 No specific news found in the last 48 hours."
            
    except Exception as e:
        print(f"Google News fetch error for {symbol}: {e}")
        return "🔹 News fetch failed."

def calculate_max_pain(symbol, options_map):
    """Calculates Options Max Pain for the nearest expiry utilizing pre-mapped options."""
    options = options_map.get(symbol, [])
    if not options: return "N/A"

    expiries = sorted(list(set(ins['expiry'] for ins in options if ins.get('expiry'))))
    if not expiries: return "N/A"
    
    nearest_expiry = expiries[0]
    current_options = [ins for ins in options if ins['expiry'] == nearest_expiry]
    trading_symbols = [f"NFO:{ins['tradingsymbol']}" for ins in current_options]

    quotes = {}
    try:
        for i in range(0, len(trading_symbols), 500):
            quotes.update(kite.quote(trading_symbols[i:i+500]))
    except Exception as e:
        print(f"Error fetching options quote for Max Pain ({symbol}): {e}")
        return "Error"

    calls, puts, strikes = {}, {}, set()

    for ins in current_options:
        ts = f"NFO:{ins['tradingsymbol']}"
        strike = ins['strike']
        oi = quotes.get(ts, {}).get('oi', 0)

        strikes.add(strike)
        if ins['instrument_type'] == 'CE':
            calls[strike] = calls.get(strike, 0) + oi
        elif ins['instrument_type'] == 'PE':
            puts[strike] = puts.get(strike, 0) + oi

    min_pain = float('inf')
    max_pain_strike = 0

    for assumed_price in strikes:
        total_pain = sum((assumed_price - strike) * calls.get(strike, 0) for strike in strikes if assumed_price > strike) + \
                     sum((strike - assumed_price) * puts.get(strike, 0) for strike in strikes if assumed_price < strike)

        if total_pain < min_pain:
            min_pain = total_pain
            max_pain_strike = assumed_price

    return max_pain_strike

def send_telegram_message(message, specific_chat_id=None):
    """Sends an HTML formatted text message to configured Telegram Bots."""
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_IDS:
        print("Warning: Telegram credentials or Chat IDs missing. Skipping notification.")
        return
        
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    targets = [specific_chat_id] if specific_chat_id else TELEGRAM_CHAT_IDS
    
    for chat_id in targets:
        payload = {
            "chat_id": chat_id,
            "text": message,
            "parse_mode": "HTML",
            "disable_web_page_preview": True 
        }
        try:
            response = session.post(url, json=payload, timeout=10)
            response.raise_for_status()
        except Exception as e:
            print(f"Failed to send Telegram message to Chat ID {chat_id}: {e}")

def telegram_poller():
    """Background thread to listen for Telegram commands."""
    global mp_bull_threshold, mp_bear_threshold, awaiting_input_for, last_update_id
    
    while True:
        try:
            url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/getUpdates"
            params = {"offset": last_update_id + 1, "timeout": 10}
            response = session.get(url, params=params, timeout=15).json()
            
            if response.get("ok"):
                for result in response["result"]:
                    last_update_id = result["update_id"]
                    message = result.get("message", {})
                    chat_id = message.get("chat", {}).get("id")
                    text = message.get("text", "").strip()
                    
                    if not text or not chat_id: 
                        continue
                    
                    str_chat_id = str(chat_id)
                    
                    if text.startswith("/"):
                        if str_chat_id != ADMIN_CHAT_ID:
                            send_telegram_message("⛔ Unauthorized: You do not have permission to use commands.", specific_chat_id=chat_id)
                            continue
                            
                        if text.startswith("/mpbull"):
                            awaiting_input_for = "mpbull"
                            send_telegram_message("Input the minimum % difference (Max Pain to LTP) for Bullish alerts:", specific_chat_id=chat_id)
                        elif text.startswith("/mpbear"):
                            awaiting_input_for = "mpbear"
                            send_telegram_message("Input the minimum % difference (Max Pain to LTP) for Bearish alerts:", specific_chat_id=chat_id)
                        elif text.startswith("/reset"):
                            mp_bull_threshold, mp_bear_threshold = 0.0, 0.0
                            awaiting_input_for = None
                            reset_msg = (
                                "🔄 <b>Admin has reset all filters:</b>\n"
                                f"📈 Bullish 'Max Pain to LTP' limit set to \u2265 0%\n"
                                f"📉 Bearish 'Max Pain to LTP' limit set to \u2264 0%"
                            )
                            send_telegram_message(reset_msg)
                            # FIX: Print to terminal when reset command is received
                            print(f"\n[Bot] Thresholds reset by Admin -> Bull MP: {mp_bull_threshold}%, Bear MP: {mp_bear_threshold}%")
                        elif text.startswith("/help"):
                            help_msg = (
                                "🛠️ <b>Bot Commands:</b>\n"
                                "/mpbull - Set Max Pain to LTP % filter for Bullish alerts\n"
                                "/mpbear - Set Max Pain to LTP % filter for Bearish alerts\n"
                                "/reset - Reset all filters to 0.0%\n"
                                "/help - Show this commands message"
                            )
                            send_telegram_message(help_msg, specific_chat_id=chat_id)
                        else:
                            send_telegram_message("Unknown command. Use /help to see available commands.", specific_chat_id=chat_id)
                    
                    elif awaiting_input_for and str_chat_id == ADMIN_CHAT_ID:
                        try:
                            new_val = float(text)
                            if awaiting_input_for == "mpbull": mp_bull_threshold = new_val
                            elif awaiting_input_for == "mpbear": mp_bear_threshold = new_val
                                
                            broadcast_msg = (
                                "🔔 <b>Admin updated filters:</b>\n"
                                f"📈 Bullish 'Max Pain to LTP' limit set to \u2265 {mp_bull_threshold:g}%\n"
                                f"📉 Bearish 'Max Pain to LTP' limit set to \u2264 -{mp_bear_threshold:g}%"
                            )
                            send_telegram_message(broadcast_msg)
                            print(f"\n[Bot] Thresholds updated by Admin -> Bull MP: {mp_bull_threshold}%, Bear MP: {mp_bear_threshold}%")
                            awaiting_input_for = None
                        except ValueError:
                            send_telegram_message("Invalid input. Please input a numeric % value (e.g., 3).", specific_chat_id=chat_id)
                            
        except requests.exceptions.RequestException:
            pass 
        except Exception as e:
            print(f"Telegram poller error: {e}")
            
        time.sleep(1)

def dispatch_alert(row, header, idx, options_map, alert_type):
    """Helper function to fetch news, calc Max Pain, and send an alert sequentially."""
    global mp_bull_threshold, mp_bear_threshold
    
    symbol = row['Symbol']
    safe_symbol = html.escape(str(symbol)) 
    max_pain = calculate_max_pain(symbol, options_map)
    
    max_pain_to_ltp_str = "N/A"
    mp_to_ltp = None
    if isinstance(max_pain, (int, float)) and max_pain > 0:
        mp_to_ltp = round(((row['LTP'] - max_pain) / max_pain) * 100, 2)
        max_pain_to_ltp_str = f"{mp_to_ltp}%"
        
    if alert_type == "Bull":
        if mp_bull_threshold > 0.0 and (mp_to_ltp is None or mp_to_ltp < mp_bull_threshold):
            return False 
        mp_cond_str = f"for MP % \u2265 {mp_bull_threshold:g}%"
    elif alert_type == "Bear":
        if mp_bear_threshold > 0.0 and (mp_to_ltp is None or mp_to_ltp > -mp_bear_threshold):
            return False 
        val_str = f"-{mp_bear_threshold:g}" if mp_bear_threshold > 0 else "0"
        mp_cond_str = f"for MP % \u2264 {val_str}%"

    google_news = get_google_news(symbol)
    trend_emoji = "📈" if row['Status'] == 'Gainer' else "📉"
    
    pivot_res_safe = html.escape(str(row['Pivot_Result']))
    bb_res_safe = html.escape(str(row['BB_Result']))
    
    msg = (
        f"<b>{header} #{idx}</b>\n{mp_cond_str}\n\n"
        f"🏢 <b>Symbol:</b> #{safe_symbol}\n\n"
        f"{row['History']}\n"
        f"{trend_emoji} <b>Daily Change:</b> {row['%_Change']}%\n"
        f"⚡ <b>Vol Shock:</b> {row['Volume_Shock']}x\n\n"
        f"🩸 <b>Max Pain:</b> ₹{max_pain}\n"
        f"🧲 <b>Max Pain to LTP:</b> {max_pain_to_ltp_str}\n\n"
        f"🎯 <b>Daily R3:</b> ₹{row['D_R3']}\n"
        f"🎯 <b>Daily S3:</b> ₹{row['D_S3']}\n"
        f"🚩 <b>Daily Pivot Result:</b> {pivot_res_safe}\n"
        f"📏 <b>Daily Pivot %:</b> {row['Pivot_Pct']}\n\n"
        f"💠 <b>Daily Upper BB:</b> ₹{row['Upper_BB']}\n"
        f"💠 <b>Daily Lower BB:</b> ₹{row['Lower_BB']}\n"
        f"🚩 <b>Daily BB Result:</b> {bb_res_safe}\n"
        f"📏 <b>Daily BB %:</b> {row['BB_Pct']}\n\n"
        f"🌟 <b>52 Week High:</b> ₹{row['High_52']}\n"
        f"💫 <b>52 Week Low:</b> ₹{row['Low_52']}\n\n"
        f"📰 <b>Recent Headlines:</b>\n{google_news}"
    )
    send_telegram_message(msg)
    return True

def run_live_scan(fno_symbols, options_map, nse_instruments):
    """Loads cache, performs 1 instant live quote, computes tech, and generates reports."""
    print("--- STARTING LIVE MARKET SCAN ---")
    print("Loading data from 01_EQFNO_All.csv...")
    dump_df = pd.read_csv("01_EQFNO_All.csv")
    
    print("Fetching live market prices (Instant)...")
    try:
        current_data = kite.quote(nse_instruments)
    except Exception as e:
        print(f"Error fetching Live Quote: {e}")
        exit(1)
        
    records = []
    print(f"Calculating Pivot & BB data...")
    
    for row in dump_df.to_dict('records'):
        symbol = row['Symbol']
        nse_key = f"NSE:{symbol}"
        
        ltp = current_data.get(nse_key, {}).get('last_price', 0)
        today_vol = current_data.get(nse_key, {}).get('volume', 0)
        
        high_52_cache = row.get('High_52', 0.0)
        low_52_cache = row.get('Low_52', 0.0)
        
        if low_52_cache == 0.0: low_52_cache = ltp
        if high_52_cache == 0.0: high_52_cache = ltp
            
        high_52 = max(high_52_cache, ltp)
        low_52 = min(low_52_cache, ltp)
        
        prev_close = row['Previous_Close']
        pct_change = round(((ltp - prev_close) / prev_close) * 100, 2) if prev_close > 0 else 0.0

        pm_o, pm_h, pm_l, pm_c = row['PM_O'], row['PM_H'], row['PM_L'], row['PM_C']
        
        # --- FIBONACCI PIVOTS ---
        pm_pivot = round((pm_h + pm_l + pm_c) / 3, 2) if pm_h > 0 else 0.0
        pm_range = pm_h - pm_l
        
        d_r3 = round(pm_pivot + 1.000 * pm_range, 2) if pm_h > 0 else 0.0
        d_r2 = round(pm_pivot + 0.618 * pm_range, 2) if pm_h > 0 else 0.0
        d_r1 = round(pm_pivot + 0.382 * pm_range, 2) if pm_h > 0 else 0.0
        d_s1 = round(pm_pivot - 0.382 * pm_range, 2) if pm_h > 0 else 0.0
        d_s2 = round(pm_pivot - 0.618 * pm_range, 2) if pm_h > 0 else 0.0
        d_s3 = round(pm_pivot - 1.000 * pm_range, 2) if pm_h > 0 else 0.0
        
        # --- DAILY PIVOT RESULT & % ---
        pivot_res, pivot_pct = "-", "-"
        if pm_pivot > 0:
            if ltp > d_r3: pivot_res = "LTP > R3"
            elif ltp > d_r2: pivot_res = "LTP > R2"
            elif ltp > d_r1: pivot_res = "LTP > R1"
            elif ltp > pm_pivot: pivot_res = "LTP > Pivot"
            elif ltp < d_s3: pivot_res = "LTP < S3"
            elif ltp < d_s2: pivot_res = "LTP < S2"
            elif ltp < d_s1: pivot_res = "LTP < S1"
            else: pivot_res = "LTP < Pivot"

        if ltp > d_r3 and d_r3 > 0: pivot_pct = f"+{round(((ltp - d_r3) / d_r3) * 100, 2)}%"
        elif ltp < d_s3 and d_s3 > 0: pivot_pct = f"{round(((ltp - d_s3) / d_s3) * 100, 2)}%"
        
        # --- BOLLINGER BANDS ---
        upper_bb, lower_bb = 0.0, 0.0
        hist_closes_str = str(row['Hist_Closes_24'])
        hist_closes = []
        
        if hist_closes_str and hist_closes_str != 'nan':
            hist_closes = [float(x) for x in hist_closes_str.split(',')]
            closes_25 = hist_closes + [ltp] 
            if len(closes_25) == 25:
                s = pd.Series(closes_25)
                sma25 = s.mean()
                std25 = s.std(ddof=0)
                upper_bb = round(sma25 + (2 * std25), 2)
                lower_bb = round(sma25 - (2 * std25), 2)

        # --- DAILY BB RESULT & % ---
        bb_result, bb_pct = "-", "-"
        if upper_bb > 0 and lower_bb > 0:
            if ltp > upper_bb:
                bb_result = "LTP > Upper BB"
                bb_pct = f"+{round(((ltp - upper_bb) / upper_bb) * 100, 2)}%"
            elif ltp < lower_bb:
                bb_result = "LTP < Lower BB"
                bb_pct = f"{round(((ltp - lower_bb) / lower_bb) * 100, 2)}%"
            else:
                bb_result = "LTP is within BB"

        avg_vol_20 = row['Avg_Vol_20']
        volume_shock = round(today_vol / avg_vol_20, 2) if avg_vol_20 > 0 else 0.0
        
        status, bb_to_ltp = "-", 0.0
        
        if d_r3 > 0 and upper_bb > 0:
            if ltp > d_r3 and ltp > upper_bb:
                status = "Gainer"
                bb_to_ltp = round(((ltp - upper_bb) / upper_bb) * 100, 2)
            elif ltp < d_s3 and ltp < lower_bb:
                status = "Loser"
                if lower_bb > 0:
                    bb_to_ltp = round(((ltp - lower_bb) / lower_bb) * 100, 2)

        history_block = "➖ ➖ ➖ ➖ ➖"
        if len(hist_closes) >= 5:
            n_1_close, n_2_close, n_3_close, n_4_close, n_5_close = hist_closes[-1], hist_closes[-2], hist_closes[-3], hist_closes[-4], hist_closes[-5]
            
            t4_dot = "🟢" if n_4_close >= n_5_close else "🔴"
            t3_dot = "🟢" if n_3_close >= n_4_close else "🔴"
            t2_dot = "🟢" if n_2_close >= n_3_close else "🔴"
            t1_dot = "🟢" if n_1_close >= n_2_close else "🔴"
            today_dot = "🟢" if ltp >= n_1_close else "🔴"
            
            target_val = None
            if status == "Gainer": target_val = max([n_4_close, n_3_close, n_2_close, n_1_close, ltp])
            elif status == "Loser": target_val = min([n_4_close, n_3_close, n_2_close, n_1_close, ltp])

            def format_value(val):
                val_str = f"{val:.2f}"
                spaces = '\u2007' * max(0, 9 - len(val_str))
                return f"{spaces}<b><i>{val_str}</i></b>" if val == target_val else f"{spaces}{val_str}"

            history_block = (
                "📊 <b>Daily History (T-4 to T)</b>\n"
                "Tag\u2007\u2007\u2007\u2007\u2007\u2007Closing\n"
                f"{t4_dot} {format_value(n_4_close)}\n"
                f"{t3_dot} {format_value(n_3_close)}\n"
                f"{t2_dot} {format_value(n_2_close)}\n"
                f"{t1_dot} {format_value(n_1_close)}\n"
                f"{today_dot} {format_value(ltp)}"
            )

        records.append({
            "Symbol": symbol,
            "Previous_Close": prev_close,
            "PM_O": pm_o, "PM_H": pm_h, "PM_L": pm_l, "PM_C": pm_c,
            "PM_Pivot": pm_pivot, "PM_R3": d_r3, "PM_S3": d_s3,
            "D_R3": d_r3, "D_R2": d_r2, "D_R1": d_r1, "D_Pivot": pm_pivot, "D_S1": d_s1, "D_S2": d_s2, "D_S3": d_s3,
            "Pivot_Result": pivot_res, "Pivot_Pct": pivot_pct,
            "Upper_BB": upper_bb, "Lower_BB": lower_bb, "BB_Result": bb_result, "BB_Pct": bb_pct,
            "LTP": ltp, "%_Change": pct_change, "BB_to_LTP": bb_to_ltp,
            "Volume_Shock": volume_shock, "History": history_block, "Status": status,
            "High_52": high_52, "Low_52": low_52    
        })
        
    df = pd.DataFrame(records)
    df.to_csv("01_EQFNO_Dump.csv", mode='w', index=False)
    
    export_cols = [
        "Symbol", "LTP", "%_Change", "PM_R3", "PM_S3", "Upper_BB", "Lower_BB", "BB_to_LTP", 
        "Volume_Shock", "History", "Status", "High_52", "Low_52",
        "D_R3", "D_R2", "D_R1", "D_Pivot", "D_S1", "D_S2", "D_S3", "Pivot_Result", "Pivot_Pct",
        "BB_Result", "BB_Pct"
    ]
    
    alert_gainers_df = df[df['Status'] == 'Gainer'][export_cols].sort_values(by="%_Change", ascending=False)
    alert_gainers_df.to_csv("01_EQFNO_Gainers.csv", mode='w', index=False)
    
    alert_losers_df = df[df['Status'] == 'Loser'][export_cols].sort_values(by="%_Change", ascending=True)
    alert_losers_df.to_csv("01_EQFNO_Losers.csv", mode='w', index=False)
    
    print(f"Dispatching Telegram Alerts to {len(TELEGRAM_CHAT_IDS)} chat(s)...")
    
    # --- 1. Process Bullish Alerts First ---
    sent_bull = 0
    if not alert_gainers_df.empty:
        for row in alert_gainers_df.to_dict('records'):
            if dispatch_alert(row, "🚀 BULLISH BREAKOUT 🚀", sent_bull + 1, options_map, "Bull"):
                sent_bull += 1
                
    if sent_bull == 0:
        send_telegram_message(f"<b>🚀 BULLISH BREAKOUT 🚀</b>\nfor MP % \u2265 {mp_bull_threshold:g}%\n\nNo bullish setups passed.")

    # --- 2. Process Bearish Alerts Second ---
    sent_bear = 0
    if not alert_losers_df.empty:
        for row in alert_losers_df.to_dict('records'):
            if dispatch_alert(row, "🩸 BEARISH BREAKDOWN 🩸", sent_bear + 1, options_map, "Bear"):
                sent_bear += 1
                
    if sent_bear == 0:
        val_str = f"-{mp_bear_threshold:g}" if mp_bear_threshold > 0 else "0"
        send_telegram_message(f"<b>🩸 BEARISH BREAKDOWN 🩸</b>\nfor MP % \u2264 {val_str}%\n\nNo bearish setups passed.")

    summary_msg = f"<b>✅ Scan Complete!</b>\nSent {sent_bull} Bullish and {sent_bear} Bearish Alerts."
    print(f"✅ Scan Complete!\nSent {sent_bull} Bullish and {sent_bear} Bearish Alerts.")
    send_telegram_message(summary_msg)

if __name__ == "__main__":
    if not API_KEY or not API_SECRET:
        print("Error: ZERODHA_API_KEY or ZERODHA_API_SECRET not found in .env file.")
        exit(1)
        
    authenticate()
    options_map, symbols = {}, []
    tokens, nse_instruments = {}, []
    
    print("\nStarting Telegram Bot Listener...")
    threading.Thread(target=telegram_poller, daemon=True).start()
    print("Starting Continuous Automated Scanner. Press Ctrl+C to stop.")
    
    while True:
        try:
            if not is_dump_fresh():
                options_map, symbols = get_fno_instruments_and_symbols()
                tokens = get_instrument_tokens(symbols)
                nse_instruments = [f"NSE:{s}" for s in symbols]
                create_daily_dump(symbols, tokens, nse_instruments)
            else:
                if not options_map:
                    options_map, symbols = get_fno_instruments_and_symbols()
                    nse_instruments = [f"NSE:{s}" for s in symbols]
                print("\n✅ Daily Cache exists and is fresh. Skipping historical data fetch.")
            
            current_time = datetime.now().strftime('%H:%M:%S')
            cycle_msg = f"[{current_time}] Initiating new scan.."
            
            print(f"\n{cycle_msg}")
            send_telegram_message(f"🔄 <b>{cycle_msg}</b>")
                
            run_live_scan(symbols, options_map, nse_instruments)
            
            print("\n⏳ Cycle complete. Waiting 2 minutes before the next scan...")
            time.sleep(120)
            
        except KeyboardInterrupt:
            print("\n🛑 Script manually stopped by user. Exiting gracefully.")
            break
        except Exception as e:
            print(f"\n⚠️ Unexpected error during execution: {e}")
            print("⏳ Waiting 2 minutes before retrying...")
            time.sleep(120)