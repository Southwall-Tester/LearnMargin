"""Create the independent, portable skill archive."""
from pathlib import Path
from zipfile import ZIP_DEFLATED, ZipFile

from check_skill import check

root = Path(__file__).resolve().parents[1]
skill = root / "skills" / "learnmargin"
check(skill)
output = root / "dist" / "learnmargin-skill.zip"
output.parent.mkdir(exist_ok=True)
with ZipFile(output, "w", ZIP_DEFLATED) as archive:
    for path in sorted(skill.rglob("*")):
        if path.is_file() and "__pycache__" not in path.parts:
            archive.write(path, Path("learnmargin") / path.relative_to(skill))
with ZipFile(output) as archive:
    assert archive.testzip() is None
print(output)
