"""
Binance Futures WebSocket Client — Multi-Symbol
================================================
Nhận giá + kline real-time cho nhiều symbols (BTC, ETH, ...).
DataFrame caching: 200 bars per symbol per TF.
Auto reconnect khi mất kết nối.

Usage:
    ws = BinanceWS(symbols=['btcusdt', 'ethusdt'])
    ws.start()
    ws.init_cache(fetch_fn)
    price = ws.get_price('btcusdt')
    df = ws.get_cached_df('btcusdt', '15m')
    ws.stop()
"""
import json
import time
import threading
import logging
import numpy as np
import pandas as pd
import websocket

logger = logging.getLogger('scalp')

CACHE_SIZE = 200


class BinanceWS:
    """Binance Futures WebSocket — multi-symbol giá + kline + DataFrame cache."""

    WS_URL = "wss://fstream.binance.com/stream"

    def __init__(self, symbols=None, testnet=False):
        if symbols is None:
            symbols = ['btcusdt']
        self.symbols = [s.lower() for s in symbols]

        self._prices = {}           # {symbol: price}
        self._kline_closed = {}     # {symbol_interval: True/False}
        self._connected = False
        self._ws = None
        self._thread = None
        self._lock = threading.Lock()
        self._stop_event = threading.Event()
        self._last_msg_time = 0
        self._reconnect_count = 0

        # DataFrame cache: {symbol: {interval: DataFrame}}
        self._cache = {}
        for sym in self.symbols:
            self._cache[sym] = {'5m': None, '15m': None, '1h': None}
        self._cache_ready = False

        if testnet:
            self.WS_URL = "wss://stream.binancefuture.com/stream"

        # Build streams for all symbols
        self._streams = []
        for sym in self.symbols:
            self._streams.extend([
                f"{sym}@markPrice@1s",
                f"{sym}@kline_5m",
                f"{sym}@kline_15m",
                f"{sym}@kline_1h",
            ])

    def start(self):
        """Start WebSocket in background thread."""
        if self._thread and self._thread.is_alive():
            return
        self._stop_event.clear()
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()
        for _ in range(50):
            if self._connected and any(self._prices.values()):
                break
            time.sleep(0.1)
        for sym in self.symbols:
            p = self._prices.get(sym)
            if p:
                logger.info(f"  📡 WS {sym.upper()}: ${p:,.2f}")
        if not any(self._prices.values()):
            logger.info(f"  ⚠️ WebSocket: waiting for prices...")

    def stop(self):
        """Stop WebSocket."""
        self._stop_event.set()
        if self._ws:
            try:
                self._ws.close()
            except:
                pass
        self._connected = False

    def init_cache(self, fetch_fn=None):
        """
        Initialize DataFrame cache via REST API (one-time at startup).
        fetch_fn: callable(interval, limit, symbol) → DataFrame
        """
        if fetch_fn is None:
            logger.info("  ⚠️ No fetch function for cache init")
            return

        for sym in self.symbols:
            symbol_upper = sym.upper() + ('USDT' if 'usdt' not in sym else '')
            sym_key = sym if 'usdt' in sym else sym + 'usdt'
            for tf in ['5m', '15m', '1h']:
                try:
                    df = fetch_fn(tf, CACHE_SIZE, symbol=symbol_upper)
                    if df is not None and len(df) > 0:
                        with self._lock:
                            self._cache[sym_key][tf] = df.copy()
                        logger.info(f"  📦 Cache {sym.upper()} {tf}: {len(df)} bars")
                    else:
                        logger.info(f"  ⚠️ Cache {sym.upper()} {tf}: no data")
                except Exception as e:
                    logger.info(f"  ❌ Cache {sym.upper()} {tf} error: {e}")

        with self._lock:
            self._cache_ready = all(
                self._cache[sym][tf] is not None and len(self._cache[sym][tf]) > 0
                for sym in self.symbols
                for tf in ['5m', '15m', '1h']
            )
        if self._cache_ready:
            total = len(self.symbols) * 3
            logger.info(f"  ✅ All caches ready ({total} streams × 200 bars)!")

    def get_cached_df(self, symbol, interval):
        """Get cached DataFrame (thread-safe copy)."""
        sym = symbol.lower().replace('usdt', '') + 'usdt' if 'usdt' not in symbol.lower() else symbol.lower()
        with self._lock:
            cache = self._cache.get(sym, {})
            df = cache.get(interval)
            if df is not None:
                return df.copy()
        return None

    def is_cache_ready(self):
        """Check if all caches are initialized."""
        with self._lock:
            return self._cache_ready

    def get_price(self, symbol=None):
        """Get latest price for symbol (thread-safe)."""
        with self._lock:
            if symbol:
                sym = symbol.lower().replace('usdt', '') + 'usdt' if 'usdt' not in symbol.lower() else symbol.lower()
                return self._prices.get(sym)
            # Backward compat: return first symbol price
            for p in self._prices.values():
                if p:
                    return p
            return None

    def get_kline_15m_closed(self, symbol=None):
        """Check if a new 15m candle just closed. Resets flag."""
        with self._lock:
            if symbol:
                sym = symbol.lower().replace('usdt', '') + 'usdt' if 'usdt' not in symbol.lower() else symbol.lower()
                key = f"{sym}_15m"
            else:
                key = f"{self.symbols[0]}_15m"
            if self._kline_closed.get(key, False):
                self._kline_closed[key] = False
                return True
            return False

    def get_any_15m_closed(self):
        """Check if ANY symbol had a 15m candle close. Returns list of symbols."""
        closed = []
        with self._lock:
            for sym in self.symbols:
                key = f"{sym}_15m"
                if self._kline_closed.get(key, False):
                    self._kline_closed[key] = False
                    closed.append(sym)
        return closed

    def get_kline_5m_closed(self, symbol=None):
        """Check if a new 5m candle just closed. Resets flag."""
        with self._lock:
            if symbol:
                sym = symbol.lower().replace('usdt', '') + 'usdt' if 'usdt' not in symbol.lower() else symbol.lower()
                key = f"{sym}_5m"
            else:
                key = f"{self.symbols[0]}_5m"
            if self._kline_closed.get(key, False):
                self._kline_closed[key] = False
                return True
            return False

    def get_any_5m_closed(self):
        """Check if ANY symbol had a 5m candle close. Returns list of symbols."""
        closed = []
        with self._lock:
            for sym in self.symbols:
                key = f"{sym}_5m"
                if self._kline_closed.get(key, False):
                    self._kline_closed[key] = False
                    closed.append(sym)
        return closed

    def is_connected(self):
        """Check if WS is connected and receiving data."""
        with self._lock:
            if not self._connected:
                return False
            return (time.time() - self._last_msg_time) < 10

    def _append_candle(self, symbol, interval, k):
        """Append closed candle to cached DataFrame (called within lock)."""
        try:
            ts = pd.Timestamp(k['t'], unit='ms')
            cache = self._cache.get(symbol, {})
            df = cache.get(interval)
            if df is None:
                return

            if ts in df.index:
                df.loc[ts, ['open', 'high', 'low', 'close', 'volume']] = [
                    float(k['o']), float(k['h']), float(k['l']),
                    float(k['c']), float(k['v'])
                ]
            else:
                new_row = pd.DataFrame({
                    'open': [float(k['o'])],
                    'high': [float(k['h'])],
                    'low': [float(k['l'])],
                    'close': [float(k['c'])],
                    'volume': [float(k['v'])],
                }, index=[ts])
                df = pd.concat([df, new_row])
                if len(df) > CACHE_SIZE:
                    df = df.iloc[-CACHE_SIZE:]

            self._cache[symbol][interval] = df
        except Exception as e:
            logger.info(f"  ⚠️ Cache append {symbol}/{interval} error: {e}")

    def _run(self):
        """Main WS loop with auto reconnect."""
        while not self._stop_event.is_set():
            try:
                url = f"{self.WS_URL}?streams={'/'.join(self._streams)}"
                self._ws = websocket.WebSocketApp(
                    url,
                    on_message=self._on_message,
                    on_error=self._on_error,
                    on_close=self._on_close,
                    on_open=self._on_open,
                )
                self._ws.run_forever(ping_interval=30, ping_timeout=10)
            except Exception as e:
                logger.info(f"  ⚠️ WS error: {e}")

            if self._stop_event.is_set():
                break

            self._connected = False
            self._reconnect_count += 1
            wait = min(5 * self._reconnect_count, 30)
            logger.info(f"  🔄 WS reconnect #{self._reconnect_count} in {wait}s...")
            time.sleep(wait)

    def _on_open(self, ws):
        with self._lock:
            self._connected = True
            self._reconnect_count = 0
            self._last_msg_time = time.time()

    def _on_close(self, ws, close_status_code, close_msg):
        with self._lock:
            self._connected = False

    def _on_error(self, ws, error):
        logger.info(f"  ⚠️ WS error: {error}")

    def _on_message(self, ws, message):
        try:
            data = json.loads(message)
            stream = data.get('stream', '')
            payload = data.get('data', {})

            with self._lock:
                self._last_msg_time = time.time()

                # markPrice stream: {symbol}@markPrice@1s
                if 'markPrice' in stream:
                    sym = stream.split('@')[0]  # e.g. 'btcusdt'
                    self._prices[sym] = float(payload.get('p', 0))

                # kline stream: {symbol}@kline_{interval}
                elif 'kline' in stream:
                    sym = stream.split('@')[0]  # e.g. 'btcusdt'
                    k = payload.get('k', {})
                    interval = k.get('i', '')
                    is_closed = k.get('x', False)

                    if is_closed:
                        key = f"{sym}_{interval}"
                        self._kline_closed[key] = True
                        self._append_candle(sym, interval, k)

        except Exception:
            pass
