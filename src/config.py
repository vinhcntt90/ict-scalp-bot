import os
from dotenv import load_dotenv

# Load environment variables from .env file
load_dotenv()

class Config:
    # Security / Credentials (set in .env file)
    TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
    TELEGRAM_CHAT_IDS = [
        chat_id.strip() 
        for chat_id in os.getenv("TELEGRAM_CHAT_IDS", "").split(",") 
        if chat_id.strip()
    ]
    
    # Bật/Tắt gửi Telegram (True = gửi, False = không gửi)
    SEND_TO_TELEGRAM = True

    # Google Sheets Cloud URL
    GOOGLE_SHEETS_URL = os.getenv("GOOGLE_SHEETS_URL", "")
    
    # Paths
    BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    ARTIFACTS_DIR = os.getenv("ARTIFACTS_DIR", os.path.join(os.path.expanduser('~'), 'Desktop'))
    
    # Charting
    DEFAULT_TIMEFRAME = "15m"
    LIMIT = 1000
    
    # Flags
    BACKTEST_MODE = False

    # Binance Testnet (Demo Trading)
    DEMO_MODE = True  # True = Testnet, False = Paper only
    BINANCE_TESTNET_KEY = os.getenv("BINANCE_TESTNET_KEY", "")
    BINANCE_TESTNET_SECRET = os.getenv("BINANCE_TESTNET_SECRET", "")
    BINANCE_TESTNET_URL = "https://testnet.binancefuture.com"

    @classmethod
    def validate(cls):
        if not cls.TELEGRAM_BOT_TOKEN:
            print("[!] Warning: TELEGRAM_BOT_TOKEN not found in .env")
        if not cls.TELEGRAM_CHAT_IDS:
            print("[!] Warning: TELEGRAM_CHAT_IDS not found in .env")
        
        # Ensure output directory exists
        if not os.path.exists(cls.ARTIFACTS_DIR):
            os.makedirs(cls.ARTIFACTS_DIR)

# Auto-validate on import
Config.validate()
