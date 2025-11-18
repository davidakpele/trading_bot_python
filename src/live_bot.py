# src/live_bot.py
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

def run_live(symbol, lots=0.01, sl_pips=8, tp_pips=12, window=50, poll_interval=30, login=None, password=None, server=None, path=None):
    # Connect MT5
    ok = connect_mt5(login=login, password=password, server=server, path=path)
    if not ok:
        raise SystemExit("MT5 connect failed")

    # Load model and encoders - handle both old and new model formats
    model_data = joblib.load(MODEL_PATH)
    
    # Check if model is saved as dictionary (new format) or directly as classifier (old format)
    if isinstance(model_data, dict):
        clf = model_data['model']
        feature_names = model_data.get('feature_names', [])
        logger.info(f"Loaded model with {len(feature_names)} features, trained on {model_data.get('training_date', 'unknown date')}")
    else:
        clf = model_data
        feature_names = []
        logger.info("Loaded model (old format)")
    
    label_enc = joblib.load(LABEL_ENCODER_PATH)
    
    # Load symbol encoder if it exists
    symbol_enc = None
    if os.path.exists(SYMBOL_ENCODER_PATH):
        symbol_enc = joblib.load(SYMBOL_ENCODER_PATH)

    logger.info(f"Starting live loop for {symbol} (lots={lots})")
    
    try:
        while True:
            # Get latest data
            df = get_latest_ticks(symbol, n=window)
            if df.empty or len(df) < 10:
                logger.warning("Not enough data, sleeping")
                time.sleep(poll_interval)
                continue
                
            # Add symbol column and indicators
            df['symbol'] = symbol
            df = add_all_indicators(df)
            
            # Get the latest row
            latest = df.iloc[-1].copy()
            
            # Prepare features - use feature names from model if available
            if feature_names:
                feature_cols = feature_names
                logger.debug(f"Using feature names from model: {feature_cols}")
            else:
                feature_cols = ['open','high','low','close','volume','hl_range','oc_change','return',
                               'ema_5','ema_20','sma_5','sma_20','rsi','atr']
                # Add symbol encoding if encoder exists
                if symbol_enc is not None:
                    feature_cols.append('symbol_enc')
            
            # Add symbol encoding if available and needed
            if symbol_enc is not None and 'symbol_enc' in feature_cols:
                try:
                    latest['symbol_enc'] = symbol_enc.transform([symbol])[0]
                except Exception as e:
                    logger.warning(f"Symbol encoding failed: {e}")
                    latest['symbol_enc'] = 0
            
            # Ensure all features exist and handle missing ones
            missing_features = []
            for col in feature_cols:
                if col not in latest:
                    logger.warning(f"Missing feature: {col}, setting to 0")
                    latest[col] = 0
                    missing_features.append(col)
            
            if missing_features:
                logger.info(f"Set {len(missing_features)} missing features to 0: {missing_features}")
            
            # Create feature vector
            X_values = []
            for col in feature_cols:
                value = latest[col]
                # Convert to numeric, handling any conversion errors
                try:
                    X_values.append(float(value))
                except (ValueError, TypeError):
                    X_values.append(0.0)
                    logger.warning(f"Could not convert feature {col} to float, using 0")
            
            X = np.array(X_values).reshape(1, -1)
            
            # Make prediction
            pred = clf.predict(X)[0]
            signal = label_enc.inverse_transform([pred])[0]
            
            logger.info(f"{symbol} close={latest['close']:.5f} predicted => {signal}")
            
            # Check for existing positions to avoid multiple trades
            positions = mt5.positions_get(symbol=symbol)
            if positions:
                logger.info(f"Already have {len(positions)} open position(s) for {symbol}, skipping trade")
                time.sleep(poll_interval)
                continue
            
            # Skip if signal is 'hold'
            if signal == 'hold':
                logger.info("Hold signal - no trade executed")
                time.sleep(poll_interval)
                continue
            
            # Trading logic
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
            
            # Calculate prices with proper pip size
            digits = symbol_info.digits
            pip_size = 0.0001 if digits == 5 else 0.001
            
            if signal == 'buy':
                price = tick.ask
                sl = price - sl_pips * pip_size
                tp = price + tp_pips * pip_size
                res = place_order(symbol, 'buy', lots=lots, price=price, sl=sl, tp=tp)
                if res:
                    if res.retcode == mt5.TRADE_RETCODE_DONE:
                        logger.info(f"✅ BUY order executed at {price:.5f}, SL: {sl:.5f}, TP: {tp:.5f}")
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
                        logger.info(f"✅ SELL order executed at {price:.5f}, SL: {sl:.5f}, TP: {tp:.5f}")
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