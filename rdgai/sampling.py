"""
Reproducible random samples of variation units, split into example and test units.

The sample is stored in a sidecar YAML file next to the TEI file (by default ``<file>.sample.yaml``)
so that the TEI file read by teiphy is unchanged, and so that an evaluation can rely on a split that the
annotation tools cannot alter.
"""
import os
import random
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, Field

Role = Literal["example", "test"]


class SampleUnit(BaseModel):
    id: str
    role: Role


class Sample(BaseModel):
    seed: int
    size: int
    test_fraction: float
    max_pairs: int | None = Field(default=None, description="Units with more pairs than this were not eligible.")
    ignore_types: list[str] = Field(default_factory=list)
    eligible_count: int = Field(description="Number of units the sample was drawn from.")
    created: str
    source: str = Field(description="Name of the TEI file the sample was drawn from.")
    units: list[SampleUnit] = Field(description="The sampled units in document order.")

    def role_of(self, unit_id:str) -> Role|None:
        for unit in self.units:
            if unit.id == unit_id:
                return unit.role
        return None

    def ids(self, role:Role|None=None) -> list[str]:
        return [unit.id for unit in self.units if role is None or unit.role == role]


def default_sample_path(tei_path:Path|str) -> Path:
    tei_path = Path(tei_path)
    return tei_path.with_name(f"{tei_path.stem}.sample.yaml")


def draw(
    eligible_ids:list[str],
    size:int,
    seed:int,
    test_fraction:float=0.5,
    document_order:list[str]|None=None,
) -> list[SampleUnit]:
    """
    Draws `size` units from `eligible_ids` and assigns round(size * test_fraction) of them to the test set.

    The result depends only on the set of eligible ids, the size, the seed and the test fraction
    (not on the order of `eligible_ids`). Units are returned in document order if `document_order` is given.
    """
    if size < 1:
        raise ValueError("The sample size must be at least 1.")
    if size > len(eligible_ids):
        raise ValueError(f"Cannot sample {size} units: only {len(eligible_ids)} are eligible.")
    if not 0.0 <= test_fraction <= 1.0:
        raise ValueError("test_fraction must be between 0 and 1.")

    rng = random.Random(seed)
    chosen = rng.sample(sorted(eligible_ids), size)

    shuffled = list(chosen)
    rng.shuffle(shuffled)
    test_count = round(size * test_fraction)
    test_ids = set(shuffled[:test_count])

    if document_order is not None:
        position = {unit_id: index for index, unit_id in enumerate(document_order)}
        chosen.sort(key=lambda unit_id: position.get(unit_id, len(position)))

    return [SampleUnit(id=unit_id, role="test" if unit_id in test_ids else "example") for unit_id in chosen]


def load_sample(path:Path|str) -> Sample|None:
    path = Path(path)
    if not path.exists():
        return None
    return Sample.model_validate(yaml.safe_load(path.read_text(encoding="utf-8")))


def save_sample(sample:Sample, path:Path|str):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            yaml.safe_dump(sample.model_dump(), f, sort_keys=False, allow_unicode=True)
        os.replace(tmp, path)
    finally:
        if os.path.exists(tmp):
            os.remove(tmp)


def now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")
