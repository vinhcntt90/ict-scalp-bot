"""
ICT Multi-TF Backtest — 1H Signal → 15m Confirm → 5m Entry
===========================================================
Chạy: py -3 backtest_scalp.py
      py -3 backtest_scalp.py --days 30 --capital 350

ICT Flow:
  - 1H: Liquidity Sweep + MSS → signal
  - 15m: Structure shift confirm
  - 5m: Entry refinement
  - SL: Sweep candle (1H)
  - TP: 2×ATR
"""
import sys
import os
import json
import requests
import argparse
import pandas as pd
import numpy as np
from datetime import datetime

sys.path.insert(0, os.path.dirname(__file__))

from src.config import Config
from src.data import get_btc_data, fetcher
from src.ict_core import (
    calc_atr, detect_swing_points, detect_order_blocks, detect_fvg,
    generate_ict_signal
)
from src.notifications import send_telegram_message, esc
from src.scalp_strategy import (
    TAKER_FEE, QUICK_TP_PCT, BE_ATR_MULT, MAX_HOLD_BARS, BUFFER_PCT, MIN_RR,
    is_kill_zone, prepare_market_data, get_htf_bias,
    detect_signal, detect_breakout, scan_5m_entry, check_5m_bos, check_volume_confirmation,
    get_sweep_sl, get_1h_sweep_sl, calc_sl, calc_tp,
    calc_position_size, check_exit, check_sl_intrabar, handle_reverse,
)




def run_scalp_backtest(days=30, capital=350.0, risk_pct=5.0, leverage=50, start_date=None, end_date=None, symbol='BTCUSDT'):
    """
    ICT Scalp: 15m signal (sweep), 1H SL, 5m entry.
    """
    timeframe = '15m'
    htf_tf = '1h'
    entry_tf = '5m'
    candles_per_day = 96  # 15m

    date_filter = None
    if start_date:
        from datetime import timezone
        sd = pd.Timestamp(start_date)
        ed = pd.Timestamp(end_date) + pd.Timedelta(hours=23, minutes=59) if end_date else pd.Timestamp.now()
        days_from_now = (pd.Timestamp.now() - sd).days + 5
        days = days_from_now
        date_filter = (sd, ed)

    limit = days * candles_per_day + 200

    date_label = f"{start_date} → {end_date}" if start_date else f"{days} ngày"
    print(f"\n{'═' * 60}")
    print(f"  ⚡ ICT SCALP — {symbol} | 15m Signal | 1H SL | 5m Entry")
    print(f"  Vốn: ${capital:,.2f} | Risk: {risk_pct}% | Leverage: {leverage}x")
    print(f"  Khoảng: {date_label}")
    print(f"{'═' * 60}\n")

    # Helper: fetch historical data with pagination
    def fetch_historical(tf, start_ms, end_ms):
        all_data = []
        current_start = start_ms
        while current_start < end_ms:
            url = f'https://fapi.binance.com/fapi/v1/klines?symbol={symbol}&interval={tf}&startTime={current_start}&endTime={end_ms}&limit=1000'
            try:
                r = requests.get(url, timeout=15)
                data = r.json()
                if not data:
                    break
                all_data.extend(data)
                current_start = data[-1][0] + 1
                if len(data) < 1000:
                    break
            except:
                break
        if not all_data:
            return None
        df_h = pd.DataFrame(all_data, columns=['time','open','high','low','close','volume','ct','qav','trades','tbbav','tbqav','ignore'])
        df_h['time'] = pd.to_datetime(df_h['time'], unit='ms')
        df_h.set_index('time', inplace=True)
        for col in ['open','high','low','close','volume']:
            df_h[col] = df_h[col].astype(float)
        print(f"  ✅ {len(df_h)} candles ({tf}) [{df_h.index[0]} → {df_h.index[-1]}]")
        return df_h

    # 1. Fetch data
    print(f"[1/3] Fetching data...")
    if date_filter:
        import time as _time
        start_ms = int(date_filter[0].timestamp() * 1000)
        end_ms = int(date_filter[1].timestamp() * 1000)
        lookback_ms = 200 * 15 * 60 * 1000  # 200 bars × 15min
        df = fetch_historical(timeframe, start_ms - lookback_ms, end_ms)
        htf_df = fetch_historical(htf_tf, start_ms - lookback_ms, end_ms)
        ltf_df = fetch_historical(entry_tf, start_ms - lookback_ms, end_ms)
    else:
        df = fetcher.fetch_symbol_data(symbol, timeframe, limit)
        htf_df = fetcher.fetch_symbol_data(symbol, htf_tf, days * 24 + 200)
        ltf_df = fetcher.fetch_symbol_data(symbol, entry_tf, days * 288 + 200)

    if df is None or len(df) < 200:
        print("  ❌ Không đủ data!")
        return None

    print(f"  ✅ {len(df)} candles (15m)")
    if htf_df is not None:
        print(f"  ✅ {len(htf_df)} candles (1h) for SL")

    # 2. Pre-compute structures
    print(f"[2/3] Running ICT scalp backtest...")
    ctx = prepare_market_data(df, htf_df)
    atr_s = ctx['atr_series']
    o, h, l, c = ctx['o'], ctx['h'], ctx['l'], ctx['c']
    m15_swing_h, m15_swing_l = ctx['m15_swing_h'], ctx['m15_swing_l']
    print(f"    15m Swing: {len(m15_swing_h)}H / {len(m15_swing_l)}L")
    print(f"    1H Swing: {len(ctx['h1_swing_h'])}H / {len(ctx['h1_swing_l'])}L")
    print(f"    15m OB: {len(ctx['m15_bull_obs'])}Bull / {len(ctx['m15_bear_obs'])}Bear")
    print(f"    15m FVG: {len(ctx['m15_bull_fvgs'])}Bull / {len(ctx['m15_bear_fvgs'])}Bear")


    trades = []
    balance = capital
    position = None
    skipped_bias = 0
    skipped_vsa = 0
    breakout_trades = 0
    last_exit_bar = 0
    cooldown = 0
    max_hold = MAX_HOLD_BARS
    start_idx = 200

    total = len(df) - start_idx
    pct_step = max(1, total // 10)

    for i in range(start_idx, len(df)):
        price = c[i]
        atr = atr_s.iloc[i] if i < len(atr_s) and not pd.isna(atr_s.iloc[i]) else 0
        if atr == 0:
            continue

        progress = i - start_idx
        if progress % pct_step == 0:
            print(f"  {progress * 100 // total}%...", end=' ', flush=True)

        # Date range filter
        if date_filter:
            bar_time = df.index[i]
            if bar_time < date_filter[0] or bar_time > date_filter[1]:
                continue

        # --- A. Manage open position ---
        if position is not None:
            pos_atr = position.get('atr', atr)
            position['bars_held'] = i - position['bar']

            # Step 1: SL check using intrabar price (low/high)
            sl_price = l[i] if position['action'] == 'LONG' else h[i]
            hit, pnl, reason = check_sl_intrabar(position, sl_price)

            # Step 2: If SL not hit, check other exits using close price
            if not hit:
                hit, pnl, reason = check_exit(position, price, leverage)

            # Close trade
            if hit:
                pnl_pct = pnl / position['entry'] * 100
                pnl_dollar = position['size'] * pnl_pct / 100
                fee = position['size'] * TAKER_FEE * 2
                pnl_dollar -= fee
                balance += pnl_dollar

                trade = {
                    **position,
                    'close_bar': i,
                    'close_price': price,
                    'close_time': str(df.index[i] + pd.Timedelta(hours=7)),
                    'pnl': round(pnl, 2),
                    'pnl_pct': round(pnl_pct, 2),
                    'pnl_dollar': round(pnl_dollar, 2),
                    'balance_after': round(balance, 2),
                    'close_reason': reason,
                }
                trades.append(trade)
                position = None
                last_exit_bar = i

        # --- B. Check for new signal (or reverse) ---
        if i > start_idx + 2:
            timestamp = df.index[i]
            trend_bias = get_htf_bias(df, ctx['m15_swing_h'], ctx['m15_swing_l'], timestamp)

            # Signal detection (Sweep + Bias)
            new_action, _, _ = detect_signal(ctx, i, trend_bias)
            is_breakout = False
            breakout_level = None

            if new_action is None:
                bo_action, bo_level, _ = detect_breakout(ctx, i)
                if bo_action:
                    new_action = bo_action
                    is_breakout = True
                    breakout_level = bo_level

            if new_action is not None:
                should_open = False

                if position is None:
                    if (i - last_exit_bar) >= cooldown:
                        should_open = True
                elif position['action'] != new_action:
                    rev_trade, balance = handle_reverse(position, price, balance)
                    if rev_trade:
                        rev_trade['close_bar'] = i
                        rev_trade['close_time'] = str(df.index[i] + pd.Timedelta(hours=7))
                        rev_trade['close_reason'] = f'🔄 Reverse → {new_action}'
                        trades.append(rev_trade)
                    position = None
                    should_open = True

                if should_open:
                    action = new_action

                    if is_breakout:
                        breakout_trades += 1
                        signal_src = 'BO_Momentum'
                        entry = float(price)

                        # Volume confirm
                        vol_sma = pd.Series(df['volume'].values).rolling(20).mean().values[i]
                        if df['volume'].values[i] < vol_sma * 1.5:
                            continue

                        # SL: below/above broken swing level
                        if action == 'LONG':
                            sl = breakout_level - atr * 0.5
                        else:
                            sl = breakout_level + atr * 0.5
                        sl_tag = 'BO_SL'
                        signal_src += ':' + sl_tag

                    else:
                        signal_src = 'BOS+VSA'

                        # VOLUME CONFIRMATION
                        if not check_volume_confirmation(df, i):
                            continue

                        # 5M ENTRY + BOS
                        bar_start = df.index[i-1] if i > 0 else df.index[i]
                        bar_end = df.index[i]
                        entry, has_5m = scan_5m_entry(action, price, ltf_df, bar_start, bar_end)
                        if has_5m:
                            signal_src += ':5m'

                        # BOS 5m CONFIRMATION
                        has_bos = check_5m_bos(action, ltf_df, bar_start, bar_end)
                        if not has_bos:
                            continue
                        signal_src += ':BOS'

                        # SL — 1H SWEEP → ATR
                        sl_1h = get_sweep_sl(action, timestamp, htf_df,
                                             ctx['h1_swing_h'], ctx['h1_swing_l'])
                        sl, sl_tag = calc_sl(action, entry, atr, sl_1h)
                        if sl_tag:
                            signal_src += ':' + sl_tag

                    # TP — ICT Structure
                    tp1, tp2, tp3 = calc_tp(action, entry, atr, i, ctx)

                    # R:R FILTER
                    sl_dist = abs(entry - sl)
                    tp1_dist = abs(tp1 - entry)
                    if sl_dist > 0 and tp1_dist < sl_dist * MIN_RR:
                        continue

                    # POSITION SIZING
                    margin, size = calc_position_size(balance, risk_pct, leverage, entry, sl)

                    position = {
                        'action': action,
                        'entry': entry,
                        'entry_15m': float(price),
                        'sl': sl,
                        'tp1': tp1,
                        'tp2': tp2,
                        'tp3': tp3,
                        'size': size,
                        'bar': i,
                        'open_time': str(timestamp + pd.Timedelta(hours=7)),
                        'atr': atr,
                        'src': signal_src,
                    }

    print()

    # 3. Summary
    total_trades = len(trades)
    wins = [t for t in trades if t['pnl_dollar'] > 0]
    losses = [t for t in trades if t['pnl_dollar'] <= 0]
    win_rate = len(wins) / total_trades * 100 if total_trades > 0 else 0
    total_pnl = sum(t['pnl_dollar'] for t in trades)

    # Max drawdown
    peak = capital
    max_dd = 0
    running = capital
    for t in trades:
        running += t['pnl_dollar']
        if running > peak:
            peak = running
        dd = (peak - running) / peak * 100
        if dd > max_dd:
            max_dd = dd

    print(f"\n{'═' * 60}")
    print(f"  ⚡ KẾT QUẢ SCALP BACKTEST")
    print(f"{'═' * 60}")
    print(f"  Vốn đầu: ${capital:,.2f}")
    print(f"  Vốn cuối: ${balance:,.2f}")
    print(f"  PnL: ${total_pnl:,.2f} ({total_pnl/capital*100:+.1f}%)")
    print(f"  Max Drawdown: {max_dd:.1f}%")
    print(f"  Tổng lệnh: {total_trades}")
    print(f"  Win: {len(wins)} | Loss: {len(losses)}")
    print(f"  Winrate: {win_rate:.1f}%")
    print(f"  Skipped (15m bias): {skipped_bias}")
    print(f"  Skipped (VSA): {skipped_vsa}")
    print(f"  Breakout trades: {breakout_trades}")
    if wins:
        print(f"  Avg Win: ${sum(t['pnl_dollar'] for t in wins)/len(wins):,.2f}")
    if losses:
        print(f"  Avg Loss: ${sum(t['pnl_dollar'] for t in losses)/len(losses):,.2f}")
    print(f"{'═' * 60}")

    # PRE-SIGNAL stats
    pre_trades = [t for t in trades if ':PRE' in t.get('src', '')]
    normal_trades = [t for t in trades if ':PRE' not in t.get('src', '')]
    pre_wins = [t for t in pre_trades if t['pnl_dollar'] > 0]
    normal_wins = [t for t in normal_trades if t['pnl_dollar'] > 0]
    if pre_trades:
        pre_wr = len(pre_wins) / len(pre_trades) * 100
        normal_wr = len(normal_wins) / len(normal_trades) * 100 if normal_trades else 0
        print(f"\n⚠️  PRE-SIGNAL: {len(pre_trades)} trades (Win: {len(pre_wins)}, WR: {pre_wr:.0f}%)")
        print(f"⚡  Regular:    {len(normal_trades)} trades (Win: {len(normal_wins)}, WR: {normal_wr:.0f}%)")

    # Top 5 Win / Loss
    sorted_wins = sorted(wins, key=lambda t: t['pnl_dollar'], reverse=True)[:5]
    sorted_losses = sorted(losses, key=lambda t: t['pnl_dollar'])[:5]
    if sorted_wins:
        print(f"\n🏆 TOP 5 WIN:")
        for t in sorted_wins:
            src = t.get('src', '')
            print(f"✅ {t['action']} ${t['pnl_dollar']:+,.1f} {t['close_reason']} [{src}]")
    if sorted_losses:
        print(f"\n💀 TOP 5 LOSS:")
        for t in sorted_losses:
            src = t.get('src', '')
            print(f"❌ {t['action']} ${t['pnl_dollar']:+,.1f} {t['close_reason']} [{src}]")

    # 4. Trade list
    print(f"\n{'─' * 120}")
    print(f"  {'#':>3} | {'Action':>5} | {'Open Time':>19} | {'Entry':>10} | {'SL':>10} | {'TP1':>10} | {'TP2':>10} | {'TP3':>10} | {'PnL $':>8} | {'Close Time':>19} | Result")
    print(f"{'─' * 120}")
    for idx, t in enumerate(trades, 1):
        icon = '✅' if t['pnl_dollar'] > 0 else '❌'
        src = t.get('src', '')
        tag = f"[{src}]"
        entry_15m = t.get('entry_15m', t['entry'])
        entry_str = f"${t['entry']:>9,.2f}" if abs(t['entry'] - entry_15m) < 0.01 else f"${t['entry']:,.0f}-${entry_15m:,.0f}"
        open_t = t.get('open_time', '')[:19]
        close_t = t.get('close_time', '')[:19]
        sl_str = f"${t.get('sl', 0):>9,.2f}"
        tp1_str = f"${t.get('tp1', 0):>9,.2f}"
        tp2_str = f"${t.get('tp2', 0):>9,.2f}"
        tp3_str = f"${t.get('tp3', 0):>9,.2f}"
        print(f"  {idx:>3} | {t['action']:>5} | {open_t:>19} | {entry_str:>10} | {sl_str:>10} | {tp1_str:>10} | {tp2_str:>10} | {tp3_str:>10} | ${t['pnl_dollar']:>7,.2f} | {close_t:>19} | {icon} {t['close_reason']} {tag}")

    # 5. Log to Google Sheets
    log_scalp_to_sheets(trades, capital, balance, total_pnl, win_rate, max_dd, days)

    # 6. Send to Telegram (disabled)
    # send_backtest_telegram(trades, capital, balance, total_pnl, win_rate, max_dd, leverage, risk_pct, date_label)

    return {
        'trades': trades,
        'df': df,
        'capital': capital,
        'final_balance': balance,
        'pnl': total_pnl,
        'win_rate': win_rate,
        'max_drawdown': max_dd,
    }


def log_scalp_to_sheets(trades, capital, final_balance, total_pnl, win_rate, max_dd, days):
    """Log trades to Google Sheets via batch rows."""
    url = Config.GOOGLE_SHEETS_URL
    if not url:
        print("  ⚠️ GOOGLE_SHEETS_URL not set")
        return

    print(f"\n[*] Logging {len(trades)} scalp trades to Google Sheets (batch)...")

    # Build rows: [Open Time, Close Time, Action, Entry, SL, TP1, TP2, TP3, Source, PnL($), PnL(%), Reason]
    rows = []
    for t in trades:
        win = '✅' if t['pnl_dollar'] > 0 else '❌'
        rows.append([
            str(t['open_time']),           # Open Time (VN)
            str(t['close_time']),          # Close Time (VN)
            t['action'],                   # Action
            round(t['entry'], 2),          # Entry
            round(t['sl'], 2),             # SL
            round(t.get('tp1', 0), 2),     # TP1
            round(t.get('tp2', 0), 2),     # TP2
            round(t.get('tp3', 0), 2),     # TP3
            t.get('src', ''),              # Source
            round(t['pnl_dollar'], 2),     # PnL ($)
            f"{t['pnl_pct']:.2f}%",        # PnL (%) — no '+' to avoid Sheets formula error
            f"{win} {t['close_reason']}",  # Reason
        ])

    # Summary row
    wins = len([t for t in trades if t['pnl_dollar'] > 0])
    rows.append([
        '📊 SUMMARY', '',
        f'{len(trades)} trades',
        f'Win:{wins} Loss:{len(trades)-wins}',
        f'WR:{win_rate:.0f}%',
        f'DD:{max_dd:.1f}%',
        '', '',
        f'ICT Scalp 15m',
        round(total_pnl, 2),
        round(total_pnl / capital * 100, 2),
        f'${capital:.0f} → ${final_balance:.0f}',
    ])

    try:
        payload = {'rows': rows}
        r = requests.post(url, json=payload, timeout=60, headers={'Content-Type': 'application/json'})
        if r.status_code == 200:
            print(f"  ✅ Batch logged {len(rows)} rows to Google Sheets")
        else:
            print(f"  ⚠️ Sheet error: HTTP {r.status_code}")
    except Exception as e:
        print(f"  ⚠️ Sheet error: {e}")


def send_backtest_telegram(trades, capital, balance, total_pnl, win_rate, max_dd, leverage, risk_pct, date_label):
    """Gửi tổng hợp kết quả backtest lên Telegram."""
    total_trades = len(trades)
    wins = [t for t in trades if t['pnl_dollar'] > 0]
    losses = [t for t in trades if t['pnl_dollar'] <= 0]
    pnl_pct = total_pnl / capital * 100 if capital > 0 else 0

    avg_win = sum(t['pnl_dollar'] for t in wins) / len(wins) if wins else 0
    avg_loss = sum(t['pnl_dollar'] for t in losses) / len(losses) if losses else 0
    rr = abs(avg_win / avg_loss) if avg_loss != 0 else 0

    pnl_emoji = '📈' if total_pnl >= 0 else '📉'
    wr_emoji = '🟢' if win_rate >= 50 else '🔴'

    # Top 5 Win / Loss
    top_wins = sorted(wins, key=lambda t: t['pnl_dollar'], reverse=True)[:5]
    top_losses = sorted(losses, key=lambda t: t['pnl_dollar'])[:5]
    top5_lines = []
    if top_wins:
        top5_lines.append("🏆 *TOP 5 WIN:*")
        for t in top_wins:
            src = t.get('src', '')
            pnl_str = f"${t['pnl_dollar']:+,.1f}"
            top5_lines.append(f"✅ {esc(t['action'])} {esc(pnl_str)} {esc(t['close_reason'])} \\[{esc(src)}\\]")
    if top_losses:
        top5_lines.append("\n💀 *TOP 5 LOSS:*")
        for t in top_losses:
            src = t.get('src', '')
            pnl_str = f"${t['pnl_dollar']:+,.1f}"
            top5_lines.append(f"❌ {esc(t['action'])} {esc(pnl_str)} {esc(t['close_reason'])} \\[{esc(src)}\\]")
    top5_text = '\n'.join(top5_lines)

    msg = f"""⚡ *ICT SCALP BACKTEST*

📅 {esc(date_label)}
💰 Vốn: ${esc(f'{capital:,.0f}')} \\| x{esc(str(leverage))} \\| {esc(f'{risk_pct}')}%

{pnl_emoji} *KẾT QUẢ:*
├ Balance: ${esc(f'{balance:,.2f}')}
├ PnL: {esc(f'${total_pnl:+,.2f}')} \\({esc(f'{pnl_pct:+.1f}%')}\\)
├ Max DD: {esc(f'{max_dd:.1f}%')}
└ R:R: {esc(f'{rr:.2f}x')}

📊 *THỐNG KÊ:*
├ Tổng: {esc(str(total_trades))} lệnh
├ {wr_emoji} Win: {esc(str(len(wins)))} \\| Loss: {esc(str(len(losses)))}
├ Winrate: {esc(f'{win_rate:.1f}%')}
├ Avg Win: {esc(f'${avg_win:+,.2f}')}
└ Avg Loss: {esc(f'${avg_loss:+,.2f}')}

{top5_text}"""

    try:
        send_telegram_message(msg, parse_mode='MarkdownV2')
        print(f"\n  ✅ Telegram: Backtest summary sent!")
    except Exception as e:
        print(f"\n  ⚠️ Telegram error: {e}")


def plot_scalp_chart(df, trades, days, capital, final_balance):
    """Vẽ candlestick chart cho scalp."""
    import matplotlib
    matplotlib.use('Agg')
    import matplotlib.pyplot as plt
    import matplotlib.dates as mdates
    from matplotlib.lines import Line2D

    fig, (ax1, ax2) = plt.subplots(2, 1, figsize=(22, 12), gridspec_kw={'height_ratios': [3, 1]},
                                     sharex=True)
    fig.patch.set_facecolor('#1a1a2e')

    # Candlestick
    ax1.set_facecolor('#16213e')
    dates = df.index
    opens = df['open'].values
    highs = df['high'].values
    lows = df['low'].values
    closes = df['close'].values

    for i in range(len(df)):
        color = '#00e676' if closes[i] >= opens[i] else '#ff1744'
        body_bottom = min(opens[i], closes[i])
        body_top = max(opens[i], closes[i])
        ax1.plot([dates[i], dates[i]], [lows[i], highs[i]], color=color, linewidth=0.3, alpha=0.7)
        ax1.bar(dates[i], body_top - body_bottom, bottom=body_bottom,
                color=color, width=0.4 * (dates[1] - dates[0]), alpha=0.85, edgecolor=color)

    # EMA lines
    ax1.plot(dates, df['ema9'].values, color='#ffeb3b', linewidth=0.8, alpha=0.7, label='EMA9')
    ax1.plot(dates, df['ema21'].values, color='#2196f3', linewidth=0.8, alpha=0.7, label='EMA21')

    # Trade markers
    for idx, t in enumerate(trades):
        entry_time = pd.Timestamp(t['open_time'])
        close_time = pd.Timestamp(t['close_time'])
        entry_price = t['entry']
        is_win = t['pnl_dollar'] > 0

        marker = '^' if t['action'] == 'LONG' else 'v'
        ecolor = '#00e676' if t['action'] == 'LONG' else '#ff1744'
        ax1.scatter(entry_time, entry_price, marker=marker, color=ecolor,
                   s=100, zorder=5, edgecolors='white', linewidth=1)

        # SL/TP lines
        ax1.hlines(t['sl'], entry_time, close_time, colors='#ff1744',
                   linestyles='dashed', linewidth=0.8, alpha=0.5)
        ax1.hlines(t.get('tp1', entry_price), entry_time, close_time, colors='#00e676',
                   linestyles='dashed', linewidth=0.8, alpha=0.4)

        # Close marker
        close_color = '#00e676' if is_win else '#ff1744'
        close_marker = '*' if is_win else 'x'
        ax1.scatter(close_time, t.get('close_price', entry_price), marker=close_marker,
                   color=close_color, s=60, zorder=5)

        ax1.annotate(f"#{idx+1}", xy=(entry_time, entry_price),
                    fontsize=5, color='white', alpha=0.7,
                    xytext=(3, 8 if t['action'] == 'LONG' else -12),
                    textcoords='offset points')

    ax1.set_title(f"SCALP BTC 15M — {days}d | ${capital:.0f} -> ${final_balance:.0f}",
                  color='white', fontsize=14, fontweight='bold', pad=15)
    ax1.set_ylabel('Price ($)', color='white', fontsize=10)
    ax1.tick_params(colors='white')
    ax1.grid(True, alpha=0.15, color='white')

    # Volume
    ax2.set_facecolor('#16213e')
    vol_colors = ['#00e676' if closes[i] >= opens[i] else '#ff1744' for i in range(len(df))]
    ax2.bar(dates, df['volume'].values, color=vol_colors, alpha=0.5,
            width=0.4 * (dates[1] - dates[0]))
    ax2.set_ylabel('Volume', color='white', fontsize=10)
    ax2.tick_params(colors='white')
    ax2.grid(True, alpha=0.15, color='white')

    ax2.xaxis.set_major_formatter(mdates.DateFormatter('%m/%d %H:%M'))
    plt.xticks(rotation=45, ha='right')

    legend_elements = [
        Line2D([0], [0], color='#ffeb3b', linewidth=1, label='EMA9'),
        Line2D([0], [0], color='#2196f3', linewidth=1, label='EMA21'),
        Line2D([0], [0], marker='^', color='w', markerfacecolor='#00e676', markersize=8, label='LONG'),
        Line2D([0], [0], marker='v', color='w', markerfacecolor='#ff1744', markersize=8, label='SHORT'),
        Line2D([0], [0], marker='*', color='w', markerfacecolor='#00e676', markersize=8, label='Win'),
        Line2D([0], [0], marker='x', color='#ff1744', markersize=8, label='Loss'),
    ]
    ax1.legend(handles=legend_elements, loc='upper left', fontsize=7,
              facecolor='#16213e', edgecolor='white', labelcolor='white')

    plt.tight_layout()

    chart_path = os.path.join(os.path.dirname(__file__), 'backtest_chart_scalp.png')
    plt.savefig(chart_path, dpi=150, bbox_inches='tight', facecolor=fig.get_facecolor())
    plt.close()
    print(f"\n  📈 Chart saved: {chart_path}")

    try:
        os.startfile(chart_path)
    except Exception:
        pass

    return chart_path


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description='Scalp 15M Backtest')
    parser.add_argument('--days', type=int, default=7, help='Số ngày test')
    parser.add_argument('--start', default=None, help='Ngày bắt đầu (YYYY-MM-DD)')
    parser.add_argument('--end', default=None, help='Ngày kết thúc (YYYY-MM-DD)')
    parser.add_argument('--capital', type=float, default=350.0, help='Vốn ($)')
    parser.add_argument('--risk', type=float, default=5.0, help='Risk pct per trade')
    parser.add_argument('--leverage', type=int, default=50, help='Đòn bẩy')
    parser.add_argument('--symbol', default='BTCUSDT', help='Trading pair (e.g. ETHUSDT, SOLUSDT)')
    args = parser.parse_args()

    result = run_scalp_backtest(days=args.days, capital=args.capital, risk_pct=args.risk,
                                leverage=args.leverage, start_date=args.start, end_date=args.end,
                                symbol=args.symbol)
