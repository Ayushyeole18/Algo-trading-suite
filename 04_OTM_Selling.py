import os
import time
import sys
import logging
import datetime
from dateutil.relativedelta import relativedelta
import pandas as pd
import pandas_ta as ta
from dotenv import load_dotenv
from kiteconnect import KiteConnect
from kiteconnect.exceptions import NetworkException, TokenException, DataException

# ==========================================
# 1. STRATEGY CONFIGURATION
# ==========================================
class Config:
    # Initial Thresholds
    PRE_SCAN_MOVE_PCT = 8 
    
    # Trigger Thresholds
    MAX_PAIN_DIFF_PCT = 11
    BB_DIFF_PCT = 11

    # Technical Settings (Daily Chart & Monthly Pivot)
    BB_LENGTH = 25
    BB_MULT = 2
    
    # Option Liquidity Filters
    MAX_SPREAD_PCT = 10.0
    MIN_LIQUIDITY_PCT = 5.0      
    MIN_PREMIUM_PCT = 1.5        
    
    # Execution & Optimization
    API_SLEEP_TIME = 0.5         # Respect Kite's 3 requests/sec rate limit
    CYCLE_SLEEP_TIME = 30.0      # 30 seconds gap between consecutive cycle checks

# ==========================================
# 2. LOGGING SETUP (For 24/7 Headless Run)
# ==========================================
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(message)s',
    handlers=[
        logging.FileHandler("extreme_divergence_bot.log"),
        logging.StreamHandler(sys.stdout)
    ]
)
logger = logging.getLogger(__name__)

# ==========================================
# 3. KITE CONNECT STRATEGY ENGINE
# ==========================================
class KiteExtremeDivergenceBot:
    def __init__(self, api_key, access_token):
        self.kite = KiteConnect(api_key=api_key)
        self.kite.set_access_token(access_token)
        
        # Memory Caches
        self.instruments = pd.DataFrame()
        self.token_map = {}
        self.fno_universe = []  # Will be dynamically populated on startup
        self.active_orders = {} # State Machine: tracks pending orders
        
        self.load_instruments()

    def load_instruments(self):
        """Fetches and caches the master instrument list from Kite and dynamically builds the F&O universe."""
        logger.info("Fetching master instrument list from Kite...")
        try:
            instr_list = self.kite.instruments()
            self.instruments = pd.DataFrame(instr_list)
            
            # 1. Cache NSE Equity tokens for historical data calls
            nse_eq = self.instruments[(self.instruments['exchange'] == 'NSE') & (self.instruments['segment'] == 'NSE')]
            self.token_map = dict(zip(nse_eq['tradingsymbol'], nse_eq['instrument_token']))
            
            # 2. Dynamically build the F&O Universe
            # Get all unique underlying names from the NFO (Futures & Options) exchange
            nfo_df = self.instruments[self.instruments['exchange'] == 'NFO']
            unique_nfo_names = nfo_df['name'].dropna().unique()
            
            # Intersect NFO names with NSE equity symbols to extract pure F&O Stocks (safely excludes indices)
            self.fno_universe = sorted([sym for sym in unique_nfo_names if sym in self.token_map])
            
            logger.info(f"Instruments loaded successfully. Dynamic F&O Universe mapped: {len(self.fno_universe)} stocks.")
        except Exception as e:
            logger.error(f"Failed to load instruments: {e}")
            raise

    def get_fast_movers(self):
        """Module 0: The Pre-Scan Filter using batch quotes."""
        symbols = [f"NSE:{sym}" for sym in self.fno_universe]
        fast_movers = []
        
        try:
            # Kite allows max 500 symbols per quote call. 
            # Chunking ensures robust execution if the F&O universe exceeds 500.
            quotes = {}
            for i in range(0, len(symbols), 500):
                chunk = symbols[i:i+500]
                quotes.update(self.kite.quote(chunk))
                
            for key, data in quotes.items():
                symbol = key.split(':')[1]
                last_price = data['last_price']
                prev_close = data['ohlc']['close']
                
                if prev_close > 0:
                    pct_change = ((last_price - prev_close) / prev_close) * 100
                    # Using absolute value ensures BOTH gainers and losers are captured
                    if abs(pct_change) >= Config.PRE_SCAN_MOVE_PCT:
                        # Now also returning the pct_change so the main loop can identify Gainers/Losers
                        fast_movers.append((symbol, last_price, pct_change))
                        
            return fast_movers
        except Exception as e:
            logger.error(f"Pre-scan quote fetch failed: {e}")
            return []

    def get_monthly_pivots(self, symbol, token):
        """Calculates Auto 25 Monthly Pivots using exact calendar math."""
        today = datetime.date.today()
        first_day_this_month = today.replace(day=1)
        last_day_prev_month = first_day_this_month - datetime.timedelta(days=1)
        first_day_prev_month = last_day_prev_month.replace(day=1)

        try:
            hist_data = self.kite.historical_data(
                instrument_token=token,
                from_date=first_day_prev_month,
                to_date=last_day_prev_month,
                interval='day'
            )
            if not hist_data:
                return None, None
                
            df = pd.DataFrame(hist_data)
            high_m = df['high'].max()
            low_m = df['low'].min()
            close_m = df.iloc[-1]['close']
            
            pivot = (high_m + low_m + close_m) / 3
            range_m = high_m - low_m
            
            r3 = pivot + (1.000 * range_m)
            s3 = pivot - (1.000 * range_m)
            return r3, s3
        except Exception as e:
            logger.error(f"[{symbol}] Error fetching monthly data: {e}")
            return None, None

    def get_technical_data(self, symbol, token):
        """Calculates Daily Bollinger Bands safely and returns with Pivots."""
        to_date = datetime.date.today()
        from_date = to_date - datetime.timedelta(days=60) # Buffer for 25 trading days
        
        try:
            hist_data = self.kite.historical_data(token, from_date, to_date, 'day')
            if not hist_data:
                return None
                
            df = pd.DataFrame(hist_data)
            
            if len(df) < Config.BB_LENGTH:
                return None
                
            # Isolate the Bollinger Band DataFrame to dynamically pull column names
            bbands = df.ta.bbands(length=Config.BB_LENGTH, std=Config.BB_MULT)
            
            if bbands is None or bbands.empty:
                return None
                
            # Dynamically fetch the names of the Upper and Lower Band columns
            ubb_col = [col for col in bbands.columns if col.startswith('BBU')]
            lbb_col = [col for col in bbands.columns if col.startswith('BBL')]
            
            if not ubb_col or not lbb_col:
                return None
                
            ubb = bbands[ubb_col[0]].iloc[-1]
            lbb = bbands[lbb_col[0]].iloc[-1]
            
            r3, s3 = self.get_monthly_pivots(symbol, token)
            if r3 is None or s3 is None: 
                return None
                
            return {'UBB': ubb, 'LBB': lbb, 'R3': r3, 'S3': s3}
        except Exception as e:
            logger.error(f"[{symbol}] Error calculating technicals: {e}")
            return None

    def get_option_chain_quotes(self, symbol):
        """Filters NFO instruments for current expiry and fetches live quotes."""
        nfo = self.instruments[(self.instruments['exchange'] == 'NFO') & 
                               (self.instruments['name'] == symbol) & 
                               (self.instruments['segment'] == 'NFO-OPT')].copy()
        
        if nfo.empty: return None
        
        nfo['expiry'] = pd.to_datetime(nfo['expiry']).dt.date
        future_dates = nfo[nfo['expiry'] >= datetime.date.today()]['expiry'].unique()
        if len(future_dates) == 0: return None
        
        nearest_expiry = min(future_dates)
        chain = nfo[nfo['expiry'] == nearest_expiry]
        
        tokens = chain['instrument_token'].tolist()
        quotes = {}
        
        try:
            for i in range(0, len(tokens), 500):
                chunk = tokens[i:i+500]
                quotes.update(self.kite.quote(chunk))
            
            options_data = []
            for _, row in chain.iterrows():
                q = quotes.get(str(row['instrument_token']))
                if q:
                    options_data.append({
                        'tradingsymbol': row['tradingsymbol'],
                        'instrument_token': row['instrument_token'],
                        'strike': row['strike'],
                        'type': row['instrument_type'],
                        'lot_size': row['lot_size'],
                        'oi': q['oi'],
                        'best_bid': q['depth']['buy'][0]['price'] if q['depth']['buy'] else 0,
                        'best_ask': q['depth']['sell'][0]['price'] if q['depth']['sell'] else 0,
                        'ltp': q['last_price']
                    })
            return pd.DataFrame(options_data)
        except Exception as e:
            logger.error(f"[{symbol}] Error fetching option quotes: {e}")
            return None

    def calculate_max_pain(self, chain_df):
        """Calculates Max Pain using live Open Interest."""
        strikes = chain_df['strike'].unique()
        pain_values = []
        
        for strike in strikes:
            total_pain = 0
            for _, opt in chain_df.iterrows():
                if opt['type'] == 'CE' and opt['strike'] < strike:
                    total_pain += (strike - opt['strike']) * opt['oi']
                elif opt['type'] == 'PE' and opt['strike'] > strike:
                    total_pain += (opt['strike'] - strike) * opt['oi']
            pain_values.append((strike, total_pain))
            
        if not pain_values:
            return None
            
        return min(pain_values, key=lambda x: x[1])[0]

    def hunt_bounded_strike(self, symbol, ltp, direction, chain_df):
        """Slices the last 3 OTM strikes and applies strict % filters."""
        if direction == "CALL":
            otm = chain_df[(chain_df['type'] == 'CE') & (chain_df['strike'] > ltp)].sort_values('strike')
        else:
            otm = chain_df[(chain_df['type'] == 'PE') & (chain_df['strike'] < ltp)].sort_values('strike', ascending=False)
            
        if len(otm) < 3: return None
        bounded_strikes = otm.tail(3).to_dict('records')
        
        # Determine strict interval to find the exact ATM strike without dividing by zero
        unique_strikes = sorted(chain_df['strike'].unique())
        if len(unique_strikes) < 2:
            return None
            
        # Calculate the minimum difference between adjacent unique strikes
        strike_interval = min([unique_strikes[i+1] - unique_strikes[i] for i in range(len(unique_strikes)-1)])
        
        if strike_interval <= 0:
            return None
            
        # ATM Open Interest for relative liquidity check
        atm_strike = round(ltp / strike_interval) * strike_interval
        atm_opt = chain_df[(chain_df['strike'] == atm_strike) & (chain_df['type'] == ('CE' if direction == 'CALL' else 'PE'))]
        atm_oi = atm_opt['oi'].values[0] if not atm_opt.empty else 0

        if atm_oi == 0: return None

        for contract in bounded_strikes:
            bid, ask, oi = contract['best_bid'], contract['best_ask'], contract['oi']
            
            if ask <= 0 or bid <= 0: continue
                
            spread_pct = ((ask - bid) / ask) * 100
            premium_yield = (bid / ltp) * 100
            rel_liq = (oi / atm_oi) * 100
            
            if (spread_pct <= Config.MAX_SPREAD_PCT and 
                premium_yield >= Config.MIN_PREMIUM_PCT and 
                rel_liq >= Config.MIN_LIQUIDITY_PCT):
                return contract
                
        return None

    def evaluate_new_vs_pending(self, symbol, new_contract, pending_data):
        """State Machine Logic: Determines if the new setup overrides the pending order."""
        new_strike = new_contract['strike']
        new_ask = new_contract['best_ask']
        pending_strike = pending_data['strike']
        pending_ask = pending_data['price']
        direction = pending_data['direction']
        
        # Priority 1: Farther OTM?
        if (direction == "CALL" and new_strike > pending_strike) or (direction == "PUT" and new_strike < pending_strike):
            logger.info(f"[{symbol}] Found farther OTM strike ({new_strike} vs {pending_strike}). Cancelling & Replacing.")
            self.kite.cancel_order(variety=self.kite.VARIETY_REGULAR, order_id=pending_data['order_id'])
            return "REPLACE"
            
        # Priority 2: Same strike, higher premium?
        if new_strike == pending_strike and new_ask > pending_ask:
            logger.info(f"[{symbol}] Found higher premium for same strike ({new_ask} vs {pending_ask}). Modifying order.")
            return "MODIFY"
            
        return "IGNORE"

    def run(self):
        """Main non-blocking execution loop."""
        logger.info("Algo Started: Non-Blocking Extreme Divergence Bot Initialized.")
        
        while True:
            try:
                # Cycle Tracking Variables
                cycle_matches = 0
                cycle_new_orders = 0
                cycle_modifications = 0
                cycle_replacements = 0
                
                time.sleep(Config.API_SLEEP_TIME)
                
                # 1. Sync Active Positions
                net_positions = self.kite.positions()['net']
                active_symbols = [p['tradingsymbol'].split('2')[0] for p in net_positions if p['quantity'] != 0]
                
                # 2. Fast Pre-Scan Filter
                fast_movers = self.get_fast_movers()
                
                # Unpack the 3rd variable (pct_change)
                for stock, ltp, pct_change in fast_movers:
                    if stock in active_symbols: 
                        logger.info(f"[{stock}] Skipped analysis: Stock is already an active position.")
                        continue
                        
                    token = self.token_map.get(stock)
                    if not token: 
                        logger.warning(f"[{stock}] Skipped analysis: Instrument token not found.")
                        continue
                        
                    # 3. Check Pending Order Status
                    if stock in self.active_orders:
                        order_history = self.kite.order_history(self.active_orders[stock]['order_id'])
                        order_status = order_history[-1]['status'] if order_history else 'UNKNOWN'
                        
                        if order_status == 'COMPLETE':
                            logger.info(f"[{stock}] Pending order FILLED! Moving to active positions.")
                            del self.active_orders[stock]
                            continue
                        elif order_status in ['CANCELLED', 'REJECTED']:
                            del self.active_orders[stock]
                    
                    time.sleep(Config.API_SLEEP_TIME)
                    
                    # 4. Strategy Math (Max Pain & Confluence)
                    chain_df = self.get_option_chain_quotes(stock)
                    if chain_df is None or chain_df.empty: 
                        logger.warning(f"[{stock}] Skipped analysis: Could not fetch valid Option Chain (Empty/None).")
                        continue
                        
                    max_pain = self.calculate_max_pain(chain_df)
                    if max_pain is None: 
                        logger.warning(f"[{stock}] Skipped analysis: Could not calculate Max Pain.")
                        continue
                        
                    max_pain_diff = abs(ltp - max_pain) / max_pain * 100
                    
                    # Force technical calculation so we can log it regardless of whether it triggers an order
                    tech = self.get_technical_data(stock, token)
                    if not tech: 
                        logger.warning(f"[{stock}] Skipped analysis: Could not calculate Technicals (Insufficient historical data/Pivots).")
                        continue
                        
                    direction = None
                    
                    if pct_change >= 0:
                        # Gainer Math & Logging
                        ubb_diff = ((ltp - tech['UBB']) / tech['UBB']) * 100
                        r3_diff = ((ltp - tech['R3']) / tech['R3']) * 100
                        
                        logger.info(f"[{stock}] GAINER STATS | LTP: {ltp:.2f} ({pct_change:.2f}%) | MaxPain: {max_pain} ({max_pain_diff:.2f}%) | UBB: {tech['UBB']:.2f} ({ubb_diff:.2f}%) | R3: {tech['R3']:.2f} ({r3_diff:.2f}%)")
                        
                        if ltp > max_pain and max_pain_diff >= Config.MAX_PAIN_DIFF_PCT and ubb_diff >= Config.BB_DIFF_PCT and ltp > tech['R3']:
                            direction = "CALL"
                    else:
                        # Loser Math & Logging
                        lbb_diff = ((tech['LBB'] - ltp) / tech['LBB']) * 100
                        s3_diff = ((tech['S3'] - ltp) / tech['S3']) * 100
                        
                        logger.info(f"[{stock}] LOSER STATS  | LTP: {ltp:.2f} ({pct_change:.2f}%) | MaxPain: {max_pain} ({max_pain_diff:.2f}%) | LBB: {tech['LBB']:.2f} ({lbb_diff:.2f}%) | S3: {tech['S3']:.2f} ({s3_diff:.2f}%)")
                        
                        if ltp < max_pain and max_pain_diff >= Config.MAX_PAIN_DIFF_PCT and lbb_diff >= Config.BB_DIFF_PCT and ltp < tech['S3']:
                            direction = "PUT"
                        
                        
                    # 5. Strike Hunting & Execution (Only if conditions matched)
                    if direction:
                        contract = self.hunt_bounded_strike(stock, ltp, direction, chain_df)
                        if not contract: continue
                        
                        # Increment Match Counter
                        cycle_matches += 1
                            
                        # Non-Blocking Execution / State Machine Update
                        if stock in self.active_orders:
                            action = self.evaluate_new_vs_pending(stock, contract, self.active_orders[stock])
                            
                            if action == "MODIFY":
                                self.kite.modify_order(
                                    variety=self.kite.VARIETY_REGULAR,
                                    order_id=self.active_orders[stock]['order_id'],
                                    price=contract['best_ask']
                                )
                                self.active_orders[stock]['price'] = contract['best_ask']
                                cycle_modifications += 1
                                
                            elif action == "REPLACE":
                                new_order_id = self.kite.place_order(
                                    variety=self.kite.VARIETY_REGULAR,
                                    exchange=self.kite.EXCHANGE_NFO,
                                    tradingsymbol=contract['tradingsymbol'],
                                    transaction_type=self.kite.TRANSACTION_TYPE_SELL,
                                    quantity=self.instruments[self.instruments['tradingsymbol'] == contract['tradingsymbol']]['lot_size'].values[0],
                                    product=self.kite.PRODUCT_NRML,
                                    order_type=self.kite.ORDER_TYPE_LIMIT,
                                    price=contract['best_ask']
                                )
                                self.active_orders[stock] = {'order_id': new_order_id, 'strike': contract['strike'], 'price': contract['best_ask'], 'direction': direction}
                                cycle_replacements += 1
                        else:
                            # Fresh Limit Order
                            order_id = self.kite.place_order(
                                variety=self.kite.VARIETY_REGULAR,
                                exchange=self.kite.EXCHANGE_NFO,
                                tradingsymbol=contract['tradingsymbol'],
                                transaction_type=self.kite.TRANSACTION_TYPE_SELL,
                                quantity=self.instruments[self.instruments['tradingsymbol'] == contract['tradingsymbol']]['lot_size'].values[0],
                                product=self.kite.PRODUCT_NRML,
                                order_type=self.kite.ORDER_TYPE_LIMIT,
                                price=contract['best_ask']
                            )
                            logger.info(f"[{stock}] Fresh Limit Order Placed at {contract['best_ask']} for {contract['tradingsymbol']}. Order ID: {order_id}")
                            self.active_orders[stock] = {'order_id': order_id, 'strike': contract['strike'], 'price': contract['best_ask'], 'direction': direction}
                            cycle_new_orders += 1

                # 6. Cycle Completion Summary Output
                pending_count = len(self.active_orders)
                pending_list = list(self.active_orders.keys())
                
                # Split fast_movers into Gainers and Losers for summary
                gainers = [s[0] for s in fast_movers if s[2] >= 0]
                losers = [s[0] for s in fast_movers if s[2] < 0]
                
                logger.info("-" * 65)
                logger.info(f"CYCLE COMPLETE   | Gainers Scanned : {len(gainers)} ({', '.join(gainers) if gainers else 'None'})")
                logger.info(f"                 | Losers Scanned  : {len(losers)} ({', '.join(losers) if losers else 'None'})")
                logger.info(f"Matches Found    | Valid Setups Hit    : {cycle_matches}")
                logger.info(f"Order Actions    | Placed: {cycle_new_orders} | Modified: {cycle_modifications} | Replaced: {cycle_replacements}")
                logger.info(f"Pending Orders   | Total Active ({pending_count})   : {pending_list if pending_count > 0 else 'None'}")
                logger.info("-" * 65)
                
                # Apply the delay before starting the next full cycle
                time.sleep(Config.CYCLE_SLEEP_TIME)

            except NetworkException as e:
                logger.error(f"Network Error (Rate Limit/Connection): {e}")
                time.sleep(5)
            except TokenException as e:
                logger.critical(f"Token Expired! Halting execution: {e}")
                break
            except Exception as e:
                logger.error(f"Critical loop error: {e}", exc_info=True)
                time.sleep(2)

# ==========================================
# 4. INITIALIZATION (With Environment Loading)
# ==========================================
if __name__ == "__main__":
    # Load environment variables from .env file
    load_dotenv()
    
    API_KEY = os.getenv("ZERODHA_API_KEY")
    ACCESS_TOKEN = os.getenv("ZERODHA_ACCESS_TOKEN")
    
    # Pre-flight safety check to ensure credentials exist
    if not API_KEY or not ACCESS_TOKEN:
        logger.critical("Error: ZERODHA_API_KEY or ZERODHA_ACCESS_TOKEN is missing from your .env file!")
        sys.exit(1)
        
    logger.info("Credentials successfully verified from environment. Initializing bot...")
    
    # Wrap the execution in a try-except block to gracefully catch Ctrl+C
    try:
        bot = KiteExtremeDivergenceBot(API_KEY, ACCESS_TOKEN)
        bot.run()
    except KeyboardInterrupt:
        # Catch the Ctrl+C signal and prevent the traceback from printing, using ASCII-safe text
        print("\n") 
        logger.info("[STOP] Script manually terminated by user (Ctrl+C). Exiting gracefully...")
        sys.exit(0)