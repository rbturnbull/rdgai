"""
Rule-based labels for typed subreadings.

ECM apparatuses type some readings as subreadings of another reading: e.g. orthographic readings ``ao``,
``ao1``, ``ao2`` are spellings of reading ``a``. Given a mapping from reading type to category
(e.g. orthographic → Orthography), the pairs these readings form with their parent are labelled by rule:

1. subreading ↔ its parent: the mapped category;
2. two subreadings of the same parent and type: the mapped category.

Any other pair with a subreading (e.g. ``ao``–``b`` or ``ao``–``bo``) is an ordinary pair, classified by an annotator
or the pipeline like any other.

A subreading's parent is found by removing the rule's suffix and any digits after it from the reading's id
(``ao2`` → ``a``). Readings whose parent cannot be found, and subreadings of subreadings, are reported, never guessed.

The rules are stored in the TEI file next to the category definitions::

    <interpGrp type="subreading-rules">
      <interp xml:id="rule-orthographic" type="orthographic" ana="#Orthography" n="o">…</interp>
    </interpGrp>

``@type`` is the reading type, ``@ana`` the category and ``@n`` the id suffix. Labels made by a rule have
``resp="#rule-orthographic"``, pointing to the rule.
"""
import re
from dataclasses import dataclass, field

DEFAULT_SUFFIXES = {"orthographic": "o", "defective": "f"}
RULE_GROUP_TYPE = "subreading-rules"


@dataclass(frozen=True)
class SubreadingRule:
    reading_type: str
    category: str
    suffix: str

    @property
    def resp_id(self) -> str:
        return "rule-" + re.sub(r"[^A-Za-z0-9_.\-]", "-", self.reading_type)

    def description(self) -> str:
        return (
            f"Readings of type '{self.reading_type}' are subreadings of the reading whose id is theirs without the suffix "
            f"'{self.suffix}' and any digits after it. A subreading and its parent, and two such subreadings of the same "
            f"parent, are labelled {self.category}."
        )


@dataclass
class Parents:
    parents: dict[str, str] = field(default_factory=dict)   # subreading id -> parent id
    types: dict[str, str] = field(default_factory=dict)     # subreading id -> reading type
    unresolved: list[str] = field(default_factory=list)     # typed readings whose parent is not in the unit
    nested: list[tuple[str, str]] = field(default_factory=list)  # (subreading, parent) where the parent is itself typed

    @property
    def skipped(self) -> set[str]:
        return set(self.unresolved) | {reading for reading, _ in self.nested}


@dataclass
class Coverage:
    rule: int          # 1 (subreading and parent) or 2 (sibling subreadings)
    reading_type: str


def find_parents(app, rules:list[SubreadingRule]) -> Parents:
    """ Finds the parent of each typed subreading in a unit (an rdgai.apparatus.App). """
    by_type = {rule.reading_type: rule for rule in rules}
    readings = {reading.n: reading for reading in app.readings}
    result = Parents()
    for reading in app.readings:
        rule = by_type.get(reading.type)
        if rule is None:
            continue
        match = re.match(rf"^(?P<parent>.+?){re.escape(rule.suffix)}\d*$", reading.n)
        if not match or match["parent"] not in readings:
            result.unresolved.append(reading.n)
            continue
        parent = readings[match["parent"]]
        if parent.type:
            result.nested.append((reading.n, parent.n))
            continue
        result.parents[reading.n] = parent.n
        result.types[reading.n] = rule.reading_type
    return result


def coverage(active:str, passive:str, parents:Parents) -> Coverage | None:
    """ Which rule covers the pair active → passive, or None if it is a pair for an annotator. """
    if active in parents.skipped or passive in parents.skipped:
        return None
    if active not in parents.parents and passive not in parents.parents:
        return None
    active_parent = parents.parents.get(active, active)
    passive_parent = parents.parents.get(passive, passive)
    active_type = parents.types.get(active)
    passive_type = parents.types.get(passive)
    if active_parent != passive_parent:
        return None  # a subreading and another reading (or another reading's subreading): an ordinary pair
    if active == active_parent:
        return Coverage(1, passive_type)
    if passive == passive_parent:
        return Coverage(1, active_type)
    if active_type == passive_type:
        return Coverage(2, active_type)
    return None  # subreadings of the same parent but of different types: no rule


def build_rules(stored:list[SubreadingRule], rules:dict[str, str]|None=None, suffixes:dict[str, str]|None=None) -> list[SubreadingRule] | None:
    """
    Combines rules given as options ({reading type: category}, {reading type: suffix}) with the stored rules.
    Given rules replace the stored ones; suffixes alone change the suffix of stored rules. Returns None if
    nothing was given (use the stored rules).
    """
    suffixes = suffixes or {}
    known = {rule.reading_type: rule for rule in stored}
    if not rules and not suffixes:
        return None
    if not rules:
        rules = {rule.reading_type: rule.category for rule in stored}
    result = []
    for reading_type, category in rules.items():
        suffix = suffixes.get(reading_type) or (known[reading_type].suffix if reading_type in known else DEFAULT_SUFFIXES.get(reading_type))
        if not suffix:
            raise ValueError(f"No id suffix known for reading type '{reading_type}': give one, e.g. {reading_type}=o.")
        result.append(SubreadingRule(reading_type=reading_type, category=category, suffix=suffix))
    return result


def parse_assignments(values:list[str]|None, option:str) -> dict[str, str]:
    """ ['orthographic=Orthography'] -> {'orthographic': 'Orthography'} """
    result = {}
    for value in values or []:
        key, separator, item = value.partition("=")
        if not separator or not key.strip() or not item.strip():
            raise ValueError(f"{option} expects TYPE=VALUE, e.g. orthographic=Orthography (got '{value}').")
        result[key.strip()] = item.strip()
    return result
