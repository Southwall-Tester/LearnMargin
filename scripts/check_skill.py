"""Check the distributable skill's references, chapter coverage and portability."""
import re
from pathlib import Path


def check(root: Path) -> None:
    entry = (root / "SKILL.md").read_text(encoding="utf-8")
    assert re.search(r"(?m)^name: learnmargin$", entry), "Unexpected skill name"
    chapters = set()
    for path in root.rglob("*"):
        if not path.is_file() or "__pycache__" in path.parts:
            continue
        assert path.suffix not in {".epub", ".pdf", ".pyc"}, f"Unexpected packaged file: {path.name}"
        if path.suffix not in {".md", ".py", ".html", ".css", ".yaml"}:
            continue
        text = path.read_text(encoding="utf-8")
        assert not re.search(r"[A-Za-z]:[\\/]Users[\\/]|1779775642187", text), "Private machine reference"
        if path.suffix == ".md":
            for link in re.findall(r"\]\(([^)]+)\)", text):
                if "://" not in link and not link.startswith("#"):
                    assert (path.parent / link.split("#")[0]).exists(), f"Missing link: {path.name} -> {link}"
        if path.name in {"book-foundations.md", "book-practice-memory.md", "book-mastery-exams.md"}:
            chapters.update(int(number) for number in re.findall(r"(?m)^## 第(\d+)章", text))
    assert chapters == set(range(1, 19)), "Incomplete chapter coverage"


if __name__ == "__main__":
    check(Path(__file__).resolve().parents[1] / "skills" / "learnmargin")
    print("Skill links, portability and 18-chapter coverage: passed")
