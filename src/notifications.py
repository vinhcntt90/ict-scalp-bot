import requests
import json
import os
from datetime import datetime
from .config import Config


def send_telegram_photo(image_path, caption=""):
    """Send photo to all configured Telegram channels"""
    if not Config.SEND_TO_TELEGRAM:
        print("[!] Telegram sending disabled.")
        return
    
    if not Config.TELEGRAM_BOT_TOKEN:
        print("[!] Telegram Bot Token not set.")
        return

    for chat_id in Config.TELEGRAM_CHAT_IDS:
        url = f"https://api.telegram.org/bot{Config.TELEGRAM_BOT_TOKEN}/sendPhoto"
        try:
            with open(image_path, 'rb') as photo:
                payload = {
                    'chat_id': chat_id,
                    'caption': caption,
                    'parse_mode': 'MarkdownV2'
                }
                files = {'photo': photo}
                response = requests.post(url, data=payload, files=files, timeout=20)
                
                if response.status_code == 200:
                    print(f"  ✅ Telegram: Photo sent to {chat_id}!")
                else:
                    print(f"  ❌ Telegram Error ({chat_id}): {response.text}")
        except Exception as e:
            print(f"  ❌ Telegram Connection Error {chat_id}: {e}")


def send_telegram_message(message, parse_mode='MarkdownV2'):
    """Send text message to all configured Telegram channels"""
    if not Config.SEND_TO_TELEGRAM:
        return
    
    if not Config.TELEGRAM_BOT_TOKEN:
        return

    for chat_id in Config.TELEGRAM_CHAT_IDS:
        url = f"https://api.telegram.org/bot{Config.TELEGRAM_BOT_TOKEN}/sendMessage"
        payload = {
            'chat_id': chat_id,
            'text': message,
            'parse_mode': parse_mode
        }
        try:
            response = requests.post(url, json=payload, timeout=10)
            if response.status_code != 200:
                print(f"  ❌ Telegram Message Error ({chat_id}): {response.text}")
        except Exception as e:
            print(f"  ❌ Telegram Message Connection Error {chat_id}: {e}")


def send_telegram_media_group(image_paths, caption=""):
    """Send multiple photos as a media group (album) to Telegram"""
    if not Config.SEND_TO_TELEGRAM:
        print("[!] Telegram sending disabled.")
        return
    
    for chat_id in Config.TELEGRAM_CHAT_IDS:
        url = f"https://api.telegram.org/bot{Config.TELEGRAM_BOT_TOKEN}/sendMediaGroup"
        
        try:
            media = []
            files = {}
            
            for i, path in enumerate(image_paths):
                file_key = f"photo{i}"
                media_item = {
                    "type": "photo",
                    "media": f"attach://{file_key}"
                }
                if i == 0 and caption:
                    media_item["caption"] = caption
                    media_item["parse_mode"] = "MarkdownV2"
                
                media.append(media_item)
                files[file_key] = open(path, 'rb')
            
            payload = {
                'chat_id': chat_id,
                'media': json.dumps(media)
            }
            
            response = requests.post(url, data=payload, files=files, timeout=30)
            
            for f in files.values():
                f.close()
            
            if response.status_code == 200:
                print(f"  ✅ Telegram: Album {len(image_paths)} photos sent to {chat_id}!")
            else:
                print(f"  ❌ Telegram Album Error ({chat_id}): {response.text}")
                
        except Exception as e:
            print(f"  ❌ Telegram Connection Error {chat_id}: {e}")


def esc(text):
    """Helper to escape MarkdownV2 special characters"""
    if not isinstance(text, str):
        text = str(text)
    # MarkdownV2 reserved characters
    reserved = ['_', '*', '[', ']', '(', ')', '~', '`', '>', '#', '+', '-', '=', '|', '{', '}', '.', '!']
    for char in reserved:
        text = text.replace(char, f'\\{char}')
    return text


def create_telegram_caption(plan, analyses, pivots, poc_data, df, derivatives_data=None, smc_data=None, ob_walls=None, smt_data=None, adv_fibo=None, tdi_data=None):
    """Create caption for Telegram photo"""
    current_price = df['close'].iloc[-1]
    rsi = df['RSI'].iloc[-1]

    # Multi-TF Bias
    tf_lines = []
    for tf in ['15m', '1H', '4H', '1D']:
        if tf in analyses:
            bias = analyses[tf]['bias']
            emoji = "🟢" if bias == 'BULLISH' else ("🔴" if bias == 'BEARISH' else "⚪")
            tf_lines.append(f"{emoji} {esc(tf)}")
    
    # ChartPrime Analysis
    cp_info = ""
    if plan.get('cp_data'):
        cp = plan['cp_data']
        tl = cp['trend_levels']
        lro = cp['lro']
        # Pre-format CP values
        trend_high = f"{tl['trend_high']:,.0f}"
        trend_low = f"{tl['trend_low']:,.0f}"
        lro_val = f"{lro['oscillator']:.2f}"
        score_val = f"{cp['score']:+d}"

        cp_info = f"""
💠 *ChartPrime \\(15m\\):*
• Xu hướng: {esc(tl['trend_direction'])}
  H:${esc(trend_high)} L:${esc(trend_low)}
• LRO: {esc(lro_val)} \\({esc(lro['status'])}\\)
• Điểm: {esc(score_val)} \\({esc(cp['entry_signal'])}\\)"""

    # Derivatives
    deriv_info = ""
    if derivatives_data and derivatives_data.get('funding_rate_pct') is not None:
        fr = derivatives_data.get('funding_rate_pct', 0)
        ls = derivatives_data.get('ls_ratio', 0)
        long_pct = derivatives_data.get('long_ratio', 0) * 100
        oi = derivatives_data.get('open_interest', 0)
        
        fr_signal = "⚠️ Long Crowded" if fr > 0.05 else ("🟢 Short Crowded" if fr < -0.01 else "Neutral")
        
        # Pre-format derivatives values
        fr_str = f"{fr:.4f}%"
        ls_str = f"{ls:.2f}"
        lp_str = f"{long_pct:.0f}%"
        oi_str = f"{oi:,.0f}"

        deriv_info = f"""
📊 *Dữ liệu Phái sinh:*
• Funding: {esc(fr_str)} \\({esc(fr_signal)}\\)
• L/S: {esc(ls_str)} \\({esc(lp_str)} Long\\)
• OI: {esc(oi_str)} BTC"""

    # Order Book Walls (Compact)
    ob_info = ""
    if ob_walls:
        lines = []
        if ob_walls.get('sell_walls'):
            # Top 3 Sells
            sells = ob_walls['sell_walls'][:3]
            # Simplify: Format substrings first
            sell_items = []
            for w in sells:
                price_k = f"{w['price']/1000:.1f}k"
                vol = f"{w['volume']:.0f}"
                sell_items.append(f"${esc(price_k)}\\({esc(vol)}\\)")
            sell_str = ", ".join(sell_items)
            lines.append(f"🧱 Res: {sell_str}")
        
        if ob_walls.get('buy_walls'):
            # Top 3 Buys
            buys = ob_walls['buy_walls'][:3]
            buy_items = []
            for w in buys:
                price_k = f"{w['price']/1000:.1f}k"
                vol = f"{w['volume']:.0f}"
                buy_items.append(f"${esc(price_k)}\\({esc(vol)}\\)")
            buy_str = ", ".join(buy_items)
            lines.append(f"🛡️ Sup: {buy_str}")
            
        if lines:
             ob_info = f"\n📒 *Tường Order Book:* " + " \\| ".join(lines)

    # SMC Info
    smc_info = ""
    if smc_data:
        wyckoff = smc_data.get('wyckoff', {})
        lines = []
        if wyckoff.get('phase'):
            conf_str = f"{wyckoff.get('confidence', 0)}%"
            lines.append(f"• Wyckoff: {esc(wyckoff['phase'])} \\({esc(conf_str)}\\)")
            
        # MMXM Info
        mmxm = smc_data.get('mmxm')
        if mmxm:
            mmxm_desc = f"{mmxm['type']} ({mmxm['phase']})"
            mmxm_line = f"• MMXM: {esc(mmxm_desc)}"
            if mmxm.get('target'):
                tgt_val = f"${mmxm['target']:,.0f}"
                mmxm_line += f" → {esc(tgt_val)}"
            lines.append(mmxm_line)
            
        # PO3 Info
        po3 = smc_data.get('po3')
        if po3 and po3.get('bias') != 'NEUTRAL':
            # Avoid redundancy if phase already contains bias info
            phase_clean = po3['phase'].replace(' (Bullish)', '').replace(' (Bearish)', '')
            po3_desc = f"{phase_clean} ({po3['bias']})"
            lines.append(f"• PO3: {esc(po3_desc)}")
            
        # IPDA Info
        ipda = smc_data.get('ipda')
        if ipda:
            # Show 20d & 40d ranges
            ipda_line = f"• IPDA: {esc(ipda['context'])} \\({esc(ipda['bias'])}\\)"
            lines.append(ipda_line)

        # Silver Bullet Info
        sb = smc_data.get('silver_bullet')
        if sb:
            # sb is a list, get the latest
            latest_sb = sb[0]
            sb_price = f"{latest_sb['price']:,.0f}"
            sb_icon = "🔫" 
            lines.append(f"• {sb_icon} Silver Bullet: {esc(latest_sb['session'])} @ ${esc(sb_price)}")

        # Unicorn Model
        unicorns = smc_data.get('unicorns')
        if unicorns:
            # Get latest unicorn
            latest_u = unicorns[-1]
            u_price = f"{latest_u['price']:,.0f}"
            u_type = "Bullish" if 'Bullish' in latest_u['type'] else "Bearish"
            u_icon = "🦄"
            lines.append(f"• {u_icon} Unicorn: {esc(u_type)} @ ${esc(u_price)} \\(Đảo chiều mạnh\\)")

        # Rejection Block
        rbs = smc_data.get('rejection_blocks', [])
        if rbs:
            # Get latest RB
            latest_rb = rbs[-1]
            rb_price = f"{latest_rb['price']:,.0f}"
            rb_type = "Bullish" if 'Bullish' in latest_rb['type'] else "Bearish"
            rb_icon = "🕯️"
            lines.append(f"• {rb_icon} Rejection: {esc(rb_type)} @ ${esc(rb_price)} \\(Rút chân\\)")

        # Propulsion Block
        pbs = smc_data.get('propulsion_blocks', [])
        if pbs:
            # Get latest PB
            latest_pb = pbs[-1]
            pb_price = f"{latest_pb['price']:,.0f}"
            pb_type = "Bullish" if 'Bullish' in latest_pb['type'] else "Bearish"
            pb_icon = "🚀"
            lines.append(f"• {pb_icon} Propulsion: {esc(pb_type)} @ ${esc(pb_price)} \\(Tiếp diễn xu hướng\\)")

        # Standard Deviation Targets
        sd = smc_data.get('sd_levels', {})
        if sd:
            # Show 2.0 and 2.5 as key TPs
            tp2 = sd.get('2.0 SD') or sd.get('-2.0 SD')
            tp25 = sd.get('2.5 SD') or sd.get('-2.5 SD')
            
            if tp2 or tp25:
                sd_msg = "• 🎯 TP \\(SD\\): "
                if tp2: sd_msg += f"2\\.0\\(${esc(f'{tp2:,.0f}')}\\) "
                if tp25: sd_msg += f"\\| 2\\.5\\(${esc(f'{tp25:,.0f}')}\\)"
                sd_msg += " \\(Mục tiêu giá\\)"
                lines.append(sd_msg)

        # IFVG Info
        ifvgs = smc_data.get('ifvgs', [])
        if ifvgs:
            # Show top 1 nearest
            ifvg = ifvgs[0]
            ifvg_price = f"{ifvg['bottom']:,.0f}"
            lines.append(f"• IFVG: {esc(ifvg['type'])} ${esc(ifvg_price)} \\(Lật S/R\\)")

        # Breaker/Mitigation Info
        breakers = smc_data.get('breakers', [])
        mitigations = smc_data.get('mitigations', [])
        
        block_lines = []
        for b in breakers[:1]:
            b_price = f"{b['price']:,.0f}"
            block_lines.append(f"Breaker ${esc(b_price)} \\(Phá vỡ\\)")
        for m in mitigations[:1]:
            m_price = f"{m['price']:,.0f}"
            block_lines.append(f"Mitigation ${esc(m_price)} \\(Giảm thiểu\\)")
            
        if block_lines:
            lines.append(f"• Blocks: {', '.join(block_lines)}")

        if lines:
            smc_info = "\n\n🧠 *Phân tích SMC:*\n" + "\n".join(lines)
            
    # Advanced Fibonacci Info
    fibo_info = ""
    if adv_fibo:
        lines = []
        # Confluence
        confluences = adv_fibo.get('confluence', [])
        if confluences:
             lines.append(f"• 🧱 Confluence \\(Bê\\-tông\\):")
             for c in confluences[:2]: # Show max 2
                 p_val = f"{c['price']:,.0f}"
                 lines.append(f"  \\- ${esc(p_val)} \\({esc(c['levels'])}\\)")
        
        # ABCD
        abcd = adv_fibo.get('abcd')
        if abcd:
            tp_val = f"{abcd['targets']['1.0']:,.0f}"
            lines.append(f"• 📉 ABCD: {esc(abcd['type'])} \\(TP: ${esc(tp_val)}\\)")
            
        # Wicks
        wicks = adv_fibo.get('wicks', [])
        if wicks:
            lines.append(f"• 🕯️ Inst\\. Levels \\(50% Râu\\):")
            for w in wicks:
                p_val = f"{w['price']:,.0f}"
                lines.append(f"  \\- ${esc(p_val)} \\({esc(w['type'])}\\)")
                
        if lines:
            fibo_info = "\n\n🌀 *Fibo Nâng Cao \\(Phase 3\\):*\n" + "\n".join(lines)
    


    # Summary
    last = df.iloc[-1]
    macd_status = "Bullish" if last['MACD'] > last['MACD_Signal'] else "Bearish"
    ma_status = "Bullish" if last['MA20'] > last['MA50'] else "Bearish"
    
    smt_str = "None"
    if smt_data:
        smt_str = f"{esc(smt_data['type'])} \\({esc(smt_data['case'])}\\)"

    # Pre-format summary values
    rsi_str = f"{rsi:.1f}"
    macd_val = f"{last['MACD']:.0f}"
    poc_str = f"{poc_data['poc']:,.0f}"
    pp_str = f"{pivots['PP']:,.0f}"

    summary_info = f"""
📋 *TỔNG KẾT*
• RSI \\(14\\): {esc(rsi_str)}
• MACD: {esc(macd_val)} \\({esc(macd_status)}\\)
• MA20\\/50: {esc(ma_status)}
• POC: ${esc(poc_str)}
• Pivot PP: ${esc(pp_str)}
• Phân Kỳ SMT: {smt_str}"""

    # Market Structure Info
    ms_info = ""
    if smc_data and smc_data.get('market_structure'):
        ms = smc_data['market_structure']
        ms_trend = ms.get('trend', 'NEUTRAL')
        ms_emoji = '🟢' if ms_trend == 'BULLISH' else ('🔴' if ms_trend == 'BEARISH' else '⚪')
        
        ms_lines = [f"{ms_emoji} Trend: {esc(ms_trend)}"]
        
        if ms.get('last_bos'):
            bos = ms['last_bos']
            bos_price = f"${bos['price']:,.0f}"
            ms_lines.append(f"• BOS {esc(bos['direction'])} @ {esc(bos_price)}")
        
        if ms.get('last_choch'):
            choch = ms['last_choch']
            choch_price = f"${choch['price']:,.0f}"
            ms_lines.append(f"• CHoCH {esc(choch['direction'])} @ {esc(choch_price)}")
        
        level_parts = []
        if ms.get('strong_high'):
            sh_str = f"${ms['strong_high']:,.0f}"
            level_parts.append(f"SH: {esc(sh_str)}")
        if ms.get('weak_low'):
            wl_str = f"${ms['weak_low']:,.0f}"
            level_parts.append(f"WL: {esc(wl_str)}")
        if level_parts:
            ms_lines.append(f"• {' \\| '.join(level_parts)}")
        
        ms_info = "\n\n🏗️ *Cấu Trúc Thị Trường:*\n" + "\n".join(ms_lines)

    # TDI Info
    tdi_info = ""
    if tdi_data:
        tdi_emoji = '🟢' if 'BULL' in tdi_data['bias'] else ('🔴' if 'BEAR' in tdi_data['bias'] else '⚪')
        rsi_val = f"{tdi_data['rsi']:.1f}"
        sig_val = f"{tdi_data['signal_line']:.1f}"
        base_val = f"{tdi_data['base_line']:.1f}"
        
        tdi_lines = [f"{tdi_emoji} Bias: {esc(tdi_data['bias'])}"]
        tdi_lines.append(f"• RSI: {esc(rsi_val)} \\| Sig: {esc(sig_val)} \\| Base: {esc(base_val)}")
        
        if tdi_data.get('is_squeeze'):
            tdi_lines.append(f"• ⚡ BB Squeeze \\- Breakout sắp tới")
        
        # Show latest signal
        if tdi_data.get('signals'):
            latest = tdi_data['signals'][0]
            tdi_lines.append(f"• {esc(latest[1])}")
        
        tdi_info = "\n\n📈 *TDI Signal:*\n" + "\n".join(tdi_lines)

    # Price Action Info
    pa_info = ""
    pa_lines = []
    
    # Candlestick patterns
    if smc_data and smc_data.get('candle_pattern'):
        cp = smc_data['candle_pattern']
        if cp.get('pattern'):
            cp_emoji = '🟢' if cp['type'] == 'BULLISH' else ('🔴' if cp['type'] == 'BEARISH' else '⚪')
            cp_name = esc(cp['pattern'].replace('_', ' ').title())
            pa_lines.append(f"{cp_emoji} {cp_name} \\(Str: {esc(str(cp['strength']))}\\)")
            
            # Show Entry/SL/TP for main pattern
            if cp.get('entry') and cp['entry'] > 0 and cp['type'] in ['BULLISH', 'BEARISH']:
                entry_str = f"{cp['entry']:,.0f}"
                sl_str = f"{cp['sl']:,.0f}"
                tp1_str = f"{cp['tp1']:,.0f}"
                tp2_str = f"{cp['tp2']:,.0f}"
                rr_str = f"{cp.get('rr', 0):.1f}"
                pa_lines.append(f"  Entry: ${esc(entry_str)} \\| SL: ${esc(sl_str)}")
                pa_lines.append(f"  TP1: ${esc(tp1_str)} \\| TP2: ${esc(tp2_str)} \\| R\\/R: 1:{esc(rr_str)}")
            
            # Show extra patterns (name only)
            for ap in cp.get('all_patterns', [])[1:2]:
                ap_name = esc(ap['pattern'].replace('_', ' ').title())
                ap_emoji = '🟢' if ap['type'] == 'BULLISH' else ('🔴' if ap['type'] == 'BEARISH' else '⚪')
                pa_lines.append(f"• {ap_emoji} {ap_name} \\(Str: {esc(str(ap['strength']))}\\)")
    
    # Chart patterns (Double Top/Bottom)
    if smc_data and smc_data.get('chart_patterns'):
        for chart in smc_data['chart_patterns'][:2]:
            ch_emoji = '🟢' if chart['type'] == 'BULLISH' else '🔴'
            ch_name = esc(chart['pattern'].replace('_', ' ').title())
            ch_status = '✅' if chart.get('confirmed') else '⏳'
            ch_target = f" → ${esc(f"{chart['target']:,.0f}")}" if chart.get('target') else ''
            pa_lines.append(f"• {ch_emoji} {ch_status} {ch_name}{ch_target}")

    # Other patterns (Triangles, Harmonics)
    if smc_data and smc_data.get('patterns'):
        for p in smc_data['patterns'][:2]:
            p_name = esc(p.get('name', 'Unknown'))
            p_target = f" → ${esc(f"{p['target']:,.0f}")}" if p.get('target') else ''
            p_emoji = '🟢' if 'bull' in p.get('type','').lower() else ('🔴' if 'bear' in p.get('type','').lower() else '⚪')
            pa_lines.append(f"• {p_emoji} {p_name}{p_target}")
    
    if pa_lines:
        pa_info = "\n\n🕯️ *Price Action:*\n" + "\n".join(pa_lines)

    # Recommendation
    dir_emoji = "🟢" if plan['direction'] == 'LONG' else ("🔴" if plan['direction'] == 'SHORT' else "⚪")
    
    # Get win probabilities for both directions
    long_prob = plan.get('long_win_prob')
    short_prob = plan.get('short_win_prob')
    
    def format_prob(prob, label):
        if prob is None:
            return f"{esc(label)}: N\\/A"
        pct = prob * 100
        emoji = "🟢" if pct >= 65 else ("🟡" if pct >= 50 else "🔴")
        pct_str = f"{pct:.0f}%"
        return f"{emoji} {esc(label)}: {esc(pct_str)}"
    
    rec_info = ""
    # Pre-format score
    score_str = f"{plan['score']:+d}"
    
    if plan['direction'] in ['LONG', 'SHORT']:
        active_plan = plan['long'] if plan['direction'] == 'LONG' else plan['short']
        entry = active_plan.get('entry', 0)
        sl = active_plan.get('sl', 0)
        tp1 = active_plan.get('tp1', 0)
        tp2 = active_plan.get('tp2', 0)
        tp3 = active_plan.get('tp3', 0)
        rr = active_plan.get('rr', 0)
        win = active_plan.get('winrate', 0)
        
        # Pre-format values
        entry_str = f"{entry:,.0f}"
        sl_str = f"{sl:,.0f}"
        tp1_str = f"{tp1:,.0f}"
        tp2_str = f"{tp2:,.0f}"
        tp3_str = f"{tp3:,.0f}"
        rr_str = f"{rr:.2f}"
        win_str = f"{win:.0f}%"
        
        rec_info = f"""
{dir_emoji} *KHUYẾN NGHỊ*
\\>\\>\\> {esc(plan['direction'])} tại ${esc(entry_str)}
   SL: ${esc(sl_str)}
   TP1: ${esc(tp1_str)} \\| TP2: ${esc(tp2_str)} \\| TP3: ${esc(tp3_str)}
   R\\/R: 1:{esc(rr_str)} \\| Thắng: {esc(win_str)}
   Điểm: {esc(score_str)}
   {format_prob(long_prob, 'LONG')} \\| {format_prob(short_prob, 'SHORT')}"""
    else:
        rec_info = f"""
{dir_emoji} *KHUYẾN NGHỊ*
   Chờ tín hiệu rõ ràng
   Điểm: {esc(score_str)}
   {format_prob(long_prob, 'LONG')} \\| {format_prob(short_prob, 'SHORT')}"""

    # Special Analysis Summary
    special_lines = []
    gann = plan.get('gann')
    if gann:
        special_lines.append(f"Gann: {esc(gann['signal'])}")
    
    lunar = plan.get('lunar')
    if lunar:
        phase_name = lunar.get('phase', {}).get('phase_name', 'Unknown')
        special_lines.append(f"Lunar: {esc(phase_name)}")
        
    special_summary = ""
    if special_lines:
        special_summary = "\n🔮 " + " \\| ".join(special_lines)

    # Final Caption
    caption = f"""📊 *Phân Tích BTC\\/USDT*
🕐 {esc(datetime.now().strftime("%d/%m/%Y %H:%M"))}

💰 *Giá:* ${esc(f"{current_price:,.0f}")}

*Xu hướng Đa khung:* {" ".join(tf_lines)}

{summary_info}
{rec_info}
{ms_info}
{tdi_info}{special_summary}
{pa_info}

ℹ️ _Xem chi tiết SMC, Fibo & OrderBook ở tin nhắn sau\\._"""
    
    return caption


def create_detailed_tech_analysis_message(plan, derivatives_data=None, smc_data=None, ob_walls=None, adv_fibo=None):
    """
    Create a detailed text message with SMC, Fibo, Walls, Derivatives.
    """
    # 1. Derivatives
    deriv_info = ""
    if derivatives_data and derivatives_data.get('funding_rate_pct') is not None:
        fr = derivatives_data.get('funding_rate_pct', 0)
        ls = derivatives_data.get('ls_ratio', 0)
        long_pct = derivatives_data.get('long_ratio', 0) * 100
        oi = derivatives_data.get('open_interest', 0)
        
        fr_signal = "⚠️ Long Crowded" if fr > 0.05 else ("🟢 Short Crowded" if fr < -0.01 else "Neutral")
        
        # Pre-format derivatives values
        fr_str = f"{fr:.4f}%"
        ls_str = f"{ls:.2f}"
        lp_str = f"{long_pct:.0f}%"
        oi_str = f"{oi:,.0f}"

        deriv_info = f"""
📊 *Dữ liệu Phái sinh:*
• Funding: {esc(fr_str)} \\({esc(fr_signal)}\\)
• L/S: {esc(ls_str)} \\({esc(lp_str)} Long\\)
• OI: {esc(oi_str)} BTC"""

    # 2. Order Book Walls (Compact)
    ob_info = ""
    if ob_walls:
        lines = []
        if ob_walls.get('sell_walls'):
            # Top 3 Sells
            sells = ob_walls['sell_walls'][:3]
            # Simplify: Format substrings first
            sell_items = []
            for w in sells:
                price_k = f"{w['price']/1000:.1f}k"
                vol = f"{w['volume']:.0f}"
                sell_items.append(f"${esc(price_k)}\\({esc(vol)}\\)")
            sell_str = ", ".join(sell_items)
            lines.append(f"🧱 Res: {sell_str}")
        
        if ob_walls.get('buy_walls'):
            # Top 3 Buys
            buys = ob_walls['buy_walls'][:3]
            buy_items = []
            for w in buys:
                price_k = f"{w['price']/1000:.1f}k"
                vol = f"{w['volume']:.0f}"
                buy_items.append(f"${esc(price_k)}\\({esc(vol)}\\)")
            buy_str = ", ".join(buy_items)
            lines.append(f"🛡️ Sup: {buy_str}")
            
        if lines:
             ob_info = f"\n📒 *Tường Order Book \\(Cá Voi\\):* " + " \\| ".join(lines)

    # 3. Special Analysis (Gann & Lunar)
    # Check if data is in plan (it should be passed in 'plan' dict)
    special_info = ""
    gann = plan.get('gann')
    lunar = plan.get('lunar')
    
    if gann or lunar:
        lines = []
        if gann:
            lines.append(f"• 📐 Gann: {esc(gann['signal'])} \\({esc(gann['desc'])}\\)")
            
        if lunar:
            phase = lunar.get('phase', {})
            sig = lunar.get('signal', {})
            p_name = esc(phase.get('phase_name', 'Unknown'))
            s_sent = esc(sig.get('sentiment', 'NEUTRAL'))
            lines.append(f"• 🌑 Lunar: {p_name} \\({s_sent}\\)")
            
            # Mercury Retrograde
            merc = lunar.get('mercury', {})
            if merc.get('is_retrograde'):
                lines.append(f"• ⚠️ Mercury Retrograde: TRUE \\(Caution\\)")

        if lines:
            special_info = "\n🔮 *Special Analysis:*\n" + "\n".join(lines) + "\n"

    # 4. SMC Info
    smc_info = ""
    if smc_data:
        wyckoff = smc_data.get('wyckoff', {})
        lines = []
        if wyckoff.get('phase'):
            conf_str = f"{wyckoff.get('confidence', 0)}%"
            lines.append(f"• Wyckoff: {esc(wyckoff['phase'])} \\({esc(conf_str)}\\)")
            
        # MMXM Info
        mmxm = smc_data.get('mmxm')
        if mmxm:
            mmxm_desc = f"{mmxm['type']} ({mmxm['phase']})"
            mmxm_line = f"• MMXM: {esc(mmxm_desc)}"
            if mmxm.get('target'):
                tgt_val = f"${mmxm['target']:,.0f}"
                mmxm_line += f" → {esc(tgt_val)}"
            lines.append(mmxm_line)
            
        # PO3 Info
        po3 = smc_data.get('po3')
        if po3 and po3.get('bias') != 'NEUTRAL':
            phase_clean = po3['phase'].replace(' (Bullish)', '').replace(' (Bearish)', '')
            po3_desc = f"{phase_clean} ({po3['bias']})"
            lines.append(f"• PO3: {esc(po3_desc)}")
            
        # IPDA Info
        ipda = smc_data.get('ipda')
        if ipda:
            ipda_line = f"• IPDA: {esc(ipda['context'])} \\({esc(ipda['bias'])}\\)"
            lines.append(ipda_line)

        # Silver Bullet
        sb = smc_data.get('silver_bullet')
        if sb:
            latest_sb = sb[0]
            sb_price = f"{latest_sb['price']:,.0f}"
            sb_icon = "🔫" 
            lines.append(f"• {sb_icon} Silver Bullet: {esc(latest_sb['session'])} @ ${esc(sb_price)}")

        # Unicorn
        unicorns = smc_data.get('unicorns')
        if unicorns:
            latest_u = unicorns[-1]
            u_price = f"{latest_u['price']:,.0f}"
            u_type = "Bullish" if 'Bullish' in latest_u['type'] else "Bearish"
            u_icon = "🦄"
            lines.append(f"• {u_icon} Unicorn: {esc(u_type)} @ ${esc(u_price)} \\(Đảo chiều mạnh\\)")

        # Rejection Block
        rbs = smc_data.get('rejection_blocks', [])
        if rbs:
            latest_rb = rbs[-1]
            rb_price = f"{latest_rb['price']:,.0f}"
            rb_type = "Bullish" if 'Bullish' in latest_rb['type'] else "Bearish"
            rb_icon = "🕯️"
            lines.append(f"• {rb_icon} Rejection: {esc(rb_type)} @ ${esc(rb_price)} \\(Rút chân\\)")

        # Propulsion Block
        pbs = smc_data.get('propulsion_blocks', [])
        if pbs:
            latest_pb = pbs[-1]
            pb_price = f"{latest_pb['price']:,.0f}"
            pb_type = "Bullish" if 'Bullish' in latest_pb['type'] else "Bearish"
            pb_icon = "🚀"
            lines.append(f"• {pb_icon} Propulsion: {esc(pb_type)} @ ${esc(pb_price)} \\(Tiếp diễn xu hướng\\)")

        # SD Targets
        sd = smc_data.get('sd_levels', {})
        if sd:
            tp2 = sd.get('2.0 SD') or sd.get('-2.0 SD')
            tp25 = sd.get('2.5 SD') or sd.get('-2.5 SD')
            if tp2 or tp25:
                sd_msg = "• 🎯 TP \\(SD\\): "
                if tp2: sd_msg += f"2\\.0\\(${esc(f'{tp2:,.0f}')}\\) "
                if tp25: sd_msg += f"\\| 2\\.5\\(${esc(f'{tp25:,.0f}')}\\)"
                sd_msg += " \\(Mục tiêu giá\\)"
                lines.append(sd_msg)

        # IFVG
        ifvgs = smc_data.get('ifvgs', [])
        if ifvgs:
            ifvg = ifvgs[0]
            ifvg_price = f"{ifvg['bottom']:,.0f}"
            lines.append(f"• IFVG: {esc(ifvg['type'])} ${esc(ifvg_price)} \\(Lật S/R\\)")

        # Blocks
        breakers = smc_data.get('breakers', [])
        mitigations = smc_data.get('mitigations', [])
        block_lines = []
        for b in breakers[:1]:
            b_price = f"{b['price']:,.0f}"
            block_lines.append(f"Breaker ${esc(b_price)} \\(Phá vỡ\\)")
        for m in mitigations[:1]:
            m_price = f"{m['price']:,.0f}"
            block_lines.append(f"Mitigation ${esc(m_price)} \\(Giảm thiểu\\)")
        if block_lines:
            lines.append(f"• Blocks: {', '.join(block_lines)}")

        # General Signals (OB Rejection, etc.)
        sig_lines = []
        seen_sigs = set() # Avoid duplicates
        for cat, desc, typ in smc_data.get('signals', []):
            # Skip already handled categories
            if cat in ['Structure', 'Wyckoff', 'IPDA', 'FVG']: continue
            
            # Create unique key
            sig_key = f"{cat}:{desc}"
            if sig_key in seen_sigs: continue
            seen_sigs.add(sig_key)
            
            emoji = "🟢" if typ == 'BULL' else ("🔴" if typ == 'BEAR' else "⚪")
            sig_lines.append(f"• {emoji} {esc(cat)}: {esc(desc)}")
            
        if sig_lines:
             lines.append(f"\n🔔 *Tín hiệu khác:*")
             lines.extend(sig_lines)

        if lines:
            smc_info = "\n\n🧠 *Phân tích SMC:*\n" + "\n".join(lines)
            
    # 4. Advanced Fibonacci Info
    fibo_info = ""
    if adv_fibo:
        lines = []
        # Confluence
        confluences = adv_fibo.get('confluence', [])
        if confluences:
             lines.append(f"• 🧱 Confluence \\(Bê\\-tông\\):")
             for c in confluences[:2]:
                 p_val = f"{c['price']:,.0f}"
                 lines.append(f"  \\- ${esc(p_val)} \\({esc(c['levels'])}\\)")
        
        # ABCD
        abcd = adv_fibo.get('abcd')
        if abcd:
            tp_val = f"{abcd['targets']['1.0']:,.0f}"
            lines.append(f"• 📉 ABCD: {esc(abcd['type'])} \\(TP: ${esc(tp_val)}\\)")
            
        # Wicks
        wicks = adv_fibo.get('wicks', [])
        if wicks:
            lines.append(f"• 🕯️ Inst\\. Levels \\(50% Râu\\):")
            for w in wicks:
                p_val = f"{w['price']:,.0f}"
                lines.append(f"  \\- ${esc(p_val)} \\({esc(w['type'])}\\)")
                
        if lines:
            fibo_info = "\n\n🌀 *Fibo Nâng Cao \\(Phase 3\\):*\n" + "\n".join(lines)
            
    # Pattern Detection Info
    patterns = smc_data.get('patterns', []) if smc_data else []
    pattern_lines = []
    if patterns:
        for p in patterns[:2]:
            target = p.get('target')
            target_str = ""
            if target:
                t_val = f"{target:,.0f}"
                target_str = f" → ${esc(t_val)}"
            pattern_lines.append(f"• {esc(p['name'])}{target_str}")
    else:
        pattern_lines.append("• None detected")
    pattern_info = "\n\n📐 *Mô hình Nến/Giá:*\n" + "\n".join(pattern_lines)
    
    # Combine Header
    msg = f"""🔍 *PHÂN TÍCH KỸ THUẬT CHI TIẾT*
    
{deriv_info}{ob_info}{smc_info}{fibo_info}{special_info}{pattern_info}"""
    
    return msg




def create_smt_message(smt_data):
    """Create a separate message for SMT Divergence"""
    if not smt_data:
        return None
    
    smt_emoji = "🟢" if smt_data['type'] == 'BULLISH' else "🔴"
    case_short = smt_data.get('case', '').replace('_', ' ')
    
    msg = f"""{smt_emoji} *PHÁT HIỆN PHÂN KỲ SMT*
🔹 *Loại:* {esc(smt_data['type'])}
🔹 *Trường hợp:* {esc(case_short)}
🔹 *Mô tả:* 
   {esc(smt_data['description'])}
   
⚠️ _Lưu ý: SMT là tín hiệu đảo chiều mạnh khi kết hợp với Quét Thanh Khoản \\(Sweep\\) hoặc Golden Pocket\\._"""
    return msg


def create_extended_analysis_message(plan):
    """Create a separate message for Golden Pocket and Elliott Wave strategies"""
    
    def format_prob(prob, label):
        if prob is None:
            return f"{esc(label)}: N\\/A"
        pct = prob * 100
        emoji = "🟢" if pct >= 65 else ("🟡" if pct >= 50 else "🔴")
        return f"{emoji} {esc(label)}: {pct:.0f}%"

    # Golden Pocket Strategy
    gp_info = ""
    gp_strat = plan.get('golden_pocket_strategy')
    if gp_strat:
        gp = gp_strat.get('golden_pocket', {})
        gp_emoji = "🟢" if gp_strat['action'] == 'LONG' else ("🔴" if gp_strat['action'] == 'SHORT' else "⚪")
        
        gp_info = f"""
🏆 *CHIẾN LƯỢC GOLDEN POCKET \\(15m\\):*
• Xu hướng: {esc(gp_strat['trend'])}
• Vùng: ${esc(f"{gp.get('low', 0):,.0f}")} \\- ${esc(f"{gp.get('high', 0):,.0f}")}
{gp_emoji} {esc(gp_strat['action'])}"""

        # AI Win Probability
        gp_long = gp_strat.get('long_win_prob')
        gp_short = gp_strat.get('short_win_prob')
        if gp_long is not None and gp_short is not None:
            hypo_label = " _\\(Giả định\\)_" if not gp_strat['valid'] else ""
            gp_info += f"\n{format_prob(gp_long, 'LONG')} \\| {format_prob(gp_short, 'SHORT')}{hypo_label}"
        
        if gp_strat['valid']:
            gp_info += f"""
   Entry1: ${esc(f"{gp_strat.get('entry1', gp_strat['entry']):,.0f}")} 
   Entry2: ${esc(f"{gp_strat.get('entry2', gp_strat['entry']):,.0f}")} 
   SL: ${esc(f"{gp_strat['sl']:,.0f}")}
   TP1: ${esc(f"{gp_strat['tp1']:,.0f}")} \\| TP2: ${esc(f"{gp_strat['tp2']:,.0f}")}"""
        else:
            reason = gp_strat['reason']
            gp_info += f"\n   {esc(reason)}"
            if gp_strat.get('entry') and gp_strat.get('sl') and gp_strat.get('tp1'):
                gp_info += f"""
   _\\(Giả định nếu vào lệnh:\\)_
   Entry: ${esc(f"{gp_strat['entry']:,.0f}")}
   SL: ${esc(f"{gp_strat['sl']:,.0f}")} \\(TP1: ${esc(f"{gp_strat['tp1']:,.0f}")}\\)"""

    # Elliott Wave Fibo Strategy
    ew_info = ""
    ew_strat = plan.get('elliott_wave_fibo')
    if ew_strat:
        ote = ew_strat.get('ote_zone', {})
        ew_emoji = "🟢" if ew_strat['action'] == 'LONG' else ("🔴" if ew_strat['action'] == 'SHORT' else "⚪")
        
        ew_info = f"""
🌊 *SÓNG ELLIOTT FIBO \\(AI\\):*
• Sóng Đẩy: {esc(ew_strat.get('impulse_type', 'N/A'))}
• Vùng OTE: ${esc(f"{ote.get('low', 0):,.0f}")} \\- ${esc(f"{ote.get('high', 0):,.0f}")}
{ew_emoji} {esc(ew_strat['action'])}"""
        
        # AI Win Probability
        ew_long = ew_strat.get('long_win_prob')
        ew_short = ew_strat.get('short_win_prob')
        if ew_long is not None and ew_short is not None:
            hypo_label = " _\\(Giả định\\)_" if not ew_strat['valid'] else ""
            ew_info += f"\n{format_prob(ew_long, 'LONG')} \\| {format_prob(ew_short, 'SHORT')}{hypo_label}"
        
        if ew_strat['valid']:
            ew_info += f"""
   Entry: ${esc(f"{ew_strat['entry']:,.0f}")}
   SL: ${esc(f"{ew_strat['sl']:,.0f}")}
   TP1: ${esc(f"{ew_strat['tp1']:,.0f}")} \\| TP2: ${esc(f"{ew_strat['tp2']:,.0f}")}"""
        else:
            reason = ew_strat['reason']
            ew_info += f"\n   {esc(reason)}"
            if ew_strat.get('entry') and ew_strat.get('sl') and ew_strat.get('tp1'):
                ew_info += f"""
   _\\(Giả định nếu vào lệnh:\\)_
   Entry: ${esc(f"{ew_strat['entry']:,.0f}")}
   SL: ${esc(f"{ew_strat['sl']:,.0f}")} \\(TP1: ${esc(f"{ew_strat['tp1']:,.0f}")}\\)"""

    # Advanced Fibo Strategy (V2)
    af_info = ""
    af_strat = plan.get('advanced_fibo_strategy')
    if af_strat:
        af_emoji = "🟢" if af_strat['action'] == 'LONG' else ("🔴" if af_strat['action'] == 'SHORT' else "⚪")
        
        # Confidence stars (1-5 scale)
        score = af_strat.get('confidence_score', 0)
        stars = "⭐" * min(score, 5) if score > 0 else "—"
        
        # Counter-trend warning
        ct_warn = "\n⚠️ _COUNTER\\-TREND \\(Ngược xu hướng\\)_" if af_strat.get('counter_trend') else ""
        
        # Factors list
        factors = af_strat.get('factors', [])
        factors_str = ', '.join(esc(f) for f in factors) if factors else 'None'
        
        af_info = f"""
🌀 *CHIẾN LƯỢC FIBO NÂNG CAO \\(V2\\):*
• Xu hướng: {esc(af_strat.get('trend_context', 'N/A'))}
• RSI: {esc(str(af_strat.get('rsi', 'N/A')))}
• Yếu tố: {factors_str}
• Độ tin cậy: {stars} \\({score} điểm\\)
{af_emoji} {esc(af_strat['action'])}{ct_warn}"""

        if af_strat['valid']:
             af_info += f"""
   Entry: ${esc(f"{af_strat['entry']:,.0f}")}
   SL: ${esc(f"{af_strat['sl']:,.0f}")}
   TP1: ${esc(f"{af_strat['tp1']:,.0f}")} \\| TP2: ${esc(f"{af_strat['tp2']:,.0f}")}"""
        else:
             af_info += f"\n   {esc(af_strat['reason'])}"
        
        # AI Win Probability
        af_long = af_strat.get('long_win_prob')
        af_short = af_strat.get('short_win_prob')
        if af_long is not None and af_short is not None:
            hypo_label = " _\\(Giả định\\)_" if not af_strat['valid'] else ""
            af_info += f"\n{format_prob(af_long, 'LONG')} \\| {format_prob(af_short, 'SHORT')}{hypo_label}"

    # Fibo Inversion
    inv_info = ""
    fibo_inv = plan.get('fibo_inversion')
    if fibo_inv:
        inv_emoji = "📈" if fibo_inv['type'] == 'BULLISH' else "📉"
        targets_str = ""
        for r, price in fibo_inv['targets'].items():
            marker = "✅" if r in ['1.272', '1.618'] else "🔹"
            targets_str += f"\n   {marker} {esc(r)}: ${esc(f'{price:,.0f}')}"
        inv_info = f"""
📡 *FIBO INVERSION \\({esc(fibo_inv['type'])}\\):*
{inv_emoji} A\\=${esc(f"{fibo_inv['A']:,.0f}")} → B\\=${esc(f"{fibo_inv['B']:,.0f}")} → C\\=${esc(f"{fibo_inv['C']:,.0f}")}
• Correction Range: ${esc(f"{fibo_inv['correction_range']:,.0f}")}{targets_str}"""

    # SD Projections
    sd_info = ""
    concise_info = ""
    sd_proj = plan.get('sd_projections')
    if sd_proj:
        zones_str = ""
        target_sd_price = 0
        for z in sd_proj.get('zones', []):
            z_price = z['price']
            zones_str += f"\n   {z['emoji']} {esc(z['level'])}: ${esc(f'{z_price:,.0f}')}"
            if '2.5 SD' in z['level']:
                target_sd_price = z_price
        
        rev_str = ""
        if sd_proj.get('reversal_signal'):
            rev = sd_proj['reversal_signal']
            rev_price = rev['price']
            rev_str = f"\n🚨 _REVERSAL: {esc(rev['action'])} @ ${esc(f'{rev_price:,.0f}')}_"
        sd_info = f"""
📊 *SD PROJECTIONS \\({esc(sd_proj['source'])}\\):*
• Base: ${esc(f"{sd_proj['base_low']:,.0f}")} \\- ${esc(f"{sd_proj['base_high']:,.0f}")} \\(1 SD \\= ${esc(f"{sd_proj['sd_range']:,.0f}")}\\){zones_str}{rev_str}"""

        # Append other session ranges if available
        session_ranges = plan.get('smc', {}).get('session_ranges', {})
        other_sessions = []
        if 'london' in session_ranges:
            lon = session_ranges['london']
            l_low = esc(f"{lon.get('low', 0):,.0f}")
            l_high = esc(f"{lon.get('high', 0):,.0f}")
            other_sessions.append(f"• London: ${l_low} \\- ${l_high}")
            
        if 'new_york' in session_ranges:
            ny = session_ranges['new_york']
            n_low = esc(f"{ny.get('low', 0):,.0f}")
            n_high = esc(f"{ny.get('high', 0):,.0f}")
            other_sessions.append(f"• New York: ${n_low} \\- ${n_high}")
            
        if other_sessions:
            sd_info += "\n" + "\n".join(other_sessions)

        # Generate Concise Conclusion
        conclusions = []
        # Fibo Inv (Scalp)
        if fibo_inv:
            f_trend = fibo_inv['type']
            f_target = fibo_inv['targets'].get('1.618', 0)
            f_action = "LONG" if f_trend == 'BULLISH' else "SHORT"
            f_icon = "🟢" if f_trend == 'BULLISH' else "🔴"
            conclusions.append(f"{f_icon} *Ngắn hạn \\(Scalp\\):* {f_action} theo Fibo Inv → TP ${esc(f'{f_target:,.0f}')}")
        
        # SD Proj (Swing)
        s_dir = sd_proj['direction']
        s_action = "LONG" if s_dir == 'BULL' else "SHORT"
        s_icon = "🟢" if s_dir == 'BULL' else "🔴"
        # If target_sd_price is 0 (not found), default to something or skip?
        # Usually 2.5 SD exists.
        if target_sd_price:
            conclusions.append(f"{s_icon} *Trung hạn \\(Swing\\):* {s_action} về vùng Institutional → ${esc(f'{target_sd_price:,.0f}')}")
        
        # Reversal (Always show BOTH Bắt đỉnh AND Bắt đáy)
        sd_range = sd_proj.get('sd_range', 0)
        base_high = sd_proj.get('base_high', 0)
        base_low = sd_proj.get('base_low', 0)
        
        # Calculate 4.0 SD in both directions
        sd_4_up = base_low + sd_range * 4.0    # Peak (upside extreme)
        sd_4_down = base_high - sd_range * 4.0  # Bottom (downside extreme)
        
        if sd_4_up and sd_range > 0:
            conclusions.append(f"🔴 *Bắt đỉnh \\(Short\\):* Chờ phản ứng tại ${esc(f'{sd_4_up:,.0f}')} \\(\\+4\\.0 SD\\)")
        if sd_4_down and sd_range > 0:
            conclusions.append(f"🟢 *Bắt đáy \\(Long\\):* Chờ phản ứng tại ${esc(f'{sd_4_down:,.0f}')} \\(\\-4\\.0 SD\\)")
            
        if conclusions:
            concise_info = "\n\n💡 *KẾT LUẬN GỌN:*\n" + "\n".join(conclusions)

    # CRT + VSA (Multi-Timeframe)
    crt_info = ""
    crt_data = plan.get('crt_vsa')
    if crt_data:
        crt_tf = crt_data.get('timeframe', '1h').upper()
        crt_emoji = "🟢" if crt_data['signal'] == 'Buy' else "🔴"
        fresh_tag = " _\\(MỚI\\!\\)_" if crt_data.get('is_current') else ""
        e_str = esc(f"{crt_data['entry']:,.0f}")
        s_str = esc(f"{crt_data['sl']:,.0f}")
        t_str = esc(f"{crt_data['tp']:,.0f}")
        r_str = esc(f"{crt_data['rr']:.1f}")
        v_str = esc(f"{crt_data['vol_spike']:.1f}x")
        mtf = plan.get('crt_vsa_mtf', {})
        mtf_line = ""
        if mtf:
            b_e = "🟢" if mtf.get('bias') == 'BULLISH' else ('🔴' if mtf.get('bias') == 'BEARISH' else '⚪')
            mtf_line = f"\n{b_e} MTF: {esc(mtf.get('bias','N/A'))} \\(Bull {esc(str(mtf.get('bull_score',0)))}\\/Bear {esc(str(mtf.get('bear_score',0)))}\\)"
        crt_info = f"\n\n🎯 *CRT \\+ VSA \\({esc(crt_tf)}\\):*\n{crt_emoji} {esc(crt_data['signal'])}{fresh_tag}\n• Entry: ${e_str} \\| SL: ${s_str}\n• TP: ${t_str} \\| R\\/R: 1:{r_str}\n• Vol: {v_str} SMA20{mtf_line}"

    # VSA Context
    vsa_info = ""
    vsa_ctx = plan.get('vsa_context')
    if vsa_ctx and vsa_ctx.get('summary', '').find('No VSA') == -1 and vsa_ctx.get('summary'):
        vl = []
        nd = vsa_ctx.get('no_demand')
        if nd:
            nd_v = f"{nd['volume_ratio']:.1f}x"
            vl.append(f"⚠️ No Demand \\(SOW\\) Vol {esc(nd_v)}")
        ts = vsa_ctx.get('test_supply')
        if ts:
            sup = " \\[SUPPORT\\]" if ts.get('near_support') else ""
            vl.append(f"✅ Test Supply \\(SOS\\){sup}")
        sv = vsa_ctx.get('stopping_volume')
        if sv:
            sve = "🟢" if sv['bias'] == 'BULLISH' else "🔴"
            vl.append(f"{sve} Stopping Vol \\({esc(sv['bias'])}\\)")
        if vl:
            be = "🟢" if vsa_ctx['bias'] == 'BULLISH' else ('🔴' if vsa_ctx['bias'] == 'BEARISH' else '⚪')
            vl.append(f"Bias: {be} {esc(vsa_ctx['bias'])}")
            vsa_info = f"\n\n📊 *VSA Context \\(MTF\\):*\n• " + "\n• ".join(vl)

    msg = f"""🔍 *PHÂN TÍCH CHIẾN LƯỢC CHI TIẾT*
{gp_info}
{ew_info}
{af_info}
{crt_info}
{vsa_info}
{inv_info}
{sd_info}{concise_info}"""
    return msg
