# src/preprocess.py
import pandas as pd
from sklearn.preprocessing import LabelEncoder

def load_dataset(path):
    df = pd.read_csv(path, parse_dates=['timestamp'])
    return df

def prepare_features(df):
    df = df.copy()
    # Ensure sorted per symbol
    df = df.sort_values(['symbol','timestamp']).reset_index(drop=True)
    # create common features
    df['hl_range'] = df['high'] - df['low']
    df['oc_change'] = df['close'] - df['open']
    df['return'] = df.groupby('symbol')['close'].pct_change().fillna(0)
    # label encoding for symbol (if needed)
    le = LabelEncoder()
    df['symbol_enc'] = le.fit_transform(df['symbol'])
    return df, le
