CREATE TABLE IF NOT EXISTS schema_metadata (
    schema_version INTEGER PRIMARY KEY,
    initialized_at_utc TIMESTAMPTZ NOT NULL,
    config_hash VARCHAR NOT NULL,
    git_commit VARCHAR NOT NULL
);

CREATE TABLE IF NOT EXISTS run_metadata (
    run_id UUID PRIMARY KEY DEFAULT uuid(),
    command VARCHAR NOT NULL,
    asof_date DATE,
    signal_generated_at_utc TIMESTAMPTZ NOT NULL,
    config_hash VARCHAR NOT NULL,
    git_commit VARCHAR NOT NULL,
    data_snapshot_id VARCHAR NOT NULL,
    provider_versions_json VARCHAR NOT NULL,
    universe_version VARCHAR NOT NULL,
    theme_version VARCHAR NOT NULL,
    created_at_utc TIMESTAMPTZ NOT NULL
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
    ingested_at_utc TIMESTAMPTZ NOT NULL
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
    ingested_at_utc TIMESTAMPTZ NOT NULL,
    PRIMARY KEY (symbol, price_date, provider)
);

CREATE TABLE IF NOT EXISTS corporate_actions (
    symbol VARCHAR NOT NULL,
    action_type VARCHAR NOT NULL,
    effective_date DATE NOT NULL,
    ratio_or_amount DOUBLE NOT NULL,
    source VARCHAR NOT NULL,
    ingested_at_utc TIMESTAMPTZ NOT NULL,
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
    created_at_utc TIMESTAMPTZ NOT NULL
);

CREATE TABLE IF NOT EXISTS fundamental_facts (
    fact_id UUID PRIMARY KEY DEFAULT uuid(),
    symbol VARCHAR NOT NULL,
    cik VARCHAR,
    accession_number VARCHAR,
    filing_url VARCHAR,
    provider_fact_id VARCHAR,
    revision_number INTEGER NOT NULL DEFAULT 0,
    is_restatement BOOLEAN NOT NULL DEFAULT false,
    fiscal_period VARCHAR NOT NULL,
    fiscal_year INTEGER NOT NULL,
    fiscal_quarter INTEGER,
    form_type VARCHAR NOT NULL,
    metric_name VARCHAR NOT NULL,
    metric_value DOUBLE NOT NULL,
    period_end_date DATE NOT NULL,
    filing_date DATE,
    accepted_at TIMESTAMPTZ,
    earnings_release_datetime TIMESTAMPTZ,
    available_at TIMESTAMPTZ,
    source VARCHAR NOT NULL,
    provider_updated_at TIMESTAMPTZ,
    ingested_at_utc TIMESTAMPTZ NOT NULL
);

CREATE TABLE IF NOT EXISTS themes (
    theme_id VARCHAR PRIMARY KEY,
    name VARCHAR NOT NULL,
    theme_type VARCHAR NOT NULL,
    discovery_date DATE,
    source VARCHAR NOT NULL,
    confidence DOUBLE,
    evidence VARCHAR,
    created_at_utc TIMESTAMPTZ NOT NULL
);

CREATE TABLE IF NOT EXISTS theme_members (
    theme_id VARCHAR NOT NULL,
    symbol VARCHAR NOT NULL,
    valid_from DATE NOT NULL,
    valid_to DATE,
    source VARCHAR NOT NULL,
    confidence DOUBLE NOT NULL,
    evidence VARCHAR,
    created_at_utc TIMESTAMPTZ NOT NULL,
    PRIMARY KEY (theme_id, symbol, valid_from, source)
);

CREATE TABLE IF NOT EXISTS technical_indicators (
    asof_date DATE NOT NULL,
    symbol VARCHAR NOT NULL,
    close DOUBLE NOT NULL,
    sma_50 DOUBLE,
    sma_150 DOUBLE,
    sma_200 DOUBLE,
    ema_21 DOUBLE,
    atr_14 DOUBLE,
    volume_10d_avg DOUBLE,
    volume_50d_avg DOUBLE,
    pct_from_252d_high DOUBLE,
    rs_lookback_return DOUBLE,
    rs_percentile DOUBLE,
    trend_stage VARCHAR NOT NULL,
    universe_eligible BOOLEAN NOT NULL,
    benchmark_symbol BOOLEAN NOT NULL,
    signal_generated_at_utc TIMESTAMPTZ NOT NULL,
    config_hash VARCHAR NOT NULL,
    git_commit VARCHAR NOT NULL,
    data_snapshot_id VARCHAR NOT NULL,
    universe_version VARCHAR NOT NULL,
    theme_version VARCHAR NOT NULL,
    PRIMARY KEY (asof_date, symbol)
);

CREATE TABLE IF NOT EXISTS market_regime (
    asof_date DATE PRIMARY KEY,
    spy_stage VARCHAR NOT NULL,
    qqq_stage VARCHAR NOT NULL,
    spy_above_50dma BOOLEAN NOT NULL,
    spy_above_200dma BOOLEAN NOT NULL,
    qqq_above_50dma BOOLEAN NOT NULL,
    qqq_above_200dma BOOLEAN NOT NULL,
    pct_universe_above_50dma DOUBLE NOT NULL,
    pct_universe_above_200dma DOUBLE NOT NULL,
    pct_universe_stage2 DOUBLE NOT NULL,
    risk_state VARCHAR NOT NULL,
    signal_generated_at_utc TIMESTAMPTZ NOT NULL,
    config_hash VARCHAR NOT NULL,
    git_commit VARCHAR NOT NULL,
    data_snapshot_id VARCHAR NOT NULL,
    universe_version VARCHAR NOT NULL,
    theme_version VARCHAR NOT NULL
);

CREATE TABLE IF NOT EXISTS theme_scores (
    asof_date DATE NOT NULL,
    theme_id VARCHAR NOT NULL,
    theme_score DOUBLE NOT NULL,
    technical_relative_strength DOUBLE NOT NULL,
    breadth DOUBLE NOT NULL,
    fundamental_acceleration DOUBLE NOT NULL,
    catalyst_score DOUBLE NOT NULL,
    risk_valuation_penalty DOUBLE NOT NULL,
    component_coverage_pct DOUBLE NOT NULL,
    members_count INTEGER NOT NULL,
    raw_members_count INTEGER NOT NULL,
    eligible_members_count INTEGER NOT NULL,
    excluded_members_count INTEGER NOT NULL,
    technical_coverage_pct DOUBLE NOT NULL,
    theme_fundamental_coverage_pct DOUBLE NOT NULL,
    members_with_valid_fundamentals INTEGER NOT NULL,
    signal_generated_at_utc TIMESTAMPTZ NOT NULL,
    config_hash VARCHAR NOT NULL,
    git_commit VARCHAR NOT NULL,
    data_snapshot_id VARCHAR NOT NULL,
    universe_version VARCHAR NOT NULL,
    theme_version VARCHAR NOT NULL,
    PRIMARY KEY (asof_date, theme_id)
);

CREATE TABLE IF NOT EXISTS stock_scores (
    asof_date DATE NOT NULL,
    symbol VARCHAR NOT NULL,
    theme_id VARCHAR NOT NULL,
    stock_opportunity_score DOUBLE NOT NULL,
    theme_score DOUBLE NOT NULL,
    rs_percentile DOUBLE NOT NULL,
    fundamental_acceleration DOUBLE NOT NULL,
    setup_quality DOUBLE NOT NULL,
    volume_accumulation DOUBLE NOT NULL,
    risk_reward DOUBLE NOT NULL,
    liquidity DOUBLE NOT NULL,
    component_coverage_pct DOUBLE NOT NULL,
    state VARCHAR NOT NULL,
    signal_generated_at_utc TIMESTAMPTZ NOT NULL,
    config_hash VARCHAR NOT NULL,
    git_commit VARCHAR NOT NULL,
    data_snapshot_id VARCHAR NOT NULL,
    universe_version VARCHAR NOT NULL,
    theme_version VARCHAR NOT NULL,
    PRIMARY KEY (asof_date, symbol, theme_id)
);

CREATE TABLE IF NOT EXISTS watchlist (
    asof_date DATE NOT NULL,
    symbol VARCHAR NOT NULL,
    theme_id VARCHAR NOT NULL,
    state VARCHAR NOT NULL,
    reason VARCHAR NOT NULL,
    signal_generated_at_utc TIMESTAMPTZ NOT NULL,
    config_hash VARCHAR NOT NULL,
    git_commit VARCHAR NOT NULL,
    data_snapshot_id VARCHAR NOT NULL,
    universe_version VARCHAR NOT NULL,
    theme_version VARCHAR NOT NULL,
    PRIMARY KEY (asof_date, symbol, theme_id)
);

CREATE TABLE IF NOT EXISTS setups (
    asof_date DATE NOT NULL,
    symbol VARCHAR NOT NULL,
    theme_id VARCHAR NOT NULL,
    setup_type VARCHAR NOT NULL,
    state VARCHAR NOT NULL,
    setup_quality DOUBLE NOT NULL,
    trigger_price DOUBLE,
    stop_loss DOUBLE,
    reward_risk DOUBLE,
    consolidation_start DATE,
    consolidation_end DATE,
    pivot_price DOUBLE,
    depth DOUBLE,
    atr_contraction BOOLEAN NOT NULL,
    volume_contraction BOOLEAN NOT NULL,
    evidence_json VARCHAR NOT NULL,
    signal_generated_at_utc TIMESTAMPTZ NOT NULL,
    config_hash VARCHAR NOT NULL,
    git_commit VARCHAR NOT NULL,
    data_snapshot_id VARCHAR NOT NULL,
    universe_version VARCHAR NOT NULL,
    theme_version VARCHAR NOT NULL,
    PRIMARY KEY (asof_date, symbol, theme_id, setup_type)
);

CREATE TABLE IF NOT EXISTS signals (
    asof_date DATE NOT NULL,
    symbol VARCHAR NOT NULL,
    theme_id VARCHAR NOT NULL,
    setup_type VARCHAR NOT NULL,
    state VARCHAR NOT NULL,
    action_category VARCHAR NOT NULL,
    actionable BOOLEAN NOT NULL,
    reason VARCHAR NOT NULL,
    execution_model VARCHAR NOT NULL,
    entry_trigger DOUBLE,
    stop_loss DOUBLE,
    reward_risk DOUBLE,
    setup_data_present BOOLEAN NOT NULL,
    execution_data_quality_pass BOOLEAN NOT NULL,
    price_snapshot_quality_pass BOOLEAN NOT NULL,
    data_quality_pass BOOLEAN NOT NULL,
    data_quality_reason VARCHAR NOT NULL,
    market_gate_pass BOOLEAN NOT NULL,
    market_gate_reason VARCHAR NOT NULL,
    market_regime_risk_state VARCHAR NOT NULL,
    portfolio_risk_pass BOOLEAN NOT NULL,
    portfolio_risk_reason VARCHAR NOT NULL,
    signal_generated_at_utc TIMESTAMPTZ NOT NULL,
    config_hash VARCHAR NOT NULL,
    git_commit VARCHAR NOT NULL,
    data_snapshot_id VARCHAR NOT NULL,
    universe_version VARCHAR NOT NULL,
    theme_version VARCHAR NOT NULL,
    PRIMARY KEY (asof_date, symbol, theme_id, setup_type)
);
