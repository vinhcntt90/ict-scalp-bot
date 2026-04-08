"""
Google Sheets Logger
Ghi lịch sử scan vào Google Sheets qua Google Apps Script Web App
"""
import requests
import json
from datetime import datetime


class SheetsLogger:
    """Log scan results to Google Sheets via Apps Script Web App"""

    def __init__(self, web_app_url=None):
        """
        web_app_url: URL of the deployed Google Apps Script Web App
        Get this URL by deploying the Apps Script (see setup guide below)
        """
        self.web_app_url = web_app_url
        self.enabled = bool(web_app_url)

    def log_scan(self, analysis):
        """Send scan results to Google Sheets"""
        if not self.enabled:
            print("  [!] Google Sheets logger not configured (no web_app_url)")
            return False

        try:
            intent = analysis.get('intent', {})
            price = analysis.get('price', {})
            oi = analysis.get('oi', {})
            funding = analysis.get('funding', {})
            cvd = analysis.get('cvd', {})
            ls = analysis.get('ls', {})
            volatility = analysis.get('volatility', {})
            liquidation = analysis.get('liquidation', {})
            mtf = analysis.get('mtf', {})
            sr = analysis.get('sr', {})

            # Prepare row data
            row = {
                'timestamp': datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
                'price': price.get('price', 0),
                'price_change_1h': price.get('change_pct', 0),
                'oi_change_24h': oi.get('change_pct', 0),
                'oi_btc': oi.get('current_oi', 0),
                'oi_spike': 'YES' if oi.get('is_spike') else 'NO',
                'funding_pct': funding.get('current_pct', 0),
                'funding_sentiment': funding.get('sentiment', ''),
                'funding_consecutive_neg': funding.get('consecutive_negative', 0),
                'funding_consecutive_pos': funding.get('consecutive_positive', 0),
                'cvd_direction': cvd.get('cvd_direction', ''),
                'buy_dominance': cvd.get('buy_dominance', 50),
                'ls_retail': ls.get('current', 0),
                'ls_whale': ls.get('top_trader', 0),
                'verdict': intent.get('verdict', ''),
                'confidence': intent.get('confidence', 0),
                'action': intent.get('action', ''),
                'signals_count': len(intent.get('signals', [])),
                'signals': ' | '.join(intent.get('signals', []))[:500],  # Truncate
                'bb_squeeze': volatility.get('squeeze_level', 'NONE'),
                'liq_long_usd': liquidation.get('long_liq_usd', 0),
                'liq_short_usd': liquidation.get('short_liq_usd', 0),
                'tf_alignment': mtf.get('tf_alignment', ''),
                'nearest_resistance': sr.get('nearest_resistance', ''),
                'nearest_support': sr.get('nearest_support', ''),
            }

            r = requests.post(
                self.web_app_url,
                json=row,
                timeout=15,
                headers={'Content-Type': 'application/json'}
            )

            if r.status_code == 200:
                print(f"  ✅ Google Sheets: Logged scan at {row['timestamp']}")
                return True
            else:
                print(f"  ⚠️ Google Sheets: HTTP {r.status_code}")
                return False

        except Exception as e:
            print(f"  [!] Google Sheets error: {e}")
            return False
