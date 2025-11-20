# src/monitor.py - Enhanced version
import MetaTrader5 as mt5
from datetime import datetime, timedelta
from tabulate import tabulate
import time
import os

class TradingMonitor:
    def __init__(self, symbol=None):
        self.symbol = symbol
        self.last_check_time = None
        
    def get_account_info(self):
        """Get account information"""
        account_info = mt5.account_info()
        if account_info is None:
            return None
        return {
            'balance': account_info.balance,
            'equity': account_info.equity,
            'margin': account_info.margin,
            'free_margin': account_info.margin_free,
            'profit': account_info.profit,
            'margin_level': account_info.margin_level if account_info.margin > 0 else 0
        }
    
    def get_open_positions(self):
        """Get all open positions"""
        if self.symbol:
            positions = mt5.positions_get(symbol=self.symbol)
        else:
            positions = mt5.positions_get()
        
        if positions is None or len(positions) == 0:
            return []
        
        position_list = []
        for pos in positions:
            position_list.append({
                'ticket': pos.ticket,
                'symbol': pos.symbol,
                'type': 'BUY' if pos.type == mt5.ORDER_TYPE_BUY else 'SELL',
                'volume': pos.volume,
                'open_price': pos.price_open,
                'current_price': pos.price_current,
                'sl': pos.sl,
                'tp': pos.tp,
                'profit': pos.profit,
                'time': datetime.fromtimestamp(pos.time)
            })
        return position_list
    
    def get_todays_history(self):
        """Get today's closed trades"""
        today = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0)
        tomorrow = today + timedelta(days=1)
        
        deals = mt5.history_deals_get(today, tomorrow)
        
        if deals is None or len(deals) == 0:
            return []
        
        trade_deals = [d for d in deals if d.entry != mt5.DEAL_ENTRY_IN]
        
        history = []
        for deal in trade_deals:
            if deal.entry == mt5.DEAL_ENTRY_OUT:
                history.append({
                    'ticket': deal.position_id,
                    'symbol': deal.symbol,
                    'type': 'BUY' if deal.type == mt5.DEAL_TYPE_SELL else 'SELL',
                    'volume': deal.volume,
                    'profit': deal.profit,
                    'time': datetime.fromtimestamp(deal.time)
                })
        
        return history
    
    def calculate_pips(self, pos):
        """Calculate pips for a position"""
        symbol_info = mt5.symbol_info(pos['symbol'])
        if not symbol_info:
            return 0
        
        digits = symbol_info.digits
        pip_size = 0.0001 if digits == 5 else 0.01 if digits == 3 else 0.001
        
        if pos['type'] == 'BUY':
            pips = (pos['current_price'] - pos['open_price']) / pip_size
        else:
            pips = (pos['open_price'] - pos['current_price']) / pip_size
        return round(pips, 1)
    
    def display_monitor(self):
        """Display the monitor dashboard"""
        os.system('cls' if os.name == 'nt' else 'clear')
        
        print("=" * 100)
        print(f"SCALPING BOT MONITOR - {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
        print("=" * 100)
        
        account = self.get_account_info()
        if account:
            print("\nACCOUNT SUMMARY")
            print("-" * 100)
            print(f"Balance        : ${account['balance']:.2f}")
            print(f"Equity         : ${account['equity']:.2f}")
            print(f"Margin         : ${account['margin']:.2f}")
            print(f"Free Margin    : ${account['free_margin']:.2f}")
            print(f"Profit         : ${account['profit']:.2f}")
            print(f"Margin Level   : {account['margin_level']:.2f}%")
        else:
            print("\nCould not retrieve account information")
        
        positions = self.get_open_positions()
        print(f"\nOPEN POSITIONS ({len(positions)} active)")
        print("-" * 100)
        
        if positions:
            table_data = []
            for pos in positions:
                pips = self.calculate_pips(pos)
                table_data.append([
                    pos['ticket'],
                    pos['symbol'],
                    pos['type'],
                    pos['volume'],
                    f"{pos['open_price']:.5f}",
                    f"{pos['current_price']:.5f}",
                    f"{pos['sl']:.5f}" if pos['sl'] else 'None',
                    f"{pos['tp']:.5f}" if pos['tp'] else 'None',
                    f"{pips:+.1f}",
                    f"${pos['profit']:.2f}",
                    pos['time'].strftime('%Y-%m-%d %H:%M:%S')
                ])
            headers = ['Ticket', 'Symbol', 'Type', 'Volume', 'Open Price', 'Current', 'SL', 'TP', 'Pips', 'Profit', 'Time']
            print(tabulate(table_data, headers=headers, tablefmt='grid'))
        else:
            print("No open positions - waiting for trading signals")
        history = self.get_todays_history()
        print(f"\nTODAY'S PERFORMANCE ({len(history)} closed trades)")
        print("-" * 100)
        if history:
            total_profit = sum(h['profit'] for h in history)
            winning_trades = [h for h in history if h['profit'] > 0]
            losing_trades = [h for h in history if h['profit'] < 0]
            print(f"Total Trades   : {len(history)}")
            print(f"Winning Trades : {len(winning_trades)} ({len(winning_trades)/len(history)*100:.1f}%)")
            print(f"Losing Trades  : {len(losing_trades)} ({len(losing_trades)/len(history)*100:.1f}%)")
            print(f"Total P/L      : ${total_profit:.2f}")
            if winning_trades:
                avg_win = sum(h['profit'] for h in winning_trades) / len(winning_trades)
                print(f"Avg Win        : ${avg_win:.2f}")
            if losing_trades:
                avg_loss = sum(h['profit'] for h in losing_trades) / len(losing_trades)
                print(f"Avg Loss       : ${avg_loss:.2f}")
        else:
            print("✓ No closed trades today - bot is monitoring for opportunities")
        print(f"\nRECENT TRADE HISTORY (Last 10)")
        print("-" * 100)
        
        if history:
            recent = history[-10:]
            table_data = []
            for h in recent:
                profit_indicator = "✓" if h['profit'] > 0 else "✗"
                table_data.append([
                    h['ticket'],
                    h['symbol'],
                    h['type'],
                    h['volume'],
                    f"${h['profit']:.2f}",
                    profit_indicator,
                    h['time'].strftime('%H:%M:%S')
                ])
            headers = ['Ticket', 'Symbol', 'Type', 'Volume', 'Profit', 'Result', 'Close Time']
            print(tabulate(table_data, headers=headers, tablefmt='grid'))
        else:
            print("Waiting for first trade execution...")
        print("\n" + "=" * 100)
        if self.symbol:
            print(f"Monitoring: {self.symbol} | Status: ACTIVE | Last Update: {datetime.now().strftime('%H:%M:%S')}")
        else:
            print(f"Monitoring: ALL SYMBOLS | Status: ACTIVE | Last Update: {datetime.now().strftime('%H:%M:%S')}")
        print("=" * 100)
        print("Press Ctrl+C to stop monitoring")
        print("=" * 100)
    
    def run_monitor(self, refresh_interval=5):
        """Run the monitor with auto-refresh"""
        try:
            while True:
                self.display_monitor()
                time.sleep(refresh_interval)
        except KeyboardInterrupt:
            print("\n\nMonitor stopped by user")
        except Exception as e:
            print(f"\n\nError in monitor: {e}")
            import traceback
            traceback.print_exc()