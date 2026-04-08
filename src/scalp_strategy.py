"""
Scalp Strategy — Shared Trading Logic
======================================
Module dùng chung cho cả backtest_scalp.py và live_scalp.py.
Chỉ cần update 1 chỗ khi nâng cấp strategy.

Bao gồm:
  1. Constants
  2. Kill Zone
  3. Data Preprocessing (ATR, swing, OB, FVG)
  4. HTF Bias (1H ICT + EMA)
  5. Signal Detection (ICT Sweep + bias filter)
  6. 5m Entry Scan
  7. SL Calculation (1H sweep + ATR fallback)
  8. TP Calculation (OB → FVG → Swing → ATR)
  9. Position Sizing
  10. Exit Management (Quick TP, BE, SL, TP1 trail, Time exit)
  11. Reverse Trade
"""
import numpy as np
import pandas as pd

from src.ict_core import (
    calc_atr,
    detect_swing_points,
    detect_order_blocks,
    detect_fvg,
    detect_liquidity_sweep,
)


# ============================================================
# 1. CONSTANTS
# ============================================================
TAKER_FEE = 0.0005       # Phí Binance Futures: 0.05% mỗi chiều
QUICK_TP_PCT = 0.30      # Chốt lời khi profit ≥ 30% margin
BE_ATR_MULT = 0.5        # Dời SL về entry khi lãi > 0.5×ATR
MAX_HOLD_BARS = 6        # Tối đa giữ lệnh (bars)
BUFFER_PCT = 0.0005      # Buffer cho SL (0.05%)
TRAILING_ATR = 0.5       # Trailing stop distance sau TP1
MIN_RR = 1.0             # TP1 phải >= MIN_RR × SL distance (R:R filter)
ATR_SL_MULT = 1.2        # ATR multiplier cho SL fallback
VOL_MULT = 1.5           # Volume phải >= VOL_MULT × SMA(20) để confirm


# ============================================================
# 2. KILL ZONE
# ============================================================
def is_kill_zone(timestamp):
    """
    Check if timestamp is in Kill Zone (giờ VN = UTC+7).
    London: 14:00-17:00 VN (07:00-10:00 UTC)
    NY:     20:00-23:00 VN (13:00-16:00 UTC)
    """
    hour = timestamp.hour  # UTC hour from Binance
    if 7 <= hour < 10:
        return 'LONDON'
    if 13 <= hour < 16:
        return 'NY'
    if 12 <= hour < 14:
        return 'OVERLAP'
    return None


# ============================================================
# 3. DATA PREPROCESSING
# ============================================================
def prepare_market_data(df, htf_df=None, m30_df=None):
    """
    Pre-compute all ICT structures from 15m + 1H data.
    Returns a MarketContext dict.
    """
    o = df['open'].values
    h = df['high'].values
    l = df['low'].values
    c = df['close'].values
    atr_series = calc_atr(df, 14)
    atr_vals = atr_series.values if hasattr(atr_series, 'values') else atr_series

    # 15m structures
    m15_swing_h, m15_swing_l = detect_swing_points(h, l, lookback=5)
    m15_bull_obs, m15_bear_obs = detect_order_blocks(o, h, l, c, atr_vals)
    m15_bull_fvgs, m15_bear_fvgs = detect_fvg(h, l, c, atr_vals)

    # 1H structures
    h1_swing_h, h1_swing_l = [], []
    h1_bull_obs, h1_bear_obs = [], []
    h1_bull_fvgs, h1_bear_fvgs = [], []
    h1_h, h1_l = None, None
    h1_atr_vals = None
    if htf_df is not None and len(htf_df) > 50:
        h1_o = htf_df['open'].values
        h1_h = htf_df['high'].values
        h1_l = htf_df['low'].values
        h1_c = htf_df['close'].values
        h1_atr_s = calc_atr(htf_df, 14)
        h1_atr_vals = h1_atr_s.values if hasattr(h1_atr_s, 'values') else h1_atr_s
        h1_swing_h, h1_swing_l = detect_swing_points(h1_h, h1_l, lookback=5)
        h1_bull_obs, h1_bear_obs = detect_order_blocks(h1_o, h1_h, h1_l, h1_c, h1_atr_vals)
        h1_bull_fvgs, h1_bear_fvgs = detect_fvg(h1_h, h1_l, h1_c, h1_atr_vals)

    result = {
        'df': df,
        'htf_df': htf_df,
        'o': o, 'h': h, 'l': l, 'c': c,
        'atr_series': atr_series,
        'atr_vals': atr_vals,
        'm15_swing_h': m15_swing_h,
        'm15_swing_l': m15_swing_l,
        'm15_bull_obs': m15_bull_obs,
        'm15_bear_obs': m15_bear_obs,
        'm15_bull_fvgs': m15_bull_fvgs,
        'm15_bear_fvgs': m15_bear_fvgs,
        'h1_swing_h': h1_swing_h,
        'h1_swing_l': h1_swing_l,
        'h1_bull_obs': h1_bull_obs,
        'h1_bear_obs': h1_bear_obs,
        'h1_bull_fvgs': h1_bull_fvgs,
        'h1_bear_fvgs': h1_bear_fvgs,
        'h1_h': h1_h,
        'h1_l': h1_l,
    }

    # 30m structures (for TP)
    m30_bull_obs, m30_bear_obs = [], []
    m30_bull_fvgs, m30_bear_fvgs = [], []
    m30_swing_h, m30_swing_l = [], []
    if m30_df is not None and len(m30_df) > 50:
        m30_o = m30_df['open'].values
        m30_h = m30_df['high'].values
        m30_l = m30_df['low'].values
        m30_c = m30_df['close'].values
        m30_atr_s = calc_atr(m30_df, 14)
        m30_atr_vals = m30_atr_s.values if hasattr(m30_atr_s, 'values') else m30_atr_s
        m30_swing_h, m30_swing_l = detect_swing_points(m30_h, m30_l, lookback=5)
        m30_bull_obs, m30_bear_obs = detect_order_blocks(m30_o, m30_h, m30_l, m30_c, m30_atr_vals)
        m30_bull_fvgs, m30_bear_fvgs = detect_fvg(m30_h, m30_l, m30_c, m30_atr_vals)

    result['m30_bull_obs'] = m30_bull_obs
    result['m30_bear_obs'] = m30_bear_obs
    result['m30_bull_fvgs'] = m30_bull_fvgs
    result['m30_bear_fvgs'] = m30_bear_fvgs
    result['m30_swing_h'] = m30_swing_h
    result['m30_swing_l'] = m30_swing_l

    return result


# ============================================================
# 4. HTF BIAS (1H ICT Structure + EMA)
# ============================================================
def get_htf_bias(htf_df, h1_swing_h, h1_swing_l, timestamp=None):
    """
    1H Bias: ICT structure (HH/HL/LH/LL) + EMA 20/50.
    Both must agree. Disagree = None (blocked).

    Args:
        timestamp: if provided, filter data up to this timestamp (for backtest)
                   if None, use all data (for live)
    """
    if htf_df is None or len(htf_df) < 50:
        return None

    htf_c = htf_df['close'].values

    if timestamp is not None:
        # Backtest mode: filter data up to timestamp
        mask = htf_df.index <= timestamp
        if mask.sum() < 20:
            return None
        h1_idx = mask.sum() - 1

        # EMA bias (rolling)
        e20 = pd.Series(htf_c[:h1_idx+1]).ewm(span=20).mean().iloc[-1]
        e50 = pd.Series(htf_c[:h1_idx+1]).ewm(span=50).mean().iloc[-1]
        ema_bias = 'LONG' if e20 > e50 else 'SHORT'

        # ICT structure bias (filter by bar index)
        recent_sh = [s for s in h1_swing_h if s['bar'] <= h1_idx]
        recent_sl = [s for s in h1_swing_l if s['bar'] <= h1_idx]
    else:
        # Live mode: use all data
        htf_ema20 = pd.Series(htf_c).ewm(span=20).mean().values
        htf_ema50 = pd.Series(htf_c).ewm(span=50).mean().values
        ema_bias = 'LONG' if htf_ema20[-1] > htf_ema50[-1] else 'SHORT'

        recent_sh = h1_swing_h
        recent_sl = h1_swing_l

    # ICT structure: expanded patterns
    # Equal = within 0.15% tolerance (consolidation / accumulation zone)
    ict_bias = None
    if len(recent_sh) >= 2 and len(recent_sl) >= 2:
        sh1, sh2 = recent_sh[-2], recent_sh[-1]
        sl1, sl2 = recent_sl[-2], recent_sl[-1]
        eq_tol = 0.0015  # 0.15% tolerance for Equal High/Low

        hh = sh2['price'] > sh1['price']
        hl = sl2['price'] > sl1['price']
        lh = sh2['price'] < sh1['price']
        ll = sl2['price'] < sl1['price']
        eh = abs(sh2['price'] - sh1['price']) / sh1['price'] < eq_tol  # Equal High
        el = abs(sl2['price'] - sl1['price']) / sl1['price'] < eq_tol  # Equal Low

        # Bullish patterns (đáy nâng hoặc đỉnh phá)
        if hh and hl:           # ⭐⭐⭐ Classic uptrend
            ict_bias = 'LONG'
        elif hh and el:         # ⭐⭐ Breakout + accumulation
            ict_bias = 'LONG'
        elif eh and hl:         # ⭐⭐ Đáy nâng, sắp breakout
            ict_bias = 'LONG'

        # Bearish patterns (đỉnh hạ hoặc đáy phá)
        elif lh and ll:         # ⭐⭐⭐ Classic downtrend
            ict_bias = 'SHORT'
        elif lh and el:         # ⭐⭐ Breakdown + distribution
            ict_bias = 'SHORT'
        elif eh and ll:         # ⭐⭐ Đỉnh giữ, đáy phá
            ict_bias = 'SHORT'

    # Both must agree
    if ict_bias and ict_bias == ema_bias:
        return ict_bias      # Strong confirmation
    elif ict_bias is None:
        return ema_bias      # No ICT data → fallback EMA only
    else:
        return None          # Disagree → block signal


# ============================================================
# 5. SIGNAL DETECTION (ICT Liquidity Sweep)
# ============================================================
def detect_signal(ctx, i, htf_bias):
    """
    No-sweep signal: candle direction + 1H bias filter.

    Returns:
        (action, None, None) or (None, None, None)
    """
    if htf_bias is None:
        return None, None, None

    c = ctx['c']
    o = ctx['o']
    if i < 2:
        return None, None, None

    # Direction from 15m candle
    if c[i] > o[i] and htf_bias == 'LONG':
        return 'LONG', None, None
    elif c[i] < o[i] and htf_bias == 'SHORT':
        return 'SHORT', None, None

    return None, None, None


# ============================================================
# 6. 5m ENTRY SCAN
# ============================================================
def scan_5m_entry(action, price, ltf_df, bar_start, bar_end):
    """
    Tìm nến 5m thuận chiều trong 15m bar → entry tốt hơn.

    Returns:
        (entry_price, has_5m_entry)
    """
    entry = price
    has_5m = False

    if ltf_df is not None:
        ltf_window = ltf_df[(ltf_df.index > bar_start) & (ltf_df.index <= bar_end)]
        if len(ltf_window) >= 2:
            ltf_c = ltf_window['close'].values
            ltf_o = ltf_window['open'].values
            for j in range(len(ltf_window)):
                if action == 'LONG' and ltf_c[j] > ltf_o[j]:
                    entry = ltf_c[j]
                    has_5m = True
                    break
                elif action == 'SHORT' and ltf_c[j] < ltf_o[j]:
                    entry = ltf_c[j]
                    has_5m = True
                    break

    return entry, has_5m


# ============================================================
# 6b. 5m BOS (Break of Structure) CONFIRMATION
# ============================================================
def check_5m_bos(action, ltf_df, bar_start, bar_end):
    """
    Check if 5m shows Break of Structure confirming sweep direction.

    LONG:  5m phải break trên swing high gần nhất (đảo chiều lên)
    SHORT: 5m phải break dưới swing low gần nhất (đảo chiều xuống)

    Returns:
        True if BOS confirmed, False otherwise
    """
    if ltf_df is None:
        return False

    # Lấy 5m data: mở rộng window để có swing context
    # Lấy 6 bars trước bar_start + 3 bars trong bar hiện tại
    extended_start = bar_start - pd.Timedelta(minutes=30)  # 6 bars × 5m
    ltf_window = ltf_df[(ltf_df.index > extended_start) & (ltf_df.index <= bar_end)]
    if len(ltf_window) < 4:
        return False

    ltf_h = ltf_window['high'].values
    ltf_l = ltf_window['low'].values
    ltf_c = ltf_window['close'].values

    # Tìm swing high/low đơn giản trên 5m (lookback 2)
    swing_highs = []
    swing_lows = []
    for j in range(2, len(ltf_window) - 1):
        if ltf_h[j] >= ltf_h[j-1] and ltf_h[j] >= ltf_h[j-2] and ltf_h[j] >= ltf_h[j+1] if j+1 < len(ltf_h) else True:
            swing_highs.append(ltf_h[j])
        if ltf_l[j] <= ltf_l[j-1] and ltf_l[j] <= ltf_l[j-2] and ltf_l[j] <= ltf_l[j+1] if j+1 < len(ltf_l) else True:
            swing_lows.append(ltf_l[j])

    if not swing_highs and not swing_lows:
        return False

    # Check BOS trên nến 5m trong 15m bar hiện tại
    current_bars = ltf_df[(ltf_df.index > bar_start) & (ltf_df.index <= bar_end)]
    if len(current_bars) == 0:
        return False

    if action == 'LONG' and swing_highs:
        recent_sh = swing_highs[-1]
        # Nến 5m close > swing high gần nhất → BOS up
        for j in range(len(current_bars)):
            if current_bars['close'].iloc[j] > recent_sh:
                return True
    elif action == 'SHORT' and swing_lows:
        recent_sl = swing_lows[-1]
        # Nến 5m close < swing low gần nhất → BOS down
        for j in range(len(current_bars)):
            if current_bars['close'].iloc[j] < recent_sl:
                return True

    return False


# ============================================================
# 6c. VOLUME CONFIRMATION
# ============================================================
def check_volume_confirmation(df, i, vol_mult=None):
    """
    Check if sweep candle has high volume (> VOL_MULT × SMA20).

    Returns:
        True if volume confirmed, False otherwise
    """
    if vol_mult is None:
        vol_mult = VOL_MULT

    if 'volume' not in df.columns:
        return True  # No volume data → skip check

    vol = df['volume'].values
    if i < 20:
        return True

    vol_sma = np.mean(vol[i-20:i])
    if vol_sma <= 0:
        return True

    return vol[i] >= vol_sma * vol_mult


# ============================================================
# 7. SL CALCULATION
# ============================================================
def get_sweep_sl(action, timestamp, tf_df, swing_h, swing_l):
    """
    Find nearest sweep candle for SL on any timeframe.
    Works with 1H, 30m, or any timeframe data.
    """
    if tf_df is None or len(tf_df) < 50:
        return None

    tf_h = tf_df['high'].values
    tf_l = tf_df['low'].values
    tf_c = tf_df['close'].values
    tf_o = tf_df['open'].values

    mask = tf_df.index <= timestamp
    if mask.sum() < 5:
        return None
    tf_idx = mask.sum() - 1

    for lookback in range(0, min(10, tf_idx)):
        bi = tf_idx - lookback
        tf_atr = abs(tf_h[bi] - tf_l[bi])
        sweep_t, sweep_p, sweep_b = detect_liquidity_sweep(
            tf_h, tf_l, tf_c, tf_o,
            swing_h, swing_l, bi, tf_atr)
        if sweep_t:
            if action == 'LONG':
                return tf_l[sweep_b]
            else:
                return tf_h[sweep_b]
    return None


# Backward compat alias
def get_1h_sweep_sl(action, timestamp, htf_df, h1_swing_h, h1_swing_l):
    return get_sweep_sl(action, timestamp, htf_df, h1_swing_h, h1_swing_l)


def calc_sl(action, entry, atr, sl_1h, sl_30m=None):
    """
    SL priority: 1H sweep → ATR fallback.
    Returns (sl_price, source_tag)
      source_tag: '1hSL' or '' (ATR fallback)
    """
    if action == 'LONG':
        if sl_1h and 0 < (entry - sl_1h) <= atr * 3.0:
            sl = sl_1h - entry * BUFFER_PCT
            return sl, '1hSL'
        return entry - atr * ATR_SL_MULT, ''
    else:
        if sl_1h and 0 < (sl_1h - entry) <= atr * 3.0:
            sl = sl_1h + entry * BUFFER_PCT
            return sl, '1hSL'
        return entry + atr * ATR_SL_MULT, ''


# ============================================================
# 8. TP CALCULATION (1H OB/FVG → 15m OB/FVG → Swing → ATR)
# ============================================================
def calc_tp(action, entry, atr, bar_idx, ctx):
    """
    TP from ICT structure: 1H OB/FVG → 15m fallback → ATR.
    Returns (tp1, tp2, tp3) sorted by distance from entry.
    """
    tp1, tp2, tp3 = None, None, None
    lookback = 200

    # 1H structures (primary for TP)
    tp_bear_obs = ctx.get('h1_bear_obs', [])
    tp_bull_obs = ctx.get('h1_bull_obs', [])
    tp_bear_fvgs = ctx.get('h1_bear_fvgs', [])
    tp_bull_fvgs = ctx.get('h1_bull_fvgs', [])
    tp_swing_h = ctx.get('h1_swing_h', [])
    tp_swing_l = ctx.get('h1_swing_l', [])

    # 15m structures (fallback)
    m15_bear_obs = ctx['m15_bear_obs']
    m15_bull_obs = ctx['m15_bull_obs']
    m15_bear_fvgs = ctx['m15_bear_fvgs']
    m15_bull_fvgs = ctx['m15_bull_fvgs']
    m15_swing_h = ctx['m15_swing_h']
    m15_swing_l = ctx['m15_swing_l']

    if action == 'LONG':
        # TP1: 1H Bear OB → 15m Bear OB
        for ob in tp_bear_obs:
            if not ob['mitigated'] and ob['low'] > entry and (bar_idx // 4 - ob['bar']) < lookback:
                tp1 = ob['low']
                break
        if tp1 is None:
            for ob in m15_bear_obs:
                if not ob['mitigated'] and ob['low'] > entry and (bar_idx - ob['bar']) < lookback:
                    tp1 = ob['low']
                    break

        # TP2: 1H Bear FVG → 15m Bear FVG
        for fvg in tp_bear_fvgs:
            if not fvg['filled'] and fvg['bottom'] > entry:
                if tp1 is None or abs(fvg['bottom'] - tp1) > atr * 0.3:
                    tp2 = fvg['bottom']
                    break
        if tp2 is None:
            for fvg in m15_bear_fvgs:
                if not fvg['filled'] and fvg['bottom'] > entry and (bar_idx - fvg['bar']) < lookback:
                    if tp1 is None or abs(fvg['bottom'] - tp1) > atr * 0.3:
                        tp2 = fvg['bottom']
                        break

        # TP3: 1H Swing High → 15m Swing High
        for sh in reversed(tp_swing_h):
            if sh['price'] > entry:
                if (tp1 is None or abs(sh['price'] - tp1) > atr * 0.3) and \
                   (tp2 is None or abs(sh['price'] - tp2) > atr * 0.3):
                    tp3 = sh['price']
                    break
        if tp3 is None:
            for sh in reversed(m15_swing_h):
                if sh['bar'] < bar_idx and sh['price'] > entry:
                    if (tp1 is None or abs(sh['price'] - tp1) > atr * 0.3) and \
                       (tp2 is None or abs(sh['price'] - tp2) > atr * 0.3):
                        tp3 = sh['price']
                        break

    else:  # SHORT
        # TP1: 1H Bull OB → 15m Bull OB
        for ob in reversed(tp_bull_obs):
            if not ob['mitigated'] and ob['high'] < entry:
                tp1 = ob['high']
                break
        if tp1 is None:
            for ob in reversed(m15_bull_obs):
                if not ob['mitigated'] and ob['high'] < entry and (bar_idx - ob['bar']) < lookback:
                    tp1 = ob['high']
                    break

        # TP2: 1H Bull FVG → 15m Bull FVG
        for fvg in reversed(tp_bull_fvgs):
            if not fvg['filled'] and fvg['top'] < entry:
                if tp1 is None or abs(fvg['top'] - tp1) > atr * 0.3:
                    tp2 = fvg['top']
                    break
        if tp2 is None:
            for fvg in reversed(m15_bull_fvgs):
                if not fvg['filled'] and fvg['top'] < entry and (bar_idx - fvg['bar']) < lookback:
                    if tp1 is None or abs(fvg['top'] - tp1) > atr * 0.3:
                        tp2 = fvg['top']
                        break

        # TP3: 1H Swing Low → 15m Swing Low
        for sl_pt in reversed(tp_swing_l):
            if sl_pt['price'] < entry:
                if (tp1 is None or abs(sl_pt['price'] - tp1) > atr * 0.3) and \
                   (tp2 is None or abs(sl_pt['price'] - tp2) > atr * 0.3):
                    tp3 = sl_pt['price']
                    break
        if tp3 is None:
            for sl_pt in reversed(m15_swing_l):
                if sl_pt['bar'] < bar_idx and sl_pt['price'] < entry:
                    if (tp1 is None or abs(sl_pt['price'] - tp1) > atr * 0.3) and \
                       (tp2 is None or abs(sl_pt['price'] - tp2) > atr * 0.3):
                        tp3 = sl_pt['price']
                        break

    # Fallback ATR
    mult = 1 if action == 'LONG' else -1
    if tp1 is None:
        tp1 = entry + mult * atr * 1.5
    if tp2 is None:
        tp2 = entry + mult * atr * 2.5
    if tp3 is None:
        tp3 = entry + mult * atr * 3.5

    # Sort by distance from entry (closest first)
    tps = sorted([tp1, tp2, tp3], key=lambda x: abs(x - entry))
    return tps[0], tps[1], tps[2]


# ============================================================
# 9. POSITION SIZING
# ============================================================
def calc_position_size(balance, risk_pct, leverage, entry=None, sl=None):
    """
    Risk-based Position Sizing.
    - Risk Amount = balance × risk_pct%
    - Size = Risk Amount / SL distance (%)
    - Margin = Size / leverage
    - Leverage chỉ dùng để check margin, không dùng để tính size.

    Fallback: nếu không có entry/sl, dùng fixed margin (backward compat).
    Returns (margin, size)
    """
    risk_amount = balance * risk_pct / 100  # Số tiền chấp nhận mất

    if entry and sl and entry > 0:
        sl_dist_pct = abs(entry - sl) / entry  # SL distance as %
        if sl_dist_pct > 0:
            size = risk_amount / sl_dist_pct
            margin = size / leverage
            # Safety: margin không vượt 5% balance
            max_margin = balance * 0.05
            if margin > max_margin:
                margin = max_margin
                size = margin * leverage
            return round(margin, 2), round(size, 2)

    # Fallback: fixed margin
    margin = risk_amount
    size = margin * leverage
    return round(margin, 2), round(size, 2)


# ============================================================
# 10. EXIT MANAGEMENT
# ============================================================
def check_sl_intrabar(position, price):
    """
    Check SL hit only (for realtime/intrabar checking).
    - Live: called every tick with current_price
    - Backtest: called with low[i] (LONG) or high[i] (SHORT)

    Returns:
        (hit, pnl, reason) or (False, 0, '')
    """
    action = position['action']
    entry = position['entry']

    if action == 'LONG' and price <= position['sl']:
        pnl = position['sl'] - entry
        reason = '📈 Trail TP1' if position.get('tp1_hit') else '🛑 SL'
        return True, pnl, reason
    elif action == 'SHORT' and price >= position['sl']:
        pnl = entry - position['sl']
        reason = '📈 Trail TP1' if position.get('tp1_hit') else '🛑 SL'
        return True, pnl, reason

    return False, 0, ''


def check_exit(position, price, leverage):
    """
    Check NON-SL exit conditions (Quick TP, BE, TP1 trail, Trailing, Time exit).
    SL is checked separately by check_sl_intrabar().

    Returns:
        (hit, pnl_raw, reason)
        Position dict is modified in-place (BE, trailing, tp1_hit).
    """
    action = position['action']
    entry = position['entry']
    atr = position.get('atr', 0)

    # Unrealized PnL
    unrealized = (price - entry) if action == 'LONG' else (entry - price)
    in_profit = unrealized > 0

    hit = False
    pnl = 0
    reason = ''

    # ====== 1. QUICK TP: 30% margin ======
    size = position['size']
    margin = size / leverage if leverage > 0 else size
    target_profit = margin * QUICK_TP_PCT
    pnl_pct_check = unrealized / entry * 100
    pnl_dollar_check = size * pnl_pct_check / 100
    if pnl_dollar_check >= target_profit:
        pnl = unrealized
        hit = True
        reason = f'💰 Quick TP (+{pnl_dollar_check:.1f}$)'
        return hit, pnl, reason

    # ====== 2. AUTO BE: dời SL lên entry khi lãi > 0.5×ATR ======
    if in_profit and unrealized > atr * BE_ATR_MULT and not position.get('be_set'):
        position['sl'] = entry
        position['be_set'] = True

    # ====== 3. TP1 HIT → BE + trailing trigger ======
    if not position.get('tp1_hit'):
        tp1 = position.get('tp1', 0)
        if action == 'LONG' and price >= tp1:
            position['sl'] = entry
            position['be_set'] = True
            position['tp1_hit'] = True
        elif action == 'SHORT' and price <= tp1:
            position['sl'] = entry
            position['be_set'] = True
            position['tp1_hit'] = True

    # ====== 4. TRAILING STOP — chỉ sau TP1 (0.5×ATR) ======
    if in_profit and position.get('tp1_hit'):
        trail_dist = atr * TRAILING_ATR
        if action == 'LONG':
            trail_sl = price - trail_dist
            if trail_sl > position['sl']:
                position['sl'] = trail_sl
        else:
            trail_sl = price + trail_dist
            if trail_sl < position['sl']:
                position['sl'] = trail_sl

    # ====== 5. TIME EXIT ======
    bars_held = position.get('bars_held', 0)
    if bars_held >= MAX_HOLD_BARS:
        pnl = unrealized
        hit = True
        reason = f'⏰ Time Exit ({MAX_HOLD_BARS} bars)'
        return hit, pnl, reason

    return hit, pnl, reason


# ============================================================
# 11. REVERSE TRADE
# ============================================================
def handle_reverse(position, price, balance):
    """
    Close old position when reversing.
    Returns (trade_dict, new_balance) or (None, balance) if no position.
    """
    if position is None:
        return None, balance

    action = position['action']
    entry = position['entry']
    unrealized = (price - entry) if action == 'LONG' else (entry - price)
    pnl_pct = unrealized / entry * 100
    pnl_dollar = position['size'] * pnl_pct / 100

    # Trừ phí sàn
    fee = position['size'] * TAKER_FEE * 2
    pnl_dollar -= fee

    new_balance = balance + pnl_dollar

    trade = {
        **position,
        'close_price': price,
        'pnl': round(unrealized, 2),
        'pnl_pct': round(pnl_pct, 2),
        'pnl_dollar': round(pnl_dollar, 2),
        'balance_after': round(new_balance, 2),
    }

    return trade, new_balance
