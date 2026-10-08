"""Verify DELETE /documents does not crash the Q2 backend."""
import io  # noqa: B1008
import time  # noqa: B1008

import requests  # noqa: B1008

BASE = "http://localhost:8002"
content = b"# t\n\ntest delete safety doc.\n"
r = requests.post(
    f"{BASE}/documents",
    files={"file": ("__deltest.md", content, "text/markdown")},
    timeout=120,
)
print("upload:", r.status_code)
did = r.json()["doc_id"]
r2 = requests.delete(f"{BASE}/documents/{did}", timeout=30)
print("delete:", r2.status_code, r2.json())
time.sleep(1)
h = requests.get(f"{BASE}/health", timeout=15)
print("health after delete:", h.status_code, h.json()["status"])
