import pandas as pd
import requests
from binance.client import Client
from .config import Config

class DataFetcher:
    def __init__(self):
        self.use_binance_lib = False
        self.client = None
        
        try:
            self.client = Client()
            self.use_binance_lib = True
            print("[*] Binance Client initialized successfully")
        except Exception as e:
            print(f"[!] Failed to init Binance Client: {e}")
            self.use_binance_lib = False

    def fetch_btc_data(self, timeframe='15m', limit=1000):
        """
        Fetch BTC/USDT data from Binance
        Returns DataFrame with index as Datetime
        """
        symbol = 'BTCUSDT'
        print(f"[*] Fetching {symbol} {timeframe} data (limit={limit})...")
        
        try:
            if self.use_binance_lib and self.client:
                # Use official library
                klines = self.client.get_klines(symbol=symbol, interval=timeframe, limit=limit)
                df = pd.DataFrame(klines, columns=[
                    'timestamp', 'open', 'high', 'low', 'close', 'volume',
                    'close_time', 'quote_asset_volume', 'trades', 
                    'taker_buy_base', 'taker_buy_quote', 'ignore'
                ])
                
                df['timestamp'] = pd.to_datetime(df['timestamp'], unit='ms')
                df.set_index('timestamp', inplace=True)
                
                # Convert to numeric
                for col in ['open', 'high', 'low', 'close', 'volume']:
                    df[col] = pd.to_numeric(df[col], errors='coerce')
                    
                # Add typical price if needed
                df['hl2'] = (df['high'] + df['low']) / 2
                df['hlc3'] = (df['high'] + df['low'] + df['close']) / 3
                
                return df
                
            else:
                # Fallback to direct REST API
                base_url = "https://api.binance.com/api/v3/klines"
                params = {
                    'symbol': symbol,
                    'interval': timeframe,
                    'limit': limit
                }
                response = requests.get(base_url, params=params, timeout=10)
                if response.status_code == 200:
                    data = response.json()
                    df = pd.DataFrame(data, columns=[
                        'timestamp', 'open', 'high', 'low', 'close', 'volume',
                        'close_time', 'quote_asset_volume', 'trades', 
                        'taker_buy_base', 'taker_buy_quote', 'ignore'
                    ])
                    df['timestamp'] = pd.to_datetime(df['timestamp'], unit='ms')
                    df.set_index('timestamp', inplace=True)
                    
                    for col in ['open', 'high', 'low', 'close', 'volume']:
                        df[col] = pd.to_numeric(df[col], errors='coerce')
                        
                    return df
                else:
                    print(f"[!] API Error: {response.text}")
                    return None
                    
        except Exception as e:
            print(f"  [!] Error fetching data: {e}")
            return None

    def fetch_order_book(self, limit=50):
        """
        Fetch Order Book (Depth) from Binance
        Returns: dict with 'bids' and 'asks' (price, qty)
        """
        symbol = 'BTCUSDT'
        try:
            if self.use_binance_lib and self.client:
                depth = self.client.get_order_book(symbol=symbol, limit=limit)
            else:
                base_url = "https://api.binance.com/api/v3/depth"
                response = requests.get(base_url, params={'symbol': symbol, 'limit': limit}, timeout=5)
                if response.status_code == 200:
                    depth = response.json()
                else:
                    print(f"  [!] API Error fetching order book: {response.text}")
                    return None
            
            # Process walls (simple large orders)
            return {
                'bids': [(float(p), float(q)) for p, q in depth['bids']],
                'asks': [(float(p), float(q)) for p, q in depth['asks']]
            }
        except Exception as e:
            print(f"  [!] Error fetching order book: {e}")
            return None

    def fetch_binance_futures_depth(self, limit=1000):
        """Fetch Binance Futures depth (BTCUSDT)"""
        try:
            url = f"https://fapi.binance.com/fapi/v1/depth?symbol=BTCUSDT&limit={limit}"
            r = requests.get(url, timeout=5)
            if r.status_code == 200:
                data = r.json()
                return data
            return None
        except Exception as e:
            print(f"  [!] Error fetching Binance Futures depth: {e}")
            return None

    def fetch_bybit_depth(self, category='spot', symbol='BTCUSDT', limit=200):
        """Fetch Bybit depth (Spot/Linear)"""
        try:
            # category: spot, linear, inverse
            url = f"https://api.bybit.com/v5/market/orderbook?category={category}&symbol={symbol}&limit={limit}"
            r = requests.get(url, timeout=5)
            if r.status_code == 200:
                data = r.json()
                if data['retCode'] == 0:
                    # Bybit returns bids/asks as [[price, size], ...] strings
                    return {
                        'bids': data['result']['b'][0:limit],
                        'asks': data['result']['a'][0:limit]
                    }
            return None
        except Exception as e:
            print(f"  [!] Error fetching Bybit {category} depth: {e}")
            return None

    def fetch_okx_depth(self, instId='BTC-USDT', limit=400):
        """Fetch OKX depth"""
        try:
            url = f"https://www.okx.com/api/v5/market/books?instId={instId}&sz={limit}"
            r = requests.get(url, timeout=5)
            if r.status_code == 200:
                data = r.json()
                if data['code'] == '0':
                    # OKX returns bids/asks as [[price, size, ...], ...]
                    # For SWAP, size is in contracts (usually 0.01 BTC for BTC-USDT-SWAP)
                    # For SPOT, size is in BTC
                    multiplier = 0.01 if 'SWAP' in instId else 1.0
                    
                    bids = []
                    for x in data['data'][0]['bids']:
                        bids.append([x[0], float(x[1]) * multiplier])
                        
                    asks = []
                    for x in data['data'][0]['asks']:
                        asks.append([x[0], float(x[1]) * multiplier])
                        
                    return {'bids': bids, 'asks': asks}
            return None
        except Exception as e:
            print(f"  [!] Error fetching OKX {instId} depth: {e}")
            return None

    def get_aggregated_walls(self, depths, threshold_multiplier=3.0, step=100):
        """
        Aggregate liquidity from multiple sources and find walls.
        depths: list of depth dicts {'bids': [], 'asks': []}
        """
        if not depths: return None
        
        try:
            # Helper to aggregate
            def aggregate_volume(orders, all_walls, is_bid=True):
                for p, q in orders:
                    vol = float(q)
                    if is_bid:
                        price_level = (int(float(p)) // step) * step 
                    else:
                        price_level = (int(float(p)) // step) * step
                    
                    all_walls[price_level] = all_walls.get(price_level, 0.0) + vol
                return all_walls

            bid_walls_agg = {}
            ask_walls_agg = {}
            
            valid_sources = 0
            for d in depths:
                if not d or 'bids' not in d or 'asks' not in d: continue
                aggregate_volume(d['bids'], bid_walls_agg, is_bid=True)
                aggregate_volume(d['asks'], ask_walls_agg, is_bid=False)
                valid_sources += 1
                
            if valid_sources == 0: return None
            
            # Calculate average volume of these aggregated levels
            if not bid_walls_agg or not ask_walls_agg: return None
            
            # Simple average of all non-zero levels
            avg_bid_vol = sum(bid_walls_agg.values()) / len(bid_walls_agg)
            avg_ask_vol = sum(ask_walls_agg.values()) / len(ask_walls_agg)
            
            final_buy_walls = []
            for p, vol in bid_walls_agg.items():
                if vol > avg_bid_vol * threshold_multiplier: 
                    final_buy_walls.append({
                        'price': p, 
                        'volume': vol,
                        'strength': vol/avg_bid_vol if avg_bid_vol > 0 else 0
                    })
            
            final_sell_walls = []
            for p, vol in ask_walls_agg.items():
                if vol > avg_ask_vol * threshold_multiplier:
                    final_sell_walls.append({
                        'price': p, 
                        'volume': vol,
                        'strength': vol/avg_ask_vol if avg_ask_vol > 0 else 0
                    })
            
            # Sort
            final_buy_walls.sort(key=lambda x: x['volume'], reverse=True)
            final_sell_walls.sort(key=lambda x: x['volume'], reverse=True)
            
            return {
                'buy_walls': final_buy_walls[:5], # Return top 5
                'sell_walls': final_sell_walls[:5]
            }
        except Exception as e:
            print(f"  [!] Error aggregating walls: {e}")
            import traceback
            traceback.print_exc()
            return None

    def get_order_book_walls(self, depth, threshold_multiplier=3.0, step=100):
        # Legacy wrapper if only 1 depth passed
        return self.get_aggregated_walls([depth], threshold_multiplier, step)

    def fetch_derivatives_data(self):
        """
        Fetch Funding Rate, Open Interest, Long/Short Ratio from Binance Futures
        No API key required - Public endpoints
        """
        data = {
            'funding_rate': None,
            'funding_rate_pct': None,
            'open_interest': None,
            'open_interest_usd': None,
            'long_ratio': None,
            'short_ratio': None,
            'ls_ratio': None,
            'top_trader_ls_ratio': None,
        }
        
        try:
            # 1. Funding Rate
            r = requests.get(
                'https://fapi.binance.com/fapi/v1/fundingRate',
                params={'symbol': 'BTCUSDT', 'limit': 1},
                timeout=10
            )
            if r.status_code == 200:
                fr_data = r.json()[0]
                data['funding_rate'] = float(fr_data['fundingRate'])
                data['funding_rate_pct'] = data['funding_rate'] * 100  # Convert to percentage
                data['mark_price'] = float(fr_data.get('markPrice', 0))
            
            # 2. Open Interest
            r = requests.get(
                'https://fapi.binance.com/fapi/v1/openInterest',
                params={'symbol': 'BTCUSDT'},
                timeout=10
            )
            if r.status_code == 200:
                oi_data = r.json()
                data['open_interest'] = float(oi_data['openInterest'])
                # Estimate USD value
                if data.get('mark_price'):
                    data['open_interest_usd'] = data['open_interest'] * data['mark_price']
            
            # 3. Global Long/Short Ratio (Retail traders)
            r = requests.get(
                'https://fapi.binance.com/futures/data/globalLongShortAccountRatio',
                params={'symbol': 'BTCUSDT', 'period': '1h', 'limit': 1},
                timeout=10
            )
            if r.status_code == 200:
                ls_data = r.json()[0]
                data['long_ratio'] = float(ls_data['longAccount'])
                data['short_ratio'] = float(ls_data['shortAccount'])
                data['ls_ratio'] = float(ls_data['longShortRatio'])
            
            # 4. Top Trader Long/Short Ratio (Whales)
            r = requests.get(
                'https://fapi.binance.com/futures/data/topLongShortAccountRatio',
                params={'symbol': 'BTCUSDT', 'period': '1h', 'limit': 1},
                timeout=10
            )
            if r.status_code == 200:
                top_data = r.json()[0]
                data['top_trader_ls_ratio'] = float(top_data['longShortRatio'])
                data['top_long_ratio'] = float(top_data['longAccount'])
                data['top_short_ratio'] = float(top_data['shortAccount'])
                
        except Exception as e:
            print(f"  [!] Error fetching derivatives data: {e}")
        
        return data

    def get_derivatives_signal(self, derivatives_data):
        """
        Analyze derivatives data and return trading signals
        """
        signals = []
        score = 0
        
        fr = derivatives_data.get('funding_rate_pct', 0) or 0
        ls_ratio = derivatives_data.get('ls_ratio', 1) or 1
        top_ls = derivatives_data.get('top_trader_ls_ratio', 1) or 1
        
        # 1. Funding Rate Analysis
        if fr > 0.05:  # High positive funding = too many longs
            signals.append(('Funding', f'{fr:.4f}% - Long Crowded', 'BEAR'))
            score -= 1
        elif fr < -0.01:  # Negative funding = shorts paying longs
            signals.append(('Funding', f'{fr:.4f}% - Short Crowded', 'BULL'))
            score += 1
        else:
            signals.append(('Funding', f'{fr:.4f}% - Neutral', 'NEUT'))
        
        # 2. Long/Short Ratio (Retail)
        if ls_ratio > 2.5:  # Retail too long
            signals.append(('L/S Ratio', f'{ls_ratio:.2f} ({derivatives_data.get("long_ratio", 0)*100:.0f}% Long) - Bearish', 'BEAR'))
            score -= 1
        elif ls_ratio < 1.2:  # Retail too short
            signals.append(('L/S Ratio', f'{ls_ratio:.2f} ({derivatives_data.get("short_ratio", 0)*100:.0f}% Short) - Bullish', 'BULL'))
            score += 1
        else:
            long_pct = derivatives_data.get('long_ratio', 0) * 100
            signals.append(('L/S Ratio', f'{ls_ratio:.2f} ({long_pct:.0f}% Long)', 'NEUT'))
        
        # 3. Top Trader (Whales) Analysis
        if top_ls > 2.0:  # Whales are long
            signals.append(('Whales', f'L/S {top_ls:.2f} - Bullish', 'BULL'))
            score += 1
        elif top_ls < 1.0:  # Whales are short
            signals.append(('Whales', f'L/S {top_ls:.2f} - Bearish', 'BEAR'))
            score -= 1
        else:
            signals.append(('Whales', f'L/S {top_ls:.2f} - Neutral', 'NEUT'))
        
        return {
            'signals': signals,
            'score': score,
            'funding_rate': fr,
            'ls_ratio': ls_ratio,
            'top_ls_ratio': top_ls,
        }

    def fetch_oi_history(self, symbol='BTCUSDT', period='5m', limit=30):
        """Fetch Open Interest history from Binance Futures"""
        try:
            r = requests.get(
                'https://fapi.binance.com/futures/data/openInterestHist',
                params={'symbol': symbol, 'period': period, 'limit': limit},
                timeout=10
            )
            if r.status_code == 200:
                data = r.json()
                df = pd.DataFrame(data)
                df['timestamp'] = pd.to_datetime(df['timestamp'], unit='ms')
                df['sumOpenInterest'] = pd.to_numeric(df['sumOpenInterest'])
                df['sumOpenInterestValue'] = pd.to_numeric(df['sumOpenInterestValue'])
                return df
            return None
        except Exception as e:
            print(f"  [!] Error fetching OI history: {e}")
            return None

    def fetch_funding_history(self, symbol='BTCUSDT', limit=20):
        """Fetch Funding Rate history from Binance Futures"""
        try:
            r = requests.get(
                'https://fapi.binance.com/fapi/v1/fundingRate',
                params={'symbol': symbol, 'limit': limit},
                timeout=10
            )
            if r.status_code == 200:
                data = r.json()
                df = pd.DataFrame(data)
                df['fundingTime'] = pd.to_datetime(df['fundingTime'], unit='ms')
                df['fundingRate'] = pd.to_numeric(df['fundingRate'])
                if 'markPrice' in df.columns:
                    df['markPrice'] = pd.to_numeric(df['markPrice'])
                return df
            return None
        except Exception as e:
            print(f"  [!] Error fetching funding history: {e}")
            return None

    def fetch_taker_buy_sell_volume(self, symbol='BTCUSDT', period='5m', limit=30):
        """Fetch Taker Buy/Sell Volume from Binance Futures for CVD calculation"""
        try:
            r = requests.get(
                'https://fapi.binance.com/futures/data/takerlongshortRatio',
                params={'symbol': symbol, 'period': period, 'limit': limit},
                timeout=10
            )
            if r.status_code == 200:
                data = r.json()
                df = pd.DataFrame(data)
                df['timestamp'] = pd.to_datetime(df['timestamp'], unit='ms')
                df['buyVol'] = pd.to_numeric(df['buyVol'])
                df['sellVol'] = pd.to_numeric(df['sellVol'])
                df['buySellRatio'] = pd.to_numeric(df['buySellRatio'])
                return df
            return None
        except Exception as e:
            print(f"  [!] Error fetching taker volume: {e}")
            return None

    def fetch_ls_ratio_history(self, symbol='BTCUSDT', period='5m', limit=30):
        """Fetch Global Long/Short Account Ratio history"""
        try:
            r = requests.get(
                'https://fapi.binance.com/futures/data/globalLongShortAccountRatio',
                params={'symbol': symbol, 'period': period, 'limit': limit},
                timeout=10
            )
            if r.status_code == 200:
                data = r.json()
                df = pd.DataFrame(data)
                df['timestamp'] = pd.to_datetime(df['timestamp'], unit='ms')
                df['longShortRatio'] = pd.to_numeric(df['longShortRatio'])
                df['longAccount'] = pd.to_numeric(df['longAccount'])
                df['shortAccount'] = pd.to_numeric(df['shortAccount'])
                return df
            return None
        except Exception as e:
            print(f"  [!] Error fetching L/S ratio history: {e}")
            return None

    def fetch_symbol_data(self, symbol='ETHUSDT', timeframe='15m', limit=150):
        """
        Fetch klines for any symbol from Binance.
        Used for SMT Divergence (fetching ETH data to compare with BTC).
        """
        print(f"[*] Fetching {symbol} {timeframe} data (limit={limit})...")
        try:
            if self.use_binance_lib and self.client:
                klines = self.client.get_klines(symbol=symbol, interval=timeframe, limit=limit)
            else:
                base_url = "https://api.binance.com/api/v3/klines"
                params = {'symbol': symbol, 'interval': timeframe, 'limit': limit}
                response = requests.get(base_url, params=params, timeout=10)
                if response.status_code != 200:
                    print(f"  [!] API Error: {response.text}")
                    return None
                klines = response.json()

            df = pd.DataFrame(klines, columns=[
                'timestamp', 'open', 'high', 'low', 'close', 'volume',
                'close_time', 'quote_asset_volume', 'trades',
                'taker_buy_base', 'taker_buy_quote', 'ignore'
            ])
            df['timestamp'] = pd.to_datetime(df['timestamp'], unit='ms')
            df.set_index('timestamp', inplace=True)
            for col in ['open', 'high', 'low', 'close', 'volume']:
                df[col] = pd.to_numeric(df[col], errors='coerce')
            return df

        except Exception as e:
            print(f"  [!] Error fetching {symbol} data: {e}")
            return None

# Global instance
fetcher = DataFetcher()

def get_btc_data(timeframe='15m', limit=1000):
    return fetcher.fetch_btc_data(timeframe, limit)

def get_eth_data(timeframe='15m', limit=150):
    """Fetch ETHUSDT data for SMT Divergence analysis."""
    return fetcher.fetch_symbol_data('ETHUSDT', timeframe, limit)

def get_derivatives_data():
    return fetcher.fetch_derivatives_data()

def get_derivatives_signal(derivatives_data):
    return fetcher.get_derivatives_signal(derivatives_data)

def fetch_order_book(limit=50):
    return fetcher.fetch_order_book(limit)

def fetch_binance_futures_depth(limit=1000):
   return fetcher.fetch_binance_futures_depth(limit)

def fetch_bybit_depth(category='spot', symbol='BTCUSDT', limit=200):
   return fetcher.fetch_bybit_depth(category, symbol, limit)

def fetch_okx_depth(instId='BTC-USDT', limit=400):
   return fetcher.fetch_okx_depth(instId, limit)

def get_aggregated_walls(depths, threshold_multiplier=3.0, step=100):
    return fetcher.get_aggregated_walls(depths, threshold_multiplier, step)

def get_order_book_walls(depth, threshold_multiplier=3.0, step=100):
    # Backward compatibility
    return fetcher.get_order_book_walls(depth, threshold_multiplier, step)

