"""
⚡ LIVE SCALP SIGNAL BOT — Multi-Symbol (BTC + ETH)
Chạy liên tục, check mỗi 15 phút.
Phát signal lên Telegram + Google Sheets.
Paper trading (không đặt lệnh thật).

Usage:
    py -3 live_scalp.py --capital 350 --leverage 50
"""

import sys, os, time, json, argparse, requests
import pandas as pd
import numpy as np
from datetime import datetime
import logging
from concurrent.futures import ThreadPoolExecutor

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

# ============================================================
# LOGGING SETUP — Console + File (logs/scalp_YYYY-MM-DD.log)
# ============================================================
LOG_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'logs')
os.makedirs(LOG_DIR, exist_ok=True)

def _get_log_file():
    return os.path.join(LOG_DIR, f"scalp_{datetime.now().strftime('%Y-%m-%d')}.log")

# Setup logger
logger = logging.getLogger('scalp')
logger.setLevel(logging.INFO)
logger.handlers.clear()

# Console handler
ch = logging.StreamHandler(sys.stdout)
ch.setFormatter(logging.Formatter('%(message)s'))
logger.addHandler(ch)

# File handler (daily rotation)
fh = logging.FileHandler(_get_log_file(), encoding='utf-8')
fh.setFormatter(logging.Formatter('%(asctime)s | %(message)s', datefmt='%H:%M:%S'))
logger.addHandler(fh)

# Override print → logger (all existing prints go to file too)
_builtin_print = print
def print(*args, **kwargs):
    msg = ' '.join(str(a) for a in args)
    logger.info(msg)
    # Rotate file handler at midnight
    expected = _get_log_file()
    if fh.baseFilename != expected:
        fh.close()
        fh.baseFilename = expected
        fh.stream = open(expected, 'a', encoding='utf-8')
from src.config import Config
from src.data import get_btc_data, fetcher
from src.notifications import send_telegram_message, esc


# ============================================================
# SCALP STRATEGY CONFIG (from shared module)
# ============================================================
from src.scalp_strategy import (
    TAKER_FEE, QUICK_TP_PCT, BE_ATR_MULT, MAX_HOLD_BARS, BUFFER_PCT,
    TRAILING_ATR, MIN_RR, ATR_SL_MULT, prepare_market_data, get_htf_bias,
    detect_signal, scan_5m_entry, check_5m_bos, check_volume_confirmation,
    get_sweep_sl, get_1h_sweep_sl, calc_sl, calc_tp,
    calc_position_size, check_exit, check_sl_intrabar, handle_reverse,
)
from src.ict_core import detect_swing_points, detect_liquidity_sweep, calc_atr

RISK_PCT = 5.0          # % vốn mỗi lệnh
LEVERAGE = 50           # Đòn bẩy
COOLDOWN_BARS = 0       # Bỏ cooldown
VN_OFFSET = pd.Timedelta(hours=7)  # UTC+7
SYMBOLS = ['BTCUSDT']  # Default, overridden by --symbols arg


# Helper: fetch data with retry
def fetch_with_retry(tf, limit, retries=3, symbol='BTCUSDT'):
    """Fetch data with retry logic. Returns None only if all retries fail."""
    for attempt in range(retries):
        data = fetcher.fetch_symbol_data(symbol, tf, limit)
        if data is not None and len(data) > 0:
            return data
        if attempt < retries - 1:
            print(f"  ⚠️ API retry {attempt+1}/{retries} ({symbol} {tf})")
            time.sleep(2)
    print(f"  ❌ API failed after {retries} retries ({symbol} {tf})")
    return None


def to_vn_time(utc_str):
    """Convert UTC timestamp string to VN time (UTC+7)."""
    try:
        ts = pd.Timestamp(utc_str) + VN_OFFSET
        return ts.strftime('%Y-%m-%d %H:%M')
    except:
        return utc_str


# ============================================================
# SIGNAL DETECTION — ICT Liquidity Sweep (synced with backtest)
# ============================================================
def detect_scalp_signals(df, htf_df=None, ltf_df=None, m30_df=None, from_cache=False):
    """Detect ICT Liquidity Sweep signals on 15m data (uses shared module).
    from_cache: True nếu dùng WS cache (last bar = vừa đóng, không có nến mới)
                False nếu dùng REST API (last bar = nến mới chưa đóng)
    """
    if df is None or len(df) < 200:
        return []

    # WS cache: last bar = vừa đóng → dùng df[-1]
    # REST API: last bar = nến mới (vol≈0) → dùng df[-2]
    i = len(df) - 1 if from_cache else len(df) - 2
    if i < 50:
        return []

    # Prepare market data
    ctx = prepare_market_data(df, htf_df)
    atr_series = ctx['atr_series']
    atr = atr_series.iloc[i] if i < len(atr_series) and not pd.isna(atr_series.iloc[i]) else 0
    if atr <= 0:
        return []

    # HTF Bias
    htf_bias = get_htf_bias(htf_df, ctx['h1_swing_h'], ctx['h1_swing_l']) if htf_df is not None else None

    # Signal detection (sweep + bias filter)
    action, sweep_price, sweep_bar = detect_signal(ctx, i, htf_bias)
    if action is None:
        if htf_bias:
            # Check if blocked by bias for logging
            from src.ict_core import detect_liquidity_sweep as _dls
            st, sp, sb = _dls(ctx['h'], ctx['l'], ctx['c'], ctx['o'],
                              ctx['m15_swing_h'], ctx['m15_swing_l'], i, atr)
            if st:
                test_action = 'LONG' if st == 'BULLISH_SWEEP' else 'SHORT'
                if htf_bias and test_action != htf_bias:
                    print(f"  ⏭️ Skipped {test_action}: ngược 1H bias ({htf_bias})")
        return []

    signal_src = 'ICT:Sweep'
    timestamp = df.index[i]
    c = ctx['c']

    # VOLUME CONFIRMATION
    if not check_volume_confirmation(df, i):
        sym_name = df.attrs.get('symbol', '') if hasattr(df, 'attrs') else ''
        sym_short = sym_name.replace('USDT', '') if sym_name else ''
        print(f"  ⏭️ Skipped: Low volume ({sym_short})")
        try:
            skip_msg = f"""⏭️ *SKIPPED \\- Low Volume*{f" \\\\({esc(sym_short)}\\\\)" if sym_short else ""}

{esc(action)} @ ${esc(f"{c[i]:,.2f}")}
Sweep detected nhưng Volume yếu
🕐 {esc(str(timestamp + pd.Timedelta(hours=7)))}"""
            send_telegram_message(skip_msg, parse_mode='MarkdownV2')
        except:
            pass
        return []

    # 5m Entry
    bar_start = df.index[i-1] if i > 0 else df.index[i]
    bar_end = df.index[i]
    entry, has_5m = scan_5m_entry(action, c[i], ltf_df, bar_start, bar_end)
    if has_5m:
        signal_src += ':5m'

    # BOS 5m CONFIRMATION
    has_bos = check_5m_bos(action, ltf_df, bar_start, bar_end)
    if not has_bos:
        print(f"  ⏭️ Skipped: No BOS on 5m")
        return []
    signal_src += ':BOS'

    # SL: 1H sweep → ATR
    sl_1h = get_sweep_sl(action, timestamp, htf_df,
                         ctx['h1_swing_h'], ctx['h1_swing_l'])
    sl, sl_tag = calc_sl(action, entry, atr, sl_1h)
    if sl_tag:
        signal_src += ':' + sl_tag

    # TP: ICT structure
    tp1, tp2, tp3 = calc_tp(action, entry, atr, i, ctx)

    # R:R FILTER — TP1 phải đủ xa so với SL
    sl_dist = abs(entry - sl)
    tp1_dist = abs(tp1 - entry)
    if sl_dist > 0 and tp1_dist < sl_dist * MIN_RR:
        print(f"  ⏭️ Skipped: R:R too low (TP1={tp1_dist:.0f} < {MIN_RR}×SL={sl_dist*MIN_RR:.0f})")
        return []

    sig = {
        'action': action,
        'src': signal_src,
        'tf': '15m',
        'entry': entry,
        'entry_15m': float(c[i]),
        'sl': sl,
        'tp1': tp1,
        'tp2': tp2,
        'tp3': tp3,
        'atr': atr,
        'htf_bias': htf_bias,
        'sweep_price': sweep_price,
        'time': str(timestamp + pd.Timedelta(hours=7)),
    }
    return [sig]




# ============================================================
# TELEGRAM ALERT
# ============================================================
def send_scalp_alert(signal, capital, leverage, risk_pct, testnet=False):
    """Gửi signal scalp lên Telegram."""
    action = signal['action']
    entry = signal['entry']
    sl = signal['sl']
    tp1 = signal['tp1']
    tp2 = signal['tp2']
    tp3 = signal['tp3']
    src = signal['src']
    atr = signal['atr']
    htf = signal.get('htf_bias', 'N/A')
    sym = signal.get('symbol', 'BTCUSDT')
    sym_short = sym.replace('USDT', '')
    pair = f"{sym_short}/USDT"

    margin = capital * risk_pct / 100
    size = margin * leverage
    sl_pct = abs(entry - sl) / entry * 100
    tp1_pct = abs(tp1 - entry) / entry * 100

    emoji = '🟢' if action == 'LONG' else '🔴'
    dir_text = 'LONG 📈' if action == 'LONG' else 'SHORT 📉'

    entry_15m = signal.get('entry_15m', entry)
    has_two_entries = abs(entry - entry_15m) > 0.01

    entry_line = f"💰 Entry: ${esc(f'{entry:,.2f}')} \\- ${esc(f'{entry_15m:,.2f}')}" if has_two_entries else f"💰 Entry: ${esc(f'{entry:,.2f}')}"

    mode_footer = "_DEMO Testnet \\- Đã gửi lệnh_" if testnet else "_Paper Trading \\- Không đặt lệnh thật_"

    header = "⚡ *SCALP SIGNAL*"

    msg = f"""{header}

{emoji} *{esc(dir_text)}* {esc(pair)}

{entry_line}
🛑 SL: ${esc(f"{sl:,.2f}")} \\({esc(f"{sl_pct:.2f}%")}\\)
🎯 TP1: ${esc(f"{tp1:,.2f}")} \\({esc(f"{tp1_pct:.2f}%")}\\)
🎯 TP2: ${esc(f"{tp2:,.2f}")}
🎯 TP3: ${esc(f"{tp3:,.2f}")}

📊 1H Bias: {esc(htf or 'N/A')}
⚙️ Leverage: {esc(str(leverage))}x"""

    try:
        send_telegram_message(msg, parse_mode='MarkdownV2')
        print(f"  ✅ Telegram: Scalp {sym_short} {action} alert sent!")
    except Exception as e:
        print(f"  ⚠️ Telegram error: {e}")


# ============================================================
# GOOGLE SHEETS LOG
# ============================================================
def log_signal_to_sheets(signal, capital, leverage, risk_pct):
    """Log signal lên Google Sheets."""
    url = Config.GOOGLE_SHEETS_URL
    if not url:
        return

    sym = signal.get('symbol', 'BTCUSDT')
    sym_short = sym.replace('USDT', '')
    margin = capital * risk_pct / 100
    size = margin * leverage

    try:
        payload = {
            'action': 'log_smc',
            'scan_data': {
                'event': f"SC_SIGNAL_{signal['action']}",
                'trade_action': signal['action'],
                'entry': signal['entry'],
                'sl': signal['sl'],
                'tp1': signal['tp1'],
                'tp2': signal['tp2'],
                'tp3': signal['tp3'],
                'score': 0,
                'reasons': f"LIVE Scalp [{signal['src']}] {sym_short} | Bias: {signal.get('htf_bias', 'N/A')}",
                'timeframe': '15m',
                'opened_at': signal['time'],
                'current_price': signal['entry'],
                'pnl': 0,
                'pnl_pct': 0,
                'close_reason': f"{sym_short} | Size: ${size:.0f} | Margin: ${margin:.2f}",
            },
            'scan_time': signal['time'],
        }
        r = requests.post(url, json=payload, timeout=15, headers={'Content-Type': 'application/json'})
        if r.status_code == 200:
            print(f"  ✅ Sheet: {sym_short} signal logged")
        else:
            print(f"  ⚠️ Sheet error: HTTP {r.status_code}")
    except Exception as e:
        print(f"  ⚠️ Sheet error: {e}")


# ============================================================
# POSITION TRACKER (Paper Trading) + PERSISTENT STATE
# ============================================================
DATA_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'data')
STATE_FILE = os.path.join(DATA_DIR, 'scalp_state.json')
JOURNAL_FILE = os.path.join(DATA_DIR, 'scalp_trades.json')
os.makedirs(DATA_DIR, exist_ok=True)


class PaperPosition:
    """Track virtual positions for paper trading (multi-symbol, shared balance)."""
    def __init__(self):
        self.positions = {}     # {symbol: position_dict}
        self.trades = []
        self.balance = 0
        self.initial_capital = 0
        self._last_save_time = 0

    @property
    def position(self):
        """Backward compat: return first active position or None."""
        for pos in self.positions.values():
            if pos:
                return pos
        return None

    def get_position(self, symbol):
        """Get position for specific symbol."""
        return self.positions.get(symbol)

    def open(self, signal, capital, leverage, risk_pct, symbol='BTCUSDT'):
        margin, size = calc_position_size(self.balance, risk_pct, leverage, signal['entry'], signal['sl'])
        self.positions[symbol] = {
            'action': signal['action'],
            'entry': signal['entry'],
            'sl': signal['sl'],
            'tp1': signal['tp1'],
            'tp2': signal['tp2'],
            'tp3': signal['tp3'],
            'size': size,
            'margin': margin,
            'src': signal['src'],
            'open_time': signal['time'],
            'atr': signal['atr'],
            'bars': 0,
            'be_moved': False,
            'symbol': symbol,
        }
        sym_short = symbol.replace('USDT', '')
        print(f"  📋 Paper {sym_short} position opened: {signal['action']} @ ${signal['entry']:,.2f}")
        self.save_state()

    def close(self, reason, current_price, symbol='BTCUSDT'):
        pos = self.positions.get(symbol)
        if not pos:
            return
        action = pos['action']
        entry = pos['entry']

        if action == 'LONG':
            pnl_pct = (current_price - entry) / entry
        else:
            pnl_pct = (entry - current_price) / entry
        pnl_dollar = pnl_pct * pos['size']

        fee = pos['size'] * TAKER_FEE * 2
        pnl_dollar -= fee

        self.balance += pnl_dollar
        win = '✅' if pnl_dollar > 0 else '❌'

        trade = {
            'action': action,
            'entry': entry,
            'close_price': current_price,
            'pnl_dollar': round(pnl_dollar, 2),
            'pnl_pct': round(pnl_pct * 100, 2),
            'reason': reason,
            'src': pos['src'],
            'open_time': pos['open_time'],
            'close_time': datetime.now().strftime('%Y-%m-%d %H:%M'),
            'balance_after': round(self.balance, 2),
            'symbol': symbol,
        }
        self.trades.append(trade)
        self.positions[symbol] = None

        sym_short = symbol.replace('USDT', '')
        print(f"  {win} Paper {sym_short} close: {reason} | PnL: ${pnl_dollar:.2f} ({pnl_pct*100:.2f}%)")

        self.save_state()
        self._append_journal(trade)
        return trade

    # --- Persistent State ---
    def save_state(self):
        """Save balance, positions, trades to JSON."""
        try:
            state = {
                'balance': round(self.balance, 2),
                'initial_capital': self.initial_capital,
                'positions': self.positions,
                'trades': self.trades[-50:],
                'last_updated': datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
            }
            with open(STATE_FILE, 'w', encoding='utf-8') as f:
                json.dump(state, f, ensure_ascii=False, indent=2)
            self._last_save_time = time.time()
        except Exception as e:
            print(f"  ⚠️ Save state error: {e}")

    def load_state(self):
        """Load state from JSON. Returns True if loaded."""
        try:
            if not os.path.exists(STATE_FILE):
                return False
            with open(STATE_FILE, 'r', encoding='utf-8') as f:
                state = json.load(f)
            self.balance = state.get('balance', self.balance)
            self.initial_capital = state.get('initial_capital', self.initial_capital)
            # Support both old (single position) and new (multi-position) format
            if 'positions' in state:
                self.positions = state['positions']
            elif 'position' in state and state['position']:
                sym = state['position'].get('symbol', 'BTCUSDT')
                self.positions = {sym: state['position']}
            self.trades = state.get('trades', [])
            last = state.get('last_updated', 'N/A')
            print(f"  📥 State loaded: Balance ${self.balance:,.2f} | Trades: {len(self.trades)} | Last: {last}")
            for sym, pos in self.positions.items():
                if pos:
                    sym_short = sym.replace('USDT', '')
                    print(f"  📋 Open {sym_short} position: {pos['action']} @ ${pos['entry']:,.2f}")
            return True
        except Exception as e:
            print(f"  ⚠️ Load state error: {e}")
            return False

    def auto_save(self):
        """Auto save mỗi 5 phút."""
        if time.time() - self._last_save_time > 300:
            self.save_state()

    def sync_balance(self, testnet):
        """Sync paper balance với Testnet balance thật."""
        if not testnet:
            return
        try:
            bal = testnet.get_balance()
            if bal and bal['balance'] > 0:
                old = self.balance
                self.balance = bal['balance']
                diff = self.balance - old
                if abs(diff) > 0.01:
                    print(f"  🔄 Balance synced: ${old:,.2f} → ${self.balance:,.2f} (Δ${diff:+,.2f})")
        except Exception as e:
            print(f"  ⚠️ Balance sync error: {e}")

    def _append_journal(self, trade):
        """Append trade to journal file (không mất khi restart)."""
        try:
            journal = []
            if os.path.exists(JOURNAL_FILE):
                with open(JOURNAL_FILE, 'r', encoding='utf-8') as f:
                    journal = json.load(f)
            journal.append(trade)
            with open(JOURNAL_FILE, 'w', encoding='utf-8') as f:
                json.dump(journal, f, ensure_ascii=False, indent=2)
        except Exception as e:
            print(f"  ⚠️ Journal error: {e}")

    def get_today_trades(self):
        """Lấy trades của ngày hôm nay (VN time)."""
        today = (datetime.now() + pd.Timedelta(hours=0)).strftime('%Y-%m-%d')  # Local time
        return [t for t in self.trades if t.get('close_time', '').startswith(today)]


# ============================================================
# DAILY SUMMARY (gửi Telegram lúc 0:00 VN)
# ============================================================
def send_daily_summary(paper, capital):
    """Gửi tổng kết cuối ngày lên Telegram."""
    today_trades = paper.get_today_trades()
    total = len(today_trades)
    wins = [t for t in today_trades if t['pnl_dollar'] > 0]
    losses = [t for t in today_trades if t['pnl_dollar'] <= 0]
    day_pnl = sum(t['pnl_dollar'] for t in today_trades)
    total_pnl = paper.balance - paper.initial_capital
    wr = len(wins) / total * 100 if total > 0 else 0

    pos_str = 'Không' if not paper.position else f"{paper.position['action']} @ ${paper.position['entry']:,.2f}"

    msg = f"""\U0001f4ca *DAILY SUMMARY*

\U0001f4c5 {esc(datetime.now().strftime('%d/%m/%Y'))}

\U0001f4b0 Balance: ${esc(f'{paper.balance:,.2f}')}
Total PnL: {esc(f'${total_pnl:+,.2f}')} \\({esc(f'{total_pnl/paper.initial_capital*100:+.1f}%')}\\)

\U0001f4ca *HÔM NAY:*
├ Lệnh: {esc(str(total))}
├ Win: {esc(str(len(wins)))} \\| Loss: {esc(str(len(losses)))}
├ Winrate: {esc(f'{wr:.0f}%')}
└ PnL ngày: {esc(f'${day_pnl:+,.2f}')}

\U0001f4cb Position: {esc(pos_str)}

_Auto report \\- 0:00 VN_"""

    try:
        send_telegram_message(msg, parse_mode='MarkdownV2')
        print(f"  📊 Daily summary sent!")
    except Exception as e:
        print(f"  ⚠️ Daily summary error: {e}")


# ============================================================
# REAL-TIME PRICE FETCHER (REST fallback)
# ============================================================
def get_realtime_price(symbol='BTCUSDT'):
    """Get current price via Binance ticker (fallback khi WS mất kết nối)."""
    try:
        url = f'https://fapi.binance.com/fapi/v1/ticker/price?symbol={symbol}'
        r = requests.get(url, timeout=5)
        return float(r.json()['price'])
    except:
        return None


# ============================================================
# MAIN LOOP (Real-time: check giá mỗi 10s)
# ============================================================
def run_live_scalp(capital=350.0, risk_pct=5.0, leverage=50, use_testnet=False, symbols=None):
    global SYMBOLS
    if symbols:
        SYMBOLS = [s.strip().upper() for s in symbols.split(',')]
        # Thêm USDT nếu chưa có
        SYMBOLS = [s if s.endswith('USDT') else s + 'USDT' for s in SYMBOLS]
    """Main loop: real-time price monitoring + signal scan on new candle."""
    # Testnet init
    testnet = None
    if use_testnet or Config.DEMO_MODE:
        try:
            from src.testnet_trader import BinanceTestnet
            testnet = BinanceTestnet()
            for sym in SYMBOLS:
                testnet.set_leverage(leverage, symbol=sym)
            bal = testnet.get_balance()
            if bal:
                print(f"  💰 Testnet balance: ${bal['balance']:,.2f} USDT")
        except Exception as e:
            print(f"  ⚠️ Testnet init failed: {e}")
            testnet = None

    # WebSocket init (multi-symbol)
    ws_client = None
    try:
        from src.ws_client import BinanceWS
        ws_symbols = [s.lower() for s in SYMBOLS]
        ws_client = BinanceWS(symbols=ws_symbols, testnet=Config.DEMO_MODE)
        ws_client.start()
        # Init DataFrame cache (one-time REST fetch per symbol)
        ws_client.init_cache(fetch_fn=fetch_with_retry)
    except Exception as e:
        print(f"  ⚠️ WebSocket init failed: {e} — using REST fallback")
        ws_client = None

    price_source = 'WS' if (ws_client and ws_client.is_connected()) else 'REST'
    mode_str = 'DEMO (Testnet)' if testnet else 'PAPER TRADING'
    sym_str = ', '.join(s.replace('USDT', '') for s in SYMBOLS)
    print(f"\n{'═' * 60}")
    print(f"  ⚡ LIVE SCALP SIGNAL BOT — {sym_str} 15M")
    print(f"  Vốn: ${capital:,.2f} | Risk: {risk_pct}% | Leverage: {leverage}x")
    print(f"  Mode: {mode_str}")
    print(f"  📡 Price: {price_source} | Signal scan: khi nến 15m đóng")
    print(f"{'═' * 60}\n")

    paper = PaperPosition()
    paper.initial_capital = capital
    paper.balance = capital

    # Load state từ lần chạy trước
    if paper.load_state():
        print(f"  ✅ Resumed from saved state")
    else:
        print(f"  🆕 Fresh start: ${capital:,.2f}")

    last_signal_time = None
    last_candle_times = {}   # {symbol: last_candle_time} for multi-symbol
    tick_count = 0
    cooldown_until = 0
    last_daily_summary = datetime.now().strftime('%Y-%m-%d')

    # Send startup message
    startup_msg = f"""🤖 *SCALP BOT STARTED*

⚡ Mode: Paper Trading \\(Real\\-time\\)
💰 Vốn: ${esc(f"{capital:,.0f}")}
⚙️ Risk: {esc(f"{risk_pct}")}% \\| Leverage: {esc(str(leverage))}x
📊 Symbols: {esc(sym_str)} \\| TF: 15M
🕐 {esc(datetime.now().strftime("%d/%m/%Y %H:%M"))}"""

    try:
        send_telegram_message(startup_msg, parse_mode='MarkdownV2')
    except:
        pass

    while True:
        try:
            tick_count += 1
            now = datetime.now()

            # ===== REAL-TIME PRICES (WS → REST fallback) =====
            prices = {}
            for sym in SYMBOLS:
                if ws_client and ws_client.is_connected():
                    prices[sym] = ws_client.get_price(sym.lower())
                else:
                    prices[sym] = None
                # REST fallback for ANY symbol missing price
                if not prices[sym]:
                    prices[sym] = get_realtime_price(sym)
            if not any(prices.values()):
                time.sleep(1)
                continue

            # Show status every 30 ticks (~5 min)
            if tick_count % 30 == 1:
                price_str = ' | '.join(f"{s.replace('USDT','')}:${p:,.2f}" for s, p in prices.items() if p)
                print(f"\n[Tick #{tick_count}] {now.strftime('%H:%M:%S')} | {price_str} | Bal: ${paper.balance:,.2f}")

            # ===== PER-SYMBOL: SL CHECK (realtime, mỗi tick) =====
            for sym in SYMBOLS:
                cp = prices.get(sym)
                if not cp:
                    continue
                pos = paper.get_position(sym)
                if not pos:
                    continue
                sym_short = sym.replace('USDT', '')
                action = pos['action']
                entry = pos['entry']
                pnl_pct = (cp - entry) / entry if action == 'LONG' else (entry - cp) / entry
                unrealized = pnl_pct * pos['size']

                sl_hit, sl_pnl, sl_reason = check_sl_intrabar(pos, cp)
                if sl_hit:
                    trade = paper.close(sl_reason, pos['sl'], symbol=sym)
                    if trade:
                        cooldown_until = 0
                        # Close testnet position
                        if testnet:
                            try:
                                testnet.cancel_all_orders(sym)
                                testnet.market_close(sym)
                                print(f"  📡 Testnet: closed {sym_short} (SL)")
                            except Exception as e:
                                print(f"  ⚠️ Testnet close error: {e}")
                        win_emoji = '✅' if trade['pnl_dollar'] > 0 else '❌'
                        try:
                            close_msg = f"""{win_emoji} *SCALP CLOSED* \\({esc(sym_short)}\\)
{esc(trade['action'])} @ ${esc(f"{trade['entry']:,.2f}")}
Close: ${esc(f"{pos['sl']:,.2f}")} \\| Reason: {esc(sl_reason)}
PnL: {esc(f"${trade['pnl_dollar']:+,.2f}")} \\| Bal: ${esc(f"{paper.balance:,.2f}")}"""
                            send_telegram_message(close_msg, parse_mode='MarkdownV2')
                        except Exception as e:
                            print(f"  ⚠️ Telegram close alert error: {e}")
                elif tick_count % 30 == 1:
                    # Hiển thị PnL thật từ Testnet nếu có
                    real_pnl_str = ''
                    if testnet:
                        try:
                            t_pos = testnet.get_position(sym)
                            if t_pos:
                                entry = t_pos['entry_price']
                                unrealized = t_pos['unrealized_pnl']
                                real_pnl_str = f' (Testnet: entry ${entry:,.2f} | uPnL ${unrealized:+,.2f})'
                        except:
                            pass
                    print(f"  📋 {sym_short} {action} | Entry: ${pos['entry']:,.2f} | Now: ${cp:,.2f} | PnL: ${unrealized:+,.2f}{real_pnl_str}")

            # ===== CANDLE CLOSE DETECTION (15m only) =====
            ws_15m_closed = ws_client.get_kline_15m_closed() if ws_client else False

            if ws_15m_closed:
                should_check = True
            elif not (ws_client and ws_client.is_connected()):
                minute = now.minute
                should_check = ((minute % 15) >= 13 or (minute % 15) <= 1) and (tick_count % 6 == 0)
            else:
                should_check = False

            if should_check:
              for sym in SYMBOLS:
                sym_short = sym.replace('USDT', '')
                cp = prices.get(sym)
                if not cp:
                    continue

                use_cache = ws_client and ws_client.is_cache_ready()
                if use_cache:
                    df = ws_client.get_cached_df(sym.lower(), '15m')
                else:
                    df = fetch_with_retry('15m', 200, symbol=sym)
                if df is None or len(df) <= 50:
                    continue

                candle_time = str(df.index[-1])
                bar_price = float(df['close'].iloc[-1])

                # ===== EXIT CHECK (per symbol) =====
                pos = paper.get_position(sym)
                if pos:
                    if candle_time != pos.get('last_bar_time', ''):
                        pos['last_bar_time'] = candle_time
                        pos['bars'] += 1
                        pos['bars_held'] = pos['bars']
                    pos['bars_held'] = pos.get('bars', 0)
                    old_sl = pos['sl']
                    hit, pnl, reason = check_exit(pos, bar_price, leverage)
                    if hit:
                        close_p = pos['sl'] if ('🛑 SL' in reason or 'Trail TP1' in reason) else bar_price
                        trade = paper.close(reason, close_p, symbol=sym)
                        if trade:
                            cooldown_until = 0
                            # Close testnet position
                            if testnet:
                                try:
                                    testnet.cancel_all_orders(sym)
                                    testnet.market_close(sym)
                                    print(f"  📡 Testnet: closed {sym_short} ({reason})")
                                except Exception as e:
                                    print(f"  ⚠️ Testnet close error: {e}")
                            win_emoji = '✅' if trade['pnl_dollar'] > 0 else '❌'
                            try:
                                close_msg = f"""{win_emoji} *SCALP CLOSED* \\({esc(sym_short)}\\)
{esc(trade['action'])} @ ${esc(f"{trade['entry']:,.2f}")}
Close: ${esc(f"{close_p:,.2f}")} \\| Reason: {esc(reason)}
PnL: {esc(f"${trade['pnl_dollar']:+,.2f}")} \\| Bal: ${esc(f"{paper.balance:,.2f}")}"""
                                send_telegram_message(close_msg, parse_mode='MarkdownV2')
                            except Exception as e:
                                print(f"  ⚠️ Telegram exit alert error: {e}")
                    elif testnet and abs(pos['sl'] - old_sl) > 0.01:
                        # SL đã thay đổi (BE hoặc trailing) → update trên Testnet
                        sl_type = '🔒 BE' if abs(pos['sl'] - pos['entry']) < 0.01 else '📈 Trailing'
                        try:
                            testnet.update_sl(pos['action'], pos['sl'], symbol=sym)
                            print(f"  🔄 Testnet SL updated: ${old_sl:,.2f} → ${pos['sl']:,.2f} ({sym_short})")
                            sl_msg = f"""{sl_type} *SL Updated* \\({esc(sym_short)}\\)
{esc(pos['action'])} @ ${esc(f"{pos['entry']:,.2f}")}
SL: ${esc(f"{old_sl:,.2f}")} → ${esc(f"{pos['sl']:,.2f}")}"""
                            send_telegram_message(sl_msg, parse_mode='MarkdownV2')
                        except Exception as e:
                            print(f"  ⚠️ Testnet SL update error: {e}")

                # ===== SIGNAL SCAN (per symbol, 15m close only) =====
                if tick_count > cooldown_until:
                    print(f"\n  🕯️ 15m close [{sym_short}]: {to_vn_time(candle_time)} (VN)")

                    if use_cache:
                        ltf_df = ws_client.get_cached_df(sym.lower(), '5m')
                        htf_df = ws_client.get_cached_df(sym.lower(), '1h')
                    else:
                        with ThreadPoolExecutor(max_workers=2) as ex:
                            f_ltf = ex.submit(fetch_with_retry, '5m', 200, symbol=sym)
                            f_htf = ex.submit(fetch_with_retry, '1h', 200, symbol=sym)
                            ltf_df = f_ltf.result()
                            htf_df = f_htf.result()

                    signals = detect_scalp_signals(df, htf_df, ltf_df, from_cache=use_cache)

                    if signals:
                        sig = signals[0]
                        sig['symbol'] = sym

                        existing = paper.get_position(sym)
                        if existing and existing['action'] != sig['action']:
                            rev_trade = paper.close(f"🔄 Reverse → {sig['action']}", cp, symbol=sym)
                            if rev_trade:
                                # Close testnet position before reverse
                                if testnet:
                                    try:
                                        testnet.cancel_all_orders(sym)
                                        testnet.market_close(sym)
                                        print(f"  📡 Testnet: closed {sym_short} (reverse)")
                                    except Exception as e:
                                        print(f"  ⚠️ Testnet close error: {e}")
                                print(f"  🔄 REVERSE {sym_short}: {rev_trade['action']} → {sig['action']}")
                                try:
                                    rev_msg = f"""🔄 *REVERSE* \\({esc(sym_short)}\\)
{esc(rev_trade['action'])} → {esc(sig['action'])}
PnL: {esc(f"${rev_trade['pnl_dollar']:+,.2f}")} \\| Bal: ${esc(f"{paper.balance:,.2f}")}"""
                                    send_telegram_message(rev_msg, parse_mode='MarkdownV2')
                                except Exception as e:
                                    print(f"  ⚠️ Telegram reverse alert error: {e}")
                        elif existing:
                            continue  # Same direction, skip

                        if paper.get_position(sym) is None:
                            # Xác định entry thật: Testnet fill price > WS price > signal entry
                            real_entry = cp or sig['entry']

                            # 1. Testnet API TRƯỚC — giảm slippage
                            if testnet:
                                try:
                                    t_price = testnet.get_price(sym) or sig['entry']
                                    margin = paper.balance * risk_pct / 100
                                    qty = round(margin * leverage / t_price, 3)
                                    if qty >= 0.001:
                                        order = testnet.market_open(sig['action'], qty, symbol=sym)
                                        if order and 'orderId' in order:
                                            fill_price = float(order.get('avgPrice', 0))
                                            if fill_price > 0:
                                                real_entry = fill_price
                                            testnet.set_sl_tp(sig['action'], sig['sl'], sig['tp1'], symbol=sym)
                                    print(f"  📡 Testnet: {sig['action']} {qty} {sym_short} @ ${real_entry:,.2f}")
                                except Exception as e:
                                    print(f"  ⚠️ Testnet open error: {e}")

                            # Cập nhật signal với entry thật
                            sig['entry_15m'] = sig['entry']
                            sig['entry'] = real_entry

                            print(f"  🔔 SIGNAL: {sym_short} {sig['action']} [{sig['src']}] @ ${real_entry:,.2f}")
                            print(f"     SL: ${sig['sl']:,.2f} | TP1: ${sig['tp1']:,.2f}")

                            # 2. Paper position + notifications SAU
                            paper.open(sig, paper.balance, leverage, risk_pct, symbol=sym)
                            send_scalp_alert(sig, paper.balance, leverage, risk_pct, bool(testnet))
                            log_signal_to_sheets(sig, paper.balance, leverage, risk_pct)
                    else:
                        if paper.get_position(sym) is None:
                            print(f"  😴 No signal [{sym_short}]")

            # Auto save + sync balance mỗi 5 phút
            paper.auto_save()
            if testnet and tick_count % 300 == 0:
                paper.sync_balance(testnet)

            # Daily summary lúc 0:00 VN (17:00 UTC)
            today_str = datetime.now().strftime('%Y-%m-%d')
            if today_str != last_daily_summary and datetime.now().hour >= 0:
                last_daily_summary = today_str
                send_daily_summary(paper, capital)

            # Sleep — WS: 1s (real-time), REST fallback: 5s
            sleep_time = 1 if (ws_client and ws_client.is_connected()) else 5
            time.sleep(sleep_time)

        except KeyboardInterrupt:
            print(f"\n\n{'═' * 60}")
            print(f"  🛑 BOT STOPPED")
            print(f"  Balance: ${paper.balance:,.2f}")
            print(f"  PnL: ${paper.balance - capital:+,.2f}")
            wins = sum(1 for t in paper.trades if t['pnl_dollar'] > 0)
            print(f"  Trades: {len(paper.trades)} (W:{wins} L:{len(paper.trades)-wins})")
            print(f"{'═' * 60}")

            # Stop WebSocket
            if ws_client:
                ws_client.stop()

            # Save state trước khi thoát
            paper.save_state()
            print(f"  💾 State saved to {STATE_FILE}")

            stop_msg = f"""🛑 *SCALP BOT STOPPED*

💰 Balance: ${esc(f"{paper.balance:,.2f}")}
📊 PnL: {esc(f"${paper.balance - capital:+,.2f}")}
📋 Trades: {esc(str(len(paper.trades)))}"""
            try:
                send_telegram_message(stop_msg, parse_mode='MarkdownV2')
            except:
                pass
            break

        except Exception as e:
            print(f"  ❌ Error: {e}")
            time.sleep(10)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description='Live Scalp Signal Bot')
    parser.add_argument('--capital', type=float, default=350.0, help='Vốn ($)')
    parser.add_argument('--risk', type=float, default=5.0, help='Risk pct per trade')
    parser.add_argument('--leverage', type=int, default=50, help='Đòn bẩy')
    parser.add_argument('--testnet', action='store_true', help='Enable Binance Testnet trading')
    parser.add_argument('--symbols', type=str, default=None, help='Trading symbols (comma-separated, e.g. BTCUSDT,ETHUSDT)')
    args = parser.parse_args()

    run_live_scalp(capital=args.capital, risk_pct=args.risk, leverage=args.leverage, use_testnet=args.testnet, symbols=args.symbols)
