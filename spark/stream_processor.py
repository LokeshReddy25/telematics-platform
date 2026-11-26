"""
stream_processor.py
--------------------
Spark Structured Streaming job that is the backbone of the pipeline: it
reads raw vehicle events from Kafka, groups them into 60-second tumbling
windows per (driver_id, trip_id), hands each window's events to the ML
risk scorer, and persists the result to InfluxDB.

Design notes:
  - We window by event TIME (not processing time) with a watermark, so late
    or bursty event delivery doesn't corrupt trip groupings.
  - Structured Streaming's built-in aggregation functions (avg, max, ...)
    can't call an arbitrary Python function like risk_scorer.score_trip(),
    so instead we use `collect_list(struct(...))` to gather each window's
    raw events, then process the resulting micro-batch DataFrame with
    `foreachBatch`, calling into plain Python (risk_scorer + influxdb_writer)
    there. foreachBatch is the standard bridge point between Structured
    Streaming and arbitrary external I/O.
"""

import os
import sys

from pyspark.sql import SparkSession, DataFrame
from pyspark.sql.functions import col, from_json, window, collect_list, struct
from pyspark.sql.types import (
    StructType,
    StructField,
    StringType,
    DoubleType,
    BooleanType,
    TimestampType,
)

# ml/ and storage/ are sibling directories to spark/, not installed
# packages, so we add them to sys.path at runtime. This mirrors how
# devops/Dockerfile.spark copies all three directories into the same image.
sys.path.append(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "ml"))
sys.path.append(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "storage"))

from risk_scorer import score_trip  # noqa: E402
from influxdb_writer import write_scored_trip  # noqa: E402

KAFKA_BOOTSTRAP_SERVERS = os.environ.get("KAFKA_BOOTSTRAP_SERVERS", "kafka:9092")
KAFKA_TOPIC = "vehicle.trip.events"
CHECKPOINT_LOCATION = os.environ.get("SPARK_CHECKPOINT_LOCATION", "/tmp/telematics-checkpoints")
WINDOW_DURATION = "60 seconds"
WATERMARK_DELAY = "2 minutes"

# Schema mirrors the exact JSON shape produced by
# simulator/trip_generator.py / kafka_producer.py. Keeping this explicit
# (rather than inferring schema) is required for Structured Streaming and
# also documents the wire format in one place.
EVENT_SCHEMA = StructType(
    [
        StructField("driver_id", StringType(), nullable=False),
        StructField("trip_id", StringType(), nullable=False),
        StructField("timestamp", StringType(), nullable=False),
        StructField("speed_kmh", DoubleType(), nullable=False),
        StructField("braking_force_g", DoubleType(), nullable=False),
        StructField("acceleration_g", DoubleType(), nullable=False),
        StructField("latitude", DoubleType(), nullable=False),
        StructField("longitude", DoubleType(), nullable=False),
        StructField("is_night", BooleanType(), nullable=False),
        StructField("driver_profile", StringType(), nullable=False),
    ]
)


def build_spark_session() -> SparkSession:
    """Create the SparkSession configured for local/containerised streaming.

    spark.jars.packages pulls in the Kafka connector at session-build time so
    `python stream_processor.py` works standalone -- devops/Dockerfile.spark
    achieves the same thing via `spark-submit --packages` instead, since the
    container entrypoint invokes spark-submit directly rather than `python`.
    """
    return (
        SparkSession.builder.appName("TelematicsRiskScoring")
        .config("spark.jars.packages", "org.apache.spark:spark-sql-kafka-0-10_2.12:3.5.0")
        .config("spark.sql.shuffle.partitions", "4")
        .getOrCreate()
    )


def read_kafka_stream(spark: SparkSession) -> DataFrame:
    """Subscribe to the raw vehicle event topic and parse each JSON payload
    into typed columns using EVENT_SCHEMA."""
    raw = (
        spark.readStream.format("kafka")
        .option("kafka.bootstrap.servers", KAFKA_BOOTSTRAP_SERVERS)
        .option("subscribe", KAFKA_TOPIC)
        .option("startingOffsets", "latest")
        .option("failOnDataLoss", "false")
        .load()
    )

    parsed = (
        raw.select(from_json(col("value").cast("string"), EVENT_SCHEMA).alias("data"))
        .select("data.*")
        .withColumn("event_time", col("timestamp").cast(TimestampType()))
    )
    return parsed


def build_windowed_events(parsed: DataFrame) -> DataFrame:
    """
    Group raw events into 60-second tumbling windows per (driver_id,
    trip_id), collecting the raw events of each group into a list so
    process_batch() can hand them to risk_scorer.score_trip() unchanged.
    """
    event_struct = struct(
        col("driver_id"),
        col("trip_id"),
        col("timestamp"),
        col("speed_kmh"),
        col("braking_force_g"),
        col("acceleration_g"),
        col("latitude"),
        col("longitude"),
        col("is_night"),
        col("driver_profile"),
    )

    windowed = (
        parsed.withWatermark("event_time", WATERMARK_DELAY)
        .groupBy(
            window(col("event_time"), WINDOW_DURATION),
            col("driver_id"),
            col("trip_id"),
        )
        .agg(collect_list(event_struct).alias("events"))
    )
    return windowed


def process_batch(batch_df: DataFrame, batch_id: int) -> None:
    """
    foreachBatch callback: runs once per micro-batch on the Spark driver.
    Each row here is one (window, driver_id, trip_id) group with its
    collected raw events -- exactly the unit of work risk_scorer.score_trip()
    expects.
    """
    rows = batch_df.collect()
    if not rows:
        return

    for row in rows:
        trip_events = [event.asDict(recursive=True) for event in row["events"]]
        if not trip_events:
            continue

        scored = score_trip(trip_events)

        try:
            write_scored_trip(scored)
        except Exception as exc:  # noqa: BLE001 - keep the stream alive even if one write fails
            print(f"[batch {batch_id}] ERROR writing to InfluxDB for driver={scored['driver_id']}: {exc}")
            continue

        print(
            f"[batch {batch_id}] driver={scored['driver_id']} trip={scored['trip_id']} "
            f"risk_score={scored['risk_score']} category={scored['risk_category']} "
            f"harsh_brakes={scored['harsh_brake_count']} harsh_accels={scored['harsh_accel_count']}"
        )


def main() -> None:
    spark = build_spark_session()
    spark.sparkContext.setLogLevel("WARN")

    parsed_events = read_kafka_stream(spark)
    windowed_events = build_windowed_events(parsed_events)

    query = (
        windowed_events.writeStream.outputMode("update")
        .foreachBatch(process_batch)
        .option("checkpointLocation", CHECKPOINT_LOCATION)
        .trigger(processingTime="30 seconds")
        .start()
    )

    print(
        f"Streaming query started. Reading from kafka={KAFKA_BOOTSTRAP_SERVERS} "
        f"topic={KAFKA_TOPIC}, window={WINDOW_DURATION}, checkpoint={CHECKPOINT_LOCATION}"
    )
    query.awaitTermination()


if __name__ == "__main__":
    main()
