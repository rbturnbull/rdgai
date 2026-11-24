"""
Notes embedded in the text of readings.

Some apparatuses (e.g. ECM Mark) put editorial notes inside the reading text in parentheses:
``θεου (cf. 24b)``, ``om (cf. 27c)``, ``τα περι (cf. 27/4) αυτης παντα``, ``αυτον (κενον)``, ``(Is 40:4-8)``.
These are split off at runtime so that they are never treated as reading text. ``om`` (Latin *omittit*)
left on its own marks an omission.

Cross-references such as ``cf. 8b`` (same verse, word 8, reading b), ``cf. 3/4-6`` (verse 3, words 4-6) and
lists such as ``cf. 38/14, 24`` or ``cf. 18a, c`` are resolved against unit ids of the form
``<book>.<chapter>.<verse>.<start>[-<end>]``. Anything else is left unresolved.
"""
import re
from dataclasses import dataclass
from typing import Iterable

NOTE = re.compile(r"\s*\(([^()]*)\)")
OMISSION = re.compile(r"^om\.?$")
UNIT_ID = re.compile(r"^(?P<prefix>.*\.)(?P<verse>\d+)\.(?P<start>\d+)(?:-(?P<end>\d+))?$")
POSITION_ITEM = re.compile(r"^(?:(?P<verse>\d+)/)?(?P<start>\d+)(?:-(?P<end>\d+))?(?P<reading>[a-z]+)?$")
READING_ITEM = re.compile(r"^(?P<reading>[a-z]+)$")


def parse_reading_notes(text:str) -> tuple[str, list[str]]:
    """ Splits a reading's text into the text proper and the notes given in parentheses. """
    notes = [note.strip() for note in NOTE.findall(text)]
    if not notes:
        # leave text without notes exactly as it is (apart from a lone 'om')
        return ("" if OMISSION.match(text.strip()) else text), []
    cleaned = " ".join(NOTE.sub(" ", text).split())
    if OMISSION.match(cleaned):
        cleaned = ""
    return cleaned, notes


@dataclass
class NoteReference:
    label: str
    unit_id: str | None
    reading: str | None


def _unit_range(unit_id:str) -> tuple[str, int, int, int] | None:
    match = UNIT_ID.match(unit_id)
    if not match:
        return None
    start = int(match["start"])
    end = int(match["end"]) if match["end"] else start
    return match["prefix"], int(match["verse"]), start, end


def _find_unit(prefix:str, verse:int, start:int, end:int|None, unit_ids:list[str], unit_id_set:set[str]) -> str|None:
    exact = f"{prefix}{verse}.{start}" + (f"-{end}" if end is not None else "")
    if exact in unit_id_set:
        return exact
    for candidate in unit_ids:
        parsed = _unit_range(candidate)
        if parsed and parsed[0] == prefix and parsed[1] == verse and parsed[2] <= start <= parsed[3]:
            return candidate
    return None


def resolve_references(note:str, unit_id:str, unit_ids:Iterable[str]) -> list[NoteReference]:
    """
    Returns the cross-references in a note, resolved to unit ids where possible.
    A note that is not entirely a list of references (e.g. 'κενον', 'Is 40:4-8') gives an empty list.
    """
    body = note.strip()
    if body.startswith("cf."):
        body = body[3:].strip()
    items = [item.strip() for item in body.split(",")]
    if not items or not items[0] or not POSITION_ITEM.match(items[0]):
        return []
    if not all(POSITION_ITEM.match(item) or READING_ITEM.match(item) for item in items):
        return []

    unit_ids = list(unit_ids)
    unit_id_set = set(unit_ids)
    current = _unit_range(unit_id)
    verse = current[1] if current else None

    references = []
    previous_unit = None
    for item in items:
        position = POSITION_ITEM.match(item)
        if position:
            if position["verse"]:
                verse = int(position["verse"])
            target = None
            if current is not None and verse is not None:
                end = int(position["end"]) if position["end"] else None
                target = _find_unit(current[0], verse, int(position["start"]), end, unit_ids, unit_id_set)
            references.append(NoteReference(label=item, unit_id=target, reading=position["reading"]))
            previous_unit = target
        else:
            references.append(NoteReference(label=item, unit_id=previous_unit, reading=READING_ITEM.match(item)["reading"]))
    return references
