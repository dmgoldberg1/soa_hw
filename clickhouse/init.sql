CREATE DATABASE IF NOT EXISTS cinema;

CREATE TABLE IF NOT EXISTS cinema.events
(
    event_id UUID,
    user_id String,
    movie_id String,
    event_type Enum8('VIEW_STARTED' = 1, 'VIEW_FINISHED' = 2, 'VIEW_PAUSED' = 3, 'VIEW_RESUMED' = 4, 'LIKED' = 5, 'SEARCHED' = 6),
    timestamp DateTime64(3, 'UTC'),
    device_type Enum8('MOBILE' = 1, 'DESKTOP' = 2, 'TV' = 3, 'TABLET' = 4),
    session_id String,
    progress_seconds Nullable(Int32),
    event_date Date MATERIALIZED toDate(timestamp)
)
ENGINE = ReplacingMergeTree
PARTITION BY toYYYYMM(event_date)
ORDER BY (event_date, user_id, timestamp, event_id);

CREATE TABLE IF NOT EXISTS cinema.events_queue
(
    event_id UUID,
    user_id String,
    movie_id String,
    event_type Enum8('VIEW_STARTED' = 1, 'VIEW_FINISHED' = 2, 'VIEW_PAUSED' = 3, 'VIEW_RESUMED' = 4, 'LIKED' = 5, 'SEARCHED' = 6),
    timestamp DateTime64(3, 'UTC'),
    device_type Enum8('MOBILE' = 1, 'DESKTOP' = 2, 'TV' = 3, 'TABLET' = 4),
    session_id String,
    progress_seconds Nullable(Int32)
)
ENGINE = Kafka
SETTINGS
    kafka_broker_list = 'kafka-1:9092,kafka-2:9092,kafka-3:9092',
    kafka_topic_list = 'movie-events',
    kafka_group_name = 'clickhouse-movie-events',
    kafka_format = 'AvroConfluent',
    format_avro_schema_registry_url = 'http://schema-registry:8081',
    kafka_num_consumers = 1;

CREATE MATERIALIZED VIEW IF NOT EXISTS cinema.event_ingest TO cinema.events AS
SELECT * FROM cinema.events_queue;

CREATE TABLE IF NOT EXISTS cinema.daily_metrics
(
    day Date,
    computed_at DateTime64(6, 'UTC'),
    metrics Array(Tuple(metric String, dimension String, value Nullable(Float64)))
)
ENGINE = ReplacingMergeTree(computed_at)
ORDER BY day;

CREATE VIEW IF NOT EXISTS cinema.metric_values AS
SELECT day, computed_at, item.metric AS metric, item.dimension AS dimension, item.value AS value
FROM cinema.daily_metrics FINAL
ARRAY JOIN metrics AS item;
