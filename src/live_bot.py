# src/live_bot.py
import time
import joblib
import pandas as pd
import numpy as np
import MetaTrader5 as mt5
from src.utils import connect_mt5, disconnect_mt5, get_latest_ticks, place_order, logger
from src.indicators import add_all_indicators
import os
import uuid

MODEL_DIR = os.path.join(os.path.dirname(__file__), "..", "models")
MODEL_PATH = os.path.join(MODEL_DIR, "trained_scalping_model.pkl")
LABEL_ENCODER_PATH = os.path.join(MODEL_DIR, "signal_label_encoder.pkl")
SYMBOL_ENCODER_PATH = os.path.join(MODEL_DIR, "symbol_label_encoder.pkl")

STATUS_FILE = "bot_status.txt"
STOP_SIGNAL_FILE = "bot_stop_signal.txt"
BOT_CONTROL_FILE = "bot_control.json"

MAX_POSITIONS_PER_SYMBOL = 3  
MAX_TOTAL_POSITIONS = 10

def generate_trade_id():
    return str(uuid.uuid4())[:8]

def update_status(status):
    with open(STATUS_FILE, 'w') as f:
        f.write(status)

def check_stop_signal():
    return os.path.exists(STOP_SIGNAL_FILE)

def check_bot_control():
    """Check if there are control commands from the API"""
    if os.path.exists(BOT_CONTROL_FILE):
        try:
            with open(BOT_CONTROL_FILE, 'r') as f:
                control = json.load(f)
            os.remove(BOT_CONTROL_FILE)
            return control
        except:
            return None
    return None

def get_symbol_positions(symbol):
    positions = mt5.positions_get(symbol=symbol)
    if positions is None:
        return []
    return [p for p in positions if p.symbol == symbol]

def get_total_positions():
    positions = mt5.positions_get()
    return len(positions) if positions else 0

def place_order_with_id(symbol, order_type, lots, price, sl, tp, trade_id):
    """Place order with unique trade ID for tracking"""
    request = {
        "action": mt5.TRADE_ACTION_DEAL,
        "symbol": symbol,
        "volume": lots,
        "type": mt5.ORDER_TYPE_BUY if order_type.lower() == 'buy' else mt5.ORDER_TYPE_SELL,
        "price": price,
        "sl": sl,
        "tp": tp,
        "deviation": 10,
        "magic": 100,
        "comment": f"BOT_{trade_id}",
        "type_time": mt5.ORDER_TIME_GTC,
        "type_filling": mt5.ORDER_FILLING_IOC,
    }
    
    result = mt5.order_send(request)
    
    # Log trade for API tracking
    if result and result.retcode == mt5.TRADE_RETCODE_DONE:
        trade_data = {
            "trade_id": trade_id,
            "symbol": symbol,
            "type": order_type.upper(),
            "lots": lots,
            "entry_price": price,
            "sl_price": sl,
            "tp_price": tp,
            "status": "EXECUTED",
            "timestamp": datetime.now().isoformat(),
            "mt5_ticket": result.order,
            "source": "live_bot"
        }
        
        # Append to bot trades file for API to read
        bot_trades_file = "bot_trades.json"
        trades = []
        if os.path.exists(bot_trades_file):
            try:
                with open(bot_trades_file, 'r') as f:
                    trades = json.load(f)
            except:
                trades = []
        
        trades.append(trade_data)
        with open(bot_trades_file, 'w') as f:
            json.dump(trades, f, indent=2)
    
    return result

def run_live(symbol, lots=0.01, sl_pips=8, tp_pips=12, window=50, poll_interval=30, 
             login=None, password=None, server=None, path=None):
    
    if not os.path.exists(MODEL_PATH):
        logger.error(f"Model not found at {MODEL_PATH}. Please train the model first using: python main.py train")
        return
    
    if os.path.exists("emergency_stop.txt"):
        logger.warning("Emergency stop file detected - bot will not start")
        return
    
    ok = connect_mt5(login=login, password=password, server=server, path=path)
    if not ok:
        raise SystemExit("MT5 connect failed")
    
    model_data = joblib.load(MODEL_PATH)
    if isinstance(model_data, dict):
        clf = model_data['model']
        feature_names = model_data.get('feature_names', [])
        logger.info(f"Loaded model with {len(feature_names)} features, trained on {model_data.get('training_date', 'unknown date')}")
    else:
        clf = model_data
        feature_names = []
        logger.info("Loaded model (old format)")
    
    label_enc = joblib.load(LABEL_ENCODER_PATH)
    symbol_enc = None
    if os.path.exists(SYMBOL_ENCODER_PATH):
        symbol_enc = joblib.load(SYMBOL_ENCODER_PATH)

    logger.info(f"Starting live bot for {symbol} (lots={lots}, SL={sl_pips}, TP={tp_pips})")
    logger.info(f"Max positions: {MAX_POSITIONS_PER_SYMBOL} per symbol, {MAX_TOTAL_POSITIONS} total")
    logger.info(f"Poll interval: {poll_interval}s")
    logger.info(f"To stop: Create '{STOP_SIGNAL_FILE}' file or press Ctrl+C")
    
    update_status("RUNNING")
    
    try:
        iteration = 0
        while True:
            iteration += 1
            
            # Check for stop signal
            if check_stop_signal():
                logger.warning("Stop signal detected - shutting down gracefully")
                break
            
            # Check for emergency stop
            if os.path.exists("emergency_stop.txt"):
                logger.warning("Emergency stop detected - immediate shutdown")
                break
            
            # Check for control commands from API
            control = check_bot_control()
            if control and control.get('action') == 'pause':
                logger.info("Pause command received from API")
                time.sleep(control.get('duration', 60))
                continue
            elif control and control.get('action') == 'stop':
                logger.info("Stop command received from API")
                break
            
            logger.info(f"--- Iteration {iteration} ---")
            
            # Get latest market data
            df = get_latest_ticks(symbol, n=window)
            if df.empty or len(df) < 10:
                logger.warning("Not enough data, sleeping")
                time.sleep(poll_interval)
                continue
            
            # Add indicators
            df['symbol'] = symbol
            df = add_all_indicators(df)
            latest = df.iloc[-1].copy()
            
            # Prepare features
            if feature_names:
                feature_cols = feature_names
            else:
                feature_cols = ['open','high','low','close','volume','hl_range','oc_change','return',
                               'ema_5','ema_20','sma_5','sma_20','rsi','atr']
                if symbol_enc is not None:
                    feature_cols.append('symbol_enc')
            
            # Encode symbol if needed
            if symbol_enc is not None and 'symbol_enc' in feature_cols:
                try:
                    latest['symbol_enc'] = symbol_enc.transform([symbol])[0]
                except Exception as e:
                    logger.warning(f"Symbol encoding failed: {e}")
                    latest['symbol_enc'] = 0
            
            # Handle missing features
            missing_features = []
            for col in feature_cols:
                if col not in latest:
                    latest[col] = 0
                    missing_features.append(col)
            
            if missing_features:
                logger.debug(f"Set {len(missing_features)} missing features to 0")
            
            # Prepare feature array
            X_values = []
            for col in feature_cols:
                value = latest[col]
                try:
                    X_values.append(float(value))
                except (ValueError, TypeError):
                    X_values.append(0.0)
                    logger.warning(f"Could not convert feature {col} to float, using 0")
            
            X = np.array(X_values).reshape(1, -1)
            
            # Get prediction
            pred = clf.predict(X)[0]
            signal = label_enc.inverse_transform([pred])[0]
            
            logger.info(f"{symbol} | Price: {latest['close']:.5f} | Signal: {signal}")
            
            # Check position limits
            symbol_positions = get_symbol_positions(symbol)
            total_positions = get_total_positions()
            
            logger.info(f"Positions: {len(symbol_positions)}/{MAX_POSITIONS_PER_SYMBOL} for {symbol}, "
                       f"{total_positions}/{MAX_TOTAL_POSITIONS} total")
            
            # Position limit checks
            if len(symbol_positions) >= MAX_POSITIONS_PER_SYMBOL:
                logger.info(f"Max positions per symbol reached for {symbol}")
                time.sleep(poll_interval)
                continue
            
            if total_positions >= MAX_TOTAL_POSITIONS:
                logger.info(f"Max total positions reached")
                time.sleep(poll_interval)
                continue
            
            # Handle hold signal
            if signal == 'hold':
                logger.info("HOLD signal - no action")
                time.sleep(poll_interval)
                continue
            
            # Get current tick
            tick = mt5.symbol_info_tick(symbol)
            if tick is None:
                logger.warning(f"Could not get tick data for {symbol}")
                time.sleep(poll_interval)
                continue
                
            symbol_info = mt5.symbol_info(symbol)
            if not symbol_info:
                logger.error(f"Could not get symbol info for {symbol}")
                time.sleep(poll_interval)
                continue
            
            # Calculate pip size
            digits = symbol_info.digits
            pip_size = 0.0001 if digits == 5 else 0.01 if digits == 3 else 0.001
            
            # Generate trade ID for tracking
            trade_id = generate_trade_id()
            
            # Execute trade with tracking
            if signal == 'buy':
                price = tick.ask
                sl = price - sl_pips * pip_size
                tp = price + tp_pips * pip_size
                logger.info(f"Executing BUY at {price:.5f} (SL: {sl:.5f}, TP: {tp:.5f})")
                logger.info(f"Trade ID: {trade_id}")
                
                res = place_order_with_id(symbol, 'buy', lots, price, sl, tp, trade_id)
                if res:
                    if res.retcode == mt5.TRADE_RETCODE_DONE:
                        logger.info(f"BUY order executed successfully! Trade ID: {trade_id}")
                    else:
                        logger.warning(f"BUY order failed with code: {res.retcode}")
                else:
                    logger.error("BUY order submission failed")
                    
            elif signal == 'sell':
                price = tick.bid
                sl = price + sl_pips * pip_size
                tp = price - tp_pips * pip_size
                logger.info(f"Executing SELL at {price:.5f} (SL: {sl:.5f}, TP: {tp:.5f})")
                logger.info(f"Trade ID: {trade_id}")
                
                res = place_order_with_id(symbol, 'sell', lots, price, sl, tp, trade_id)
                if res:
                    if res.retcode == mt5.TRADE_RETCODE_DONE:
                        logger.info(f"SELL order executed successfully! Trade ID: {trade_id}")
                    else:
                        logger.warning(f"SELL order failed with code: {res.retcode}")
                else:
                    logger.error("SELL order submission failed")
            
            # Sleep before next iteration
            time.sleep(poll_interval)
            
    except KeyboardInterrupt:
        logger.info("Stopping via KeyboardInterrupt")
    except Exception as e:
        logger.error(f"Unexpected error: {e}")
        import traceback
        traceback.print_exc()
    finally:
        # Clean up
        update_status("STOPPED")
        if os.path.exists(STOP_SIGNAL_FILE):
            os.remove(STOP_SIGNAL_FILE)
        disconnect_mt5()
        logger.info("Bot stopped successfully")