"""Build an upload ZIP from explicitly selected application files."""

from pathlib import Path
from zipfile import ZIP_DEFLATED, ZipFile

ROOT = Path(__file__).resolve().parents[1]
OUTPUT = ROOT / "dist" / "ArbeitHelp-pythonanywhere.zip"
FILES = (
    "app.py",
    "ai_teacher.py",
    "config.py",
    "wsgi.py",
    "requirements.txt",
    ".env.example",
    "scripts/check_ai.py",
    "data/questions.json",
)


def build():
    files = [ROOT / name for name in FILES]
    for folder, extensions in (
        ("static", {".html", ".css", ".js", ".svg", ".png", ".jpg", ".webp", ".ico"}),
        ("prompts", {".txt"}),
    ):
        files.extend(
            path for path in (ROOT / folder).rglob("*")
            if path.is_file() and path.suffix.lower() in extensions
        )
    OUTPUT.parent.mkdir(exist_ok=True)
    with ZipFile(OUTPUT, "w", compression=ZIP_DEFLATED) as archive:
        for path in sorted(files):
            archive.write(path, Path("ArbeitHelp") / path.relative_to(ROOT))
    print(f"Created {OUTPUT} ({len(files)} files)")


if __name__ == "__main__":
    build()
