"""
Contextual translations of the verse with each reading in place, for the annotator.

They are written by Claude through MCP (``set_translations``) and stored in a sidecar YAML file next to the TEI file
(by default ``<file>.translations.yaml``), never in the TEI. Each translation keeps the text of the reading it was
made for, so a translation whose reading has since changed is not shown.
"""
import os
import tempfile
from pathlib import Path

import yaml
from pydantic import BaseModel, Field


class ReadingTranslation(BaseModel):
    reading: str = Field(description="The reading's text when it was translated.")
    translation: str


class UnitTranslations(BaseModel):
    source: str = ""
    when: str = ""
    readings: dict[str, ReadingTranslation] = Field(default_factory=dict)


class Translations(BaseModel):
    units: dict[str, UnitTranslations] = Field(default_factory=dict)


def default_translations_path(tei_path:Path|str) -> Path:
    tei_path = Path(tei_path)
    return tei_path.with_name(f"{tei_path.stem}.translations.yaml")


def load_translations(path:Path|str) -> Translations:
    path = Path(path)
    if not path.exists():
        return Translations()
    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    return Translations.model_validate(data)


def save_translations(translations:Translations, path:Path|str):
    """ Writes the file atomically (a temporary file renamed over it). """
    path = Path(path)
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            yaml.safe_dump(translations.model_dump(), f, sort_keys=False, allow_unicode=True, width=120)
        os.replace(tmp, path)
    finally:
        if os.path.exists(tmp):
            os.remove(tmp)
