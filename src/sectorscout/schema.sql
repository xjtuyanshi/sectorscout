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

