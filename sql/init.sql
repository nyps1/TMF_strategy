-- 建立擴充模組 TimescaleDB
CREATE EXTENSION IF NOT EXISTS timescaledb;

-- 建立資產基本資料表
CREATE TABLE IF NOT EXISTS assets (
    symbol VARCHAR(20) PRIMARY KEY,
    asset_type VARCHAR(20) NOT NULL, -- 'SPOT' or 'FUTURES'
    description VARCHAR(100),
    created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP
);

-- 建立市場價格紀錄表 (Hypertable)
CREATE TABLE IF NOT EXISTS market_prices (
    time TIMESTAMP WITH TIME ZONE NOT NULL,
    symbol VARCHAR(20) NOT NULL,
    price NUMERIC(15, 4) NOT NULL,
    volume INT NOT NULL,
    CONSTRAINT fk_asset
        FOREIGN KEY(symbol)
        REFERENCES assets(symbol)
        ON DELETE CASCADE
);

-- 建立針對時間序列優化的 Hypertable
SELECT create_hypertable('market_prices', by_range('time'), if_not_exists => TRUE);

-- 建立 Spread (價差) 紀錄表
CREATE TABLE IF NOT EXISTS spread_records (
    time TIMESTAMP WITH TIME ZONE NOT NULL,
    spot_symbol VARCHAR(20) NOT NULL,
    futures_symbol VARCHAR(20) NOT NULL,
    spread_value NUMERIC(15, 4) NOT NULL,
    CONSTRAINT fk_spot
        FOREIGN KEY(spot_symbol)
        REFERENCES assets(symbol)
        ON DELETE CASCADE,
    CONSTRAINT fk_futures
        FOREIGN KEY(futures_symbol)
        REFERENCES assets(symbol)
        ON DELETE CASCADE
);

-- 建立 Spread 紀錄的 Hypertable
SELECT create_hypertable('spread_records', by_range('time'), if_not_exists => TRUE);

-- 建立 Index 策略
-- 針對查詢近期特定資產價格的需求，建立複合索引 (Composite Index)
CREATE INDEX IF NOT EXISTS ix_market_prices_symbol_time ON market_prices (symbol, time DESC);
CREATE INDEX IF NOT EXISTS ix_spread_records_symbols_time ON spread_records (spot_symbol, futures_symbol, time DESC);
