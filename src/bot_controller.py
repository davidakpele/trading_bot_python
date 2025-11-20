# src/bot_controller.py
import asyncio
import os
import time
import uuid
import json
import threading
from datetime import datetime
from typing import Dict, List, Optional
from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import HTMLResponse
import MetaTrader5 as mt5
import uvicorn
from utils import connect_mt5, logger
from concurrent.futures import ThreadPoolExecutor
from pydantic import BaseModel

executor = ThreadPoolExecutor(max_workers=10)
# MT5 Error Code Mappings
MT5_ERRORS = {
    10001: "Requote",
    10002: "Request rejected", 
    10003: "Request canceled by trader",
    10004: "Order placement timeout",
    10005: "Invalid request",
    10006: "Invalid volume",
    10007: "Invalid price",
    10008: "Invalid stops",
    10009: "Trade is disabled",
    10010: "Market is closed",
    10011: "Insufficient funds",
    10012: "Price changed",
    10013: "Too many requests",
    10014: "No changes",
    10015: "Server is busy",
    10016: "Invalid function",
    10017: "Account is locked",
    10018: "Long positions only allowed",
    10019: "Too many orders",
    10020: "Buy orders only allowed", 
    10021: "Sell orders only allowed",
    10022: "Order is locked",
    10023: "Prohibited by FIFO rules",
    10024: "Incorrect order type",
    10025: "Position not found",
    10026: "Trade timeout",
    10027: "Invalid filling mode",
    10028: "Invalid order state",
    10029: "Invalid expiration",
    10030: "Order was canceled",
    10031: "Order was partially filled",
    10032: "Order was modified",
    10033: "Order was activated",
    10034: "Order was deleted",
    10035: "Order was suspended",
    10036: "Order was restored",
    10037: "Order was accepted",
    10038: "Order was canceled by broker",
    10039: "Order was canceled by system",
    10040: "Order was expired",
    10041: "Order was canceled by timeout",
    10042: "Order was rejected by broker",
    10043: "Order was rejected by system",
    10044: "Order was canceled by trader",
    10045: "Order was modified by trader"
}

class TradeRequest(BaseModel):
    symbol: str
    type: str
    lots: float
    sl_points: Optional[float] = 0
    tp_points: Optional[float] = 0

class StopLossUpdate(BaseModel):
    sl_points: float

class TradingController:
    def __init__(self):
        self.active_trades = {}
        self.trade_history = []
        self.bot_status = "STOPPED"
        self.bot_settings = {}
        self.trade_lock = threading.Lock()
        self.mt5_lock = threading.Lock()
        
    def generate_trade_id(self):
        return str(uuid.uuid4())[:8]
    
    def get_mt5_error_message(self, error_code: int) -> str:
        """Get human-readable MT5 error message"""
        return MT5_ERRORS.get(error_code, f"Unknown error: {error_code}")
    
    def find_position_by_trade_id(self, trade_id: str, symbol: str = None):
        """Find MT5 position by trade ID with multiple search strategies"""
        try:
            with self.mt5_lock:
                if not mt5.initialize():
                    return None
                
                # Get all positions
                if symbol:
                    positions = mt5.positions_get(symbol=symbol)
                else:
                    positions = mt5.positions_get()
                
                if not positions:
                    return None
                
                # Search strategies
                for position in positions:
                    # Search by exact comment match
                    if position.comment and trade_id in position.comment:
                        logger.info(f"Found position for trade {trade_id}: ticket {position.ticket}, comment '{position.comment}'")
                        return position
                    
                    # Search by partial comment match
                    if position.comment:
                        clean_comment = position.comment.replace("API_TRADE_", "").replace("BOT_", "")
                        if trade_id in clean_comment:
                            logger.info(f"Found position for trade {trade_id}: ticket {position.ticket}, comment '{position.comment}'")
                            return position
                
                # Log available positions for debugging
                logger.info(f"Available positions: {[(p.ticket, p.comment, p.symbol) for p in positions]}")
                logger.warning(f"No position found for trade {trade_id}")
                return None
                
        except Exception as e:
            logger.error(f"Error finding position for trade {trade_id}: {e}")
            return None
        
    def calculate_current_profit(self, trade: Dict) -> float:
        """Calculate current profit/loss for an active trade"""
        try:
            if not mt5.initialize():
                return 0.0
            
            # Get current tick
            tick = mt5.symbol_info_tick(trade["symbol"])
            if not tick:
                return 0.0
            
            # Get position from MT5
            positions = mt5.positions_get(symbol=trade["symbol"])
            if positions:
                for position in positions:
                    if (position.comment == f"API_TRADE_{trade['trade_id']}" or 
                        position.comment == f"BOT_{trade['trade_id']}" or
                        position.ticket == trade.get("mt5_ticket")):
                        return position.profit
            
            # Fallback calculation if position not found
            if trade["type"] == "BUY":
                current_price = tick.bid  # For BUY positions, we sell at bid price
                profit = (current_price - trade["entry_price"]) * trade["lots"] * 100000
            else:  # SELL
                current_price = tick.ask  # For SELL positions, we buy at ask price
                profit = (trade["entry_price"] - current_price) * trade["lots"] * 100000
            
            return round(profit, 2)
            
        except Exception as e:
            logger.error(f"Error calculating profit for trade {trade['trade_id']}: {e}")
            return 0.0
    
    def calculate_potential_profit(self, trade: Dict) -> Dict:
        """Calculate potential profit at TP and loss at SL"""
        try:
            if trade["type"] == "BUY":
                # For BUY: Profit at TP, Loss at SL
                tp_profit = (trade["tp_price"] - trade["entry_price"]) * trade["lots"] * 100000 if trade["tp_price"] else 0
                sl_loss = (trade["sl_price"] - trade["entry_price"]) * trade["lots"] * 100000 if trade["sl_price"] else 0
            else:  # SELL
                # For SELL: Profit at TP, Loss at SL
                tp_profit = (trade["entry_price"] - trade["tp_price"]) * trade["lots"] * 100000 if trade["tp_price"] else 0
                sl_loss = (trade["entry_price"] - trade["sl_price"]) * trade["lots"] * 100000 if trade["sl_price"] else 0
            
            current_profit = self.calculate_current_profit(trade)
            
            return {
                "current_profit": round(current_profit, 2),
                "potential_profit": round(tp_profit, 2) if tp_profit else 0,
                "potential_loss": round(sl_loss, 2) if sl_loss else 0,
                "profit_percentage": round((current_profit / (trade["entry_price"] * trade["lots"] * 100000)) * 100, 2) if trade["entry_price"] else 0
            }
            
        except Exception as e:
            logger.error(f"Error calculating potential profit: {e}")
            return {
                "current_profit": 0,
                "potential_profit": 0,
                "potential_loss": 0,
                "profit_percentage": 0
            }
    
    def get_trade_with_profit(self, trade: Dict) -> Dict:
        """Enrich trade data with profit information"""
        trade_data = trade.copy()
        
        if trade_data["status"] == "EXECUTED":
            profit_info = self.calculate_potential_profit(trade_data)
            trade_data.update(profit_info)
            
            # Add profit/loss status
            if profit_info["current_profit"] > 0:
                trade_data["profit_status"] = "profit"
            elif profit_info["current_profit"] < 0:
                trade_data["profit_status"] = "loss"
            else:
                trade_data["profit_status"] = "breakeven"
                
        elif trade_data["status"] == "CLOSED":
            # For closed trades, use the actual profit from MT5
            actual_profit = trade_data.get("profit", 0)
            trade_data["current_profit"] = round(actual_profit, 2)
            trade_data["profit_status"] = "profit" if actual_profit > 0 else "loss" if actual_profit < 0 else "breakeven"
        
        return trade_data

    def check_symbol_tradable(self, symbol: str) -> Dict:
        """Check if symbol is available and tradable"""
        try:
            with self.mt5_lock:
                symbol_info = mt5.symbol_info(symbol)
                if symbol_info is None:
                    return {"tradable": False, "error": f"Symbol {symbol} not found"}
                
                if not symbol_info.visible:
                    return {"tradable": False, "error": f"Symbol {symbol} is not visible"}
                
                if not symbol_info.trade_mode == mt5.SYMBOL_TRADE_MODE_FULL:
                    return {"tradable": False, "error": f"Symbol {symbol} has restricted trading mode"}
                
                return {"tradable": True, "symbol_info": symbol_info}
                
        except Exception as e:
            return {"tradable": False, "error": str(e)}
    
    def get_account_info(self) -> Dict:
        """Get account information"""
        try:
            with self.mt5_lock:
                account_info = mt5.account_info()
                if account_info is None:
                    return {"error": "Could not get account info"}
                
                return {
                    "balance": account_info.balance,
                    "equity": account_info.equity,
                    "margin": account_info.margin,
                    "free_margin": account_info.margin_free,
                    "leverage": account_info.leverage,
                    "currency": account_info.currency,
                    "server": account_info.server
                }
        except Exception as e:
            return {"error": str(e)}
    
    def sync_bot_trades(self):
        """Sync trades placed by the live bot"""
        bot_trades_file = "bot_trades.json"
        if os.path.exists(bot_trades_file):
            try:
                with open(bot_trades_file, 'r') as f:
                    bot_trades = json.load(f)
                
                # Acquire lock only for the modification
                with self.trade_lock:
                    for trade in bot_trades:
                        if trade['trade_id'] not in self.active_trades and trade['status'] == 'EXECUTED':
                            self.active_trades[trade['trade_id']] = trade
                            self.trade_history.append(trade)
                
                # Clear the file after syncing (outside lock)
                os.remove(bot_trades_file)
                
            except Exception as e:
                logger.error(f"Error syncing bot trades: {e}")
    
    def place_trade(self, symbol: str, order_type: str, lots: float, 
               sl_points: float = None, tp_points: float = None) -> Dict:
        """Place a new trade with unique ID"""
        
        try:
            # Initialize MT5 connection
            if not mt5.initialize():
                return {"success": False, "error": "MT5 not connected"}

            # Check if symbol is tradable
            symbol_check = self.check_symbol_tradable(symbol)
            if not symbol_check["tradable"]:
                return {"success": False, "error": symbol_check["error"]}
            
            symbol_info = symbol_check["symbol_info"]
            
            # Validate lot size
            if lots < symbol_info.volume_min:
                return {"success": False, "error": f"Lot size too small. Minimum: {symbol_info.volume_min}"}
            
            if lots > symbol_info.volume_max:
                return {"success": False, "error": f"Lot size too large. Maximum: {symbol_info.volume_max}"}
            
            # Generate trade ID
            trade_id = self.generate_trade_id()
            
            # Get current tick
            tick = mt5.symbol_info_tick(symbol)
            if not tick:
                return {"success": False, "error": f"Could not get tick data for {symbol}"}
            
            # Calculate prices
            if order_type.lower() == 'buy':
                price = tick.ask
                if sl_points and sl_points > 0:
                    sl = price - sl_points * (10 ** -symbol_info.digits)
                else:
                    sl = 0.0
                if tp_points and tp_points > 0:
                    tp = price + tp_points * (10 ** -symbol_info.digits)
                else:
                    tp = 0.0
            else:  # sell
                price = tick.bid
                if sl_points and sl_points > 0:
                    sl = price + sl_points * (10 ** -symbol_info.digits)
                else:
                    sl = 0.0
                if tp_points and tp_points > 0:
                    tp = price - tp_points * (10 ** -symbol_info.digits)
                else:
                    tp = 0.0
            
            # Prepare order request
            request = {
                "action": mt5.TRADE_ACTION_DEAL,
                "symbol": symbol,
                "volume": lots,
                "type": mt5.ORDER_TYPE_BUY if order_type.lower() == 'buy' else mt5.ORDER_TYPE_SELL,
                "price": price,
                "sl": sl,
                "tp": tp,
                "deviation": 20,
                "magic": 100,
                "comment": f"API_TRADE_{trade_id}",
                "type_time": mt5.ORDER_TIME_GTC,
                "type_filling": mt5.ORDER_FILLING_FOK,
            }
            
            logger.info(f"Placing trade: {symbol} {order_type} {lots} lots at {price}")
            
            # Send order
            result = mt5.order_send(request)
            
            trade_data = {
                "trade_id": trade_id,
                "symbol": symbol,
                "type": order_type.upper(),
                "lots": lots,
                "entry_price": price,
                "sl_price": sl,
                "tp_price": tp,
                "sl_points": sl_points,
                "tp_points": tp_points,
                "status": "PENDING",
                "timestamp": datetime.now().isoformat(),
                "mt5_ticket": None,
                "source": "manual"
            }
            
            if result and result.retcode == mt5.TRADE_RETCODE_DONE:
                trade_data.update({
                    "status": "EXECUTED",
                    "mt5_ticket": result.order,
                    "executed_price": result.price,
                    "execution_time": datetime.now().isoformat()
                })
                
                with self.trade_lock:
                    self.active_trades[trade_id] = trade_data
                    self.trade_history.append(trade_data.copy())
                
                return {
                    "success": True,
                    "trade_id": trade_id,
                    "message": f"Trade executed successfully at {result.price}",
                    "data": trade_data
                }
            else:
                error_msg = self.get_mt5_error_message(result.retcode) if result else "Unknown error"
                logger.error(f"Trade failed: {error_msg} (Code: {result.retcode if result else 'N/A'})")
                
                trade_data["status"] = "FAILED"
                trade_data["error"] = error_msg
                
                with self.trade_lock:
                    self.trade_history.append(trade_data)
                
                return {
                    "success": False,
                    "trade_id": trade_id,
                    "error": f"Trade failed: {error_msg}",
                    "error_code": result.retcode if result else None
                }
                
        except Exception as e:
            logger.error(f"Error placing trade: {e}")
            return {"success": False, "error": str(e)}
            
    def update_stop_loss(self, trade_id: str, sl_points: float) -> Dict:
        """Update stop loss for a trade"""
        
        with self.trade_lock:
            # Sync any bot trades first
            self.sync_bot_trades()
            
            if trade_id not in self.active_trades:
                return {"success": False, "error": f"Trade {trade_id} not found"}
            
            trade = self.active_trades[trade_id]
            
            try:
                if not mt5.initialize():
                    return {"success": False, "error": "MT5 not connected"}
                
                # Find position
                positions = mt5.positions_get(symbol=trade["symbol"])
                target_position = None
                
                for pos in positions:
                    if (pos.comment == f"API_TRADE_{trade_id}" or 
                        pos.comment == f"BOT_{trade_id}" or
                        pos.ticket == trade.get("mt5_ticket")):
                        target_position = pos
                        break
                
                if not target_position:
                    return {"success": False, "error": f"Position for trade {trade_id} not found"}
                
                symbol_info = mt5.symbol_info(trade["symbol"])
                if trade["type"] == "BUY":
                    new_sl = target_position.price_open - sl_points * (10 ** -symbol_info.digits)
                else:  # SELL
                    new_sl = target_position.price_open + sl_points * (10 ** -symbol_info.digits)
                
                # Prepare modification request
                request = {
                    "action": mt5.TRADE_ACTION_SLTP,
                    "symbol": trade["symbol"],
                    "sl": new_sl,
                    "tp": target_position.tp,
                    "position": target_position.ticket
                }
                
                result = mt5.order_send(request)
                
                if result and result.retcode == mt5.TRADE_RETCODE_DONE:
                    # Update trade data
                    trade["sl_points"] = sl_points
                    trade["sl_price"] = new_sl
                    
                    return {
                        "success": True,
                        "message": f"Stop loss updated to {sl_points} points",
                        "new_sl_price": new_sl
                    }
                else:
                    return {
                        "success": False,
                        "error": f"SL update failed: {result.retcode if result else 'Unknown error'}"
                    }
                    
            except Exception as e:
                logger.error(f"Error updating SL for trade {trade_id}: {e}")
                return {"success": False, "error": str(e)}
    
    def get_trade_history(self, filters: Dict = None) -> List[Dict]:
        """Get trade history with optional filtering and profit information"""
        # Sync any bot trades first
        self.sync_bot_trades()
        
        history = self.trade_history.copy()
        
        if filters:
            if filters.get('symbol'):
                history = [t for t in history if t['symbol'] == filters['symbol']]
            if filters.get('type'):
                history = [t for t in history if t['type'] == filters['type'].upper()]
            if filters.get('status'):
                history = [t for t in history if t['status'] == filters['status'].upper()]
            if filters.get('date_from'):
                date_from = datetime.fromisoformat(filters['date_from'])
                history = [t for t in history if datetime.fromisoformat(t['timestamp']) >= date_from]
            if filters.get('date_to'):
                date_to = datetime.fromisoformat(filters['date_to'])
                history = [t for t in history if datetime.fromisoformat(t['timestamp']) <= date_to]
        
        # Enrich with profit data
        history = [self.get_trade_with_profit(trade) for trade in history]
        
        return sorted(history, key=lambda x: x['timestamp'], reverse=True)

    def get_active_trades(self) -> List[Dict]:
        """Get all active trades with profit information"""
        # Sync any bot trades first
        self.sync_bot_trades()
        active_trades = list(self.active_trades.values())
        
        # Enrich with profit data
        return [self.get_trade_with_profit(trade) for trade in active_trades]
    
    def close_trade_internal(self, trade_id: str) -> Dict:
        """Internal method to close a trade without acquiring trade_lock"""
        try:
            with self.mt5_lock:
                if not mt5.initialize():
                    return {"success": False, "error": "MT5 not connected"}
                
                # Find position using improved search
                target_position = self.find_position_by_trade_id(trade_id)
                
                if not target_position:
                    return {"success": False, "error": f"Position for trade {trade_id} not found"}
                
                trade = self.active_trades[trade_id]
                
                # Prepare close request
                close_type = mt5.ORDER_TYPE_SELL if trade["type"] == "BUY" else mt5.ORDER_TYPE_BUY
                tick = mt5.symbol_info_tick(trade["symbol"])
                if not tick:
                    return {"success": False, "error": f"Could not get tick data for {trade['symbol']}"}
                
                close_price = tick.bid if trade["type"] == "BUY" else tick.ask
                
                request = {
                    "action": mt5.TRADE_ACTION_DEAL,
                    "symbol": trade["symbol"],
                    "volume": trade["lots"],
                    "type": close_type,
                    "position": target_position.ticket,
                    "price": close_price,
                    "deviation": 20,
                    "magic": 100,
                    "comment": f"CLOSE_{trade_id}",
                    "type_time": mt5.ORDER_TIME_GTC,
                    "type_filling": mt5.ORDER_FILLING_FOK,
                }
                
                logger.info(f"Closing trade {trade_id}: {trade['symbol']} {trade['type']} {trade['lots']} lots at {close_price}")
                
                result = mt5.order_send(request)
                
                if result and result.retcode == mt5.TRADE_RETCODE_DONE:
                    # Update trade status
                    trade["status"] = "CLOSED"
                    trade["close_price"] = result.price
                    trade["close_time"] = datetime.now().isoformat()
                    trade["profit"] = result.profit
                    
                    logger.info(f"Trade {trade_id} closed successfully at {result.price}, profit: {result.profit}")
                    
                    return {
                        "success": True,
                        "message": f"Trade {trade_id} closed successfully",
                        "close_price": result.price,
                        "profit": result.profit
                    }
                else:
                    error_msg = self.get_mt5_error_message(result.retcode) if result else "Unknown error"
                    logger.error(f"Close failed for trade {trade_id}: {error_msg}")
                    return {
                        "success": False,
                        "error": f"Close failed: {error_msg}",
                        "error_code": result.retcode if result else None
                    }
                    
        except Exception as e:
            logger.error(f"Error closing trade {trade_id}: {e}")
            return {"success": False, "error": str(e)}

    def close_trade(self, trade_id: str) -> Dict:
        """Close a specific trade by ID"""
        with self.trade_lock:
            # Sync any bot trades first
            self.sync_bot_trades()
            
            if trade_id not in self.active_trades:
                return {"success": False, "error": f"Trade {trade_id} not found"}
            
            result = self.close_trade_internal(trade_id)
            
            if result['success']:
                # Remove from active trades only if close was successful
                del self.active_trades[trade_id]
            
            return result

    def stop_all_trades(self) -> Dict:
        """Close all active trades without deadlock"""
        # Sync any bot trades first
        self.sync_bot_trades()
        
        # Get trade IDs first, then process them
        with self.trade_lock:
            trade_ids = list(self.active_trades.keys())
        
        results = []
        for trade_id in trade_ids:
            # Use the internal close method to avoid deadlock
            result = self.close_trade_internal(trade_id)
            results.append({
                "trade_id": trade_id,
                "success": result["success"],
                "message": result.get("message", result.get("error", "Unknown error"))
            })
            
            # Remove from active trades if close was successful
            if result['success']:
                with self.trade_lock:
                    if trade_id in self.active_trades:
                        del self.active_trades[trade_id]
        
        return {
            "success": True,
            "message": f"Attempted to close {len(results)} trades",
            "results": results
        }

# FastAPI Application
app = FastAPI(title="Trading Bot Controller", version="1.0.0")
trading_controller = TradingController()


def load_html_template():
    """Load HTML template directly from file"""
    template_path = os.path.join(os.path.dirname(__file__), '..', 'templates', 'index.html')
    try:
        with open(template_path, 'r', encoding='utf-8') as f:
            return f.read()
    except FileNotFoundError:
        # Fallback template if file doesn't exist
        return """
        <!DOCTYPE html>
        <html>
        <head>
            <title>Trading Bot Controller</title>
            <style>
                body { font-family: Arial, sans-serif; margin: 40px; }
                .container { max-width: 1200px; margin: 0 auto; }
                .card { border: 1px solid #ddd; padding: 20px; margin: 10px 0; border-radius: 5px; }
                .btn { padding: 10px 15px; margin: 5px; border: none; border-radius: 3px; cursor: pointer; }
                .btn-primary { background: #007bff; color: white; }
                .btn-danger { background: #dc3545; color: white; }
            </style>
        </head>
        <body>
            <div class="container">
                <h1>Trading Bot Controller</h1>
                <p>HTML template file not found. Please check the templates directory.</p>
                <p>API is running correctly. Access <a href="/docs">/docs</a> for API documentation.</p>
            </div>
        </body>
        </html>
        """


@app.get("/", response_class=HTMLResponse)
async def index():
    """Serve the main trading interface"""
    html_content = load_html_template()
    return HTMLResponse(content=html_content)


@app.get("/dashboard", response_class=HTMLResponse)
async def dashboard():
    """Alternative dashboard route"""
    html_content = load_html_template()
    return HTMLResponse(content=html_content)


@app.post("/api/trade/place")
async def place_trade(trade_request: TradeRequest):
    """Place a new trade"""
    try:
        loop = asyncio.get_event_loop()
        result = await loop.run_in_executor(
            executor,
            trading_controller.place_trade,
            trade_request.symbol,
            trade_request.type,
            trade_request.lots,
            trade_request.sl_points,
            trade_request.tp_points
        )
        
        if result['success']:
            return result
        else:
            raise HTTPException(status_code=400, detail=result['error'])
            
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))



@app.post("/api/trade/close/{trade_id}")
async def close_trade(trade_id: str):
    """Close a specific trade"""
    try:
        # Run blocking code in thread pool
        loop = asyncio.get_event_loop()
        result = await loop.run_in_executor(
            executor, 
            trading_controller.close_trade, 
            trade_id
        )
        
        if result['success']:
            return result
        else:
            raise HTTPException(status_code=400, detail=result['error'])
            
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/api/trade/update_sl/{trade_id}")
async def update_stop_loss(trade_id: str, sl_update: StopLossUpdate):
    """Update stop loss for a trade"""
    try:
        loop = asyncio.get_event_loop()
        result = await loop.run_in_executor(
            executor,
            trading_controller.update_stop_loss,
            trade_id,
            sl_update.sl_points
        )
        
        if result['success']:
            return result
        else:
            raise HTTPException(status_code=400, detail=result['error'])
            
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/api/trade/stop_all")
async def stop_all_trades():
    """Close all active trades"""
    try:
        loop = asyncio.get_event_loop()
        result = await loop.run_in_executor(
            executor,
            trading_controller.stop_all_trades
        )
        return result
        
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/trades/active")
async def get_active_trades():
    """Get all active trades"""
    try:
        loop = asyncio.get_event_loop()
        trades = await loop.run_in_executor(
            executor,
            trading_controller.get_active_trades
        )
        return {
            "success": True,
            "count": len(trades),
            "trades": trades
        }
        
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))



@app.get("/api/trades/history")
async def get_trade_history(
    symbol: Optional[str] = Query(None, description="Filter by symbol"),
    type: Optional[str] = Query(None, description="Filter by trade type"),
    status: Optional[str] = Query(None, description="Filter by status"),
    date_from: Optional[str] = Query(None, description="Filter from date (ISO format)"),
    date_to: Optional[str] = Query(None, description="Filter to date (ISO format)")
):
    """Get trade history with filters"""
    try:
        filters = {
            'symbol': symbol,
            'type': type,
            'status': status,
            'date_from': date_from,
            'date_to': date_to
        }
        
        # Remove None values
        filters = {k: v for k, v in filters.items() if v is not None}
        
        history = trading_controller.get_trade_history(filters)
        
        return {
            "success": True,
            "count": len(history),
            "filters": filters,
            "trades": history
        }
        
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/trade/{trade_id}")
async def get_trade_details(trade_id: str):
    """Get details for a specific trade"""
    try:
        # Sync any bot trades first
        trading_controller.sync_bot_trades()
        
        # Check active trades first
        if trade_id in trading_controller.active_trades:
            return {
                "success": True,
                "trade": trading_controller.active_trades[trade_id]
            }
        
        # Check history
        for trade in trading_controller.trade_history:
            if trade['trade_id'] == trade_id:
                return {
                    "success": True,
                    "trade": trade
                }
        
        raise HTTPException(status_code=404, detail=f"Trade {trade_id} not found")
        
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/health")
async def health_check():
    """Health check endpoint"""
    try:
        mt5_connected = mt5.initialize()
        trading_controller.sync_bot_trades()
        return {
            "status": "healthy",
            "mt5_connected": bool(mt5_connected),
            "active_trades": len(trading_controller.active_trades),
            "total_history_trades": len(trading_controller.trade_history),
            "timestamp": datetime.now().isoformat()
        }
    except Exception as e:
        return {
            "status": "unhealthy",
            "error": str(e)
        }


def run_api_server(host='127.0.0.1', port=8000, debug=True):
    """Run the FastAPI server"""
   
    logger.info(f"Starting Trading API Server on {host}:{port}")
    uvicorn.run(app, host=host, port=port, log_level="info" if debug else "warning")


if __name__ == '__main__':
    run_api_server()