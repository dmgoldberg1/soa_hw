CREATE TABLE IF NOT EXISTS daily_metrics (
    day date NOT NULL,
    metric text NOT NULL,
    dimension text NOT NULL,
    value double precision,
    computed_at timestamptz NOT NULL,
    PRIMARY KEY (day, metric, dimension)
);
