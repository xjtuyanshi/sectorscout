CREATE TABLE IF NOT EXISTS schema_metadata (
    schema_version INTEGER PRIMARY KEY,
    initialized_at_utc TIMESTAMP NOT NULL,
    config_hash VARCHAR NOT NULL,
    git_commit VARCHAR NOT NULL
);

CREATE TABLE IF NOT EXISTS run_metadata (
    run_id UUID PRIMARY KEY DEFAULT uuid(),
    command VARCHAR NOT NULL,
    asof_date DATE,
    signal_generated_at_utc TIMESTAMP NOT NULL,
    config_hash VARCHAR NOT NULL,
    git_commit VARCHAR NOT NULL,
    data_snapshot_id VARCHAR NOT NULL,
    provider_versions_json VARCHAR NOT NULL,
    universe_version VARCHAR NOT NULL,
    theme_version VARCHAR NOT NULL,
    created_at_utc TIMESTAMP NOT NULL
);

CREATE TABLE IF NOT EXISTS symbols (
    symbol VARCHAR PRIMARY KEY,
    name VARCHAR NOT NULL,
    exchange VARCHAR NOT NULL,
    security_type VARCHAR NOT NULL,
    is_etf BOOLEAN NOT NULL,
    is_active BOOLEAN NOT NULL,
    ipo_date DATE,
    delist_date DATE,
    first_seen_at DATE NOT NULL,
    last_seen_at DATE NOT NULL,
    source VARCHAR NOT NULL,
    provider VARCHAR NOT NULL,
    ingested_at_utc TIMESTAMP NOT NULL
);

CREATE TABLE IF NOT EXISTS daily_prices (
    symbol VARCHAR NOT NULL,
    price_date DATE NOT NULL,
    open DOUBLE NOT NULL,
    high DOUBLE NOT NULL,
    low DOUBLE NOT NULL,
    close DOUBLE NOT NULL,
    volume BIGINT NOT NULL,
    adj_open DOUBLE NOT NULL,
    adj_high DOUBLE NOT NULL,
    adj_low DOUBLE NOT NULL,
    adj_close DOUBLE NOT NULL,
    adj_volume BIGINT NOT NULL,
    provider VARCHAR NOT NULL,
    is_adjusted BOOLEAN NOT NULL,
    adjustment_warning BOOLEAN NOT NULL,
    ingested_at_utc TIMESTAMP NOT NULL,
    PRIMARY KEY (symbol, price_date, provider)
);

CREATE TABLE IF NOT EXISTS corporate_actions (
    symbol VARCHAR NOT NULL,
    action_type VARCHAR NOT NULL,
    effective_date DATE NOT NULL,
    ratio_or_amount DOUBLE NOT NULL,
    source VARCHAR NOT NULL,
    ingested_at_utc TIMESTAMP NOT NULL,
    PRIMARY KEY (symbol, action_type, effective_date, source)
);

CREATE TABLE IF NOT EXISTS data_quality_daily (
    asof_date DATE PRIMARY KEY,
    total_symbols INTEGER NOT NULL,
    symbols_with_price_data INTEGER NOT NULL,
    symbols_missing_price_data INTEGER NOT NULL,
    symbols_with_fundamental_data INTEGER NOT NULL,
    stale_price_count INTEGER NOT NULL,
    stale_fundamental_count INTEGER NOT NULL,
    split_adjustment_warnings INTEGER NOT NULL,
    provider_rate_limit_events INTEGER NOT NULL,
    fallback_to_yfinance_count INTEGER NOT NULL,
    provider_mix_json VARCHAR NOT NULL,
    created_at_utc TIMESTAMP NOT NULL
);
