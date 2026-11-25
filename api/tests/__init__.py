# Makes api/tests/ a package, so `from tests.conftest import ...` and
# `from app...` both resolve when pytest runs from api/ (pytest inserts
# the api/ directory on sys.path only when it can walk up from a test
# file through __init__.py files).
