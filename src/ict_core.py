"""
ICT/SMC Core Module — Smart Money Concepts
===========================================
Functions:
  - detect_swing_points: Swing High/Low
  - detect_liquidity_sweep: Quét thanh khoản
  - detect_market_structure: MSS/CHoCH
  - detect_order_blocks: Order Blocks
  - detect_fvg: Fair Value Gap
  - get_zone: Discount/Premium
  - is_near_poi: Giá gần POI?
  - generate_ict_signal: All-in-one signal
"""
import numpy as np
import pandas as pd


# ============================================================
# 0. ATR (inlined from smc_detector.py)
# ============================================================
def calc_atr(df, period=50):
    """ATR calculation."""
    h, l, c = df['high'], df['low'], df['close']
    tr = pd.concat([h - l, abs(h - c.shift(1)), abs(l - c.shift(1))], axis=1).max(axis=1)
    return tr.rolling(window=period).mean()


# ============================================================
# 1. SWING POINTS
# ============================================================
def detect_swing_points(h, l, lookback=5):
    """
    Swing High: h[i] cao nhất trong ±lookback bars
    Swing Low:  l[i] thấp nhất trong ±lookback bars
    Returns: (swing_highs, swing_lows) — list of {'price', 'bar'}
    """
    n = len(h)
    swing_highs, swing_lows = [], []
    for i in range(lookback, n - lookback):
        if all(h[i] > h[i - j] and h[i] > h[i + j] for j in range(1, lookback + 1)):
            swing_highs.append({'price': h[i], 'bar': i})
        if all(l[i] < l[i - j] and l[i] < l[i + j] for j in range(1, lookback + 1)):
            swing_lows.append({'price': l[i], 'bar': i})
    return swing_highs, swing_lows


# ============================================================
# 2. LIQUIDITY SWEEP
# ============================================================
def detect_liquidity_sweep(h, l, c, o, swing_highs, swing_lows, i, atr):
    """
    Bullish Sweep: price phá swing low rồi close trên → SM gom hàng
    Bearish Sweep: price phá swing high rồi close dưới → SM phân phối
    Checks 1-bar, 2-bar, and 3-bar patterns.
    Returns: (type, sweep_price, sweep_bar) or (None, None, None)
    """
    if atr <= 0:
        return None, None, None

    recent_sl = [s for s in swing_lows if s['bar'] < i - 1 and i - s['bar'] < 80]
    recent_sh = [s for s in swing_highs if s['bar'] < i - 1 and i - s['bar'] < 80]

    # Bullish Sweep: wick below swing low, close recovers above
    for sl in reversed(recent_sl[-5:]):
        # 1-bar: same candle sweeps and closes above
        if l[i] < sl['price'] and c[i] > sl['price']:
            return 'BULLISH_SWEEP', sl['price'], i
        # 2-bar: previous bar swept, current closes above
        if i >= 1 and l[i-1] < sl['price'] and c[i] > sl['price']:
            return 'BULLISH_SWEEP', sl['price'], i - 1
        # 3-bar: bar before swept, price recovered
        if i >= 2 and l[i-2] < sl['price'] and c[i] > sl['price']:
            return 'BULLISH_SWEEP', sl['price'], i - 2

    # Bearish Sweep: wick above swing high, close drops below
    for sh in reversed(recent_sh[-5:]):
        if h[i] > sh['price'] and c[i] < sh['price']:
            return 'BEARISH_SWEEP', sh['price'], i
        if i >= 1 and h[i-1] > sh['price'] and c[i] < sh['price']:
            return 'BEARISH_SWEEP', sh['price'], i - 1
        if i >= 2 and h[i-2] > sh['price'] and c[i] < sh['price']:
            return 'BEARISH_SWEEP', sh['price'], i - 2

    return None, None, None


# ============================================================
# 3. MARKET STRUCTURE — MSS/CHoCH
# ============================================================
def detect_market_structure(c, swing_highs, swing_lows, i):
    """
    MSS Bullish: giá phá swing high sau khi có lower low
    MSS Bearish: giá phá swing low sau khi có higher high
    Returns: 'BULLISH', 'BEARISH', or None
    """
    price = c[i]
    recent_sh = [s for s in swing_highs if s['bar'] < i][-3:]
    recent_sl = [s for s in swing_lows if s['bar'] < i][-3:]

    if len(recent_sh) < 2 or len(recent_sl) < 2:
        return None

    last_sh, prev_sh = recent_sh[-1]['price'], recent_sh[-2]['price']
    last_sl, prev_sl = recent_sl[-1]['price'], recent_sl[-2]['price']

    if price > last_sh and last_sl < prev_sl:
        return 'BULLISH'
    if price < last_sl and last_sh > prev_sh:
        return 'BEARISH'
    if last_sh > prev_sh and last_sl > prev_sl:
        return 'BULLISH'
    if last_sh < prev_sh and last_sl < prev_sl:
        return 'BEARISH'
    return None


# ============================================================
# 4. ORDER BLOCKS
# ============================================================
def detect_order_blocks(o, h, l, c, atr_values, max_age=200):
    """
    Bullish OB: nến giảm trước đợt tăng mạnh (>1.5×ATR)
    Bearish OB: nến tăng trước đợt giảm mạnh
    """
    n = len(o)
    bull_obs, bear_obs = [], []
    for i in range(2, n - 2):
        atr = atr_values[i] if not np.isnan(atr_values[i]) else 0
        if atr <= 0:
            continue
        if c[i] < o[i]:
            move = max(c[i + k] - c[i] for k in range(1, min(3, n - i)))
            if move > atr * 1.5:
                bull_obs.append({'high': h[i], 'low': l[i], 'mid': (h[i]+l[i])/2, 'bar': i, 'mitigated': False})
        if c[i] > o[i]:
            move = max(c[i] - c[i + k] for k in range(1, min(3, n - i)))
            if move > atr * 1.5:
                bear_obs.append({'high': h[i], 'low': l[i], 'mid': (h[i]+l[i])/2, 'bar': i, 'mitigated': False})

    for ob in bull_obs:
        for j in range(ob['bar']+3, min(ob['bar']+max_age, n)):
            if l[j] <= ob['high']:
                ob['mitigated'] = True
                break
    for ob in bear_obs:
        for j in range(ob['bar']+3, min(ob['bar']+max_age, n)):
            if h[j] >= ob['low']:
                ob['mitigated'] = True
                break
    return bull_obs, bear_obs


# ============================================================
# 5. FAIR VALUE GAP
# ============================================================
def detect_fvg(h, l, c, atr_values, max_age=200):
    """Bullish FVG: candle3.low > candle1.high. Bearish FVG: reverse."""
    n = len(h)
    bull_fvgs, bear_fvgs = [], []
    for i in range(1, n - 1):
        atr = atr_values[i] if not np.isnan(atr_values[i]) else 0
        if atr <= 0:
            continue
        if l[i+1] > h[i-1] and (l[i+1] - h[i-1]) > atr * 0.15:
            fvg = {'top': l[i+1], 'bottom': h[i-1], 'mid': (l[i+1]+h[i-1])/2, 'bar': i, 'filled': False}
            for j in range(i+2, min(i+max_age, n)):
                if l[j] <= fvg['mid']:
                    fvg['filled'] = True
                    break
            bull_fvgs.append(fvg)
        if h[i+1] < l[i-1] and (l[i-1] - h[i+1]) > atr * 0.15:
            fvg = {'top': l[i-1], 'bottom': h[i+1], 'mid': (l[i-1]+h[i+1])/2, 'bar': i, 'filled': False}
            for j in range(i+2, min(i+max_age, n)):
                if h[j] >= fvg['mid']:
                    fvg['filled'] = True
                    break
            bear_fvgs.append(fvg)
    return bull_fvgs, bear_fvgs


# ============================================================
# 6. DISCOUNT / PREMIUM ZONE
# ============================================================
def get_zone(price, swing_highs, swing_lows, i, lookback_bars=100):
    """DISCOUNT (<45%), PREMIUM (>55%), EQUILIBRIUM, or None"""
    recent_sh = [s for s in swing_highs if s['bar'] < i and i - s['bar'] < lookback_bars]
    recent_sl = [s for s in swing_lows if s['bar'] < i and i - s['bar'] < lookback_bars]
    if not recent_sh or not recent_sl:
        return None
    highest = max(s['price'] for s in recent_sh[-5:])
    lowest = min(s['price'] for s in recent_sl[-5:])
    rng = highest - lowest
    if rng <= 0:
        return None
    pos = (price - lowest) / rng
    if pos < 0.45:
        return 'DISCOUNT'
    elif pos > 0.55:
        return 'PREMIUM'
    return 'EQUILIBRIUM'


# ============================================================
# 7. POI PROXIMITY
# ============================================================
def is_near_poi(price, action, bull_obs, bear_obs, bull_fvgs, bear_fvgs, atr, bar_idx, max_age=200):
    """Check if price is near unmitigated OB or unfilled FVG."""
    proximity = atr * 1.5
    if action == 'LONG':
        for ob in reversed(bull_obs):
            if bar_idx - ob['bar'] > max_age: continue
            if not ob['mitigated'] and ob['low'] - proximity <= price <= ob['high'] + proximity:
                return True, 'OB'
        for fvg in reversed(bull_fvgs):
            if bar_idx - fvg['bar'] > max_age: continue
            if not fvg['filled'] and fvg['bottom'] - proximity <= price <= fvg['top'] + proximity:
                return True, 'FVG'
    elif action == 'SHORT':
        for ob in reversed(bear_obs):
            if bar_idx - ob['bar'] > max_age: continue
            if not ob['mitigated'] and ob['low'] - proximity <= price <= ob['high'] + proximity:
                return True, 'OB'
        for fvg in reversed(bear_fvgs):
            if bar_idx - fvg['bar'] > max_age: continue
            if not fvg['filled'] and fvg['bottom'] - proximity <= price <= fvg['top'] + proximity:
                return True, 'FVG'
    return False, None


# ============================================================
# 8. ICT SIGNAL GENERATOR (1H)
# ============================================================
def generate_ict_signal(i, o, h, l, c, atr,
                        swing_highs, swing_lows,
                        bull_obs, bear_obs,
                        bull_fvgs, bear_fvgs,
                        htf_bias=None):
    """
    Generate ICT signal at bar i (1H timeframe).
    Hard filters: Sweep + HTF Bias
    Bonus labels: MSS, POI, Zone
    Returns dict or None.
    """
    if atr <= 0 or i < 10:
        return None

    price = c[i]

    # Step 1: Liquidity Sweep (REQUIRED)
    sweep_type, sweep_price, sweep_bar = detect_liquidity_sweep(
        h, l, c, o, swing_highs, swing_lows, i, atr)
    if sweep_type is None:
        return None

    action = 'LONG' if sweep_type == 'BULLISH_SWEEP' else 'SHORT'

    # Step 2: HTF Bias (REQUIRED)
    if htf_bias is not None and htf_bias != action:
        return None

    # Step 3: Market Structure (BONUS — không filter)
    mss = detect_market_structure(c, swing_highs, swing_lows, i)

    # Step 4: POI proximity (BONUS)
    near_poi, poi_type = is_near_poi(price, action, bull_obs, bear_obs,
                                      bull_fvgs, bear_fvgs, atr, i)

    # Step 5: Discount/Premium zone (BONUS — không filter)
    zone = get_zone(price, swing_highs, swing_lows, i)

    # Build label
    src = "ICT:Sweep"
    if mss and mss == ('BULLISH' if action == 'LONG' else 'BEARISH'):
        src += "+MSS"
    if poi_type:
        src += f"+{poi_type}"
    if zone:
        src += f"+{zone[:3]}"

    return {
        'action': action,
        'src': src,
        'sweep_price': sweep_price,
        'sweep_bar': sweep_bar,
        'poi_type': poi_type,
        'zone': zone,
        'mss': mss,
    }
