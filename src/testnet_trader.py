"""
Binance Futures Testnet Trading
================================
Gửi lệnh thật lên Binance Testnet (tiền ảo, giá thật).
Hỗ trợ: Market order, Set leverage, Position info.
"""
import hmac
import hashlib
import time
import requests
from urllib.parse import urlencode
from src.config import Config


class BinanceTestnet:
    """Binance Futures Testnet API wrapper."""

    BASE_URL = "https://testnet.binancefuture.com"

    def __init__(self):
        self.api_key = Config.BINANCE_TESTNET_KEY
        self.api_secret = Config.BINANCE_TESTNET_SECRET
        self.time_offset = 0
        self.session = requests.Session()
        self.session.headers.update({
            'X-MBX-APIKEY': self.api_key
        })
        self._sync_time()
        print(f"  🔑 Binance Testnet connected")

    def _sync_time(self):
        """Sync local time with server time."""
        try:
            r = self.session.get(f"{self.BASE_URL}/fapi/v1/time", timeout=5)
            server_time = r.json()['serverTime']
            self.time_offset = server_time - int(time.time() * 1000)
            print(f"  ⏱️ Time offset: {self.time_offset}ms")
        except:
            self.time_offset = 0

    def _sign(self, params: dict) -> dict:
        """Add timestamp + signature to params."""
        params['timestamp'] = int(time.time() * 1000) + self.time_offset
        params['recvWindow'] = 10000
        query = urlencode(params)
        signature = hmac.new(
            self.api_secret.encode('utf-8'),
            query.encode('utf-8'),
            hashlib.sha256
        ).hexdigest()
        params['signature'] = signature
        return params

    def _request(self, method, endpoint, params=None):
        """Send signed request."""
        if params is None:
            params = {}
        params = self._sign(params)
        url = f"{self.BASE_URL}{endpoint}"
        try:
            if method == 'GET':
                r = self.session.get(url, params=params, timeout=10)
            elif method == 'DELETE':
                r = self.session.delete(url, params=params, timeout=10)
            else:
                r = self.session.post(url, params=params, timeout=10)
            data = r.json()
            if r.status_code != 200:
                print(f"  ⚠️ Testnet API error: {data}")
            return data
        except Exception as e:
            print(f"  ❌ Testnet request failed: {e}")
            return None

    # ============================================================
    # Account & Position
    # ============================================================
    def get_balance(self):
        """Get USDT balance."""
        data = self._request('GET', '/fapi/v2/balance')
        if data and isinstance(data, list):
            for asset in data:
                if isinstance(asset, dict) and asset.get('asset') == 'USDT':
                    return {
                        'balance': float(asset['balance']),
                        'available': float(asset['availableBalance']),
                        'unrealized_pnl': float(asset.get('crossUnPnl', 0))
                    }
        return None

    def get_position(self, symbol='BTCUSDT'):
        """Get current position."""
        data = self._request('GET', '/fapi/v2/positionRisk', {'symbol': symbol})
        if data:
            for pos in data:
                if pos['symbol'] == symbol and float(pos['positionAmt']) != 0:
                    amt = float(pos['positionAmt'])
                    return {
                        'symbol': symbol,
                        'side': 'LONG' if amt > 0 else 'SHORT',
                        'amount': abs(amt),
                        'entry_price': float(pos['entryPrice']),
                        'unrealized_pnl': float(pos['unRealizedProfit']),
                        'leverage': int(pos['leverage']),
                    }
        return None

    def set_margin_type(self, margin_type='ISOLATED', symbol='BTCUSDT'):
        """Set margin type: ISOLATED or CROSSED."""
        params = {'symbol': symbol, 'marginType': margin_type}
        params = self._sign(params)
        url = f"{self.BASE_URL}/fapi/v1/marginType"
        try:
            r = self.session.post(url, params=params, timeout=10)
            data = r.json()
            if r.status_code == 200:
                print(f"  🔒 Margin type: {margin_type} ({symbol})")
                return True
            elif data.get('code') == -4046:
                # Already set — not an error
                print(f"  🔒 Margin type: {margin_type} ({symbol}) ✓")
                return True
            else:
                print(f"  ⚠️ Margin type error: {data}")
        except Exception as e:
            print(f"  ❌ Margin type request failed: {e}")
        return False

    def set_leverage(self, leverage=50, symbol='BTCUSDT'):
        """Set leverage + isolated margin."""
        # Set isolated margin first
        self.set_margin_type('ISOLATED', symbol)
        result = self._request('POST', '/fapi/v1/leverage', {
            'symbol': symbol,
            'leverage': leverage
        })
        if result and 'leverage' in result:
            print(f"  ⚙️ Leverage set to {result['leverage']}x ({symbol})")
            return True
        return False

    # ============================================================
    # Orders
    # ============================================================
    def market_open(self, action, quantity, symbol='BTCUSDT'):
        """Open position with market order.
        action: 'LONG' or 'SHORT'
        quantity: BTC amount (e.g., 0.001)
        """
        side = 'BUY' if action == 'LONG' else 'SELL'
        params = {
            'symbol': symbol,
            'side': side,
            'type': 'MARKET',
            'quantity': f"{quantity:.3f}",
        }
        result = self._request('POST', '/fapi/v1/order', params)
        if result and 'orderId' in result:
            print(f"  ✅ Testnet ORDER: {side} {quantity} {symbol}")
            print(f"     OrderId: {result['orderId']}")
            return result
        return None

    def open_with_sl_tp(self, action, quantity, sl_price, tp_price, symbol='BTCUSDT'):
        """Open position + SL + TP in batch (1 API call).
        Gửi 3 orders cùng lúc: Entry + SL + TP.
        """
        import json
        entry_side = 'BUY' if action == 'LONG' else 'SELL'
        close_side = 'SELL' if action == 'LONG' else 'BUY'

        orders = [
            {
                'symbol': symbol,
                'side': entry_side,
                'type': 'MARKET',
                'quantity': f"{quantity:.3f}",
            },
            {
                'symbol': symbol,
                'side': close_side,
                'type': 'STOP_MARKET',
                'stopPrice': f"{sl_price:.2f}",
                'closePosition': 'true',
                'workingType': 'MARK_PRICE',
            },
            {
                'symbol': symbol,
                'side': close_side,
                'type': 'TAKE_PROFIT_MARKET',
                'stopPrice': f"{tp_price:.2f}",
                'closePosition': 'true',
                'workingType': 'MARK_PRICE',
            },
        ]

        params = {
            'batchOrders': json.dumps(orders),
        }
        params = self._sign(params)
        url = f"{self.BASE_URL}/fapi/v1/batchOrders"
        try:
            r = self.session.post(url, params=params, timeout=10)
            data = r.json()
            if r.status_code == 200 and isinstance(data, list):
                success = sum(1 for o in data if isinstance(o, dict) and 'orderId' in o)
                print(f"  ✅ Batch: {success}/3 orders filled (Entry + SL + TP)")
                return data
            else:
                print(f"  ⚠️ Batch error: {data}")
                return None
        except Exception as e:
            print(f"  ❌ Batch request failed: {e}")
            return None
        return None

    def market_close(self, symbol='BTCUSDT'):
        """Close current position with market order."""
        pos = self.get_position(symbol)
        if not pos:
            print(f"  ⚠️ No position to close")
            return None

    def update_sl(self, action, new_sl, symbol='BTCUSDT'):
        """Update SL: cancel all algo orders → set new SL via algoOrder."""
        self.cancel_all_orders(symbol)
        close_side = 'SELL' if action == 'LONG' else 'BUY'
        params = {
            'symbol': symbol,
            'side': close_side,
            'algoType': 'CONDITIONAL',
            'type': 'STOP_MARKET',
            'triggerPrice': f"{new_sl:.2f}",
            'closePosition': 'true',
            'workingType': 'MARK_PRICE',
        }
        result = self._request('POST', '/fapi/v1/algoOrder', params)
        if result and (result.get('algoId') or result.get('orderId')):
            print(f"  🔄 Testnet SL → ${new_sl:,.2f}")
            return result
        return None

    def market_close(self, symbol='BTCUSDT'):
        """Close current position with market order."""
        pos = self.get_position(symbol)
        if not pos:
            print(f"  ⚠️ No position to close")
            return None

        # Close = opposite side
        side = 'SELL' if pos['side'] == 'LONG' else 'BUY'
        params = {
            'symbol': symbol,
            'side': side,
            'type': 'MARKET',
            'quantity': f"{pos['amount']:.3f}",
            'reduceOnly': 'true'
        }
        result = self._request('POST', '/fapi/v1/order', params)
        if result and 'orderId' in result:
            print(f"  ✅ Testnet CLOSE: {side} {pos['amount']} {symbol}")
            return result
        return None

    def set_sl_tp(self, action, sl_price, tp_price=None, symbol='BTCUSDT'):
        """Set SL (and optional TP) via algoOrder API."""
        orders = []
        close_side = 'SELL' if action == 'LONG' else 'BUY'

        # SL order (algoOrder)
        sl_params = {
            'symbol': symbol,
            'side': close_side,
            'algoType': 'CONDITIONAL',
            'type': 'STOP_MARKET',
            'triggerPrice': f"{sl_price:.2f}",
            'closePosition': 'true',
            'workingType': 'MARK_PRICE',
        }
        sl_result = self._request('POST', '/fapi/v1/algoOrder', sl_params)
        if sl_result and (sl_result.get('algoId') or sl_result.get('orderId')):
            orders.append(sl_result)
            print(f"  🛑 SL set @ ${sl_price:,.2f}")

        # TP order (algoOrder)
        if tp_price:
            tp_params = {
                'symbol': symbol,
                'side': close_side,
                'algoType': 'CONDITIONAL',
                'type': 'TAKE_PROFIT_MARKET',
                'triggerPrice': f"{tp_price:.2f}",
                'closePosition': 'true',
                'workingType': 'MARK_PRICE',
            }
            tp_result = self._request('POST', '/fapi/v1/algoOrder', tp_params)
            if tp_result and (tp_result.get('algoId') or tp_result.get('orderId')):
                orders.append(tp_result)
                print(f"  🎯 TP set @ ${tp_price:,.2f}")

        return orders

    def cancel_all_orders(self, symbol='BTCUSDT'):
        """Cancel all open orders (regular + algo)."""
        # Cancel regular orders
        url = f"{self.BASE_URL}/fapi/v1/allOpenOrders"
        params = self._sign({'symbol': symbol})
        try:
            self.session.delete(url, params=params, timeout=10)
        except:
            pass
        # Cancel algo orders (SL/TP)
        try:
            algos = self._request('GET', '/fapi/v1/openAlgoOrders', {'symbol': symbol})
            if algos and isinstance(algos, list):
                for algo in algos:
                    algo_id = algo.get('algoId')
                    if algo_id:
                        self._request('DELETE', '/fapi/v1/algoOrder', {'algoId': algo_id})
        except:
            pass

    def get_price(self, symbol='BTCUSDT'):
        """Get current mark price."""
        try:
            r = self.session.get(f"{self.BASE_URL}/fapi/v1/ticker/price",
                               params={'symbol': symbol}, timeout=5)
            data = r.json()
            return float(data['price'])
        except:
            return None

