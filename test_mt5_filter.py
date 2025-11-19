# test_mt5_filter.py
import MetaTrader5 as mt5

def test_position_filtering():
    if mt5.initialize():
        print("=== TESTING MT5 POSITION FILTERING ===")
        
        # Test 1: Get all positions
        all_positions = mt5.positions_get()
        print(f"All positions: {len(all_positions) if all_positions else 0}")
        if all_positions:
            for pos in all_positions:
                print(f"  {pos.symbol} {pos.type} Ticket:{pos.ticket}")
        
        # Test 2: Get EURUSD positions
        eur_positions = mt5.positions_get(symbol="EURUSD")
        print(f"EURUSD filtered: {len(eur_positions) if eur_positions else 0}")
        if eur_positions:
            for pos in eur_positions:
                print(f"  {pos.symbol} {pos.type} Ticket:{pos.ticket}")
        
        # Test 3: Get GBPUSD positions  
        gbp_positions = mt5.positions_get(symbol="GBPUSD")
        print(f"GBPUSD filtered: {len(gbp_positions) if gbp_positions else 0}")
        if gbp_positions:
            for pos in gbp_positions:
                print(f"  {pos.symbol} {pos.type} Ticket:{pos.ticket}")
        
        mt5.shutdown()

test_position_filtering()