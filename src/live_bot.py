# src/live_bot.py - 
import time
import joblib
import pandas as pd
import numpy as np
import MetaTrader5 as mt5
from src.utils import connect_mt5, disconnect_mt5, get_latest_ticks, place_order, logger
from src.indicators import add_all_indicators
import os

MODEL_DIR = os.path.join(os.path.dirname(__file__), "..", "models")
MODEL_PATH = os.path.join(MODEL_DIR, "trained_scalping_model.pkl")
LABEL_ENCODER_PATH = os.path.join(MODEL_DIR, "signal_label_encoder.pkl")
SYMBOL_ENCODER_PATH = os.path.join(MODEL_DIR, "symbol_label_encoder.pkl")

# TRADING CONFIGURATION 
MAX_POSITIONS_PER_SYMBOL = 3  
MAX_TOTAL_POSITIONS = 10    

def get_symbol_positions(symbol):
    """Get positions for a specific symbol only"""
    positions = mt5.positions_get(symbol=symbol)
    if positions is None:
        return []
    return [p for p in positions if p.symbol == symbol]

def get_total_positions():
    """Get total number of positions across all symbols"""
    positions = mt5.positions_get()
    return len(positions) if positions else 0

def run_live(symbol, lots=0.01, sl_pips=8, tp_pips=12, window=50, poll_interval=30, login=None, password=None, server=None, path=None):
    # Check if model exists before starting
    if not os.path.exists(MODEL_PATH):
        logger.error(f"Model not found at {MODEL_PATH}. Please train the model first using: python main.py train")
        return
    
    # Check for emergency stop
    if os.path.exists("emergency_stop.txt"):
        logger.warning("Emergency stop file detected - bot will not start")
        return
    
    ok = connect_mt5(login=login, password=password, server=server, path=path)
    if not ok:
        raise SystemExit("MT5 connect failed")
    
    # Load model and encoders
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

    logger.info(f"Starting live loop for {symbol} (lots={lots}, max positions per symbol: {MAX_POSITIONS_PER_SYMBOL})")
    
    try:
        while True:
            # Emergency stop check
            if os.path.exists("emergency_stop.txt"):
                logger.warning("Emergency stop detected - shutting down")
                break
            
            df = get_latest_ticks(symbol, n=window)
            if df.empty or len(df) < 10:
                logger.warning("Not enough data, sleeping")
                time.sleep(poll_interval)
                continue
            
            df['symbol'] = symbol
            df = add_all_indicators(df)
            latest = df.iloc[-1].copy()
            
            if feature_names:
                feature_cols = feature_names
                logger.debug(f"Using feature names from model: {feature_cols}")
            else:
                feature_cols = ['open','high','low','close','volume','hl_range','oc_change','return',
                               'ema_5','ema_20','sma_5','sma_20','rsi','atr']
                if symbol_enc is not None:
                    feature_cols.append('symbol_enc')
            
            if symbol_enc is not None and 'symbol_enc' in feature_cols:
                try:
                    latest['symbol_enc'] = symbol_enc.transform([symbol])[0]
                except Exception as e:
                    logger.warning(f"Symbol encoding failed: {e}")
                    latest['symbol_enc'] = 0
            
            missing_features = []
            for col in feature_cols:
                if col not in latest:
                    logger.warning(f"Missing feature: {col}, setting to 0")
                    latest[col] = 0
                    missing_features.append(col)
            
            if missing_features:
                logger.info(f"Set {len(missing_features)} missing features to 0: {missing_features}")
            
            X_values = []
            for col in feature_cols:
                value = latest[col]
                try:
                    X_values.append(float(value))
                except (ValueError, TypeError):
                    X_values.append(0.0)
                    logger.warning(f"Could not convert feature {col} to float, using 0")
            
            X = np.array(X_values).reshape(1, -1)
            
            pred = clf.predict(X)[0]
            signal = label_enc.inverse_transform([pred])[0]
            
            logger.info(f"{symbol} close={latest['close']:.5f} predicted => {signal}")
            
            # CHECK POSITION LIMITS (ALLOW MULTIPLE POSITIONS)
            symbol_positions = get_symbol_positions(symbol)
            total_positions = get_total_positions()
            
            logger.info(f"Position status: {len(symbol_positions)}/{symbol}, {total_positions} total")
            
            # Check if we've reached position limits
            if len(symbol_positions) >= MAX_POSITIONS_PER_SYMBOL:
                logger.info(f"Max positions per symbol reached ({MAX_POSITIONS_PER_SYMBOL}) for {symbol}, skipping trade")
                time.sleep(poll_interval)
                continue
            
            if total_positions >= MAX_TOTAL_POSITIONS:
                logger.info(f"Max total positions reached ({MAX_TOTAL_POSITIONS}), skipping trade")
                time.sleep(poll_interval)
                continue
            
            if signal == 'hold':
                logger.info("Hold signal - no trade executed")
                time.sleep(poll_interval)
                continue
            
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
            
            # Correct pip size calculation for JPY pairs
            digits = symbol_info.digits
            pip_size = 0.0001 if digits == 5 else 0.01 if digits == 3 else 0.001
            
            if signal == 'buy':
                price = tick.ask
                sl = price - sl_pips * pip_size
                tp = price + tp_pips * pip_size
                res = place_order(symbol, 'buy', lots=lots, price=price, sl=sl, tp=tp)
                if res:
                    if res.retcode == mt5.TRADE_RETCODE_DONE:
                        logger.info(f"BUY order executed at {price:.5f}, SL: {sl:.5f}, TP: {tp:.5f}")
                    else:
                        logger.warning(f"BUY order failed with code: {res.retcode}")
                else:
                    logger.error("BUY order submission failed")
                    
            elif signal == 'sell':
                price = tick.bid
                sl = price + sl_pips * pip_size
                tp = price - tp_pips * pip_size
                res = place_order(symbol, 'sell', lots=lots, price=price, sl=sl, tp=tp)
                if res:
                    if res.retcode == mt5.TRADE_RETCODE_DONE:
                        logger.info(f"SELL order executed at {price:.5f}, SL: {sl:.5f}, TP: {tp:.5f}")
                    else:
                        logger.warning(f"SELL order failed with code: {res.retcode}")
                else:
                    logger.error("SELL order submission failed")
            
            time.sleep(poll_interval)
            
    except KeyboardInterrupt:
        logger.info("Stopping live loop via KeyboardInterrupt")
    except Exception as e:
        logger.error(f"Unexpected error: {e}")
        import traceback
        traceback.print_exc()
    finally:
        disconnect_mt5()