CREATE TABLE IF NOT EXISTS daily_metrics (
    metric_date date NOT NULL,
    metric text NOT NULL,
    dimension text NOT NULL,
    value double precision,
    computed_at timestamptz NOT NULL,
    PRIMARY KEY (metric_date, metric, dimension)
);

CREATE TABLE IF NOT EXISTS daily_exports (
    metric_date date PRIMARY KEY,
    pending boolean NOT NULL DEFAULT true
);
