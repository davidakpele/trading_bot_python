# debug_positions.py
import MetaTrader5 as mt5
import pandas as pd
from datetime import datetime

def debug_all_positions():
    print("=== MT5 POSITIONS DEBUG ===")
    if not mt5.initialize():
        print("Failed to initialize MT5")
        return
    
    print("MT5 initialized successfully")
    
    positions = mt5.positions_get()
    print(f" MT5.positions_get() returned: {len(positions) if positions else 0} positions")
    
    if positions:
        print("\nDETAILED POSITION INFO:")
        for i, pos in enumerate(positions):
            print(f"Position {i+1}:")
            print(f"  Symbol: {pos.symbol}")
            print(f"  Type: {'BUY' if pos.type == 0 else 'SELL'}")
            print(f"  Ticket: {pos.ticket}")
            print(f"  Volume: {pos.volume}")
            print(f"  Open Price: {pos.price_open:.5f}")
            print(f"  Current Price: {pos.price_current:.5f}")
            print(f"  Profit: ${pos.profit:.2f}")
            print(f"  Time: {datetime.fromtimestamp(pos.time)}")
            print(f"  SL: {pos.sl:.5f}" if pos.sl > 0 else "  SL: None")
            print(f"  TP: {pos.tp:.5f}" if pos.tp > 0 else "  TP: None")
            print()
    else:
        print(" No positions found in MT5 account")
        
    print("\nSYMBOL FILTERING TEST:")
    symbols_to_test = ["EURUSD", "GBPUSD", "USDJPY", "AUDUSD"]
    for symbol in symbols_to_test:
        symbol_positions = mt5.positions_get(symbol=symbol)
        count = len(symbol_positions) if symbol_positions else 0
        print(f"  {symbol}: {count} positions")
    
    mt5.shutdown()
    print("MT5 shutdown")

if __name__ == "__main__":
    debug_all_positions()