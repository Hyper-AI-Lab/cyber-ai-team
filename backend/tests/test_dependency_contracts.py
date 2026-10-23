"""Installation declarations must include the async database runtime extra."""

from pathlib import Path

from packaging.requirements import Requirement


def test_sqlalchemy_declares_asyncio_extra_for_clean_installs():
    path = Path(__file__).resolve().parents[1] / "requirements.txt"
    requirements = [
        Requirement(line.strip())
        for line in path.read_text().splitlines()
        if line.strip() and not line.lstrip().startswith("#")
    ]
    sqlalchemy = [item for item in requirements if item.name.lower() == "sqlalchemy"]
    assert len(sqlalchemy) == 1
    assert "asyncio" in sqlalchemy[0].extras
