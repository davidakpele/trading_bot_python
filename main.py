# main.py - ALTERNATIVE VERSION (if you don't want to modify monitor.py)
import argparse
import os
import sys
import MetaTrader5 as mt5

# Add the src directory to Python path
sys.path.append(os.path.join(os.path.dirname(__file__), 'src'))

from data.generate_synthetic import main as gen_main
from src.train import train_model
from src.live_bot import run_live
from src.monitor import TradingMonitor

def run_monitor_with_path(symbol=None, refresh_interval=5, mt5_path=None):
    """Wrapper function to handle MT5 path for monitor"""
    # Initialize MT5 with the provided path
    if mt5_path:
        if not mt5.initialize(mt5_path):
            print(f"❌ Failed to initialize MT5 with path: {mt5_path}")
            return
    else:
        if not mt5.initialize():
            print("❌ Failed to initialize MT5")
            return
    
    print(f"✅ MT5 initialized successfully")
    
    monitor = TradingMonitor(symbol=symbol)
    monitor.run_monitor(refresh_interval=refresh_interval)
    
    mt5.shutdown()

def run():
    parser = argparse.ArgumentParser(description="Scalping Trading Bot")
    parser.add_argument("action", choices=["gen-data", "train", "live", "monitor"], 
                       help="Action: gen-data, train, live, or monitor")
    parser.add_argument("--csv", default="data/scalping_large_dataset.csv", help="Path to CSV data")
    parser.add_argument("--symbol", default="EURUSD", help="Trading symbol")
    parser.add_argument("--lots", type=float, default=0.01, help="Lot size")
    parser.add_argument("--sl", type=int, default=8, help="Stop loss in pips")
    parser.add_argument("--tp", type=int, default=12, help="Take profit in pips")
    parser.add_argument("--minutes", type=int, default=3000, help="Minutes of synthetic data to generate")
    parser.add_argument("--mt5-path", default=None, help="Path to MT5 terminal")
    parser.add_argument("--interval", type=int, default=5, help="Monitor refresh interval in seconds")
    parser.add_argument("--poll-interval", type=int, default=10, help="Live bot polling interval in seconds")
    
    args = parser.parse_args()

    if args.action == "gen-data":
        print("🔄 Generating synthetic data...")
        gen_main(out_path=args.csv, minutes_per_symbol=args.minutes)
        
    elif args.action == "train":
        print("🤖 Training model...")
        train_model(args.csv)
        
    elif args.action == "live":
        print("🚀 Starting live trading bot...")
        run_live(
            symbol=args.symbol, 
            lots=args.lots, 
            sl_pips=args.sl, 
            tp_pips=args.tp, 
            poll_interval=args.poll_interval, 
            path=args.mt5_path
        )
        
    elif args.action == "monitor":
        print("📊 Starting trading monitor...")
        run_monitor_with_path(
            symbol=args.symbol,
            refresh_interval=args.interval,
            mt5_path=args.mt5_path
        )
        
    else:
        print("Unknown action")

if __name__ == "__main__":
    run()