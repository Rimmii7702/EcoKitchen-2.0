"""Check publication paths and common credential signatures."""
import os
import re
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DATA = {"fictional-ingredients.csv", "fictional-recipes.csv", "fictional-ingredient-catalog.csv",
        "fictional-weekly-sales.csv", "fictional-menu.json"}
BLOCKED = {".pt", ".pth", ".onnx", ".pkl", ".joblib", ".sqlite", ".sqlite3", ".db", ".parquet",
           ".arrow", ".feather", ".pem", ".key", ".p12", ".pfx", ".ipynb", ".mp4", ".zip"}
LOCAL = {".git", ".venv", "node_modules", "__pycache__", ".pytest_cache"}
PRIVATE_DIRS = {"instance", "uploads", "inventory_results", "dashboard/dist", "dashboard/.tools"}
SECRETS = re.compile(rb"AIza[A-Za-z0-9_-]{35}|gh[pousr]_[A-Za-z0-9]{36,}|github_pat_[A-Za-z0-9_]{50,}|AKIA[A-Z0-9]{16}|-----BEGIN (?:[A-Z]+ )*PRIVATE KEY-----")
ABSOLUTE = re.compile(rb"(?i)[A-Z]:(?:\\{1,2}|/)Users(?:\\{1,2}|/)")
DEPENDENCIES = re.compile(rb"(?mi)^\s*(?:(?:from|import)\s+)?(?:ultralytics|torch|torchvision)\b")


def names():
    if (ROOT / ".git").exists():
        data = subprocess.check_output(["git", "ls-files", "-z"], cwd=ROOT)
        return [name for name in data.decode("utf-8").split("\0") if name]
    paths = []
    for directory, children, files in os.walk(ROOT):
        path = Path(directory)
        children[:] = [name for name in children if name not in LOCAL
                       and (path / name).relative_to(ROOT).as_posix() not in PRIVATE_DIRS]
        paths.extend((path / name).relative_to(ROOT).as_posix() for name in files)
    return paths


def main():
    failures = []
    for name in names():
        path = ROOT / name
        if (path.is_symlink() or path.suffix.lower() in BLOCKED
                or (path.name.startswith(".env") and path.name != ".env.example")
                or path.name.lower() in {"credentials.json", ".ds_store", ".rhistory"}
                or any(name.startswith(directory + "/") for directory in PRIVATE_DIRS)
                or (name.startswith("data/") and name not in {"data/" + file for file in DATA})):
            failures.append(f"Unexpected publication path: {name}")
            continue
        data = path.read_bytes()
        if data.startswith((b"SQLite format 3\x00", b"PAR1")) or SECRETS.search(data) or ABSOLUTE.search(data):
            failures.append(f"Possible private content or computer-specific path: {name}")
        if (path.suffix == ".py" or path.name.startswith("requirements")) and DEPENDENCIES.search(data):
            failures.append(f"Object detection dependency: {name}")
    if failures:
        raise SystemExit("\n".join(sorted(failures)))
    print("Publication path, credential signature, and dependency checks passed.")


if __name__ == "__main__":
    main()
