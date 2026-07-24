"""
conftest.py
------------
Pytest auto-discovers this file and runs it before collecting any tests in
this directory. Its only job here is path setup: simulator/, ml/, and
storage/ are plain directories (not installed packages), the same way
spark/stream_processor.py reaches across them at runtime -- so tests need
the same sys.path addition to import `trip_generator` and `risk_scorer`
directly, matching the project's existing import style everywhere else.
"""

import os
import sys

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

for _subdir in ("simulator", "ml", "storage"):
    _path = os.path.join(_ROOT, _subdir)
    if _path not in sys.path:
        sys.path.insert(0, _path)
