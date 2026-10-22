import os
import tempfile

# Must be set before any app module reads settings.
_tmp = tempfile.mkdtemp(prefix="anomaly-tests-")
os.environ.setdefault("DATABASE_URL", f"sqlite+aiosqlite:///{_tmp}/test.db")
os.environ.setdefault("MODEL_DIR", f"{_tmp}/models")
os.environ.setdefault("TIMEZONE", "Asia/Jerusalem")
