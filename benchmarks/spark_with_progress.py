"""
benchmarks/spark_with_progress.py
---------------------------------
Measurement harness for the Spark job. It runs the SAME pipeline as
spark/stream_processor.py (it imports and calls that module's own
build_spark_session / read_kafka_stream / build_windowed_events /
process_batch, with the same output mode, trigger and checkpoint setting)
and only adds a StreamingQueryListener that appends each micro-batch's
progress (batchId, numInputRows, durationMs) as one JSON line to
PROGRESS_FILE.

Why a wrapper: stream_processor.py sets the log level to WARN, so its logs
never show micro-batch timings, and the measurement rules for this project
forbid editing pipeline code to add them.

Run inside the Spark image (see benchmarks/README.md for the exact docker
command); it needs /app/spark, /app/ml, /app/storage exactly as the image
lays them out.
"""

import json
import os
import sys

sys.path.insert(0, "/app/spark")

import stream_processor as sp  # noqa: E402
from pyspark.sql.streaming import StreamingQueryListener  # noqa: E402

PROGRESS_FILE = os.environ.get("PROGRESS_FILE", "/out/spark_progress.jsonl")


class ProgressLogger(StreamingQueryListener):
    def onQueryStarted(self, event):
        pass

    def onQueryProgress(self, event):
        p = event.progress
        record = {
            "batchId": p.batchId,
            "timestamp": p.timestamp,
            "numInputRows": p.numInputRows,
            "durationMs": dict(p.durationMs),
        }
        with open(PROGRESS_FILE, "a", encoding="utf-8") as f:
            f.write(json.dumps(record) + "\n")

    def onQueryIdle(self, event):
        pass

    def onQueryTerminated(self, event):
        pass


def main() -> None:
    spark = sp.build_spark_session()
    spark.sparkContext.setLogLevel("WARN")
    spark.streams.addListener(ProgressLogger())

    windowed = sp.build_windowed_events(sp.read_kafka_stream(spark))
    query = (
        windowed.writeStream.outputMode("update")
        .foreachBatch(sp.process_batch)
        .option("checkpointLocation", sp.CHECKPOINT_LOCATION)
        .trigger(processingTime="30 seconds")
        .start()
    )
    print(
        f"Streaming query started. Reading from kafka={sp.KAFKA_BOOTSTRAP_SERVERS} "
        f"topic={sp.KAFKA_TOPIC}, window={sp.WINDOW_DURATION}, checkpoint={sp.CHECKPOINT_LOCATION}",
        flush=True,
    )
    query.awaitTermination()


if __name__ == "__main__":
    main()
