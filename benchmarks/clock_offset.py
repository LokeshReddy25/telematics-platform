"""
benchmarks/clock_offset.py
--------------------------
Measures the clock offset between the host and a running container
(container_time - host_time), so e2e_latency.py can compare a host-side
Kafka ack time with the container-side `scored_at` time in InfluxDB.

Takes 7 samples via `docker exec`; uses the sample with the shortest round
trip and reports its uncertainty as +/- half that round trip.

Usage: python benchmarks/clock_offset.py telematics-spark-bench
"""

import json
import subprocess
import sys
import time

container = sys.argv[1] if len(sys.argv) > 1 else "telematics-spark-bench"
samples = []
for _ in range(7):
    t0 = time.time()
    out = subprocess.run(
        ["docker", "exec", container, "python3", "-c", "import time;print(time.time())"],
        capture_output=True,
        text=True,
        check=True,
    ).stdout.strip()
    t1 = time.time()
    c = float(out)
    samples.append({"rtt_ms": (t1 - t0) * 1000, "offset_ms": (c - (t0 + t1) / 2) * 1000})

best = min(samples, key=lambda s: s["rtt_ms"])
print(
    json.dumps(
        {
            "offset_ms": round(best["offset_ms"], 1),
            "uncertainty_ms": round(best["rtt_ms"] / 2, 1),
            "samples": [{k: round(v, 1) for k, v in s.items()} for s in samples],
        }
    )
)
