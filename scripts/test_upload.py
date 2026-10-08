"""Quick upload-path verification against the Q2 backend."""
import pathlib  # noqa: B1008
import requests  # noqa: B1008

ROOT = pathlib.Path(r"C:\Users\rouna\OneDrive\Desktop\projects\ASSESMENNT")

png = ROOT / "Question 1 & 3 (Files to use)" / "Question 1 & 3 (Files to use)" / "Question 3" / "Aadhar.png"
r2 = requests.post(
    "http://localhost:8002/documents",
    files={"file": (png.name, png.read_bytes(), "image/png")},
    timeout=60,
)
print("png upload:", r2.status_code, "->", r2.json().get("detail"))

md = ROOT / "solution" / "q1_excel_agent" / "README.md"
r = requests.post(
    "http://localhost:8002/documents",
    files={"file": (md.name, md.read_bytes(), "text/markdown")},
    timeout=120,
)
print("md upload:", r.status_code, r.json().get("filename"), r.json().get("n_chunks"), "chunks")
if r.ok:
    requests.delete(f"http://localhost:8002/documents/{r.json()['doc_id']}", timeout=30)
    print("cleanup done")
