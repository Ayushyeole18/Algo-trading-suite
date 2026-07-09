import os
import io
import sys
import time
import requests
import pandas as pd
import urllib.parse
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta
from urllib.parse import urlparse, parse_qs
from kiteconnect import KiteConnect
from dotenv import load_dotenv, set_key

# 1. Load Environment Variables
load_dotenv()
API_KEY = os.getenv("ZERODHA_API_KEY")
API_SECRET = os.getenv("ZERODHA_API_SECRET")
TELEGRAM_BOT_TOKEN = os.getenv("03_TELEGRAM_BOT_TOKEN")

# Dynamically load all Chat IDs defined in .env (e.g., TELEGRAM_CHAT_ID_01, TELEGRAM_CHAT_ID_02)
TELEGRAM_CHAT_ID_ = []
for key, value in os.environ.items():
    if key.startswith("TELEGRAM_CHAT_ID_") and value.strip():
        if value.strip() not in TELEGRAM_CHAT_ID_:
            TELEGRAM_CHAT_ID_.append(value.strip())

# Initialize KiteConnect
kite = KiteConnect(api_key=API_KEY)

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
            sys.exit(1)
        except Exception as e:
            print(f"Authentication failed: {e}")
            sys.exit(1)

def get_google_news(symbol):
    """Fetches the top 2 recent news headlines for a stock using Google News RSS."""
    try:
        search_query = f"{symbol} stock NSE India when:7d" 
        query = urllib.parse.quote(search_query)
        url = f"https://news.google.com/rss/search?q={query}&hl=en-IN&gl=IN&ceid=IN:en"
        
        response = requests.get(url, timeout=5)
        root = ET.fromstring(response.content)
        
        news_items = []
        for item in root.findall('./channel/item')[:2]:
            title = item.find('title').text
            clean_title = title.rsplit(' - ', 1)[0] 
            news_items.append(f"🔹 {clean_title}")
            
        if news_items:
            return "\n".join(news_items)
        else:
            return "🔹 No specific news found in the last 7 days."
            
    except Exception as e:
        print(f"Google News fetch error for {symbol}: {e}")
        return "🔹 News fetch failed."

def send_telegram_message(message):
    """Broadcasts an HTML formatted text message to all configured Telegram Bots."""
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID_:
        print("Warning: Telegram credentials or Chat IDs missing. Skipping notification.")
        return
        
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    
    for chat_id in TELEGRAM_CHAT_ID_:
        payload = {
            "chat_id": chat_id,
            "text": message,
            "parse_mode": "HTML",
            "disable_web_page_preview": True 
        }
        
        try:
            response = requests.post(url, json=payload)
            response.raise_for_status()
        except Exception as e:
            print(f"Failed to send Telegram message to {chat_id}: {e}")

def get_nifty250_symbols():
    """Fetches Nifty 50, Nifty Next 50, and Nifty Midcap 150 symbols dynamically from NSE."""
    print("Fetching constituents dynamically from NSE (Nifty 50 + Next 50 + Midcap 150)...")
    urls = [
        "https://archives.nseindia.com/content/indices/ind_nifty50list.csv",
        "https://archives.nseindia.com/content/indices/ind_niftynext50list.csv",
        "https://archives.nseindia.com/content/indices/ind_niftymidcap150list.csv"
    ]
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
        "Accept": "text/csv,application/csv"
    }
    
    all_symbols = set()
    try:
        for url in urls:
            response = requests.get(url, headers=headers, timeout=10)
            response.raise_for_status()
            
            if "text/html" in response.headers.get("Content-Type", ""):
                raise ValueError("NSE returned HTML instead of CSV (Site might be blocking bots).")
                
            df = pd.read_csv(io.StringIO(response.text))
            if 'Symbol' in df.columns:
                symbols = [str(sym).strip() for sym in df['Symbol'].tolist()]
                all_symbols.update(symbols)
                
        if all_symbols:
            print(f"✅ Successfully fetched {len(all_symbols)} unique symbols from NSE.")
            return sorted(list(all_symbols))
    except Exception as e:
        print(f"⚠️ Error fetching dynamic lists from NSE: {e}")
        
    print("⚠️ Falling back to recently known hardcoded Nifty 50 list...")
    fallback_list = [
        "ADANIENT", "ADANIPORTS", "APOLLOHOSP", "ASIANPAINT", "AXISBANK",
        "BAJAJ-AUTO", "BAJFINANCE", "BAJAJFINSV", "BEL", "BPCL", "BHARTIARTL",
        "BRITANNIA", "CIPLA", "COALINDIA", "DRREDDY", "EICHERMOT", "GRASIM",
        "HCLTECH", "HDFCBANK", "HDFCLIFE", "HEROMOTOCO", "HINDALCO", "HINDUNILVR",
        "ICICIBANK", "ITC", "INDUSINDBK", "INFY", "JSWSTEEL", "KOTAKBANK",
        "LTIM", "LT", "M&M", "MARUTI", "NTPC", "NESTLEIND", "ONGC", "POWERGRID",
        "RELIANCE", "SBILIFE", "SBIN", "SHRIRAMFIN", "SUNPHARMA", "TCS",
        "TATACONSUM", "TATAMOTORS", "TATASTEEL", "TECHM", "TITAN", "TRENT",
        "ULTRACEMCO", "WIPRO"
    ]
    return sorted(fallback_list)

def get_instrument_tokens(symbols):
    """Maps trading symbols to their Zerodha instrument tokens."""
    nse_instruments = kite.instruments("NSE")
    token_lookup = {}
    for ins in nse_instruments:
        if ins['tradingsymbol'] in symbols:
            token_lookup[ins['tradingsymbol']] = ins['instrument_token']
    return token_lookup

def is_dump_fresh(filename="03_Ni50_M150_Nx50_All.csv"):
    """Checks if the Dump file was already created today AND contains all necessary columns."""
    if not os.path.exists(filename):
        return False
    
    file_mtime = datetime.fromtimestamp(os.path.getmtime(filename)).date()
    if file_mtime != datetime.today().date():
        return False
        
    try:
        df = pd.read_csv(filename, nrows=1)
        required_columns = ['Recent_6_Closes', 'Hist_Weekly_Closes_24', 'PM_H', 'Hist_Daily_Closes_24', 'High_52W']
        for col in required_columns:
            if col not in df.columns:
                print(f"⚠️ Cache file is missing column: {col}. Deleting old cache and forcing regeneration...")
                os.remove(filename)
                return False
    except Exception:
        return False
        
    return True

def create_daily_dump(symbols, token_lookup):
    """Fetches 730 days of daily history to compute Previous Year, Previous Month, and Weekly data."""
    print("\n--- INITIATING WEEKLY, MONTHLY & YEARLY CACHE DUMP ---")
    print("Fetching 2 years of daily data to compute OHLC and candles...")
    
    fetch_start = (datetime.today() - timedelta(days=730)).strftime('%Y-%m-%d')
    fetch_end = datetime.today().strftime('%Y-%m-%d')
    
    today_date = datetime.today().date()
    current_year = today_date.year
    previous_year = current_year - 1
    
    first_day_of_current_month = today_date.replace(day=1)
    last_day_of_previous_month = first_day_of_current_month - timedelta(days=1)
    pm_year = last_day_of_previous_month.year
    pm_month = last_day_of_previous_month.month
    
    monday_of_current_week = today_date - timedelta(days=today_date.weekday())
    one_year_ago = today_date - timedelta(days=365)
    
    dump_records = []
    
    for i, symbol in enumerate(symbols, 1):
        token = token_lookup.get(symbol)
        
        py_o, py_h, py_l, py_c = 0.0, 0.0, 0.0, 0.0
        pm_o, pm_h, pm_l, pm_c = 0.0, 0.0, 0.0, 0.0
        high_52w, low_52w = 0.0, 0.0
        pw_c = 0.0  
        avg_weekly_vol_20 = 0.0
        cw_vol_till_yest = 0.0 
        hist_closes_str = ""
        recent_6_closes_str = ""
        hist_daily_closes_str = ""
        
        if token:
            try:
                hist_data = kite.historical_data(token, fetch_start, fetch_end, "day")
                if hist_data:
                    df = pd.DataFrame(hist_data)
                    df['date'] = pd.to_datetime(df['date']).dt.tz_localize(None)
                    df.set_index('date', inplace=True)
                    
                    # 1. Previous Year (PY) OHLC Calculation
                    py_df = df[df.index.year == previous_year]
                    if not py_df.empty:
                        py_o = float(py_df.iloc[0]['open'])
                        py_h = float(py_df['high'].max())
                        py_l = float(py_df['low'].min())
                        py_c = float(py_df.iloc[-1]['close'])

                    # 1.5 Previous Month (PM) OHLC Calculation
                    pm_df = df[(df.index.year == pm_year) & (df.index.month == pm_month)]
                    if not pm_df.empty:
                        pm_o = float(pm_df.iloc[0]['open'])
                        pm_h = float(pm_df['high'].max())
                        pm_l = float(pm_df['low'].min())
                        pm_c = float(pm_df.iloc[-1]['close'])

                    # 1.8 52-Week High & Low Calculation (Last 365 days)
                    df_52w = df[df.index.date >= one_year_ago]
                    if not df_52w.empty:
                        high_52w = round(float(df_52w['high'].max()), 2)
                        low_52w = round(float(df_52w['low'].min()), 2)

                    # 2. Daily Data Extractor (Recent 6 for tables, Recent 24 for Daily BB)
                    completed_days_all = df[df.index.date < today_date]
                    if len(completed_days_all) >= 6:
                        recent_6_closes = [str(x) for x in completed_days_all['close'].tail(6).tolist()]
                        recent_6_closes_str = ",".join(recent_6_closes)
                    
                    if len(completed_days_all) >= 24:
                        recent_24_closes = [str(x) for x in completed_days_all['close'].tail(24).tolist()]
                        hist_daily_closes_str = ",".join(recent_24_closes)

                    # 3. Weekly Data & Volume Calculation
                    completed_daily = df[df.index.date < monday_of_current_week]
                    ongoing_daily = df[df.index.date >= monday_of_current_week]
                    
                    if not completed_daily.empty:
                        weekly_df = completed_daily.resample('W-FRI').agg({
                            'open': 'first',
                            'high': 'max',
                            'low': 'min',
                            'close': 'last',
                            'volume': 'sum'
                        }).dropna()
                        
                        if not weekly_df.empty:
                            pw_c = float(weekly_df.iloc[-1]['close'])
                            closes_24 = [str(x) for x in weekly_df['close'].tail(24).tolist()]
                            hist_closes_str = ",".join(closes_24)
                            
                            vols_20 = weekly_df['volume'].tail(20).tolist()
                            if len(vols_20) > 0:
                                avg_weekly_vol_20 = round(sum(vols_20) / len(vols_20), 2)
                    
                    # 4. Accumulate current week's volume
                    past_ongoing_daily = ongoing_daily[ongoing_daily.index.date < today_date]
                    if not past_ongoing_daily.empty:
                        cw_vol_till_yest = float(past_ongoing_daily['volume'].sum())
                        
            except Exception as e:
                print(f"Error fetching/processing history for {symbol}: {e}")
            
            time.sleep(0.35) # API Rate Limit
            
        if i % 10 == 0:
            print(f"Dumped {i}/{len(symbols)} stocks...")
            
        dump_records.append({
            "Symbol": symbol,
            "PY_O": py_o,
            "PY_H": py_h,
            "PY_L": py_l,
            "PY_C": py_c,
            "PM_O": pm_o,
            "PM_H": pm_h,
            "PM_L": pm_l,
            "PM_C": pm_c,
            "High_52W": high_52w,
            "Low_52W": low_52w,
            "PW_C": pw_c,
            "Hist_Daily_Closes_24": hist_daily_closes_str,
            "Hist_Weekly_Closes_24": hist_closes_str,
            "Avg_Weekly_Vol_20": avg_weekly_vol_20,
            "CW_Vol_Till_Yest": cw_vol_till_yest,
            "Recent_6_Closes": recent_6_closes_str
        })
        
    dump_df = pd.DataFrame(dump_records)
    dump_df.to_csv("03_Ni50_M150_Nx50_All.csv", mode='w', index=False)
    print("✅ 03_Ni50_M150_Nx50_All.csv successfully created/updated with metrics.\n")

def build_history_table(title, prices, prev_prices, status):
    """Constructs an aligned vertical text-based table using Unicode Figure Spaces."""
    inds = ["🟢" if p >= pp else "🔴" for p, pp in zip(prices, prev_prices)]
    p_strs = [f"{p:.1f}" for p in prices]
    
    target_val = None
    if status == "Gainer":
        target_val = max(prices)
    elif status == "Loser":
        target_val = min(prices)
        
    widths = [len(p) for p in p_strs]
    max_w = max(widths + [7]) 
    
    table_lines = [f"📊 <b>{title}</b>"]
    
    cls_pad = max_w - 7
    if cls_pad < 0: cls_pad = 0
    header_cls = ("\u2007" * cls_pad) + "Closing"
    table_lines.append(f"Tag\u2007\u2007\u2007{header_cls}")
    
    for i in range(5):
        spaces_needed = max_w - len(p_strs[i])
        padded_p = ("\u2007" * spaces_needed) + p_strs[i]
        
        if target_val is not None and prices[i] == target_val:
            final_p = f"<b><i>{padded_p}</i></b>"
        else:
            final_p = padded_p
            
        table_lines.append(f"{inds[i]}\u2007\u2007\u2007{final_p}")
        
    return "\n".join(table_lines)

def run_live_scan(symbols):
    """Loads cache, performs instant live quote, computes tech, and generates reports."""
    
    current_time_str = datetime.now().strftime("%H:%M:%S")
    startup_msg = f"[{current_time_str}] Initiating new scan .."
    
    print(f"\n--- STARTING LIVE MARKET SCAN ---")
    print(startup_msg)
    send_telegram_message(f"🔄 <b>{startup_msg}</b>")
    
    print("Loading data from 03_Ni50_M150_Nx50_All.csv...")
    dump_df = pd.read_csv("03_Ni50_M150_Nx50_All.csv")
    
    print("Fetching live market prices (Instant)...")
    # Batch API requests (Max 500 per call, 250 is well within limit)
    nse_instruments = [f"NSE:{symbol}" for symbol in symbols]
    try:
        current_data = kite.quote(nse_instruments)
    except Exception as e:
        print(f"Error fetching Live Quote: {e}")
        return 
        
    records = []
    
    print("Calculating Pivots, BBs, and Formatting Tables...")
    for _, row in dump_df.iterrows():
        symbol = row['Symbol']
        nse_key = f"NSE:{symbol}"
        
        ltp = current_data.get(nse_key, {}).get('last_price', 0)
        today_vol = current_data.get(nse_key, {}).get('volume', 0)
        
        pw_c = row.get('PW_C', 0)
        pct_change = round(((ltp - pw_c) / pw_c) * 100, 2) if pw_c > 0 else 0.0
        
        high_52w = row.get('High_52W', 0.0)
        low_52w = row.get('Low_52W', 0.0)

        # === WEEKLY PIVOT CALCULATIONS ===
        py_h, py_l, py_c = row.get('PY_H', 0), row.get('PY_L', 0), row.get('PY_C', 0)
        
        weekly_pivot = round((py_h + py_l + py_c) / 3, 2) if py_h > 0 else 0.0
        py_range = py_h - py_l
        
        weekly_r1 = round(weekly_pivot + (0.382 * py_range), 2) if py_h > 0 else 0.0
        weekly_r2 = round(weekly_pivot + (0.618 * py_range), 2) if py_h > 0 else 0.0
        weekly_r3 = round(weekly_pivot + (1.000 * py_range), 2) if py_h > 0 else 0.0
        
        weekly_s1 = round(weekly_pivot - (0.382 * py_range), 2) if py_h > 0 else 0.0
        weekly_s2 = round(weekly_pivot - (0.618 * py_range), 2) if py_h > 0 else 0.0
        weekly_s3 = round(weekly_pivot - (1.000 * py_range), 2) if py_h > 0 else 0.0

        ltp_gt_weekly_r3 = "Yes" if (weekly_r3 > 0 and ltp > weekly_r3) else "No"
        ltp_lt_weekly_s3 = "Yes" if (weekly_s3 > 0 and ltp < weekly_s3) else "No"

        if weekly_pivot > 0:
            if ltp >= weekly_r3:
                weekly_pivot_result = "LTP > R3"
            elif ltp >= weekly_r2:
                weekly_pivot_result = "LTP > R2"
            elif ltp >= weekly_r1:
                weekly_pivot_result = "LTP > R1"
            elif ltp >= weekly_pivot:
                weekly_pivot_result = "LTP > Pivot"
            elif ltp < weekly_s3:
                weekly_pivot_result = "LTP < S3"
            elif ltp < weekly_s2:
                weekly_pivot_result = "LTP < S2"
            elif ltp < weekly_s1:
                weekly_pivot_result = "LTP < S1"
            else:
                weekly_pivot_result = "LTP < Pivot"
        else:
            weekly_pivot_result = "-"
            
        if weekly_pivot > 0 and ltp > weekly_r3:
            weekly_pivot_pct = f"+{round(((ltp - weekly_r3) / weekly_r3) * 100, 2)}%"
        elif weekly_pivot > 0 and ltp < weekly_s3:
            weekly_pivot_pct = f"{round(((ltp - weekly_s3) / weekly_s3) * 100, 2)}%"
        else:
            weekly_pivot_pct = "-"

        # === DAILY PIVOT CALCULATIONS ===
        pm_h, pm_l, pm_c = row.get('PM_H', 0), row.get('PM_L', 0), row.get('PM_C', 0)
        
        daily_pivot = round((pm_h + pm_l + pm_c) / 3, 2) if pm_h > 0 else 0.0
        pm_range = pm_h - pm_l

        daily_r1 = round(daily_pivot + (0.382 * pm_range), 2) if pm_h > 0 else 0.0
        daily_r2 = round(daily_pivot + (0.618 * pm_range), 2) if pm_h > 0 else 0.0
        daily_r3 = round(daily_pivot + (1.000 * pm_range), 2) if pm_h > 0 else 0.0
        
        daily_s1 = round(daily_pivot - (0.382 * pm_range), 2) if pm_h > 0 else 0.0
        daily_s2 = round(daily_pivot - (0.618 * pm_range), 2) if pm_h > 0 else 0.0
        daily_s3 = round(daily_pivot - (1.000 * pm_range), 2) if pm_h > 0 else 0.0
        
        ltp_gt_daily_r3 = "Yes" if (daily_r3 > 0 and ltp > daily_r3) else "No"
        ltp_lt_daily_s3 = "Yes" if (daily_s3 > 0 and ltp < daily_s3) else "No"

        if daily_pivot > 0:
            if ltp >= daily_r3:
                daily_pivot_result = "LTP > R3"
            elif ltp >= daily_r2:
                daily_pivot_result = "LTP > R2"
            elif ltp >= daily_r1:
                daily_pivot_result = "LTP > R1"
            elif ltp >= daily_pivot:
                daily_pivot_result = "LTP > Pivot"
            elif ltp < daily_s3:
                daily_pivot_result = "LTP < S3"
            elif ltp < daily_s2:
                daily_pivot_result = "LTP < S2"
            elif ltp < daily_s1:
                daily_pivot_result = "LTP < S1"
            else:
                daily_pivot_result = "LTP < Pivot"
        else:
            daily_pivot_result = "-"
            
        if daily_pivot > 0 and ltp > daily_r3:
            daily_pivot_pct = f"+{round(((ltp - daily_r3) / daily_r3) * 100, 2)}%"
        elif daily_pivot > 0 and ltp < daily_s3:
            daily_pivot_pct = f"{round(((ltp - daily_s3) / daily_s3) * 100, 2)}%"
        else:
            daily_pivot_pct = "-"

        # === DAILY BOLLINGER BANDS ===
        daily_upper_bb, daily_lower_bb = 0.0, 0.0
        hist_daily_closes_str = str(row.get('Hist_Daily_Closes_24', 'nan'))
        d_closes = []
        if hist_daily_closes_str and hist_daily_closes_str != 'nan':
            d_closes = [float(x) for x in hist_daily_closes_str.split(',')]
            d_closes_25 = d_closes + [ltp] 
            
            if len(d_closes_25) == 25:
                s_d = pd.Series(d_closes_25)
                sma25_d = s_d.mean()
                std25_d = s_d.std(ddof=0)
                daily_upper_bb = round(sma25_d + (2 * std25_d), 2)
                daily_lower_bb = round(sma25_d - (2 * std25_d), 2)

        if daily_upper_bb > 0 and daily_lower_bb > 0:
            if ltp > daily_upper_bb:
                daily_bb_result = "LTP > Upper BB"
                daily_bb_pct = f"+{round(((ltp - daily_upper_bb) / daily_upper_bb) * 100, 2)}%"
            elif ltp < daily_lower_bb:
                daily_bb_result = "LTP < Lower BB"
                daily_bb_pct = f"{round(((ltp - daily_lower_bb) / daily_lower_bb) * 100, 2)}%"
            else:
                daily_bb_result = "LTP is within BB"
                daily_bb_pct = "-"
        else:
            daily_bb_result = "-"
            daily_bb_pct = "-"

        # === WEEKLY BOLLINGER BANDS ===
        weekly_upper_bb, weekly_lower_bb = 0.0, 0.0
        hist_closes_str = str(row.get('Hist_Weekly_Closes_24', 'nan'))
        w_closes = []
        if hist_closes_str and hist_closes_str != 'nan':
            w_closes = [float(x) for x in hist_closes_str.split(',')]
            closes_25 = w_closes + [ltp] 
            
            if len(closes_25) == 25:
                s = pd.Series(closes_25)
                sma25 = s.mean()
                std25 = s.std(ddof=0)
                weekly_upper_bb = round(sma25 + (2 * std25), 2)
                weekly_lower_bb = round(sma25 - (2 * std25), 2)

        if weekly_upper_bb > 0 and weekly_lower_bb > 0:
            if ltp > weekly_upper_bb:
                weekly_bb_result = "LTP > Upper BB"
                weekly_bb_pct = f"+{round(((ltp - weekly_upper_bb) / weekly_upper_bb) * 100, 2)}%"
            elif ltp < weekly_lower_bb:
                weekly_bb_result = "LTP < Lower BB"
                weekly_bb_pct = f"{round(((ltp - weekly_lower_bb) / weekly_lower_bb) * 100, 2)}%"
            else:
                weekly_bb_result = "LTP is within BB"
                weekly_bb_pct = "-"
        else:
            weekly_bb_result = "-"
            weekly_bb_pct = "-"
                
        # === VOLUME SHOCK & STATUS ===
        live_weekly_vol = row.get('CW_Vol_Till_Yest', 0) + today_vol
        avg_weekly_vol_20 = row.get('Avg_Weekly_Vol_20', 0)
        volume_shock = round(live_weekly_vol / avg_weekly_vol_20, 2) if avg_weekly_vol_20 > 0 else 0.0
        
        status = "-"
        bb_to_ltp = 0.0 
        if weekly_r2 > 0 and weekly_upper_bb > 0:
            if (ltp > weekly_r2) and (ltp > weekly_upper_bb):
                status = "Gainer"
                bb_to_ltp = round(((ltp - weekly_upper_bb) / weekly_upper_bb) * 100, 2)
            elif (ltp < weekly_s2) and (ltp < weekly_lower_bb) and weekly_lower_bb > 0:
                status = "Loser"
                bb_to_ltp = round(((ltp - weekly_lower_bb) / weekly_lower_bb) * 100, 2)
                
        # === TABLES & DAILY CHANGE ===
        daily_table = "N/A"
        daily_pct_change = 0.0
        recent_6_closes_str = str(row.get('Recent_6_Closes', 'nan'))
        
        if recent_6_closes_str and recent_6_closes_str != 'nan':
            c = [float(x) for x in recent_6_closes_str.split(',')]
            if len(c) >= 6:
                prices = [c[-4], c[-3], c[-2], c[-1], ltp]
                prev_prices = [c[-5], c[-4], c[-3], c[-2], c[-1]]
                daily_table = build_history_table("Daily History (T-4 to T)", prices, prev_prices, status)
                
                if c[-1] > 0:
                    daily_pct_change = round(((ltp - c[-1]) / c[-1]) * 100, 2)

        weekly_table = "N/A"
        if len(w_closes) >= 6:
            prices = [w_closes[-4], w_closes[-3], w_closes[-2], w_closes[-1], ltp]
            prev_prices = [w_closes[-5], w_closes[-4], w_closes[-3], w_closes[-2], w_closes[-1]]
            weekly_table = build_history_table("Weekly History (W-4 to W)", prices, prev_prices, status)

        records.append({
            "Symbol": symbol,
            "LTP": ltp,
            "%_Change": pct_change,
            "Daily_Change": daily_pct_change,
            "High_52W": high_52w,
            "Low_52W": low_52w,
            "WTD_Volume_Shock": volume_shock,
            "LTP_gt_Daily_R3": ltp_gt_daily_r3,
            "LTP_lt_Daily_S3": ltp_lt_daily_s3,
            "LTP_gt_Weekly_R3": ltp_gt_weekly_r3,
            "LTP_lt_Weekly_S3": ltp_lt_weekly_s3,
            "BB_to_LTP": bb_to_ltp,
            "Daily_Table": daily_table,
            "Weekly_Table": weekly_table,
            "Status": status,
            "Daily_Pivot": daily_pivot,
            "Daily_R1": daily_r1,
            "Daily_R2": daily_r2,
            "Daily_R3": daily_r3,
            "Daily_S1": daily_s1,
            "Daily_S2": daily_s2,
            "Daily_S3": daily_s3,
            "Daily_Pivot_Result": daily_pivot_result,
            "Daily_Pivot_Pct": daily_pivot_pct,
            "Daily_Upper_BB": daily_upper_bb,
            "Daily_Lower_BB": daily_lower_bb,
            "Daily_BB_Result": daily_bb_result,
            "Daily_BB_Pct": daily_bb_pct,
            "Weekly_Pivot": weekly_pivot,
            "Weekly_R1": weekly_r1,
            "Weekly_R2": weekly_r2,
            "Weekly_R3": weekly_r3,
            "Weekly_S1": weekly_s1,
            "Weekly_S2": weekly_s2,
            "Weekly_S3": weekly_s3,
            "Weekly_Pivot_Result": weekly_pivot_result,
            "Weekly_Pivot_Pct": weekly_pivot_pct,
            "Weekly_Upper_BB": weekly_upper_bb,
            "Weekly_Lower_BB": weekly_lower_bb,
            "Weekly_BB_Result": weekly_bb_result,
            "Weekly_BB_Pct": weekly_bb_pct
        })
        
    # --- REPORT GENERATION ---
    df = pd.DataFrame(records)
    
    export_cols = [
        "Symbol", "LTP", "%_Change", "Daily_Change", "WTD_Volume_Shock", "High_52W", "Low_52W",
        "LTP_gt_Daily_R3", "LTP_lt_Daily_S3", "LTP_gt_Weekly_R3", "LTP_lt_Weekly_S3", "BB_to_LTP", 
        "Daily_Table", "Weekly_Table", "Status", 
        "Daily_Pivot", "Daily_R1", "Daily_R2", "Daily_R3", 
        "Daily_S1", "Daily_S2", "Daily_S3", "Daily_Pivot_Result", "Daily_Pivot_Pct", 
        "Daily_Upper_BB", "Daily_Lower_BB", "Daily_BB_Result", "Daily_BB_Pct",
        "Weekly_Pivot", "Weekly_R1", "Weekly_R2", "Weekly_R3", 
        "Weekly_S1", "Weekly_S2", "Weekly_S3", "Weekly_Pivot_Result", "Weekly_Pivot_Pct", 
        "Weekly_Upper_BB", "Weekly_Lower_BB", "Weekly_BB_Result", "Weekly_BB_Pct"
    ]
    
    # 1. Master Export
    df.to_csv("03_Ni50_M150_Nx50_Dump.csv", mode='w', index=False)
    
    # 2. Gainers Export
    gainers_df = df[df['Status'] == 'Gainer'][export_cols]
    gainers_df = gainers_df.sort_values(by="%_Change", ascending=False)
    gainers_df.to_csv("03_Ni50_M150_Nx50_Gainers.csv", mode='w', index=False)
    
    # 3. Losers Export
    losers_df = df[df['Status'] == 'Loser'][export_cols]
    losers_df = losers_df.sort_values(by="%_Change", ascending=True)
    losers_df.to_csv("03_Ni50_M150_Nx50_Losers.csv", mode='w', index=False)
    
    # --- TELEGRAM ALERTS ---
    print("Dispatching Telegram Alerts...")
    
    if not gainers_df.empty:
        for idx, (_, row) in enumerate(gainers_df.iterrows(), start=1):
            symbol = row['Symbol']
            google_news = get_google_news(symbol)
            
            daily_chg = row['Daily_Change']
            weekly_chg = row['%_Change']
            
            daily_chg_emoji = "📈" if daily_chg >= 0 else "📉"
            weekly_chg_emoji = "📈" if weekly_chg >= 0 else "📉"
            
            daily_chg_str = f"+{daily_chg}%" if daily_chg > 0 else f"{daily_chg}%"
            weekly_chg_str = f"+{weekly_chg}%" if weekly_chg > 0 else f"{weekly_chg}%"
            
            msg = (
                f"<b>🚀 N250 Weekly R2 Gainers 🚀 #{idx}</b>\n\n"
                f"🏢 <b>Symbol:</b> #{symbol}\n\n"
                f"{row['Daily_Table']}\n"
                f"{daily_chg_emoji} <b>Daily Change:</b> {daily_chg_str}\n\n"
                f"🎯 <b>Daily R3:</b> {row['Daily_R3']}\n"
                f"🎯 <b>Daily S3:</b> {row['Daily_S3']}\n"
                f"🚩 <b>Daily Pivot Result:</b> {row['Daily_Pivot_Result']}\n"
                f"📏 <b>Daily Pivot %:</b> {row['Daily_Pivot_Pct']}\n\n"
                f"🎯 <b>Daily Upper BB:</b> {row['Daily_Upper_BB']}\n"
                f"🎯 <b>Daily Lower BB:</b> {row['Daily_Lower_BB']}\n"
                f"🚩 <b>Daily BB Result:</b> {row['Daily_BB_Result']}\n"
                f"📏 <b>Daily BB %:</b> {row['Daily_BB_Pct']}\n\n"
                f"🌟 <b>52 Week High:</b> ₹{row['High_52W']}\n"
                f"💫 <b>52 Week Low:</b> ₹{row['Low_52W']}\n\n"
                f"{row['Weekly_Table']}\n"
                f"{weekly_chg_emoji} <b>Weekly Change:</b> {weekly_chg_str}\n\n"
                f"🎯 <b>Weekly R3:</b> {row['Weekly_R3']}\n"
                f"🎯 <b>Weekly S3:</b> {row['Weekly_S3']}\n"
                f"🚩 <b>Weekly Pivot Result:</b> {row['Weekly_Pivot_Result']}\n"
                f"📏 <b>Weekly Pivot %:</b> {row['Weekly_Pivot_Pct']}\n\n"
                f"🎯 <b>Weekly Upper BB:</b> {row['Weekly_Upper_BB']}\n"
                f"🎯 <b>Weekly Lower BB:</b> {row['Weekly_Lower_BB']}\n"
                f"🚩 <b>Weekly BB Result:</b> {row['Weekly_BB_Result']}\n"
                f"📏 <b>Weekly BB %:</b> {row['Weekly_BB_Pct']}\n\n"
                f"📰 <b>Recent Headlines:</b>\n{google_news}"
            )
            send_telegram_message(msg)
            time.sleep(1) 
    else:
        send_telegram_message("<b>🚀 N250 Weekly R2 Gainers 🚀</b>\nNo bullish setups found in this scan.")

    if not losers_df.empty:
        for idx, (_, row) in enumerate(losers_df.iterrows(), start=1):
            symbol = row['Symbol']
            google_news = get_google_news(symbol)
            
            daily_chg = row['Daily_Change']
            weekly_chg = row['%_Change']
            
            daily_chg_emoji = "📈" if daily_chg >= 0 else "📉"
            weekly_chg_emoji = "📈" if weekly_chg >= 0 else "📉"
            
            daily_chg_str = f"+{daily_chg}%" if daily_chg > 0 else f"{daily_chg}%"
            weekly_chg_str = f"+{weekly_chg}%" if weekly_chg > 0 else f"{weekly_chg}%"
            
            msg = (
                f"<b>🩸 N250 Weekly S2 Losers 🩸 #{idx}</b>\n\n"
                f"🏢 <b>Symbol:</b> #{symbol}\n\n"
                f"{row['Daily_Table']}\n"
                f"{daily_chg_emoji} <b>Daily Change:</b> {daily_chg_str}\n\n"
                f"🎯 <b>Daily R3:</b> {row['Daily_R3']}\n"
                f"🎯 <b>Daily S3:</b> {row['Daily_S3']}\n"
                f"🚩 <b>Daily Pivot Result:</b> {row['Daily_Pivot_Result']}\n"
                f"📏 <b>Daily Pivot %:</b> {row['Daily_Pivot_Pct']}\n\n"
                f"🎯 <b>Daily Upper BB:</b> {row['Daily_Upper_BB']}\n"
                f"🎯 <b>Daily Lower BB:</b> {row['Daily_Lower_BB']}\n"
                f"🚩 <b>Daily BB Result:</b> {row['Daily_BB_Result']}\n"
                f"📏 <b>Daily BB %:</b> {row['Daily_BB_Pct']}\n\n"
                f"🌟 <b>52 Week High:</b> ₹{row['High_52W']}\n"
                f"💫 <b>52 Week Low:</b> ₹{row['Low_52W']}\n\n"
                f"{row['Weekly_Table']}\n"
                f"{weekly_chg_emoji} <b>Weekly Change:</b> {weekly_chg_str}\n\n"
                f"🎯 <b>Weekly R3:</b> {row['Weekly_R3']}\n"
                f"🎯 <b>Weekly S3:</b> {row['Weekly_S3']}\n"
                f"🚩 <b>Weekly Pivot Result:</b> {row['Weekly_Pivot_Result']}\n"
                f"📏 <b>Weekly Pivot %:</b> {row['Weekly_Pivot_Pct']}\n\n"
                f"🎯 <b>Weekly Upper BB:</b> {row['Weekly_Upper_BB']}\n"
                f"🎯 <b>Weekly Lower BB:</b> {row['Weekly_Lower_BB']}\n"
                f"🚩 <b>Weekly BB Result:</b> {row['Weekly_BB_Result']}\n"
                f"📏 <b>Weekly BB %:</b> {row['Weekly_BB_Pct']}\n\n"
                f"📰 <b>Recent Headlines:</b>\n{google_news}"
            )
            send_telegram_message(msg)
            time.sleep(1)
    else:
        send_telegram_message("<b>🩸 N250 Weekly S2 Losers 🩸</b>\nNo bearish setups found in this scan.")

    # --- FINAL SUMMARY MESSAGE ---
    summary_text = f"✅ <b>Scan Complete!</b>\nSent {len(gainers_df)} Bullish and {len(losers_df)} Bearish Alerts."
    print(f"\n✅ Scan Complete! Sent {len(gainers_df)} Bullish and {len(losers_df)} Bearish Alerts.")
    send_telegram_message(summary_text)

if __name__ == "__main__":
    if not API_KEY or not API_SECRET:
        print("Error: ZERODHA_API_KEY or ZERODHA_API_SECRET not found in .env file.")
        sys.exit(1)
        
    try:
        while True:
            authenticate()
            symbols = get_nifty250_symbols()
            
            # Phase 1: Check if Dump needs to be created today
            if not is_dump_fresh():
                tokens = get_instrument_tokens(symbols)
                create_daily_dump(symbols, tokens)
            else:
                print("\n✅ Daily Cache exists and is fresh. Skipping historical data fetch.")
                
            # Phase 2: Run the High-Speed Live Scan
            run_live_scan(symbols)
            
            print(f"\n⏳ Sleeping for 30 minutes before the next scan... (Press Ctrl+C to stop)")
            time.sleep(1800)  # Sleep for 30 minutes (1800 seconds)
            
    except KeyboardInterrupt:
        print("\n\n🛑 Script manually stopped by user. Exiting gracefully.")
        sys.exit(0)