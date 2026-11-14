"""
Display details for the GUI's header, kept in a sidecar YAML file next to the TEI (``<file>.display.yaml``):
a title shown instead of the file name, and each annotator's short name (shown in the circle). The annotator's
full name is not kept here: it is in the TEI (``<respStmt xml:id="..."><name>``).
"""
import os
import tempfile
from pathlib import Path

import yaml


def display_path(tei_path:Path) -> Path:
    return Path(tei_path).with_name(f"{Path(tei_path).stem}.display.yaml")


def load_display(path:Path) -> dict:
    try:
        return yaml.safe_load(Path(path).read_text(encoding="utf-8")) or {}
    except FileNotFoundError:
        return {}


def save_display(data:dict, path:Path):
    path = Path(path)
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            yaml.safe_dump(data, f, sort_keys=False, allow_unicode=True)
        os.replace(tmp, path)
    finally:
        if os.path.exists(tmp):
            os.remove(tmp)


def initials(name:str|None) -> str|None:
    """ 'Robert Turnbull' -> 'RT'; a single word gives its first two letters. """
    words = [word for word in (name or "").replace("-", " ").split() if word[:1].isalpha()]
    if not words:
        return None
    if len(words) == 1:
        return words[0][:2].upper()
    return (words[0][0] + words[-1][0]).upper()
