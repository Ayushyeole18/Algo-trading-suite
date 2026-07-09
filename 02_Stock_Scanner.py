import os
import sys
import io
import time
import html
import re
import requests
import pandas as pd
import urllib.parse
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta
from statistics import mean, pstdev
from kiteconnect import KiteConnect
from dotenv import load_dotenv, set_key
from google import genai

# 1. Load Environment Variables
load_dotenv()
API_KEY = os.getenv("ZERODHA_API_KEY")
API_SECRET = os.getenv("ZERODHA_API_SECRET")
TELEGRAM_BOT_TOKEN = os.getenv("02_TELEGRAM_BOT_TOKEN")
GG_API_KEY = os.getenv("GG_API_KEY")
TELEGRAM_ADMIN_CHAT_ID = os.getenv("TELEGRAM_CHAT_IDS_01") 

if not API_KEY or not API_SECRET or not TELEGRAM_BOT_TOKEN:
    print("Error: ZERODHA_API_KEY, ZERODHA_API_SECRET, or 02_TELEGRAM_BOT_TOKEN missing in .env file.")
    sys.exit(1)

if not GG_API_KEY:
    print("Warning: GG_API_KEY missing in .env. Gemini AI features will be disabled.")

# Initialize KiteConnect
kite = KiteConnect(api_key=API_KEY)


# ==========================================
# MATH & LOGIC HELPER FUNCTIONS
# ==========================================

def calc_pivots(h, l, c):
    """Calculates standard Fibonacci pivots."""
    p = round((h + l + c) / 3, 2)
    diff = h - l
    return {
        'r3': round(p + (1.000 * diff), 2), 'r2': round(p + (0.618 * diff), 2),
        'r1': round(p + (0.382 * diff), 2), 'p': p,
        's1': round(p - (0.382 * diff), 2), 's2': round(p - (0.618 * diff), 2),
        's3': round(p - (1.000 * diff), 2)
    }

def calc_bb(closes, ltp):
    """Calculates dynamic 25-period Bollinger Bands and status relative to LTP."""
    if len(closes) < 25: 
        return 0.0, 0.0, "N/A", "-"
        
    sma, std = mean(closes), pstdev(closes)
    upper, lower = round(sma + (2 * std), 2), round(sma - (2 * std), 2)
    
    status, pct = "LTP is within BB", "-"
    if upper > 0 and lower > 0:
        if ltp > upper:
            status, pct = "LTP &gt; Upper BB", f"+{round(((ltp - upper) / upper) * 100, 2)}%"
        elif ltp < lower:
            status, pct = "LTP &lt; Lower BB", f"{round(((ltp - lower) / lower) * 100, 2)}%"
            
    return upper, lower, status, pct

def calc_pivot_status(ltp, pv):
    """Determines LTP position relative to Pivot levels."""
    if ltp > pv['r3']: return "LTP &gt; R3", f"+{round(((ltp - pv['r3']) / pv['r3']) * 100, 2)}%"
    if ltp > pv['r2']: return "LTP &gt; R2", "-"
    if ltp > pv['r1']: return "LTP &gt; R1", "-"
    if ltp > pv['p']: return "LTP &gt; Pivot", "-"
    if ltp > pv['s1']: return "LTP &lt; Pivot", "-"
    if ltp > pv['s2']: return "LTP &lt; S1", "-"
    if ltp > pv['s3']: return "LTP &lt; S2", "-"
    return "LTP &lt; S3", f"{round(((ltp - pv['s3']) / pv['s3']) * 100, 2)}%"

def extract_screener_data(tables, table_idx, title_prefix, is_annual=False):
    """Helper to extract and format net profit data from parsed Screener DataFrames safely."""
    error_msg = f"{title_prefix}:</b>\n<i>Data unavailable for this symbol.</i>"
    
    if len(tables) <= table_idx:
        return error_msg
        
    df = tables[table_idx]
    np_row = df[df.index.str.contains('Net Profit', na=False, regex=False)]
    
    if np_row.empty:
        return error_msg
        
    series = np_row.iloc[0]
    cols = [c for c in series.index if str(c).upper() != 'TTM']
    limit = 3 if is_annual else 5
    recent = series[cols].tail(limit)
    
    if recent.empty:
        return error_msg
        
    lines = [f"{title_prefix} (Last {len(recent)}):</b>"]
    for date_str, val in recent.items():
        if pd.isna(val) or str(val).strip() == '':
            lines.append(f"   ▪️ {date_str}: <i>Data Missing (NaN)</i>")
        else:
            try:
                val_float = float(str(val).replace(',', '').strip())
                lines.append(f"   ▪️ {date_str}: ₹{val_float:,.2f} Cr")
            except ValueError:
                lines.append(f"   ▪️ {date_str}: <i>Invalid Format</i>")
                
    return "\n".join(lines)

def calc_max_pain(symbol, ltp, nfo_df):
    """Calculates Max Pain for the nearest expiry if the stock belongs to NFO."""
    if nfo_df.empty: return "No", "-", "-"
    
    df_sym = nfo_df[nfo_df['name'] == symbol]
    if df_sym.empty: 
        return "No", "-", "-"
        
    is_nfo = "Yes"
    today = datetime.today().date()
    future_expiries = df_sym[df_sym['expiry'] >= today]['expiry'].unique()
    
    if len(future_expiries) == 0:
        return is_nfo, "-", "-"
        
    nearest_expiry = min(future_expiries)
    df_exp = df_sym[df_sym['expiry'] == nearest_expiry]
    
    quote_keys = [f"NFO:{ts}" for ts in df_exp['tradingsymbol'].tolist()]
    quotes = {}
    
    try:
        for i in range(0, len(quote_keys), 500):
            quotes.update(kite.quote(quote_keys[i:i+500]))
    except Exception as e:
        print(f"Quote fetch error for Max Pain: {e}")
        return is_nfo, "Fetch Error", "-"
        
    strikes_data = []
    for _, row in df_exp.iterrows():
        key = f"NFO:{row['tradingsymbol']}"
        if key in quotes:
            strikes_data.append({
                'strike': row['strike'],
                'type': row['instrument_type'],
                'oi': quotes[key].get('oi', 0)
            })
            
    df_oi = pd.DataFrame(strikes_data)
    if df_oi.empty: return is_nfo, "-", "-"
    
    strikes = sorted(df_oi['strike'].unique())
    min_pain = float('inf')
    max_pain_strike = None
    
    for assumed_strike in strikes:
        ce_mask = (df_oi['type'] == 'CE') & (df_oi['strike'] < assumed_strike)
        pe_mask = (df_oi['type'] == 'PE') & (df_oi['strike'] > assumed_strike)
        
        pain_ce = ((assumed_strike - df_oi.loc[ce_mask, 'strike']) * df_oi.loc[ce_mask, 'oi']).sum()
        pain_pe = ((df_oi.loc[pe_mask, 'strike'] - assumed_strike) * df_oi.loc[pe_mask, 'oi']).sum()
        
        total_pain = pain_ce + pain_pe
        if total_pain < min_pain:
            min_pain = total_pain
            max_pain_strike = assumed_strike
            
    if max_pain_strike is not None:
        diff = ltp - max_pain_strike
        pct = (diff / max_pain_strike) * 100
        pct_str = f"+{round(pct, 2)}%" if pct >= 0 else f"{round(pct, 2)}%"
        return is_nfo, f"{max_pain_strike}", pct_str
        
    return is_nfo, "-", "-"

def calc_all_time_return(token, ltp, df_730):
    """Calculates All Time Return, CAGR, and Period by finding the earliest available historical close price and date."""
    if df_730.empty:
        return "-", "-", "-", "-", "-"
        
    listing_price = float(df_730.iloc[0]['close'])
    earliest_date = df_730.index[0]
    
    if (datetime.today() - earliest_date).days >= 720:
        end_dt = earliest_date - timedelta(days=1)
        start_dt = end_dt - timedelta(days=2000)
        
        for _ in range(5): 
            try:
                data = kite.historical_data(token, start_dt.strftime('%Y-%m-%d'), end_dt.strftime('%Y-%m-%d'), "day")
                if not data:
                    break
                listing_price = float(data[0]['close'])
                earliest_date = pd.to_datetime(data[0]['date']).replace(tzinfo=None)
                
                end_dt = start_dt - timedelta(days=1)
                start_dt = end_dt - timedelta(days=2000)
            except Exception as e:
                print(f"Error fetching historical chunk for all-time return: {e}")
                break

    if listing_price > 0:
        # 1. Total Return Calculation
        pct = ((ltp - listing_price) / listing_price) * 100
        sign = "+" if pct >= 0 else ""
        pct_str = f"{sign}{round(pct, 2)}%"
        price_str = f"₹{listing_price}"
        date_str = earliest_date.strftime('%Y-%m-%d')
        
        # 2. CAGR & Period Calculation
        days_elapsed = (datetime.today() - earliest_date).days
        if days_elapsed > 0:
            years = days_elapsed / 365.25
            cagr = ((ltp / listing_price) ** (1 / years)) - 1
            cagr_pct = cagr * 100
            cagr_sign = "+" if cagr_pct >= 0 else ""
            cagr_str = f"{cagr_sign}{round(cagr_pct, 2)}%"
            period_str = f"{round(years, 1)} Years"
        else:
            cagr_str = "-"
            period_str = "-"
            
        return pct_str, price_str, date_str, cagr_str, period_str
        
    return "-", "-", "-", "-", "-"


def build_range_bar(low, high, current, low_label=None, high_label=None, width=12):
    """Builds a simple text-based position/range bar for Telegram messages (e.g. 52W Range, DEMA Position)."""
    try:
        low_label = low if low_label is None else low_label
        high_label = high if high_label is None else high_label
        lo, hi = (low, high) if low <= high else (high, low)

        ratio = 0.5 if hi == lo else (current - lo) / (hi - lo)
        ratio = max(0.0, min(1.0, ratio))
        pos = int(round(ratio * (width - 1)))

        bar = ["─"] * width
        bar[pos] = "🔘"
        return f"{low_label} |{''.join(bar)}| {high_label}"
    except Exception:
        return "-"


def calc_ema_metrics(daily_closes, weekly_closes, ltp):
    """Calculates 50/200-period Daily EMA and Weekly EMA vs LTP, with position bars.

    NOTE: With ~730 days of history, the "200" period EMAs (daily & especially weekly)
    are approximated using whatever data is available (span capped to series length),
    same graceful-degradation approach used elsewhere in this script (e.g. calc_bb).
    """
    def _ema(closes, period):
        if len(closes) < 10:
            return None
        s = pd.Series(closes, dtype="float64")
        span = min(period, len(s))
        return round(float(s.ewm(span=span, adjust=False).mean().iloc[-1]), 2)

    def _fmt(val, ltp_):
        if not val:
            return "-"
        pct = round(((ltp_ - val) / val) * 100, 2)
        sign = "+" if pct >= 0 else ""
        return f"{val} (LTP: {sign}{pct}%)"

    def _bar(short_val, long_val):
        if not short_val or not long_val:
            return "-"
        lo, hi = min(short_val, long_val), max(short_val, long_val)
        ratio = 0.5 if hi == lo else (ltp - lo) / (hi - lo)
        ratio = max(0.0, min(1.0, ratio))
        return build_range_bar(1, 2, 1 + ratio, "1", "2")

    dema50, dema200 = _ema(daily_closes, 50), _ema(daily_closes, 200)
    wema50, wema200 = _ema(weekly_closes, 50), _ema(weekly_closes, 200)

    return {
        "dema50_str": _fmt(dema50, ltp), "dema200_str": _fmt(dema200, ltp),
        "dema_bar": _bar(dema50, dema200), "dema200_raw": dema200,
        "wema50_str": _fmt(wema50, ltp), "wema200_str": _fmt(wema200, ltp),
        "wema_bar": _bar(wema50, wema200),
    }


def calc_price_predictions(daily_closes, ltp, dema200_raw):
    """Generates simple statistical price projections for 1M / 6M / 1Y horizons.

    IMPORTANT: This is a lightweight statistical estimate — historical drift (mean daily
    return) blended 70/30 with a pull toward the 200-day EMA — NOT a trained/validated ML
    model. Treat the "Hybrid ML" label as illustrative branding, and the output as a rough
    guide only, never as investment advice.
    """
    try:
        closes = daily_closes[-252:] if len(daily_closes) >= 30 else daily_closes
        s = pd.Series(closes, dtype="float64")
        if len(s) < 10:
            return None

        returns = s.pct_change().dropna()
        mu = float(returns.mean())

        horizons = {"1 Month": 21, "6 Months": 126, "1 Year": 252}
        predictions = {}
        for label, days in horizons.items():
            drift_price = ltp * ((1 + mu) ** days)
            target = (0.7 * drift_price) + (0.3 * dema200_raw) if dema200_raw else drift_price
            pct = ((target - ltp) / ltp) * 100
            predictions[label] = (round(target, 2), round(pct, 2))
        return predictions
    except Exception as e:
        print(f"Price prediction calc error: {e}")
        return None


def get_extra_fundamentals(symbol, chat_id):
    """Fetches Market Cap, Face Value, Debt to Equity, Sales Growth, and Shareholding
    Pattern (FII / DII / Promoter holding + change vs previous quarter) from Screener.in."""
    result = {
        "market_cap": "-", "face_value": "-", "debt_to_eq": "-", "sales_growth": "-",
        "fii_hold": "-", "fii_chg": "-", "promoter_hold": "-", "promoter_chg": "-",
        "dii_hold": "-", "dii_chg": "-",
    }
    try:
        screener_symbol = symbol.replace('&', '-').replace(' ', '-')
        urls = [
            f"https://www.screener.in/company/{screener_symbol}/consolidated/",
            f"https://www.screener.in/company/{screener_symbol}/"
        ]
        headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"}
        html_text, tables = "", []

        for url in urls:
            response = requests.get(url, headers=headers, timeout=10)
            if response.status_code == 200:
                html_text = response.text
                try:
                    tables = pd.read_html(io.StringIO(html_text))
                    if tables:
                        break
                except Exception as parse_e:
                    print(f"HTML Parse error for {symbol} (extra fundamentals): {parse_e}")
                    continue

        if not html_text:
            return result

        # --- Top Ratios block (same regex pattern already used for Dividend Yield) ---
        def _ratio(label):
            m = re.search(rf'{label}.*?class="number">([^<]+)</span>', html_text, re.IGNORECASE | re.DOTALL)
            return m.group(1).strip() if m else None

        mc = _ratio("Market Cap")
        if mc:
            result["market_cap"] = f"₹{mc} Cr"

        fv = _ratio("Face Value")
        if fv:
            result["face_value"] = fv

        dte = _ratio("Debt to equity")
        if dte:
            result["debt_to_eq"] = dte

        # --- Sales Growth (YoY, from the Sales row of the Annual results table) ---
        for tb in tables:
            first_col = tb.columns[0]
            if tb[first_col].astype(str).str.contains('Sales', na=False, regex=False).any():
                tb2 = tb.set_index(first_col)
                sales_row = tb2[tb2.index.astype(str).str.contains('Sales', na=False, regex=False)]
                if not sales_row.empty:
                    cols = [c for c in sales_row.columns if str(c).upper() != 'TTM']
                    vals = sales_row.iloc[0][cols].tail(2)
                    if len(vals) == 2:
                        try:
                            prev = float(str(vals.iloc[0]).replace(',', '').strip())
                            curr = float(str(vals.iloc[1]).replace(',', '').strip())
                            if prev != 0:
                                growth = ((curr - prev) / abs(prev)) * 100
                                result["sales_growth"] = f"{round(growth, 2)}%"
                        except Exception:
                            pass
                break

        # --- Shareholding Pattern (Promoter / FII / DII, + change vs previous quarter) ---
        for tb in tables:
            first_col = tb.columns[0]
            col_vals = tb[first_col].astype(str)
            if col_vals.str.contains('Promoter', na=False, regex=False).any() and \
               col_vals.str.contains('FII', na=False, regex=False).any():
                tb2 = tb.set_index(first_col)

                def _hold(label):
                    row = tb2[tb2.index.astype(str).str.contains(label, na=False, regex=False)]
                    if row.empty:
                        return "-", "-"
                    vals = row.iloc[0].dropna()
                    if len(vals) < 2:
                        return "-", "-"
                    try:
                        curr = float(str(vals.iloc[-1]).replace('%', '').strip())
                        prev = float(str(vals.iloc[-2]).replace('%', '').strip())
                        chg = round(curr - prev, 2)
                        chg_str = f"{'+' if chg >= 0 else ''}{chg}%"
                        return f"{curr}%", chg_str
                    except Exception:
                        return "-", "-"

                result["promoter_hold"], result["promoter_chg"] = _hold("Promoter")
                result["fii_hold"], result["fii_chg"] = _hold("FII")
                result["dii_hold"], result["dii_chg"] = _hold("DII")
                break

        return result
    except Exception as e:
        print(f"Extra fundamentals fetch error for {symbol}: {e}")
        return result


# ==========================================
# CORE API FUNCTIONS
# ==========================================

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
            print("⚠️ Saved token is expired. Requesting fresh login...")
            access_token = None 
            
    if not access_token:
        print("\nLogin to this URL to get your request token:", kite.login_url())
        redirected_url = input("Paste the ENTIRE redirected URL here: ").strip()
        
        try:
            parsed_url = urllib.parse.urlparse(redirected_url)
            request_token = urllib.parse.parse_qs(parsed_url.query)['request_token'][0]
            
            session_data = kite.generate_session(request_token, api_secret=API_SECRET)
            access_token = session_data["access_token"]
            
            set_key(env_path, "ZERODHA_ACCESS_TOKEN", access_token)
            kite.set_access_token(access_token)
            print("✅ Auth successful! New token securely saved to .env.\n")
            
        except KeyError:
            print("Error: Could not find 'request_token' in the URL.")
            sys.exit(1)
        except Exception as e:
            print(f"Authentication failed: {e}")
            sys.exit(1)

def get_nse_instruments():
    """Fetches all NSE instruments and creates a fast lookup dictionary."""
    print("Fetching NSE instruments for symbol mapping...")
    try:
        instruments = kite.instruments("NSE")
        return {ins['tradingsymbol'].upper(): ins['instrument_token'] for ins in instruments}
    except Exception as e:
        print(f"Error fetching instruments: {e}")
        return {}

def get_nfo_instruments():
    """Fetches and caches NFO options for rapid Max Pain calculations."""
    print("Fetching NFO options chain data...")
    try:
        nfo_ins = kite.instruments("NFO")
        df = pd.DataFrame(nfo_ins)
        df_opts = df[df['segment'] == 'NFO-OPT'].copy()
        df_opts['expiry'] = pd.to_datetime(df_opts['expiry']).dt.date
        return df_opts
    except Exception as e:
        print(f"Error fetching NFO instruments: {e}")
        return pd.DataFrame()

def send_telegram_message(chat_id, message, req_timeout=10):
    """Sends an HTML formatted text message to a specific Telegram Chat ID."""
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    payload = {
        "chat_id": chat_id,
        "text": message,
        "parse_mode": "HTML",
        "disable_web_page_preview": True 
    }
    try:
        response = requests.post(url, json=payload, timeout=req_timeout)
        if response.status_code != 200:
            print(f"⚠️ Telegram API Error (Code {response.status_code}): {response.text}")
            return False
        return True
    except Exception as e:
        print(f"Failed to send Telegram message to {chat_id}: {e}")
        return False

def get_gemini_analysis(symbol, chat_id):
    """Fetches a quick AI summary and outlook for the stock using the Google GenAI SDK."""
    if not GG_API_KEY:
        return "<i>Gemini API key missing. AI analysis disabled.</i>"
    
    prompt = (
        f"Provide a brief 2 to 3 sentence summary of the latest news and a quick technical/fundamental "
        f"outlook for {symbol} stock on the NSE India. Keep it strictly plain text. Do not use markdown like asterisks."
    )
    
    try:
        client = genai.Client(api_key=GG_API_KEY)
        try:
            response = client.models.generate_content(model='gemini-2.5-flash', contents=prompt)
        except Exception as e:
            error_str = str(e).upper()
            if any(x in error_str for x in ["429", "RESOURCE", "503", "UNAVAILABLE", "QUOTA"]):
                warn_msg = f"⚠️ Gemini API limit/overload for {symbol}. Attempting fallback to 2.5-flash-lite..."
                print(f"⚠️ Primary model failed. Attempting fallback. Error: {e}")
                send_telegram_message(chat_id, warn_msg)
            else:
                warn_msg = f"⚠️ Gemini primary model failed for {symbol}. Attempting fallback..."
                print(f"⚠️ Primary model failed. Attempting fallback. Error: {e}")
                send_telegram_message(chat_id, warn_msg)
            
            try:
                response = client.models.generate_content(model='gemini-2.5-flash-lite', contents=prompt)
            except Exception as fallback_e:
                fallback_error_str = str(fallback_e).upper()
                if any(x in fallback_error_str for x in ["429", "RESOURCE", "QUOTA"]):
                    send_telegram_message(chat_id, "⚠️ Gemini fallback failed. Daily API quota exceeded.")
                    return "<i>Gemini API daily quota exceeded. Analysis will resume when quota resets.</i>"
                elif any(x in fallback_error_str for x in ["503", "UNAVAILABLE"]):
                    send_telegram_message(chat_id, "⚠️ Gemini servers are completely overloaded. Analysis skipped.")
                    return "<i>Gemini servers are currently experiencing high demand. AI analysis unavailable right now.</i>"
                
                print(f"⚠️ Fallback model also failed: {fallback_e}")
                send_telegram_message(chat_id, f"⚠️ Gemini fallback model failed: {fallback_e}")
                return "<i>Gemini analysis currently unavailable due to an API error.</i>"

        return html.escape(response.text.strip())
    except Exception as e:
        print(f"Gemini API initialization error for {symbol}: {e}")
        send_telegram_message(chat_id, f"⚠️ Gemini API critical error for {symbol}.")
        return "<i>Gemini analysis currently unavailable due to an API error.</i>"

def get_google_news(symbol, chat_id):
    """Fetches the top 2 recent news headlines for a stock using Google News RSS."""
    try:
        query = urllib.parse.quote(f"{symbol} stock NSE India when:7d")
        url = f"https://news.google.com/rss/search?q={query}&hl=en-IN&gl=IN&ceid=IN:en"
        
        response = requests.get(url, timeout=5)
        root = ET.fromstring(response.content)
        
        news_items = []
        for item in root.findall('./channel/item')[:2]:
            title = item.find('title').text
            clean_title = html.escape(title.rsplit(' - ', 1)[0])
            news_items.append(f"🔹 {clean_title}")
            
        return "\n".join(news_items) if news_items else "🔹 No specific news found in the last 7 days."
    except Exception as e:
        print(f"Google News fetch error for {symbol}: {e}")
        send_telegram_message(chat_id, f"⚠️ Google News API fetch failed for {symbol}.")
        return "🔹 News fetch failed."

def get_screener_data(symbol, chat_id):
    """Fetches Quarterly Net Profit, Annual Net Profit, and Dividend Yield from Screener.in."""
    qtr_results_str = "🏛️ <b>Qtr Net Profit:</b>\n<i>Data unavailable.</i>"
    annual_results_str = "📅 <b>Annual Net Profit:</b>\n<i>Data unavailable.</i>"
    div_yield_str = "-"
    
    try:
        screener_symbol = symbol.replace('&', '-').replace(' ', '-')
        urls = [
            f"https://www.screener.in/company/{screener_symbol}/consolidated/",
            f"https://www.screener.in/company/{screener_symbol}/"
        ]
        
        headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"}
        tables = []
        html_text = ""
        
        for url in urls:
            response = requests.get(url, headers=headers, timeout=10)
            if response.status_code == 200:
                html_text = response.text
                
                # Extract Dividend Yield using Regex
                div_match = re.search(r'Dividend Yield.*?class="number">([^<]+)</span>', html_text, re.IGNORECASE | re.DOTALL)
                if div_match:
                    div_yield_str = f"{div_match.group(1).strip()}%"

                try:
                    tables = pd.read_html(io.StringIO(html_text))
                    if tables: break
                except Exception as parse_e:
                    print(f"HTML Parse error for {symbol} on Screener: {parse_e}")
                    continue
                    
        if not tables:
            return qtr_results_str, annual_results_str, div_yield_str

        np_tables = []
        for tb in tables:
            first_col = tb.columns[0]
            if tb[first_col].astype(str).str.contains('Net Profit', na=False, regex=False).any():
                tb.set_index(first_col, inplace=True)
                np_tables.append(tb)

        qtr_results_str = extract_screener_data(np_tables, 0, "🏛️ <b>Qtr Net Profit", is_annual=False)
        annual_results_str = extract_screener_data(np_tables, 1, "📅 <b>Annual Net Profit", is_annual=True)

        return qtr_results_str, annual_results_str, div_yield_str
        
    except Exception as e:
        print(f"Screener.in error for {symbol}: {e}")
        send_telegram_message(chat_id, f"⚠️ Screener.in API fetch failed for {symbol}.")
        return "🏛️ <b>Qtr Net Profit:</b>\n<i>API Fetch Error.</i>", "📅 <b>Annual Net Profit:</b>\n<i>API Fetch Error.</i>", "-"

def broadcast_to_admin(message, req_timeout=10):
    """Broadcasts a message to the predefined Admin Chat ID."""
    if TELEGRAM_ADMIN_CHAT_ID:
        send_telegram_message(TELEGRAM_ADMIN_CHAT_ID, message, req_timeout)
    else:
        print("⚠️ TELEGRAM_ADMIN_CHAT_ID not set in .env. Skipping autonomous bot broadcast.")

def build_history_table(title, prices, prev_prices):
    """Constructs an aligned vertical text-based table highlighting highest/lowest using pure emojis."""
    inds = ["🟢" if p >= pp else "🔴" for p, pp in zip(prices, prev_prices)]
    p_strs = [f"{p:.1f}" for p in prices]
    
    max_val, min_val = max(prices), min(prices)
    max_w = max([len(p) for p in p_strs] + [7]) 
    
    table_lines = [
        f"📊 <b>{title}</b>",
        f"Tag\u2007\u2007\u2007{('\u2007' * max(max_w - 7, 0))}Closing"
    ]
    
    for i in range(5):
        padded_p = ("\u2007" * (max_w - len(p_strs[i]))) + p_strs[i]
        
        if prices[i] == max_val and prices[i] == min_val:
            final_p = padded_p 
        elif prices[i] == max_val:
            final_p = f"{padded_p} 🔵" 
        elif prices[i] == min_val:
            final_p = f"{padded_p} 🟠" 
        else:
            final_p = padded_p
            
        table_lines.append(f"{inds[i]}\u2007\u2007\u2007{final_p}")
        
    return "\n".join(table_lines)


# ==========================================
# MAIN EXECUTION & POLLING
# ==========================================

def process_on_demand_stock(symbol, token, chat_id, nfo_df):
    """Fetches historical and live data for a single stock and generates the report."""
    send_telegram_message(chat_id, f"⏳ Analyzing <b>{symbol}</b>. Fetching data...")
    
    try:
        # 1. Fetch Historical Data (730 days)
        fetch_start = (datetime.today() - timedelta(days=730)).strftime('%Y-%m-%d')
        fetch_end = datetime.today().strftime('%Y-%m-%d')
        
        hist_data = kite.historical_data(token, fetch_start, fetch_end, "day")
        if not hist_data:
            send_telegram_message(chat_id, f"⚠️ No historical data found for {symbol}.")
            return
            
        df = pd.DataFrame(hist_data)
        df['date'] = pd.to_datetime(df['date']).dt.tz_localize(None)
        df.set_index('date', inplace=True)
        today_ts = pd.Timestamp(datetime.today().date())
        
        # 2A. Previous Month (PM) Data 
        last_day_prev_month = today_ts.replace(day=1) - pd.Timedelta(days=1)
        first_day_prev_month = last_day_prev_month.replace(day=1)
        pm_df = df.loc[first_day_prev_month:last_day_prev_month]
        
        if pm_df.empty:
            send_telegram_message(chat_id, f"⚠️ Insufficient historical data (no previous month data) for {symbol}.")
            return
            
        pm_pv = calc_pivots(float(pm_df['high'].max()), float(pm_df['low'].min()), float(pm_df.iloc[-1]['close']))

        # 2B. Previous Year (PY) Data
        first_day_prev_year = pd.Timestamp(year=today_ts.year - 1, month=1, day=1)
        last_day_prev_year = pd.Timestamp(year=today_ts.year - 1, month=12, day=31)
        py_df = df.loc[first_day_prev_year:last_day_prev_year]
        
        if py_df.empty:
            send_telegram_message(chat_id, f"⚠️ Insufficient historical data (no previous year data) for {symbol}.")
            return
            
        py_pv = calc_pivots(float(py_df['high'].max()), float(py_df['low'].min()), float(py_df.iloc[-1]['close']))

        # 3. Daily Data Extract
        completed_days_all = df.loc[:today_ts - pd.Timedelta(days=1)]
        if len(completed_days_all) < 25:
            send_telegram_message(chat_id, f"⚠️ Not enough daily data (min 25 days) for {symbol}.")
            return
            
        recent_24_closes = completed_days_all['close'].tail(24).tolist()
        recent_6_closes = recent_24_closes[-6:] 
        
        # 4. Weekly Data 
        monday_ts = today_ts - pd.Timedelta(days=today_ts.dayofweek)
        weekly_df = df.loc[:monday_ts - pd.Timedelta(days=1)].resample('W-FRI').agg({
            'open': 'first', 'high': 'max', 'low': 'min', 'close': 'last'
        }).dropna()
        
        pw_c = float(weekly_df.iloc[-1]['close']) if not weekly_df.empty else 0.0
        w_closes = weekly_df['close'].tail(24).tolist()
        
        # 5. 52-Week High and Low
        df_52w = df.loc[today_ts - pd.DateOffset(years=1):]
        if not df_52w.empty:
            high_52w, low_52w = round(float(df_52w['high'].max()), 2), round(float(df_52w['low'].min()), 2)
        else:
            high_52w, low_52w = "N/A", "N/A"

        # 6. Live Quote Fetch
        nse_key = f"NSE:{symbol}"
        live_quote = kite.quote([nse_key])
        ltp = live_quote.get(nse_key, {}).get('last_price', 0)
        
        if ltp == 0:
            send_telegram_message(chat_id, f"⚠️ Could not fetch live price for {symbol}.")
            return

        # 7. Technical & NFO Calculations
        pct_change = round(((ltp - pw_c) / pw_c) * 100, 2) if pw_c > 0 else 0.0
        daily_pct_change = round(((ltp - recent_6_closes[-1]) / recent_6_closes[-1]) * 100, 2)
        
        is_nfo, max_pain, max_pain_pct = calc_max_pain(symbol, ltp, nfo_df)
        all_time_ret_str, hist_price_str, hist_date_str, cagr_str, period_str = calc_all_time_return(token, ltp, df)
        
        daily_upper_bb, daily_lower_bb, daily_bb_status, daily_bb_pct = calc_bb(recent_24_closes + [ltp], ltp)
        weekly_upper_bb, weekly_lower_bb, weekly_bb_status, weekly_bb_pct = calc_bb(w_closes + [ltp], ltp)
        
        daily_pivot_status, daily_pivot_pct = calc_pivot_status(ltp, pm_pv)
        weekly_pivot_status, weekly_pivot_pct = calc_pivot_status(ltp, py_pv)

        # Status Logic using PREVIOUS MONTH r2 and WEEKLY BB
        status = "Neutral"
        if pm_pv['r2'] > 0 and weekly_upper_bb > 0:
            if (ltp > pm_pv['r2']) and (ltp > weekly_upper_bb):
                status = "Gainer"
            elif (ltp < pm_pv['s2']) and (ltp < weekly_lower_bb) and weekly_lower_bb > 0:
                status = "Loser"

        # 8. Build Tables 
        prices = recent_6_closes[-4:] + [ltp]
        prev_prices = recent_6_closes[-5:]
        daily_table = build_history_table("Daily History (T-4 to T)", prices, prev_prices)
        
        weekly_table = "N/A"
        if len(w_closes) >= 5:
            w_prices = w_closes[-4:] + [ltp]
            w_prev_prices = w_closes[-5:]
            weekly_table = build_history_table("Weekly History (W-4 to W)", w_prices, w_prev_prices)

        # 9. External API Fetching
        google_news = get_google_news(symbol, chat_id)
        gemini_analysis = get_gemini_analysis(symbol, chat_id)
        qtr_net_profit, annual_net_profit, div_yield_str = get_screener_data(symbol, chat_id) 
        extra_fund = get_extra_fundamentals(symbol, chat_id)

        # 9B. DEMA / WEMA metrics + position bars (use full available history, not just recent slices)
        full_daily_closes = completed_days_all['close'].tolist()
        full_weekly_closes = weekly_df['close'].tolist()
        ema_metrics = calc_ema_metrics(full_daily_closes, full_weekly_closes, ltp)

        # 9C. 52-Week Range position bar
        range_52w_bar = build_range_bar(low_52w, high_52w, ltp) if high_52w != "N/A" else "-"

        # 9D. Simple statistical price projections ("Hybrid ML" — see calc_price_predictions docstring)
        price_predictions = calc_price_predictions(full_daily_closes, ltp, ema_metrics["dema200_raw"])
        if price_predictions:
            pred_lines = "\n".join(
                f"🎯 <b>{label}:</b> ₹{price:,.2f} ({'+' if pct >= 0 else ''}{pct}%)"
                for label, (price, pct) in price_predictions.items()
            )
        else:
            pred_lines = "<i>Insufficient data for prediction.</i>"
        
        # Build String
        daily_chg_emoji = "📈" if daily_pct_change >= 0 else "📉"
        weekly_chg_emoji = "📈" if pct_change >= 0 else "📉"
        header_emoji = "🚀" if status == "Gainer" else "🩸" if status == "Loser" else "⚖️"
        safe_symbol = html.escape(symbol)

        msg = (
            f"<b>{header_emoji} On-Demand Report: {safe_symbol}</b> ({status})\n\n"
            f"🏦 <b>Market Cap:</b> {extra_fund['market_cap']}\n"
            f"🔖 <b>Face Value:</b> {extra_fund['face_value']}\n"
            f"📈 <b>Sales growth:</b> {extra_fund['sales_growth']}\n"
            f"⚖️ <b>Debt to Eq:</b> {extra_fund['debt_to_eq']}\n"
            f"💸 <b>Dividend Yield:</b> {div_yield_str}\n"
            f"📜 <b>History Price:</b> {hist_price_str}\n"
            f"🗓️ <b>History Date:</b> {hist_date_str}\n"
            f"⏳ <b>History Period:</b> {period_str}\n"
            f"🚀 <b>History CAGR:</b> {cagr_str}\n"
            f"🏆 <b>All Time Return:</b> {all_time_ret_str}\n\n"
            f"🌍 <b>FII Holding:</b> {extra_fund['fii_hold']}\n"
            f"🔄 <b>Chg in FII Hold:</b> {extra_fund['fii_chg']}\n"
            f"👑 <b>Promoter Holding:</b> {extra_fund['promoter_hold']}\n"
            f"🔄 <b>Chg in Prom Hold:</b> {extra_fund['promoter_chg']}\n"
            f"🏛️ <b>DII Holding:</b> {extra_fund['dii_hold']}\n"
            f"🔄 <b>Chg in DII Hold:</b> {extra_fund['dii_chg']}\n\n"
            f"{daily_table}\n"
            f"{daily_chg_emoji} <b>Daily Change:</b> {daily_pct_change}%\n\n"
            f"📦 <b>Is NFO Stock:</b> {is_nfo}\n"
            f"🧲 <b>Max Pain:</b> {max_pain}\n"
            f"📏 <b>MP to LTP %:</b> {max_pain_pct}\n\n"
            f"🎯 <b>Daily R3:</b> {pm_pv['r3']}\n"
            f"🎯 <b>Daily S3:</b> {pm_pv['s3']}\n"
            f"🚩 <b>Daily Pivot Result:</b> {daily_pivot_status}\n"
            f"📏 <b>Daily Pivot %:</b> {daily_pivot_pct}\n\n"
            f"💠 <b>Daily Upper BB:</b> {daily_upper_bb}\n"
            f"💠 <b>Daily Lower BB:</b> {daily_lower_bb}\n"
            f"🚩 <b>Daily BB Result:</b> {daily_bb_status}\n"
            f"📏 <b>Daily BB %:</b> {daily_bb_pct}\n\n"
            f"⚡ <b>50DEMA:</b> {ema_metrics['dema50_str']}\n"
            f"🛡️ <b>200DEMA:</b> {ema_metrics['dema200_str']}\n"
            f"📏 <b>DEMA Position:</b>\n{ema_metrics['dema_bar']}\n\n"
            f"🌟 <b>52W Range:</b>\n{range_52w_bar}\n\n"
            f"{weekly_table}\n"
            f"{weekly_chg_emoji} <b>Weekly Change:</b> {pct_change}%\n\n"
            f"🎯 <b>Weekly R3:</b> {py_pv['r3']}\n"
            f"🎯 <b>Weekly S3:</b> {py_pv['s3']}\n"
            f"🚩 <b>Weekly Pivot Result:</b> {weekly_pivot_status}\n"
            f"📏 <b>Weekly Pivot %:</b> {weekly_pivot_pct}\n\n"
            f"💠 <b>Weekly Upper BB:</b> {weekly_upper_bb}\n"
            f"💠 <b>Weekly Lower BB:</b> {weekly_lower_bb}\n"
            f"🚩 <b>Weekly BB Result:</b> {weekly_bb_status}\n"
            f"📏 <b>Weekly BB %:</b> {weekly_bb_pct}\n\n"
            f"⚡ <b>50WEMA:</b> {ema_metrics['wema50_str']}\n"
            f"🛡️ <b>200WEMA:</b> {ema_metrics['wema200_str']}\n"
            f"📏 <b>WEMA Position:</b>\n{ema_metrics['wema_bar']}\n\n"
            f"{qtr_net_profit}\n\n"
            f"{annual_net_profit}\n\n"
            f"🤖 <b>Gemini AI Analysis:</b>\n{gemini_analysis}\n\n"
            f"📰 <b>Recent Headlines:</b>\n{google_news}\n\n"
            f"🔮 <b>AI PRICE PREDICTIONS (Hybrid ML):</b>\n{pred_lines}"
        )
        
        if send_telegram_message(chat_id, msg):
            print(f"✅ Report sent for {symbol} to {chat_id}")

    except Exception as e:
        error_msg = f"⚠️ Error processing {symbol}: {str(e)}"
        print(error_msg)
        send_telegram_message(chat_id, error_msg)

def listen_for_telegram_commands(instrument_lookup, nfo_df):
    """Long-polling loop to listen for stock symbols sent by users via Telegram."""
    print("\n🤖 Telegram Bot is actively listening for stock symbols...")
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/getUpdates"
    last_update_id = None
    
    while True:
        try:
            params = {"timeout": 5} 
            if last_update_id is not None:
                params["offset"] = last_update_id + 1
                
            response = requests.get(url, params=params, timeout=10)
            data = response.json()
            
            if data.get("ok") and data.get("result"):
                for update in data["result"]:
                    last_update_id = update["update_id"]
                    
                    if "message" in update and "text" in update["message"]:
                        chat_id = update["message"]["chat"]["id"]
                        text = update["message"]["text"].strip().upper()
                        
                        if text == "/START":
                            send_telegram_message(chat_id, "🟢 <b>Bot Active</b>\nSend me any NSE stock symbol to get a live technical analysis.")
                            continue
                            
                        print(f"📥 Received request for '{text}' from {chat_id}")
                        
                        if text in instrument_lookup:
                            process_on_demand_stock(text, instrument_lookup[text], chat_id, nfo_df)
                        else:
                            send_telegram_message(chat_id, f"❌ Symbol <b>{text}</b> not found on NSE. Please check the spelling.")
                            
        except KeyboardInterrupt:
            raise
        except requests.exceptions.RequestException:
            time.sleep(2)
        except Exception as e:
            print(f"Telegram polling error: {e}")
            time.sleep(5)

if __name__ == "__main__":
    try:
        authenticate()
        
        instrument_lookup = get_nse_instruments()
        if not instrument_lookup:
            print("Fatal Error: Could not load instruments mapping. Exiting.")
            sys.exit(1)
            
        print(f"✅ Loaded {len(instrument_lookup)} NSE instruments.")
        
        # Load NFO data for rapid Max Pain calculations
        nfo_df = get_nfo_instruments()
        
        print("\nSending startup notification...")
        broadcast_to_admin("🟢 <b>Bot Server Online!</b>\nAuthentication successful. Listening for on-demand stock queries.")
        
        listen_for_telegram_commands(instrument_lookup, nfo_df)
            
    except KeyboardInterrupt:
        print("\n\n🛑 Script manually stopped by user. Sending termination message...")
        broadcast_to_admin("🛑 <b>Bot Server Offline</b>\nThe script was manually terminated (Ctrl+C).", req_timeout=3)
        print("Exiting gracefully.")
        sys.exit(0)