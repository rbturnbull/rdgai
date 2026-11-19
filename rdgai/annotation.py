"""
A service layer for querying and editing the classifications in a TEI file.

This is used by the MCP server (``rdgai.mcp_server``) and is independent of MCP so that it can be tested
and reused directly. The TEI file is the state: every write is saved to disk straight away.
"""
import os
import re
import json
import hashlib
import time
import threading
import tempfile
from contextlib import contextmanager
from pathlib import Path
from datetime import datetime, timezone
from typing import Literal

from lxml import etree as ET
from pydantic import BaseModel, Field

from .apparatus import Doc, App, Pair, RelationType, tei_tag, unique, relation_provenance, prune_empty_list
from .notes import resolve_references
from .diff import describe_change, label_warning
from . import search
from .rules import SubreadingRule, Parents, Coverage, find_parents, coverage as rule_coverage, RULE_GROUP_TYPE, DEFAULT_SUFFIXES

try:
    import fcntl
except ImportError:  # pragma: no cover (Windows)
    fcntl = None
from .tei import find_element, find_elements, extract_text, find_parent, parse_tei
from . import sampling
from .sampling import Sample, default_sample_path
from . import translations as translation_files


DEFAULT_IGNORE_TYPES = ("defective",)
CERTAINTY_VALUES = ("high", "medium", "low")
RDGAI_RESP = "rdgai"

DIRECTION_CONVENTION = (
    "A category describes the change from the ACTIVE (first) reading to the PASSIVE (second) reading. "
    "'Addition' means the passive reading has the extra words; 'Omission' means the passive reading lacks them. "
    "Setting a category on a pair automatically sets the inverse category on the reverse pair."
)

UnitStatus = Literal["unclassified", "partial", "classified", "all"]
SampleFilter = Literal["all", "sampled", "example", "test", "unsampled"]


class AnnotationError(Exception):
    pass


class ReadOnlyError(AnnotationError):
    pass


class DefinitionsLockedError(AnnotationError):
    pass


class UnitNotFoundError(AnnotationError):
    pass


class ConflictError(AnnotationError):
    """ A write was based on a stale view: the current categories differ from what the caller expected. """
    def __init__(self, message:str, current:dict):
        super().__init__(message)
        self.current = current


class _Unchecked:
    def __repr__(self):
        return "UNCHECKED"


UNCHECKED = _Unchecked()
SOURCE = re.compile(r"^(?P<name>[A-Za-z_][A-Za-z0-9_.\-]*)(?::(?P<model>\S+))?$")


NC_NAME = re.compile(r"^[A-Za-z_][A-Za-z0-9_.\-]*$")
XML_ID = "{http://www.w3.org/XML/1998/namespace}id"


# ---------------------------------------------------------------------------
# Result models (these become the structured output of the MCP tools)
# ---------------------------------------------------------------------------

class Category(BaseModel):
    name: str
    description: str
    inverse: str = Field(description="The category assigned to the reverse pair. Equal to name if the category is its own inverse.")
    group: str = Field(default="", description="Optional grouping hint from the <interp>'s @type or @n (used to lay out GUI buttons).")


class Definitions(BaseModel):
    language: str
    guidance: str = Field(description="Guidance given in the XML comment before the category definitions (e.g. a decision order).")
    direction_convention: str
    categories: list[Category]
    definitions_hash: str = Field(description="SHA-256 of the guidance and the categories (name, description, inverse).")


class DefinitionsChange(BaseModel):
    message: str
    relations_updated: int = Field(description="Number of labelled <relation> elements whose category was renamed or reassigned.")
    changed: list[Category] = Field(default_factory=list, description="Categories added or changed, as they now are.")
    removed: list[str] = Field(default_factory=list, description="Names of categories that no longer exist (removed or renamed).")
    guidance: str | None = Field(default=None, description="The new guidance, if it was changed.")
    definitions_hash: str = Field(description="The hash of the definitions after the change.")


class Provenance(BaseModel):
    resp: str = Field(default="", description="Who is responsible, e.g. '#RT' for a human annotator or '#rdgai' for the pipeline.")
    cert: str = Field(default="", description="Certainty: high, medium or low.")
    justification: str = ""
    justification_source: str = Field(default="", description="If the justification's wording was copied from someone else (e.g. 'claude' from a suggestion), who.")
    change: str = Field(default="", description="Pointers to <change> elements: the pipeline run (if any) and the definitions version.")
    when: str = Field(default="", description="When the label was saved (ISO 8601, UTC), if recorded.")
    model: str = Field(default="", description="The model used, if any.")
    definitions_hash: str = Field(default="", description="Hash of the category definitions in force when the label was saved, if recorded.")


class ChangeInfo(BaseModel):
    kind: str = Field(description="identical, addition, omission, reordering or changed (from a word-level diff; a reading aid, not a category).")
    text: str = Field(description="E.g. 'second reading adds: και'. A reading aid only, never a label.")


class PairInfo(BaseModel):
    active: str = Field(description="Identifier of the active (first) reading.")
    passive: str = Field(description="Identifier of the passive (second) reading.")
    active_text: str
    passive_text: str
    category: str | None = Field(description="Category for the change from active to passive, or null if unclassified.")
    inverse_category: str | None = Field(description="Category on the reverse pair (passive to active).")
    provenance: Provenance | None = None
    source: Literal["human", "pipeline", "rule"] | None = Field(default=None, description="Who made the current label, if any.")
    rule: int | None = Field(default=None, description="If a subreading rule covers this pair: 1 (subreading-parent), 2 (sibling subreadings) or 3 (copies the parents' pair). Such pairs are labelled automatically.")
    change: ChangeInfo | None = Field(default=None, description="What changes from the active to the passive reading, from a word-level diff.")
    warning: str | None = Field(default=None, description="Set if the category clearly contradicts the change (e.g. an Omission where the passive reading only adds words).")


class NoteReferenceInfo(BaseModel):
    label: str
    unit_id: str | None = Field(description="The unit referred to, if it could be found.")
    reading: str | None = None


class NoteInfo(BaseModel):
    text: str
    references: list[NoteReferenceInfo] = Field(default_factory=list)


class ReadingInfo(BaseModel):
    id: str
    text: str = Field(description="The reading text, or 'OMIT' for an empty reading (an omission). Notes are not included.")
    notes: list[NoteInfo] = Field(default_factory=list, description="Editorial notes found in the reading text (not part of the reading).")
    type: str = Field(default="", description="The reading's @type, e.g. orthographic or defective.")
    witness_count: int
    witnesses: list[str]
    excluded: bool = Field(description="True if pairs with this reading are not classified (by default, defective readings).")


class UnitSummary(BaseModel):
    id: str
    verse: str
    lemma: str
    reading_count: int
    pair_count: int = Field(description="Number of unordered pairs of substantive readings to classify (excluding pairs covered by subreading rules).")
    classified_count: int
    status: str
    sample_role: str | None = Field(default=None, description="'example' or 'test' if the unit is in the sample.")
    rule_pair_count: int = Field(default=0, description="Pairs labelled automatically by subreading rules (not counted in pair_count).")


class UnitDetail(BaseModel):
    id: str
    verse: str
    context: str = Field(description="The verse with the variation unit marked ⸂…⸃ (⸆ marks an empty base text).")
    context_before: str
    context_after: str
    readings: list[ReadingInfo]
    lacunose_witnesses: list[str]
    pairs: list[PairInfo] = Field(description="Each unordered pair of substantive readings, once.")
    status: str
    sample_role: str | None = Field(default=None, description="'example' or 'test' if the unit is in the sample.")
    translations: dict[str, str] = Field(default_factory=dict, description="English translation of the whole verse with each reading in place, by reading id (if any have been made; see set_translations).")


def check_translation_marks(reading_id:str, text:str):
    """ The words that render the reading are marked ⸂…⸃ (possibly in several spans), never nested or unclosed. """
    depth = 0
    for character in text:
        if character == "⸂":
            depth += 1
        elif character == "⸃":
            depth -= 1
        if depth not in (0, 1):
            raise AnnotationError(
                f"The marks in the translation for reading '{reading_id}' are unbalanced: mark the words that render "
                "the reading as ⸂…⸃ (several spans are allowed, but not nested), and an omission with ⸆."
            )
    if depth:
        raise AnnotationError(f"The translation for reading '{reading_id}' has a ⸂ without a closing ⸃.")


class ViewingInfo(BaseModel):
    unit_id: str = Field(description="The unit open in the annotation GUI.")
    when: str = Field(description="When the GUI opened it (ISO 8601, UTC).")


class TranslationResult(BaseModel):
    unit_id: str
    saved: list[str] = Field(description="Reading ids whose translations were saved.")
    missing: list[str] = Field(description="Substantive readings of the unit that still have no translation.")
    saved_to: str


class UntranslatedList(BaseModel):
    total: int = Field(description="Units matching the filters with a substantive reading not yet translated.")
    unit_ids: list[str]


class UnitList(BaseModel):
    total: int = Field(description="Number of units matching the filters.")
    offset: int
    units: list[UnitSummary]


class CategoryCount(BaseModel):
    category: str
    total: int = Field(description="Number of directed pairs (active → passive) with this category.")
    human: int = Field(description="Of which classified by a human annotator.")
    pipeline: int = Field(description="Of which classified by the rdgai pipeline (tentative).")
    rule: int = Field(default=0, description="Of which labelled by a subreading rule.")


class CategoryCounts(BaseModel):
    pairs_to_classify: int = Field(description="Unordered pairs of substantive readings to classify (excluding pairs covered by subreading rules).")
    classified: int = Field(description="Of which classified.")
    rule_pairs: int = Field(default=0, description="Unordered pairs covered by subreading rules.")
    categories: list[CategoryCount]


class CategoryPair(BaseModel):
    unit_id: str
    context: str
    pair: PairInfo


class CategoryPairList(BaseModel):
    category: str
    total: int
    offset: int
    pairs: list[CategoryPair]


class SampleProgress(BaseModel):
    role: str
    units: int
    units_classified: int = Field(description="Units whose pairs are all classified.")
    pairs: int = Field(description="Pairs to annotate (excluding pairs covered by subreading rules).")
    pairs_classified: int
    rule_pairs: int = Field(default=0, description="Pairs labelled automatically by subreading rules.")


class SampleUnitStatus(BaseModel):
    id: str
    role: str
    status: str
    pair_count: int
    classified_count: int
    rule_pair_count: int = 0


class SampleInfo(BaseModel):
    exists: bool
    path: str
    seed: int | None = None
    size: int | None = None
    test_fraction: float | None = None
    max_pairs: int | None = None
    eligible_count: int | None = None
    created: str | None = None
    progress: list[SampleProgress] = Field(default_factory=list, description="Progress for all sampled units and by role.")
    units: list[SampleUnitStatus] = Field(default_factory=list, description="The sampled units in document order.")


class SuggestionInfo(BaseModel):
    unit_id: str
    active: str
    passive: str
    category: str
    source: str = Field(description="Who made the suggestion, e.g. 'claude' or 'rdgai:<model>'.")
    model: str = ""
    justification: str = ""
    cert: str = ""
    when: str = ""
    definitions_hash: str = ""


class SuggestionResult(BaseModel):
    unit_id: str
    suggestion: SuggestionInfo
    inverse: SuggestionInfo


class SuggestionList(BaseModel):
    suggestions: list[SuggestionInfo]


class RuleConfig(BaseModel):
    reading_type: str
    category: str
    suffix: str


class RuleCount(BaseModel):
    rule: int = Field(description="1: a subreading and its parent; 2: two subreadings of the same parent and type.")
    covered: int = Field(description="Unordered pairs covered by this rule.")
    written: int = Field(description="Pairs whose rule label was written or updated.")
    unchanged: int = Field(description="Pairs whose rule label was already correct.")
    human: int = Field(default=0, description="Pairs left alone because a human labelled them.")


class RuleReading(BaseModel):
    unit_id: str
    reading: str
    parent: str | None = None


class RuleConflict(BaseModel):
    unit_id: str
    active: str
    passive: str
    label: str = Field(description="The human label (kept).")
    rule_label: str = Field(description="What the rule would give.")
    rule: int


class RulesReport(BaseModel):
    rules: list[RuleConfig]
    counts: list[RuleCount]
    removed: int = Field(default=0, description="Leftover rule labels removed from pairs no rule covers (e.g. written by the former rule 3). Human and pipeline labels are never removed.")
    descriptions_updated: list[str] = Field(default_factory=list, description="Stored rules whose description in the TEI was out of date and is rewritten.")
    unresolved: list[RuleReading] = Field(description="Typed readings whose parent could not be found (not guessed).")
    nested: list[RuleReading] = Field(description="Subreadings of subreadings (skipped).")
    conflicts: list[RuleConflict] = Field(description="Human labels that disagree with the rules (kept).")
    dry_run: bool


class SimilarPair(BaseModel):
    corpus: str = Field(description="'target' for this file (other corpora are references).")
    unit_id: str
    verse: str = Field(description="E.g. 'Mark 2:27'.")
    active: str
    passive: str
    active_text: str
    passive_text: str
    change: str
    category: str
    source: str = Field(description="human, or pipeline (tentative).")
    tentative: bool = Field(description="True for pipeline labels: weigh them much less than human (certain) labels.")
    cert: str = ""
    justification: str = ""
    definitions_hash: str = ""
    score: float


class SimilarResults(BaseModel):
    query: dict
    results: list[SimilarPair]


class DecisionsResult(BaseModel):
    written: list[tuple[str, str]] = Field(default_factory=list)
    skipped: list[tuple[str, str]] = Field(default_factory=list, description="Pairs labelled by someone else meanwhile (left as they are).")


class SkippedPair(BaseModel):
    pair: tuple[str, str]
    why: str


class SavedSuggestions(BaseModel):
    written: list[tuple[str, str]] = Field(default_factory=list)
    skipped: list[SkippedPair] = Field(default_factory=list)


# a reason saved on the inverse pair starts like this; copied into a label for the pair as shown, it says so instead
INVERSE_REASON = re.compile(r"^Inverse of (\S+) → (\S+):\s*")


def reason_for_pair(justification:str) -> str:
    """ A suggestion's reason as it reads for the pair shown: one written for the other direction says so. """
    match = INVERSE_REASON.match(justification or "")
    if match:
        return f"Written for {match[1]} → {match[2]}: {justification[match.end():]}"
    return (justification or "").strip()


class WriteResult(BaseModel):
    unit_id: str
    pair: PairInfo
    inverse: PairInfo
    saved_to: str


# ---------------------------------------------------------------------------
# The store
# ---------------------------------------------------------------------------

def verse_of(unit_id:str) -> str:
    """ 'Mark.1.1.12-16' -> 'Mark.1.1'. Returns the id unchanged if it is not in that form. """
    parts = unit_id.split(".")
    if len(parts) >= 4:
        return ".".join(parts[:3])
    return unit_id


def matches_verse(unit_id:str, verse:str) -> bool:
    verse = verse.strip().rstrip(".")
    return unit_id == verse or unit_id.startswith(verse + ".")


class AnnotationStore:
    """
    Wraps a TEI file for reading and writing classifications of reading pairs.

    Args:
        path: The TEI file. It is modified in place on every write.
        annotator: The xml:id of the human responsible for writes made through this store (e.g. 'RT').
        annotator_name: Optional full name, used when adding a <respStmt> for the annotator.
        read_only: If True, all writes raise ReadOnlyError.
        lock_definitions: If True, the category definitions cannot be edited (classifications still can).
        ignore_types: Readings with these @type values are excluded from pairs (teiphy collapses them).
        sample_path: The sidecar YAML file holding the sample of units (default: <file>.sample.yaml).
        translations_path: The sidecar YAML file holding contextual translations (default: <file>.translations.yaml).
        check_sample: Check on loading that an existing sample was drawn with the same ignore_types.
        reading_notes: Split notes in parentheses off reading texts (see rdgai.notes).
        lock_timeout: Seconds to wait for another process's write lock on the file.
        evaluation: Evaluation mode: no read reveals the labels or suggestions of the sample's test units, test units
            cannot be changed, and the definitions are locked. Requires a sample.
    """
    def __init__(
        self,
        path:Path|str,
        annotator:str|None=None,
        annotator_name:str|None=None,
        read_only:bool=False,
        lock_definitions:bool=False,
        ignore_types:tuple[str,...]|list[str]=DEFAULT_IGNORE_TYPES,
        sample_path:Path|str|None=None,
        translations_path:Path|str|None=None,
        check_sample:bool=True,
        reading_notes:bool=True,
        lock_timeout:float=10.0,
        evaluation:bool=False,
    ):
        self.path = Path(path)
        if not self.path.exists():
            raise FileNotFoundError(self.path)
        self.sample_path = Path(sample_path) if sample_path else default_sample_path(self.path)
        self.translations_path = Path(translations_path) if translations_path else translation_files.default_translations_path(self.path)
        self.lock_path = self.path.with_name(f".{self.path.name}.lock")
        self.viewing_path = self.path.with_name(f".{self.path.name}.viewing.json")
        self._viewing = None
        self.reading_notes = reading_notes
        self.lock_timeout = lock_timeout
        self._write_depth = 0
        self.annotator = annotator.lstrip("#") if annotator else None
        self.annotator_name = annotator_name
        self.read_only = read_only
        self.evaluation = evaluation
        self.lock_definitions = lock_definitions or evaluation
        self.ignore_types = set(ignore_types)
        self.lock = threading.RLock()
        self.doc: Doc
        self._file_id = None
        self.load()
        if check_sample or evaluation:
            self.load_sample()  # fails early if the sample was drawn with different settings
        if evaluation and self.load_sample() is None:
            raise AnnotationError(
                f"Evaluation mode needs a sample of example and test units ({self.sample_path.name} not found): draw one first."
            )

    # -- loading and saving --------------------------------------------------

    def file_id(self) -> tuple:
        """ Identifies the file's current version (a replaced file has a new inode even if its mtime is unchanged). """
        stat = self.path.stat()
        return (stat.st_ino, stat.st_mtime_ns, stat.st_size)

    @staticmethod
    def identity(stat:os.stat_result) -> tuple:
        return (stat.st_ino, stat.st_mtime_ns, stat.st_size)

    def load(self, data:bytes|None=None, stat:os.stat_result|None=None):
        """
        Loads the file. Its identity and its content come from one open file, so they always belong to the same
        version: if another process replaces the file meanwhile, the store still knows which version it holds.
        (Recording the identity after parsing could pair old content with a newer identity, and the next write would
        then skip the reload and save over the other process's work.)
        """
        with self.lock:
            if data is None:
                with open(self.path, "rb") as handle:
                    stat = os.fstat(handle.fileno())
                    data = handle.read()
            self.doc = Doc(self.path, tree=parse_tei(data), reading_notes=self.reading_notes)
            self._file_id = self.identity(stat)
            self._digest = hashlib.sha256(data).digest()
            self.rules = self.read_rules()
            self._file_pairs = None   # the whole-file pair structure (file_progress) belongs to the old document

    def sync_for_write(self):
        """
        Called with the write lock held: reloads unless the file's bytes are exactly what this store holds. Compares
        the content itself, so a write can't be fooled by a file identity that looks unchanged (a reused inode).
        """
        with open(self.path, "rb") as handle:
            stat = os.fstat(handle.fileno())
            data = handle.read()
        if hashlib.sha256(data).digest() != self._digest:
            self.load(data, stat)
        else:
            self._file_id = self.identity(stat)

    def refresh(self):
        """ Reloads the file if it has been changed by another program (e.g. a second rdgai process). """
        with self.lock:
            if self.file_id() != self._file_id:
                self.load()

    @contextmanager
    def writing(self):
        """
        Holds this store's lock and an advisory lock on the lock file next to the TEI file, so that only one
        process writes at a time. Writers reload the file inside the lock, so no other process's work is lost.
        """
        with self.lock:
            if self._write_depth > 0 or fcntl is None:
                if self._write_depth == 0:
                    self.sync_for_write()
                self._write_depth += 1
                try:
                    yield
                finally:
                    self._write_depth -= 1
                return
            handle = open(self.lock_path, "a")
            try:
                deadline = time.monotonic() + self.lock_timeout
                while True:
                    try:
                        fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
                        break
                    except BlockingIOError:
                        if time.monotonic() >= deadline:
                            raise AnnotationError(
                                f"{self.path.name} is locked by another process that is writing to it. Try again shortly."
                            )
                        time.sleep(0.05)
                # with the lock held, nobody else can change the file: make sure this store holds its current content
                self.sync_for_write()
                self._write_depth += 1
                try:
                    yield
                finally:
                    self._write_depth -= 1
                    fcntl.flock(handle, fcntl.LOCK_UN)
            finally:
                handle.close()

    def save(self):
        with self.lock:
            # TEI requires at least one <relation> in a <listRelation>: remove emptied lists (also those left by older versions)
            for list_type in ("transcriptional", "suggestion"):
                for list_relation in unique(find_elements(self.doc.tree, f".//listRelation[@type='{list_type}']")):
                    prune_empty_list(list_relation)
            fd, tmp = tempfile.mkstemp(dir=self.path.parent, prefix=f".{self.path.name}.", suffix=".tmp")
            os.close(fd)
            try:
                self.doc.write(tmp)
                with open(tmp, "rb") as handle:
                    digest = hashlib.sha256(handle.read()).digest()
                os.replace(tmp, self.path)
            finally:
                if os.path.exists(tmp):
                    os.remove(tmp)
            self._file_id = self.file_id()
            self._digest = digest

    def check_writable(self):
        if self.read_only:
            raise ReadOnlyError("This server is read-only: classifications cannot be changed.")

    # -- lookups ---------------------------------------------------------------

    def get_app(self, unit_id:str) -> App:
        try:
            return self.doc[unit_id]
        except KeyError:
            raise UnitNotFoundError(f"No variation unit with id '{unit_id}'.")

    def is_excluded(self, reading) -> bool:
        return reading.type in self.ignore_types

    def substantive_pairs(self, app:App) -> list[Pair]:
        return [
            pair for pair in app.non_redundant_pairs
            if not self.is_excluded(pair.active) and not self.is_excluded(pair.passive)
        ]

    def get_pair(self, app:App, active:str, passive:str) -> Pair:
        for reading_id in (active, passive):
            if reading_id not in app.id_to_reading:
                raise AnnotationError(
                    f"Unit '{app}' has no reading '{reading_id}'. Readings: {', '.join(app.id_to_reading)}."
                )
        if active == passive:
            raise AnnotationError("The active and passive readings must be different.")
        for pair in app.pairs:
            if pair.active.n == active and pair.passive.n == passive:
                if self.is_excluded(pair.active) or self.is_excluded(pair.passive):
                    raise AnnotationError(
                        f"Pair {active}→{passive} in '{app}' involves a reading of type "
                        f"{', '.join(sorted(self.ignore_types))} and is not classified."
                    )
                return pair
        raise AnnotationError(f"No pair {active}→{passive} in unit '{app}'.")  # pragma: no cover

    def get_relation_type(self, name:str) -> RelationType:
        name = name.lstrip("#")
        if name not in self.doc.relation_types:
            raise AnnotationError(
                f"Unknown category '{name}'. Categories: {', '.join(self.doc.relation_types)}."
            )
        return self.doc.relation_types[name]

    def hides(self, app:App) -> bool:
        """ In evaluation mode, nothing about the labels or suggestions of test units is revealed. """
        return self.evaluation and self.sample_role(str(app)) == "test"

    def check_unit_writable(self, app:App):
        """ In evaluation mode test units cannot be changed (refused the same way whether labelled or not). """
        if self.hides(app):
            raise AnnotationError(f"In evaluation mode, test unit {app} cannot be changed.")

    def is_classified(self, pair:Pair) -> bool:
        if self.hides(pair.app):
            return False
        return bool(pair.types) or bool(pair.get_inverse().types)

    def status(self, app:App) -> str:
        pairs = self.work_pairs(app)
        if not pairs:
            return "nothing_to_classify"
        classified = sum(self.is_classified(pair) for pair in pairs)
        if classified == 0:
            return "unclassified"
        if classified == len(pairs):
            return "classified"
        return "partial"

    def file_progress(self) -> dict:
        """
        Labelled pairs and complete units in the whole file. Which pairs need a label depends only on the document
        (not on the labels), so each pair and its inverse are worked out once per loaded document and reused; only the
        labels are checked each time.
        """
        with self.lock:
            if getattr(self, "_file_pairs", None) is None:
                units = []
                for app in self.doc.apps:
                    directed = {(pair.active.n, pair.passive.n): pair for pair in app.pairs}
                    units.append((app, [(pair, directed[(pair.passive.n, pair.active.n)]) for pair in self.work_pairs(app)]))
                self._file_pairs = units
            counts = dict(pairs_done=0, pairs_total=0, units_done=0, units_total=0)
            for app, pairs in self._file_pairs:
                hidden = self.hides(app)
                done = 0 if hidden else sum(bool(pair.types) or bool(inverse.types) for pair, inverse in pairs)
                counts["pairs_done"] += done
                counts["pairs_total"] += len(pairs)
                counts["units_done"] += done == len(pairs)
                counts["units_total"] += 1
            return counts

    def changes_by_id(self) -> dict:
        return {
            element.attrib.get(XML_ID): element
            for element in unique(find_elements(self.doc.tree, ".//revisionDesc/change"))
        }

    def change_info(self, change:str) -> dict:
        """
        Looks up the <change> elements referenced by a relation's @change attribute: a pipeline run or model
        (giving the model and the run's time) and the definitions version (giving the definitions hash).
        """
        info = dict(run_when="", model="", definitions_hash="")
        if not change:
            return info
        changes = self.changes_by_id()
        for token in change.split():
            element = changes.get(token.lstrip("#"))
            if element is None:
                continue
            if element.attrib.get("type") == "definitions":
                idno = next(iter(unique(find_elements(element, ".//idno"))), None)
                info["definitions_hash"] = (idno.text or "").strip() if idno is not None else ""
                continue
            model = next((extract_text(name, include_tail=False) for name in unique(find_elements(element, ".//name[@type='model']"))), "")
            if model:
                info["model"] = model
            if element.attrib.get("when") and element.attrib.get("type") != "model":
                info["run_when"] = element.attrib["when"]
        return info

    def provenance_from_relation(self, relation) -> Provenance:
        data = relation_provenance(relation)
        info = self.change_info(data["change"])
        return Provenance(
            resp=data["resp"],
            cert=data["cert"],
            justification=data["justification"],
            justification_source=data["justification_source"],
            change=data["change"],
            when=data["when"] or info["run_when"],
            model=info["model"],
            definitions_hash=info["definitions_hash"],
        )

    # -- serialisation -----------------------------------------------------------

    def pair_info(self, pair:Pair) -> PairInfo:
        category = sorted(pair.relation_type_names())
        inverse_category = sorted(pair.get_inverse().relation_type_names())
        provenance = None
        relation = pair.relation_element()
        hidden = self.hides(pair.app)
        if hidden:
            category, inverse_category, relation = [], [], None
        if relation is not None:
            provenance = self.provenance_from_relation(relation)
        covered = self.coverage_for(pair)
        change = describe_change(pair.active.text, pair.passive.text)
        return PairInfo(
            active=pair.active.n,
            passive=pair.passive.n,
            active_text=str(pair.active),
            passive_text=str(pair.passive),
            category=" ".join(category) or None,
            inverse_category=" ".join(inverse_category) or None,
            provenance=provenance,
            source=None if hidden else self.label_source(pair),
            rule=covered.rule if covered else None,
            change=ChangeInfo(kind=change.kind, text=change.text),
            warning=label_warning(" ".join(category) or None, change),
        )

    def unit_summary(self, app:App) -> UnitSummary:
        pairs = self.work_pairs(app)
        lemma = extract_text(find_element(app.element, ".//lem"))
        return UnitSummary(
            id=str(app),
            verse=verse_of(str(app)),
            lemma=lemma or "OMIT",
            reading_count=len(app.readings),
            pair_count=len(pairs),
            classified_count=sum(self.is_classified(pair) for pair in pairs),
            status=self.status(app),
            sample_role=self.sample_role(str(app)),
            rule_pair_count=len(self.rule_pairs(app)),
        )

    # -- read operations -----------------------------------------------------------

    def categories(self) -> list[Category]:
        return [
            Category(
                name=relation_type.name,
                description=relation_type.description,
                inverse=relation_type.get_inverse().name,
                group=relation_type.element.attrib.get("type") or relation_type.element.attrib.get("n") or "",
            )
            for relation_type in self.doc.relation_types.values()
        ]

    def definitions_hash(self) -> str:
        """ SHA-256 of the guidance and the categories (name, description, inverse) in the loaded document. """
        canonical = json.dumps(
            dict(
                guidance=self.doc.get_definitions_comment(),
                categories=[[category.name, category.description, category.inverse] for category in self.categories()],
            ),
            ensure_ascii=False,
        )
        return hashlib.sha256(canonical.encode("utf-8")).hexdigest()

    def definitions(self) -> Definitions:
        with self.lock:
            self.refresh()
            return Definitions(
                language=self.doc.language,
                guidance=self.doc.get_definitions_comment(),
                direction_convention=DIRECTION_CONVENTION,
                categories=self.categories(),
                definitions_hash=self.definitions_hash(),
            )

    def filter_units(self, status:UnitStatus="all", verse:str="", sample:SampleFilter="all") -> list[App]:
        """ The units matching the filters, in document order (call refresh() first). """
        apps = self.doc.apps
        if sample != "all":
            roles = self.sample_roles()
            if sample == "sampled":
                apps = [app for app in apps if str(app) in roles]
            elif sample == "unsampled":
                apps = [app for app in apps if str(app) not in roles]
            else:
                apps = [app for app in apps if roles.get(str(app)) == sample]
        if verse:
            apps = [app for app in apps if matches_verse(str(app), verse)]
        if status != "all":
            apps = [app for app in apps if self.status(app) == status]
        return apps

    def list_units(
        self,
        status:UnitStatus="all",
        verse:str="",
        sample:SampleFilter="all",
        offset:int=0,
        limit:int=50,
    ) -> UnitList:
        with self.lock:
            self.refresh()
            apps = self.filter_units(status=status, verse=verse, sample=sample)
            offset = max(offset, 0)
            limit = max(min(limit, 500), 1)
            return UnitList(
                total=len(apps),
                offset=offset,
                units=[self.unit_summary(app) for app in apps[offset:offset+limit]],
            )

    def note_info(self, app:App, note:str) -> NoteInfo:
        references = resolve_references(note, str(app), self.doc.id_to_app.keys())
        return NoteInfo(
            text=note,
            references=[NoteReferenceInfo(label=ref.label, unit_id=ref.unit_id, reading=ref.reading) for ref in references],
        )

    def ordered_pairs(self, app:App) -> list[Pair]:
        """
        The unordered pairs of substantive readings, each oriented with the better-attested reading first
        (ties in document order), sorted by the attestation rank of their readings.
        """
        readings = [reading for reading in app.readings if not self.is_excluded(reading)]
        ranked = sorted(readings, key=lambda reading: (-len(reading.witnesses), app.readings.index(reading)))
        directed = {(pair.active.n, pair.passive.n): pair for pair in app.pairs}
        return [
            directed[(ranked[i].n, ranked[j].n)]
            for i in range(len(ranked))
            for j in range(i + 1, len(ranked))
        ]

    def reading_ordered_pairs(self, app:App) -> list[Pair]:
        """
        The unordered pairs of substantive readings in reading order, each with the earlier reading first:
        a → b, a → c, …, b → c, … (the GUI's order; the pipeline and MCP use ``ordered_pairs``).
        """
        readings = [reading for reading in app.readings if not self.is_excluded(reading)]
        directed = {(pair.active.n, pair.passive.n): pair for pair in app.pairs}
        return [
            directed[(readings[i].n, readings[j].n)]
            for i in range(len(readings))
            for j in range(i + 1, len(readings))
        ]

    def get_unit(self, unit_id:str, pair_order:Literal["document", "attestation"]="document") -> UnitDetail:
        with self.lock:
            self.refresh()
            app = self.get_app(unit_id)
            pairs = self.ordered_pairs(app) if pair_order == "attestation" else self.substantive_pairs(app)
            readings = [
                ReadingInfo(
                    id=reading.n,
                    text=str(reading),
                    notes=[self.note_info(app, note) for note in reading.notes],
                    type=reading.type,
                    witness_count=len(reading.witnesses),
                    witnesses=reading.witnesses,
                    excluded=self.is_excluded(reading),
                )
                for reading in app.readings
            ]
            return UnitDetail(
                id=str(app),
                verse=verse_of(str(app)),
                context=app.text_in_context(),
                context_before=app.text_before(),
                context_after=app.text_after(),
                readings=readings,
                lacunose_witnesses=app.lacunae(),
                pairs=[self.pair_info(pair) for pair in pairs],
                status=self.status(app),
                sample_role=self.sample_role(str(app)),
                translations=self.translations_for(app),
            )

    def directed_pairs(self):
        """ Yields every directed pair of substantive readings in the document. """
        for app in self.doc.apps:
            for pair in app.pairs:
                if not self.is_excluded(pair.active) and not self.is_excluded(pair.passive):
                    yield pair

    @staticmethod
    def is_pipeline(pair:Pair) -> bool:
        return pair.rdgai_responsible()

    def category_counts(self) -> CategoryCounts:
        with self.lock:
            self.refresh()
            counts = {name: CategoryCount(category=name, total=0, human=0, pipeline=0, rule=0) for name in self.doc.relation_types}
            for pair in self.directed_pairs():
                if self.hides(pair.app):
                    continue
                source = self.label_source(pair)
                for name in pair.relation_type_names():
                    count = counts.setdefault(name, CategoryCount(category=name, total=0, human=0, pipeline=0, rule=0))
                    count.total += 1
                    setattr(count, source, getattr(count, source) + 1)
            pairs = [pair for app in self.doc.apps for pair in self.work_pairs(app)]
            return CategoryCounts(
                pairs_to_classify=len(pairs),
                classified=sum(self.is_classified(pair) for pair in pairs),
                rule_pairs=sum(len(self.rule_pairs(app)) for app in self.doc.apps),
                categories=list(counts.values()),
            )

    def list_category(
        self,
        category:str,
        source:Literal["all", "human", "pipeline"]="all",
        offset:int=0,
        limit:int=50,
    ) -> CategoryPairList:
        """ Lists the directed pairs with a category, in document order. """
        with self.lock:
            self.refresh()
            relation_type = self.get_relation_type(category)
            pairs = [pair for pair in self.directed_pairs() if relation_type in pair.types and not self.hides(pair.app)]
            if source == "human":
                pairs = [pair for pair in pairs if not self.is_pipeline(pair)]
            elif source == "pipeline":
                pairs = [pair for pair in pairs if self.is_pipeline(pair)]
            offset = max(offset, 0)
            limit = max(min(limit, 500), 1)
            return CategoryPairList(
                category=relation_type.name,
                total=len(pairs),
                offset=offset,
                pairs=[
                    CategoryPair(unit_id=str(pair.app), context=pair.app.text_in_context(), pair=self.pair_info(pair))
                    for pair in pairs[offset:offset+limit]
                ],
            )

    # -- editing the definitions ---------------------------------------------------------

    def check_definitions_editable(self):
        self.check_writable()
        if self.lock_definitions:
            raise DefinitionsLockedError("The category definitions are locked on this server.")

    def check_new_category_name(self, name:str):
        if not name or not NC_NAME.match(name):
            raise AnnotationError(
                f"'{name}' is not a valid category name: use letters, digits, '_', '-' or '.', starting with a letter or '_'."
            )
        if name in self.doc.relation_types:
            raise AnnotationError(f"A category named '{name}' already exists.")
        for element in self.doc.tree.getroot().iter():
            if isinstance(element.tag, str) and element.attrib.get(XML_ID) == name:
                raise AnnotationError(f"'{name}' is already used as an xml:id in the document.")

    def category_relations(self, list_type:str="transcriptional") -> list:
        """ The <relation> elements in all <listRelation type="..."> lists (labels or suggestions). """
        relations = []
        for list_relation in unique(find_elements(self.doc.tree, f".//listRelation[@type='{list_type}']")):
            relations.extend(unique(find_elements(list_relation, ".//relation")))
        return relations

    def relations_using(self, names:set[str], list_type:str="transcriptional") -> list:
        pointers = {f"#{name}" for name in names}
        return [
            relation for relation in self.category_relations(list_type)
            if pointers & set(relation.attrib.get("ana", "").split())
        ]

    def rename_in_relations(self, mapping:dict[str, str]) -> int:
        """
        Replaces category pointers in @ana (old name -> new name) in labels and suggestions.
        Returns the number of labels changed.
        """
        pointers = {f"#{old}": f"#{new}" for old, new in mapping.items()}
        for rule_interp in self.rule_interps():
            if rule_interp.get("ana") in pointers:
                rule_interp.set("ana", pointers[rule_interp.get("ana")])
        labels_changed = 0
        for list_type in ("transcriptional", "suggestion"):
            for relation in self.relations_using(set(mapping), list_type):
                tokens = []
                for token in relation.attrib.get("ana", "").split():
                    token = pointers.get(token, token)
                    if token not in tokens:
                        tokens.append(token)
                relation.set("ana", " ".join(tokens))
                if list_type == "transcriptional":
                    labels_changed += 1
        return labels_changed

    @staticmethod
    def set_interp_text(interp, text:str):
        for child in list(interp):
            interp.remove(child)
        interp.text = text

    def commit_definitions(
        self,
        message:str,
        relations_updated:int=0,
        changed:list[str]|None=None,
        removed:list[str]|None=None,
        guidance:str|None=None,
    ) -> DefinitionsChange:
        """
        Saves the file and reloads it so that categories and pairs are rebuilt from the TEI.
        Returns only what changed and the new hash (not the full definitions).
        """
        self.save()
        self.load()
        categories = {category.name: category for category in self.categories()}
        return DefinitionsChange(
            message=message,
            relations_updated=relations_updated,
            changed=[categories[name] for name in (changed or []) if name in categories],
            removed=list(removed or []),
            guidance=guidance,
            definitions_hash=self.definitions_hash(),
        )

    def update_category(self, name:str, description:str|None=None, new_name:str|None=None) -> DefinitionsChange:
        """
        Changes the description and/or the name of a category.
        Renaming also updates the inverse's @corresp and every classification and suggestion that uses the category.
        """
        with self.writing():
            self.check_definitions_editable()
            self.refresh()
            relation_type = self.get_relation_type(name)
            name = relation_type.name
            inverse_name = relation_type.get_inverse().name
            renaming = new_name is not None and new_name != name
            if description is None and not renaming:
                raise AnnotationError("Nothing to change: give a new description or a new name.")

            relations_updated = 0
            if renaming:
                self.check_new_category_name(new_name)
            interp = relation_type.element
            if description is not None:
                self.set_interp_text(interp, description.strip())
            if renaming:
                interp.set(XML_ID, new_name)
                for other in self.doc.relation_types.values():
                    if other.element.attrib.get("corresp", "") == f"#{name}":
                        other.element.set("corresp", f"#{new_name}")
                relations_updated = self.rename_in_relations({name: new_name})

            message = f"Renamed '{name}' to '{new_name}'." if renaming else f"Updated the description of '{name}'."
            if renaming and description is not None:
                message = f"Renamed '{name}' to '{new_name}' and updated its description."
            changed = [new_name if renaming else name]
            if renaming and inverse_name != name:
                changed.append(inverse_name)
            return self.commit_definitions(
                message, relations_updated, changed=changed, removed=[name] if renaming else [],
            )

    def add_category(
        self,
        name:str,
        description:str,
        inverse_name:str|None=None,
        inverse_description:str="",
    ) -> DefinitionsChange:
        """
        Adds a category. Without inverse_name it is its own inverse; with inverse_name a reciprocal pair
        of categories is added, linked with @corresp.
        """
        with self.writing():
            self.check_definitions_editable()
            self.refresh()
            self.check_new_category_name(name)
            if inverse_name is not None:
                if inverse_name == name:
                    raise AnnotationError("The inverse category's name must be different from the category's name.")
                self.check_new_category_name(inverse_name)
            description = (description or "").strip()
            inverse_description = (inverse_description or "").strip()
            if not description:
                raise AnnotationError("A description is required for the new category.")
            if inverse_name is not None and not inverse_description:
                raise AnnotationError("A description is required for the inverse category.")

            interp_group = self.doc.get_interpgrp()
            tag = tei_tag(interp_group, "interp")
            attrib = {XML_ID: name}
            if inverse_name:
                attrib["corresp"] = f"#{inverse_name}"
            ET.SubElement(interp_group, tag, attrib=attrib).text = description
            if inverse_name:
                ET.SubElement(interp_group, tag, attrib={XML_ID: inverse_name, "corresp": f"#{name}"}).text = inverse_description
                return self.commit_definitions(f"Added '{name}' and its inverse '{inverse_name}'.", changed=[name, inverse_name])
            return self.commit_definitions(f"Added '{name}' (its own inverse).", changed=[name])

    def remove_category(self, name:str, reassign_to:str|None=None) -> DefinitionsChange:
        """
        Removes a category together with its inverse. If any pairs are labelled with them, reassign_to must name a
        category with a corresponding inverse (the labels and suggestions are moved to it, keeping their provenance).
        Without reassign_to, suggestions that use the removed categories are deleted.
        """
        with self.writing():
            self.check_definitions_editable()
            self.refresh()
            relation_type = self.get_relation_type(name)
            inverse = relation_type.get_inverse()
            removed = {relation_type.name, inverse.name}
            removed_text = f"'{relation_type.name}'" + (f" and its inverse '{inverse.name}'" if inverse is not relation_type else "")
            rules_using = [rule.resp_id for rule in self.rules if rule.category in removed]
            if rules_using and reassign_to is None:
                raise AnnotationError(
                    f"Cannot remove {removed_text}: used by subreading rule {', '.join(rules_using)}. "
                    "Pass reassign_to to move the rule and its labels to another category."
                )

            relations_updated = 0
            dropped_suggestions = 0
            if reassign_to is not None:
                target = self.get_relation_type(reassign_to)
                if target.name in removed:
                    raise AnnotationError(f"Cannot reassign to '{target.name}': it is being removed.")
                target_inverse = target.get_inverse()
                if (inverse is relation_type) != (target_inverse is target):
                    raise AnnotationError(
                        f"Cannot reassign {removed_text} to '{target.name}' (inverse '{target_inverse.name}'): "
                        "a category that is its own inverse can only be reassigned to another such category, "
                        "and a reciprocal pair only to another reciprocal pair."
                    )
                relations_updated = self.rename_in_relations({relation_type.name: target.name, inverse.name: target_inverse.name})
            else:
                in_use = len(self.relations_using(removed))
                if in_use:
                    raise AnnotationError(
                        f"Cannot remove {removed_text}: used by {in_use} pairs. "
                        "Pass reassign_to to move these classifications to another category."
                    )
                suggestions = self.relations_using(removed, "suggestion")
                dropped_suggestions = len({
                    (find_parent(relation, "app").get(XML_ID), frozenset((relation.get("active"), relation.get("passive"))), relation.get("resp"), relation.get("change"))
                    for relation in suggestions
                })
                for relation in suggestions:
                    relation.getparent().remove(relation)

            for removed_name in removed:
                element = self.doc.relation_types[removed_name].element
                element.getparent().remove(element)

            message = f"Removed {removed_text}."
            if reassign_to is not None:
                message += f" Reassigned {relations_updated} pairs to '{target.name}'."
            if dropped_suggestions:
                message += f" Deleted {dropped_suggestions} suggestion{'s' if dropped_suggestions != 1 else ''} that used it."
            return self.commit_definitions(message, relations_updated, removed=sorted(removed))

    def update_guidance(self, guidance:str) -> DefinitionsChange:
        """ Replaces the guidance comment (e.g. the decision order) placed before the category definitions. """
        with self.writing():
            self.check_definitions_editable()
            self.refresh()
            guidance = (guidance or "").strip()
            if "--" in guidance or guidance.endswith("-"):
                raise AnnotationError("The guidance cannot contain '--' or end with '-' (it is stored in an XML comment).")
            interp_group = self.doc.get_interpgrp()
            for sibling in list(interp_group.itersiblings(preceding=True)):
                if not isinstance(sibling, ET._Comment):
                    break
                sibling.getparent().remove(sibling)
            if guidance:
                interp_group.addprevious(ET.Comment(f"\n{guidance}\n"))
            return self.commit_definitions("Updated the guidance.", guidance=guidance)

    # -- similarity search ---------------------------------------------------------------

    TENTATIVE_WEIGHT = 0.5

    def search_index(self):
        """
        The labelled directed pairs (human and pipeline, never rules) with their search terms and a BM25 index,
        rebuilt when the file changes. Test units are left out in evaluation mode.
        """
        if getattr(self, "_search_cache", (None,))[0] == self._file_id:
            return self._search_cache[1]
        entries = []
        for app in self.doc.apps:
            if self.hides(app):
                continue
            before, after = app.text_before(), app.text_after()
            for pair in app.pairs:
                if self.is_excluded(pair.active) or self.is_excluded(pair.passive):
                    continue
                source = self.label_source(pair)
                if source not in ("human", "pipeline"):
                    continue
                terms = search.pair_terms(pair.active.text, pair.passive.text, before, after)
                entries.append((app, pair, source, terms))
        index = search.BM25([terms for _, _, _, terms in entries])
        self._search_cache = (self._file_id, (entries, index))
        return entries, index

    def search_similar(
        self,
        unit_id:str,
        active:str,
        passive:str,
        limit:int=10,
        sources:tuple[str, ...]|list[str]=("human", "pipeline"),
    ) -> list[SimilarPair]:
        """
        Labelled pairs whose change resembles the change from active to passive in this unit, best first.
        Pipeline (tentative) labels count for less than human ones. The pair itself and its inverse are never
        returned, so a label cannot be evidence for itself.
        """
        with self.lock:
            self.refresh()
            app = self.get_app(unit_id)
            pair = self.get_pair(app, active, passive)
            query = search.pair_terms(pair.active.text, pair.passive.text, app.text_before(), app.text_after())
            entries, index = self.search_index()
            scored = []
            for (candidate_app, candidate, source, _), score in zip(entries, index.scores(query)):
                if score <= 0 or source not in sources:
                    continue
                if candidate_app is app and {candidate.active.n, candidate.passive.n} == {active, passive}:
                    continue
                weighted = score * (self.TENTATIVE_WEIGHT if source == "pipeline" else 1.0)
                scored.append((weighted, candidate_app, candidate, source))
            scored.sort(key=lambda item: -item[0])
            results = []
            for score, candidate_app, candidate, source in scored[:max(limit, 0)]:
                provenance = self.provenance_from_relation(candidate.relation_element())
                results.append(SimilarPair(
                    corpus="target",
                    unit_id=str(candidate_app),
                    verse=search.verse_label(str(candidate_app)),
                    active=candidate.active.n,
                    passive=candidate.passive.n,
                    active_text=str(candidate.active),
                    passive_text=str(candidate.passive),
                    change=describe_change(candidate.active.text, candidate.passive.text).text,
                    category=self.current_category(candidate) or "",
                    source=source,
                    tentative=source == "pipeline",
                    cert=provenance.cert,
                    justification=provenance.justification,
                    definitions_hash=provenance.definitions_hash,
                    score=round(score, 3),
                ))
            return results

    # -- subreading rules ----------------------------------------------------------------

    def rule_interps(self) -> list:
        group = self.rule_group()
        return unique(find_elements(group, "./interp")) if group is not None else []

    def rule_group(self, create:bool=False):
        text = find_element(self.doc.tree, ".//text")
        group = find_element(text, f".//interpGrp[@type='{RULE_GROUP_TYPE}']") if text is not None else None
        if group is None and create:
            categories = self.doc.get_interpgrp()
            group = ET.Element(tei_tag(categories, "interpGrp"), attrib={"type": RULE_GROUP_TYPE})
            categories.addnext(group)
        return group

    def read_rules(self) -> list[SubreadingRule]:
        """ The subreading rules stored in the TEI file. """
        rules = []
        for interp in self.rule_interps():
            reading_type = interp.get("type", "")
            category = interp.get("ana", "").lstrip("#")
            suffix = interp.get("n") or DEFAULT_SUFFIXES.get(reading_type, "")
            if reading_type and category and suffix:
                rules.append(SubreadingRule(reading_type=reading_type, category=category, suffix=suffix))
        return rules

    def write_rules(self, rules:list[SubreadingRule]):
        """ Stores the rules in <interpGrp type="subreading-rules"> next to the category definitions. """
        group = self.rule_group(create=True)
        for child in list(group):
            group.remove(child)
        for rule in rules:
            interp = ET.SubElement(group, tei_tag(group, "interp"), attrib={
                XML_ID: rule.resp_id, "type": rule.reading_type, "ana": f"#{rule.category}", "n": rule.suffix,
            })
            interp.text = rule.description()

    def check_rules(self, rules:list[SubreadingRule]):
        types = [rule.reading_type for rule in rules]
        if len(set(types)) != len(types):
            raise AnnotationError("Each reading type can have only one rule.")
        for rule in rules:
            self.get_relation_type(rule.category)
            if not rule.reading_type or not rule.suffix or not re.match(r"^[A-Za-z0-9_.\-]+$", rule.suffix):
                raise AnnotationError(f"Invalid rule for '{rule.reading_type}': give a reading type and an id suffix (e.g. 'o').")

    def rule_parents(self, app:App, rules:list[SubreadingRule]|None=None) -> Parents:
        return find_parents(app, self.rules if rules is None else rules)

    def coverage_for(self, pair:Pair, parents:Parents|None=None) -> Coverage|None:
        if not self.rules and parents is None:
            return None
        if self.is_excluded(pair.active) or self.is_excluded(pair.passive):
            return None
        parents = parents or self.rule_parents(pair.app)
        return rule_coverage(pair.active.n, pair.passive.n, parents)

    def rule_pairs(self, app:App) -> list[Pair]:
        """ Unordered substantive pairs covered by a subreading rule (labelled automatically). """
        if not self.rules:
            return []
        parents = self.rule_parents(app)
        return [pair for pair in self.substantive_pairs(app) if rule_coverage(pair.active.n, pair.passive.n, parents)]

    def work_pairs(self, app:App) -> list[Pair]:
        """ Unordered substantive pairs for an annotator (not covered by a subreading rule). """
        if not self.rules:
            return self.substantive_pairs(app)
        parents = self.rule_parents(app)
        return [pair for pair in self.substantive_pairs(app) if not rule_coverage(pair.active.n, pair.passive.n, parents)]

    @staticmethod
    def label_source(pair:Pair) -> str|None:
        relation = pair.relation_element()
        if relation is None:
            return None
        resp = relation.get("resp", "")
        if resp.startswith("#rule-"):
            return "rule"
        if resp == f"#{RDGAI_RESP}":
            return "pipeline"
        return "human"

    def is_tentative(self, pair:Pair) -> bool:
        """ Pipeline labels are tentative (rule labels never are: they depend only on the reading types). """
        return self.label_source(pair) == "pipeline"

    @staticmethod
    def run_tokens(relation) -> list[str]:
        return [token for token in relation.get("change", "").split() if not token.startswith("#defs-")]

    def restore_rule_labels(self, app:App, pairs:list[Pair]):
        """
        After labels are cleared: pairs covered by a rule get the rule's label back (so clearing a human override of
        a rule label restores it). Rule labels depend only on the reading types, so no other write re-applies them.
        """
        if self.rules:
            self.apply_rules_to_app(app, self.rules, report=None, only={frozenset((pair.active.n, pair.passive.n)) for pair in pairs})

    def apply_rules_to_app(self, app:App, rules:list[SubreadingRule], report:dict|None, only:set|None=None) -> bool:
        """
        Applies the rules to one unit (or to the given unordered pairs of it). Rule labels left on pairs no rule
        covers (written by the former rule 3) are removed. Returns True if anything changed.
        """
        parents = self.rule_parents(app, rules)
        by_type = {rule.reading_type: rule for rule in rules}
        changed = False
        definitions_change = None
        when = self.now()
        if report is not None:
            report["unresolved"].extend(RuleReading(unit_id=str(app), reading=reading) for reading in parents.unresolved)
            report["nested"].extend(RuleReading(unit_id=str(app), reading=reading, parent=parent) for reading, parent in parents.nested)

        for pair in self.substantive_pairs(app):
            if only is not None and frozenset((pair.active.n, pair.passive.n)) not in only:
                continue
            covered = rule_coverage(pair.active.n, pair.passive.n, parents)
            if covered is None:
                # a leftover rule label (e.g. from the former rule 3): remove it; human and pipeline labels stay
                leftover = [directed for directed in (pair, pair.get_inverse()) if self.label_source(directed) == "rule"]
                for directed in leftover:
                    directed.clear_types()
                if leftover:
                    changed = True
                    if report is not None:
                        report["removed"] += 1
                continue
            counts = report["counts"][covered.rule] if report is not None else {}
            if report is not None:
                counts["covered"] += 1
            rule = by_type[covered.reading_type]
            first, second = pair.active.n, pair.passive.n

            relation_type = self.get_relation_type(rule.category)
            if covered.rule == 1 and parents.parents.get(first) == second:
                relation_type = relation_type.get_inverse()  # subreading -> parent
            if covered.rule == 1:
                subreading = second if parents.parents.get(second) == first else first
                article = "an" if covered.reading_type[:1].lower() in "aeiou" else "a"
                justification = f"{subreading} is {article} {covered.reading_type} subreading of {parents.parents[subreading]}."
            else:
                justification = f"{first} and {second} are both {covered.reading_type} subreadings of {parents.parents[first]}."

            source = self.label_source(pair)
            if source == "human":
                if report is not None:
                    counts["human"] += 1
                    current = self.current_category(pair)
                    if current != relation_type.name:
                        report["conflicts"].append(RuleConflict(
                            unit_id=str(app), active=first, passive=second, label=current or "",
                            rule_label=relation_type.name, rule=covered.rule,
                        ))
                continue

            relation = pair.relation_element()
            inverse_relation = pair.get_inverse().relation_element()
            resp = f"#{rule.resp_id}"
            if (
                source == "rule" and relation is not None and inverse_relation is not None
                and self.current_category(pair) == relation_type.name
                and self.current_category(pair.get_inverse()) == relation_type.get_inverse().name
                and relation.get("resp") == resp and relation.get("cert") == "high"
                and relation_provenance(relation)["justification"] == justification
                and not self.run_tokens(relation)
            ):
                if report is not None:
                    counts["unchanged"] += 1
                continue

            if definitions_change is None:
                definitions_change = self.ensure_definitions_change()
            pair.set_type_with_inverse(
                relation_type,
                responsible=resp,
                cert="high",
                description=justification,
                inverse_description=justification,
                change=self.change_pointers(definitions_change),
                when=when,
            )
            changed = True
            if report is not None:
                counts["written"] += 1
        return changed

    def update_rule_descriptions(self, rules:list[SubreadingRule]) -> list[str]:
        """ Rewrites the description of stored rules whose text is out of date (e.g. still describing rule 3). """
        by_id = {rule.resp_id: rule for rule in rules}
        updated = []
        for interp in self.rule_interps():
            rule = by_id.get(interp.get(XML_ID))
            if rule is not None and extract_text(interp).strip() != rule.description():
                self.set_interp_text(interp, rule.description())
                updated.append(rule.resp_id)
        return updated

    def apply_rules(self, rules:list[SubreadingRule]|None=None, dry_run:bool=False) -> RulesReport:
        """
        Labels the pairs formed by typed subreadings (see rdgai.rules). Rules given here are stored in the file
        (replacing the stored rules) unless dry_run; otherwise the stored rules are used. Human labels are never
        changed; those that disagree with a rule are reported as conflicts. Running again changes nothing.
        """
        with self.writing():
            if self.evaluation:
                raise AnnotationError("Subreading rules cannot be applied in evaluation mode (they would change test units).")
            if not dry_run:
                self.check_writable()
            self.refresh()
            rules = list(rules) if rules is not None else list(self.rules)
            if not rules:
                raise AnnotationError("No subreading rules configured: give rules, e.g. orthographic=Orthography.")
            self.check_rules(rules)
            report = dict(
                counts={number: dict(covered=0, written=0, unchanged=0, human=0) for number in (1, 2)},
                removed=0, unresolved=[], nested=[], conflicts=[],
            )
            changed = False
            descriptions_updated = []
            if rules != self.rules:
                self.write_rules(rules)
                changed = True
            else:
                descriptions_updated = self.update_rule_descriptions(rules)
                changed = bool(descriptions_updated)
            for app in self.doc.apps:
                changed = self.apply_rules_to_app(app, rules, report) or changed

            result = RulesReport(
                rules=[RuleConfig(reading_type=rule.reading_type, category=rule.category, suffix=rule.suffix) for rule in rules],
                counts=[RuleCount(rule=number, **values) for number, values in report["counts"].items()],
                removed=report["removed"],
                descriptions_updated=descriptions_updated,
                unresolved=report["unresolved"],
                nested=report["nested"],
                conflicts=report["conflicts"],
                dry_run=dry_run,
            )
            if dry_run:
                self.load()  # discard the changes
            elif changed:
                self.save()
                self.load()
            return result

    # -- the unit open in the GUI (so that Claude can act on "the verse I'm on") ------------

    def set_viewing(self, unit_id:str):
        """
        Records the unit the GUI shows, in a small hidden file next to the TEI (so another process, e.g. a second
        MCP server, sees it). A read-only store keeps it in memory and writes nothing.
        """
        with self.lock:
            self.refresh()
            info = ViewingInfo(unit_id=str(self.get_app(unit_id)), when=sampling.now())
        self._viewing = info
        if self.read_only:
            return
        fd, tmp = tempfile.mkstemp(dir=self.viewing_path.parent, prefix=f"{self.viewing_path.name}.", suffix=".tmp")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                f.write(info.model_dump_json())
            os.replace(tmp, self.viewing_path)
        finally:
            if os.path.exists(tmp):
                os.remove(tmp)

    def viewing(self) -> ViewingInfo|None:
        """ The unit most recently opened in the GUI (by any process), if any. """
        try:
            from_file = ViewingInfo.model_validate_json(self.viewing_path.read_text(encoding="utf-8"))
        except (FileNotFoundError, ValueError):
            from_file = None
        candidates = [info for info in (from_file, self._viewing) if info is not None]
        return max(candidates, key=lambda info: info.when) if candidates else None

    # -- contextual translations (sidecar file) ------------------------------------------

    def load_translations(self) -> translation_files.Translations:
        """ Loads the translations from the sidecar file (cached until the file changes). """
        try:
            stat = self.translations_path.stat()
            key = (stat.st_ino, stat.st_mtime_ns, stat.st_size)
        except FileNotFoundError:
            return translation_files.Translations()
        if getattr(self, "_translations_cache", (None, None))[0] != key:
            self._translations_cache = (key, translation_files.load_translations(self.translations_path))
        return self._translations_cache[1]

    def translations_for(self, app:App) -> dict[str, str]:
        """ {reading id: translation} for a unit, leaving out translations made for a reading text that has changed. """
        entry = self.load_translations().units.get(str(app))
        if entry is None:
            return {}
        texts = {reading.n: str(reading) for reading in app.readings}
        return {
            reading_id: item.translation for reading_id, item in entry.readings.items()
            if texts.get(reading_id) == item.reading
        }

    def untranslated(self, app:App) -> list[str]:
        """ Substantive readings of a unit with no (current) translation. """
        done = self.translations_for(app)
        return [reading.n for reading in app.readings if not self.is_excluded(reading) and reading.n not in done]

    def untranslated_units(self, sample:SampleFilter="all", verse:str="") -> list[str]:
        with self.lock:
            self.refresh()
            return [
                str(app) for app in self.filter_units(sample=sample, verse=verse)
                if self.work_pairs(app) and self.untranslated(app)
            ]

    def set_translations(self, unit_id:str, translations:dict[str, str], source:str="claude") -> TranslationResult:
        """
        Saves translations of the verse with each reading in place (merged with the unit's existing ones).
        Only the sidecar file is written; the TEI is never changed.
        """
        with self.writing():
            self.check_writable()
            self.refresh()
            app = self.get_app(unit_id)
            if not translations:
                raise AnnotationError("Give at least one translation, as {reading id: translation}.")
            readings = {reading.n: reading for reading in app.readings}
            for reading_id, text in translations.items():
                if reading_id not in readings:
                    raise AnnotationError(f"Unit '{unit_id}' has no reading '{reading_id}'. Readings: {', '.join(readings)}.")
                if not (text or "").strip():
                    raise AnnotationError(f"The translation for reading '{reading_id}' is empty.")
                check_translation_marks(reading_id, text)
            data = translation_files.load_translations(self.translations_path)
            entry = data.units.setdefault(str(app), translation_files.UnitTranslations())
            for reading_id, text in translations.items():
                entry.readings[reading_id] = translation_files.ReadingTranslation(reading=str(readings[reading_id]), translation=text.strip())
            entry.source = source
            entry.when = sampling.now()
            translation_files.save_translations(data, self.translations_path)
            return TranslationResult(
                unit_id=str(app), saved=list(translations), missing=self.untranslated(app), saved_to=str(self.translations_path),
            )

    # -- sampling -----------------------------------------------------------------------

    def load_sample(self) -> Sample|None:
        """ Loads the sample from the sidecar file (cached until the file changes). """
        try:
            mtime = self.sample_path.stat().st_mtime_ns
        except FileNotFoundError:
            return None
        if getattr(self, "_sample_cache", (None, None))[0] != mtime:
            sample = sampling.load_sample(self.sample_path)
            if sorted(sample.ignore_types) != sorted(self.ignore_types):
                raise AnnotationError(
                    f"The sample in {self.sample_path.name} was drawn ignoring reading types "
                    f"{sorted(sample.ignore_types)}, but this server ignores {sorted(self.ignore_types)}. "
                    "Use the same --ignore-type options, or draw a new sample."
                )
            self._sample_cache = (mtime, sample)
        return self._sample_cache[1]

    def sample_roles(self) -> dict[str, str]:
        sample = self.load_sample()
        return {unit.id: unit.role for unit in sample.units} if sample else {}

    def sample_role(self, unit_id:str) -> str|None:
        return self.sample_roles().get(unit_id)

    def eligible_units(self, max_pairs:int|None=None) -> list[App]:
        """ Units with at least one pair to classify (and at most max_pairs pairs, if given). """
        eligible = []
        for app in self.doc.apps:
            pair_count = len(self.substantive_pairs(app))
            if pair_count == 0:
                continue
            if max_pairs is not None and pair_count > max_pairs:
                continue
            eligible.append(app)
        return eligible

    def sample_info(self) -> SampleInfo:
        with self.lock:
            self.refresh()
            sample = self.load_sample()
            if sample is None:
                return SampleInfo(exists=False, path=str(self.sample_path))

            units = []
            for unit in sample.units:
                if unit.id not in self.doc.id_to_app:
                    raise AnnotationError(f"Sampled unit '{unit.id}' is not in {self.path.name}.")
                app = self.doc[unit.id]
                pairs = self.work_pairs(app)
                units.append(SampleUnitStatus(
                    id=unit.id,
                    role=unit.role,
                    status=self.status(app),
                    pair_count=len(pairs),
                    classified_count=sum(self.is_classified(pair) for pair in pairs),
                    rule_pair_count=len(self.rule_pairs(app)),
                ))

            progress = []
            for role in ("all", "example", "test"):
                members = [unit for unit in units if role == "all" or unit.role == role]
                progress.append(SampleProgress(
                    role=role,
                    units=len(members),
                    units_classified=sum(unit.status == "classified" for unit in members),
                    pairs=sum(unit.pair_count for unit in members),
                    pairs_classified=sum(unit.classified_count for unit in members),
                    rule_pairs=sum(unit.rule_pair_count for unit in members),
                ))

            return SampleInfo(
                exists=True,
                path=str(self.sample_path),
                seed=sample.seed,
                size=sample.size,
                test_fraction=sample.test_fraction,
                max_pairs=sample.max_pairs,
                eligible_count=sample.eligible_count,
                created=sample.created,
                progress=progress,
                units=units,
            )

    def draw_sample(
        self,
        size:int,
        seed:int,
        test_fraction:float=0.5,
        max_pairs:int|None=None,
        overwrite:bool=False,
    ) -> SampleInfo:
        """
        Draws a reproducible random sample of units and splits it into example and test units.
        The sample is saved to the sidecar file. An existing sample is only replaced if overwrite is True.
        """
        with self.writing():
            if self.evaluation:
                raise AnnotationError("The sample cannot be drawn again in evaluation mode (it defines the test units).")
            self.check_writable()
            self.refresh()
            if self.sample_path.exists() and not overwrite:
                raise AnnotationError(
                    f"A sample already exists in {self.sample_path.name}. Pass overwrite=True to replace it."
                )
            eligible = self.eligible_units(max_pairs=max_pairs)
            try:
                units = sampling.draw(
                    [str(app) for app in eligible],
                    size=size,
                    seed=seed,
                    test_fraction=test_fraction,
                    document_order=[str(app) for app in self.doc.apps],
                )
            except ValueError as error:
                raise AnnotationError(str(error))

            sample = Sample(
                seed=seed,
                size=size,
                test_fraction=test_fraction,
                max_pairs=max_pairs,
                ignore_types=sorted(self.ignore_types),
                eligible_count=len(eligible),
                created=sampling.now(),
                source=self.path.name,
                units=units,
            )
            sampling.save_sample(sample, self.sample_path)
            return self.sample_info()

    # -- write operations -----------------------------------------------------------

    def resp_stmt_for(self, xml_id:str):
        for resp_stmt in unique(find_elements(self.doc.tree, ".//respStmt")):
            if resp_stmt.attrib.get(XML_ID) == xml_id:
                return resp_stmt
        return None

    def annotator_full_name(self) -> str|None:
        """ The annotator's name as recorded in the TEI (<respStmt xml:id="..."><name>), else as given when starting. """
        if not self.annotator:
            return None
        with self.lock:
            self.refresh()
            resp_stmt = self.resp_stmt_for(self.annotator)
            if resp_stmt is not None:
                for element in resp_stmt:
                    if isinstance(element.tag, str) and ET.QName(element).localname in ("name", "persName"):
                        text = extract_text(element).strip()
                        if text:
                            return text
        return self.annotator_name

    def set_annotator_name(self, name:str):
        """ Records the annotator's full name in the TEI header (<respStmt xml:id="..."><name>), and saves. """
        name = (name or "").strip()
        if not self.annotator:
            raise AnnotationError("No annotator is set on this server (start it with --annotator, e.g. --annotator RT).")
        if not name:
            raise AnnotationError("The name cannot be empty.")
        with self.writing():
            self.check_writable()
            self.refresh()
            resp_stmt = self.resp_stmt_for(self.annotator)
            if resp_stmt is None:
                self.ensure_resp_stmt(self.annotator, name, "classification of transcriptional changes")
            else:
                name_element = next((element for element in resp_stmt
                                     if isinstance(element.tag, str) and ET.QName(element).localname in ("name", "persName")), None)
                if name_element is None:
                    name_element = ET.SubElement(resp_stmt, tei_tag(resp_stmt, "name"))
                for child in list(name_element):
                    name_element.remove(child)
                name_element.text = name
            self.annotator_name = name
            self.save()

    def ensure_resp_stmt(self, xml_id:str, name:str, resp:str):
        """ Adds <respStmt xml:id="..."> to the titleStmt so that @resp pointers resolve. """
        tree = self.doc.tree
        for resp_stmt in unique(find_elements(tree, ".//respStmt")):
            if resp_stmt.attrib.get(XML_ID) == xml_id:
                return
        title_stmt = find_element(tree, ".//teiHeader/fileDesc/titleStmt")
        if title_stmt is None:
            return
        resp_stmt = ET.SubElement(title_stmt, tei_tag(title_stmt, "respStmt"), attrib={XML_ID: xml_id})
        ET.SubElement(resp_stmt, tei_tag(title_stmt, "resp")).text = resp
        ET.SubElement(resp_stmt, tei_tag(title_stmt, "name")).text = name

    def revision_desc(self):
        header = find_element(self.doc.tree, ".//teiHeader")
        revision_desc = find_element(header, ".//revisionDesc")
        if revision_desc is None:
            revision_desc = ET.SubElement(header, tei_tag(header, "revisionDesc"))
        return revision_desc

    @staticmethod
    def now() -> str:
        return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")

    def ensure_definitions_change(self) -> str:
        """
        Returns the xml:id of the <change type="definitions"> recording the current definitions version,
        adding it to the revisionDesc if it is not there yet.
        """
        definitions_hash = self.definitions_hash()
        change_id = f"defs-{definitions_hash[:12]}"
        if change_id not in self.changes_by_id():
            revision_desc = self.revision_desc()
            change = ET.SubElement(
                revision_desc, tei_tag(revision_desc, "change"),
                attrib={XML_ID: change_id, "type": "definitions", "when": self.now()},
            )
            change.text = "Category definitions version "
            idno = ET.SubElement(change, tei_tag(revision_desc, "idno"), attrib={"type": "sha256"})
            idno.text = definitions_hash
            idno.tail = "."
        return change_id

    def ensure_model_change(self, model:str) -> str:
        """ Returns the xml:id of a <change type="model"> naming a model, adding it if necessary. """
        slug = re.sub(r"[^A-Za-z0-9_.\-]", "-", model)
        change_id = f"model-{slug}"
        if change_id not in self.changes_by_id():
            revision_desc = self.revision_desc()
            change = ET.SubElement(revision_desc, tei_tag(revision_desc, "change"), attrib={XML_ID: change_id, "type": "model"})
            change.text = "Suggestions made with "
            name = ET.SubElement(change, tei_tag(revision_desc, "name"), attrib={"type": "model"})
            name.text = model
            name.tail = "."
        return change_id

    @staticmethod
    def change_pointers(*change_ids:str|None) -> str:
        return " ".join(f"#{change_id.lstrip('#')}" for change_id in change_ids if change_id)

    def resolve_resp(self, resp:str|None) -> str:
        resp = (resp or self.annotator or "").lstrip("#")
        if not resp:
            raise AnnotationError("No annotator is set: start the server with --annotator.")
        if resp == RDGAI_RESP:
            self.ensure_resp_stmt(RDGAI_RESP, "rdgai", "automatic classification")
        elif resp == self.annotator:
            self.ensure_resp_stmt(resp, self.annotator_name or resp, "classification of transcriptional changes")
        return resp

    @staticmethod
    def check_cert(cert:str|None, optional:bool=False):
        if optional and cert is None:
            return
        if cert not in CERTAINTY_VALUES:
            raise AnnotationError(f"cert must be one of {', '.join(CERTAINTY_VALUES)}.")

    def current_category(self, pair:Pair) -> str|None:
        return " ".join(sorted(pair.relation_type_names())) or None

    def check_expected(self, pairs:list[Pair], expected:dict):
        """ Raises ConflictError if any pair's current category differs from what the caller expected. """
        current = {}
        for pair in pairs:
            key = (pair.active.n, pair.passive.n)
            if key not in expected or expected[key] is UNCHECKED:
                continue
            want = expected[key].lstrip("#") if expected[key] else None
            have = self.current_category(pair)
            if want != have:
                current[key] = have
        if current:
            details = ", ".join(f"{active}→{passive} is now {category or 'unclassified'}" for (active, passive), category in current.items())
            raise ConflictError(f"The classification changed since it was read: {details}. Reload and try again.", current)

    def write_results(self, app:App, pairs:list[Pair]) -> list[WriteResult]:
        return [
            WriteResult(unit_id=str(app), pair=self.pair_info(pair), inverse=self.pair_info(pair.get_inverse()), saved_to=str(self.path))
            for pair in pairs
        ]

    def set_classifications(
        self,
        unit_id:str,
        pairs:list[tuple[str, str]],
        category:str,
        justification:str="",
        cert:str="high",
        resp:str|None=None,
        change:str|None=None,
        expected:dict|None=None,
        justification_source:str|None=None,
    ) -> list[WriteResult]:
        """
        Sets the same category on several pairs of a unit (each from its active to its passive reading),
        replacing existing categories and setting the inverses. All pairs are checked before anything is
        written, and the file is saved once.

        expected: optional {(active, passive): category or None}. If any pair's current category differs,
        ConflictError is raised and nothing is written.
        justification_source: if the justification's wording was copied from someone else (e.g. 'claude' from a
        suggestion), who; recorded as the @resp of the justification's <desc>.
        """
        with self.writing():
            self.check_writable()
            self.refresh()
            if not pairs:
                raise AnnotationError("No pairs given.")
            self.check_cert(cert)
            app = self.get_app(unit_id)
            self.check_unit_writable(app)
            pair_objects = [self.get_pair(app, active, passive) for active, passive in pairs]
            relation_type = self.get_relation_type(category)
            self.check_expected(pair_objects, expected or {})
            resp = self.resolve_resp(resp)
            description_resp = None
            if justification_source and (justification or "").strip():
                source_name = justification_source.lstrip("#")
                if not NC_NAME.match(source_name):
                    raise AnnotationError(f"Invalid justification source '{justification_source}': use a name such as 'claude'.")
                if source_name == "claude":
                    self.ensure_resp_stmt("claude", "Claude", "suggested classifications")
                description_resp = f"#{source_name}"

            change_attr = self.change_pointers(change, self.ensure_definitions_change())
            when = self.now()
            justification = (justification or "").strip()
            for pair in pair_objects:
                inverse_justification = f"Inverse of {pair.active.n} → {pair.passive.n}: {justification}" if justification else ""
                pair.set_type_with_inverse(
                    relation_type,
                    responsible=f"#{resp}",
                    cert=cert,
                    description=justification,
                    inverse_description=inverse_justification,
                    change=change_attr,
                    when=when,
                    description_resp=description_resp,
                )
            self.save()
            return self.write_results(app, pair_objects)

    def set_classification(
        self,
        unit_id:str,
        active:str,
        passive:str,
        category:str,
        justification:str="",
        cert:str="high",
        resp:str|None=None,
        change:str|None=None,
        expected_category=UNCHECKED,
    ) -> WriteResult:
        """
        Sets the category for the change from the active reading to the passive reading.
        Replaces any existing category, sets the inverse on the reverse pair and saves the file.
        The label records when it was saved and the definitions version in force.

        resp defaults to the store's annotator. If expected_category is given (a category, or None for
        'unclassified') and the pair's current category differs, ConflictError is raised.
        """
        return self.set_classifications(
            unit_id, [(active, passive)], category, justification=justification, cert=cert, resp=resp, change=change,
            expected={(active, passive): expected_category},
        )[0]

    def write_decisions(
        self,
        unit_id:str,
        decisions:list[tuple[str, str, str, str, str]],
        resp:str|None=None,
        change:str|None=None,
    ) -> DecisionsResult:
        """
        Writes several different decisions for one unit, (active, passive, category, justification, cert) each, with
        one save. Everything is checked before anything is written. Only unlabelled pairs are written: a pair that
        someone labelled meanwhile is skipped and reported, never overwritten. Used by the pipeline.
        """
        with self.writing():
            self.check_writable()
            self.refresh()
            app = self.get_app(unit_id)
            self.check_unit_writable(app)
            checked = []
            for active, passive, category, justification, cert in decisions:
                self.check_cert(cert)
                checked.append((self.get_pair(app, active, passive), self.get_relation_type(category), (justification or "").strip(), cert))
            resp = self.resolve_resp(resp)
            change_attr = self.change_pointers(change, self.ensure_definitions_change())
            when = self.now()
            result = DecisionsResult()
            for pair, relation_type, justification, cert in checked:
                key = (pair.active.n, pair.passive.n)
                if pair.relation_element() is not None or pair.get_inverse().relation_element() is not None:
                    result.skipped.append(key)
                    continue
                pair.set_type_with_inverse(
                    relation_type,
                    responsible=f"#{resp}",
                    cert=cert,
                    description=justification,
                    inverse_description=f"Inverse of {key[0]} → {key[1]}: {justification}" if justification else "",
                    change=change_attr,
                    when=when,
                )
                result.written.append(key)
            if result.written:
                self.save()
            return result

    def save_suggestions(self, unit_id:str, pairs:list[tuple[str, str]], kind:Literal["labels", "reasons"]) -> SavedSuggestions:
        """
        The GUI's "Save all suggestions" (kind 'labels') and "Save all reasons" (kind 'reasons') for the given pairs of a
        unit, each in the direction shown, with one save. The annotator becomes responsible for what is saved; a copied
        reason is recorded as the suggester's wording.

        labels: an unlabelled pair whose suggestions all agree is labelled with that category, the suggestion's reason
        and its certainty (high if none). Labelled pairs, disagreeing suggestions and rule pairs are skipped.
        reasons: a pair the annotator labelled without a reason gets the reason of a suggestion for the same category.
        Existing reasons are never replaced. Nothing is saved on test units, whose suggestions the GUI hides.
        """
        if kind not in ("labels", "reasons"):
            raise AnnotationError("kind must be 'labels' or 'reasons'.")
        with self.writing():
            self.check_writable()
            self.refresh()
            app = self.get_app(unit_id)
            self.check_unit_writable(app)
            result = SavedSuggestions()
            if self.sample_role(str(app)) == "test" or self.hides(app):
                return result
            resp = self.resolve_resp(None)
            change_attr = self.change_pointers(None, self.ensure_definitions_change())
            when = self.now()
            for active, passive in pairs:
                pair = self.get_pair(app, active, passive)
                info = self.pair_info(pair)
                suggestions = self.suggestions_for(app, pair)
                skip = lambda why: result.skipped.append(SkippedPair(pair=(active, passive), why=why))
                if self.coverage_for(pair) is not None or info.source == "rule":
                    skip("by rule")
                    continue
                if kind == "labels":
                    if info.category or info.inverse_category:
                        skip("labelled")
                        continue
                    if not suggestions:
                        skip("no suggestion")
                        continue
                    if len({suggestion.category for suggestion in suggestions}) > 1:
                        skip("suggestions disagree")
                        continue
                    chosen = next((suggestion for suggestion in suggestions if suggestion.justification), suggestions[0])
                    category, cert = chosen.category, chosen.cert or "high"
                else:
                    if not info.category:
                        skip("not labelled")
                        continue
                    if info.source != "human":
                        skip(f"labelled by the {info.source}")
                        continue
                    if info.provenance is not None and info.provenance.justification:
                        skip("has a reason")
                        continue
                    with_reasons = [suggestion for suggestion in suggestions if suggestion.justification]
                    chosen = next((suggestion for suggestion in with_reasons if suggestion.category == info.category), None)
                    if chosen is None:
                        skip("different category" if with_reasons else "no suggested reason")
                        continue
                    category, cert = info.category, (info.provenance.cert if info.provenance else "") or "high"
                reason = reason_for_pair(chosen.justification)
                source_name = chosen.source.split(":")[0]
                description_resp = None
                if reason and NC_NAME.match(source_name):
                    if source_name == "claude":
                        self.ensure_resp_stmt("claude", "Claude", "suggested classifications")
                    description_resp = f"#{source_name}"
                pair.set_type_with_inverse(
                    self.get_relation_type(category),
                    responsible=f"#{resp}",
                    cert=cert,
                    description=reason,
                    inverse_description=f"Inverse of {active} → {passive}: {reason}" if reason else "",
                    change=change_attr,
                    when=when,
                    description_resp=description_resp,
                )
                result.written.append((active, passive))
            if result.written:
                self.save()
            return result

    def set_certainty(self, unit_id:str, pairs:list[tuple[str, str]], cert:str) -> list[WriteResult]:
        """
        Changes the certainty of labelled pairs (and their inverses) without changing their categories or
        justifications. The annotator becomes responsible, and the time and definitions version are updated.
        """
        with self.writing():
            self.check_writable()
            self.refresh()
            if not pairs:
                raise AnnotationError("No pairs given.")
            self.check_cert(cert)
            app = self.get_app(unit_id)
            self.check_unit_writable(app)
            pair_objects = [self.get_pair(app, active, passive) for active, passive in pairs]
            unlabelled = [f"{pair.active.n}→{pair.passive.n}" for pair in pair_objects if pair.relation_element() is None]
            if unlabelled:
                raise AnnotationError(f"Pairs not classified, so their certainty cannot be set: {', '.join(unlabelled)}.")
            resp = self.resolve_resp(None)
            definitions_change = self.ensure_definitions_change()
            when = self.now()
            for pair in pair_objects:
                for directed in (pair, pair.get_inverse()):
                    relation = directed.relation_element()
                    if relation is None:
                        continue
                    relation.set("resp", f"#{resp}")
                    relation.set("cert", cert)
                    tokens = [token for token in relation.attrib.get("change", "").split() if not token.startswith("#defs-")]
                    relation.set("change", self.change_pointers(*tokens, definitions_change))
                    self.stamp(relation, when)
            self.save()
            return self.write_results(app, pair_objects)

    @staticmethod
    def stamp(relation, when:str):
        """ Sets the <date when> inside the relation's justification <desc>, creating them if necessary. """
        desc = next((child for child in relation if isinstance(child.tag, str) and ET.QName(child).localname == "desc"), None)
        if desc is None:
            desc = ET.SubElement(relation, tei_tag(relation, "desc"), attrib={"type": "justification"})
        date = next((child for child in desc if isinstance(child.tag, str) and ET.QName(child).localname == "date"), None)
        if date is None:
            date = ET.SubElement(desc, tei_tag(relation, "date"))
        date.set("when", when)

    def clear_classification(self, unit_id:str, active:str, passive:str, expected_category=UNCHECKED) -> WriteResult:
        """ Removes the classification of a pair and its inverse and saves the file. """
        with self.writing():
            self.check_writable()
            self.refresh()
            app = self.get_app(unit_id)
            self.check_unit_writable(app)
            pair = self.get_pair(app, active, passive)
            self.check_expected([pair], {(active, passive): expected_category})
            pair.clear_types_with_inverse()
            self.restore_rule_labels(app, [pair])
            self.save()
            return self.write_results(app, [pair])[0]

    def clear_classifications(self, unit_id:str, pairs:list[tuple[str, str]], expected:dict|None=None) -> list[WriteResult]:
        """ Removes the classifications of several pairs (and their inverses), saving once. All-or-nothing. """
        with self.writing():
            self.check_writable()
            self.refresh()
            if not pairs:
                raise AnnotationError("No pairs given.")
            app = self.get_app(unit_id)
            self.check_unit_writable(app)
            pair_objects = [self.get_pair(app, active, passive) for active, passive in pairs]
            self.check_expected(pair_objects, expected or {})
            for pair in pair_objects:
                pair.clear_types_with_inverse()
            self.restore_rule_labels(app, pair_objects)
            self.save()
            return self.write_results(app, pair_objects)

    def start_run(self, model:str, description:str="") -> str:
        """
        Records a pipeline run as a <change> in the revisionDesc and returns its xml:id.
        Relations written during the run point to it with @change.
        """
        with self.writing():
            self.check_writable()
            self.refresh()
            revision_desc = self.revision_desc()
            now = datetime.now(timezone.utc).replace(microsecond=0)
            change_id = f"rdgai-run-{now.strftime('%Y%m%dT%H%M%SZ')}"
            existing = set(self.changes_by_id())
            suffix = 1
            base = change_id
            while change_id in existing:
                suffix += 1
                change_id = f"{base}-{suffix}"
            self.ensure_resp_stmt(RDGAI_RESP, "rdgai", "automatic classification")
            change = ET.SubElement(
                revision_desc,
                tei_tag(revision_desc, "change"),
                attrib={XML_ID: change_id, "when": now.isoformat().replace("+00:00", "Z"), "who": f"#{RDGAI_RESP}"},
            )
            change.text = (description.strip() + " " if description.strip() else "") + "Classification by rdgai using "
            model_name = ET.SubElement(change, tei_tag(revision_desc, "name"), attrib={"type": "model"})
            model_name.text = model
            model_name.tail = "."
            self.save()
            return change_id

    # -- suggestions -------------------------------------------------------------------

    @staticmethod
    def parse_source(source:str) -> tuple[str, str]:
        match = SOURCE.match((source or "").strip().lstrip("#"))
        if not match:
            raise AnnotationError(
                f"Invalid source '{source}': use a name such as 'claude', optionally with a model, e.g. 'rdgai:gpt-4o'."
            )
        return match["name"], match["model"] or ""

    def suggestion_list(self, app:App, create:bool=False):
        list_relation = find_element(app.element, ".//listRelation[@type='suggestion']")
        if list_relation is None and create:
            note = ET.SubElement(app.element, tei_tag(app.element, "note"))
            list_relation = ET.SubElement(note, tei_tag(app.element, "listRelation"), attrib={"type": "suggestion"})
        return list_relation

    def suggestion_relations(self, app:App) -> list:
        list_relation = self.suggestion_list(app)
        if list_relation is None:
            return []
        return unique(find_elements(list_relation, "./relation"))

    def suggestion_info(self, app:App, relation) -> SuggestionInfo:
        provenance = self.provenance_from_relation(relation)
        name = provenance.resp.lstrip("#")
        return SuggestionInfo(
            unit_id=str(app),
            active=relation.get("active"),
            passive=relation.get("passive"),
            category=" ".join(token.lstrip("#") for token in relation.get("ana", "").split()),
            source=f"{name}:{provenance.model}" if provenance.model else name,
            model=provenance.model,
            justification=provenance.justification,
            cert=provenance.cert,
            when=provenance.when,
            definitions_hash=provenance.definitions_hash,
        )

    def remove_suggestion_relations(self, app:App, readings:set[str], source:str|None=None):
        for relation in self.suggestion_relations(app):
            if {relation.get("active"), relation.get("passive")} != readings:
                continue
            if source is not None and self.suggestion_info(app, relation).source != source:
                continue
            relation.getparent().remove(relation)
        list_relation = self.suggestion_list(app)
        if list_relation is not None and len(list_relation) == 0:
            note = list_relation.getparent()
            note.remove(list_relation)
            if len(note) == 0 and not (note.text or "").strip():
                note.getparent().remove(note)

    def suggest_classification(
        self,
        unit_id:str,
        active:str,
        passive:str,
        category:str,
        justification:str="",
        source:str="claude",
        cert:str|None=None,
        change:str|None=None,
        allow_test_unit:bool=False,
    ) -> SuggestionResult:
        """
        Records a suggested category for the change from the active to the passive reading (and the inverse
        for the reverse pair). Suggestions are kept in <listRelation type="suggestion">, separate from labels,
        and replace earlier suggestions from the same source for the pair.

        Suggestions on the sample's test units are refused unless allow_test_unit (they would leak into the
        evaluation); in evaluation mode they are always refused.
        """
        with self.writing():
            self.check_writable()
            self.refresh()
            name, model = self.parse_source(source)
            source = f"{name}:{model}" if model else name
            self.check_cert(cert, optional=True)
            app = self.get_app(unit_id)
            self.check_unit_writable(app)
            if self.sample_role(str(app)) == "test" and not allow_test_unit:
                raise AnnotationError(
                    f"{app} is a test unit: suggestions there would leak into the evaluation. "
                    "Pass allow_test_unit to make one anyway."
                )
            pair = self.get_pair(app, active, passive)
            relation_type = self.get_relation_type(category)

            if name == RDGAI_RESP:
                self.ensure_resp_stmt(RDGAI_RESP, "rdgai", "automatic classification")
            elif name == "claude":
                self.ensure_resp_stmt("claude", "Claude", "suggested classifications")
            else:
                self.ensure_resp_stmt(name, name, "suggested classifications")

            self.remove_suggestion_relations(app, {active, passive}, source=source)
            change_attr = self.change_pointers(change, self.ensure_model_change(model) if model else None, self.ensure_definitions_change())
            when = self.now()
            justification = (justification or "").strip()
            list_relation = self.suggestion_list(app, create=True)
            created = []
            for (first, second, relation_type_, text) in (
                (active, passive, relation_type, justification),
                (passive, active, relation_type.get_inverse(), f"Inverse of {active} → {passive}: {justification}" if justification else ""),
            ):
                attrib = {"active": first, "passive": second, "ana": f"#{relation_type_.name}", "resp": f"#{name}"}
                if cert:
                    attrib["cert"] = cert
                attrib["change"] = change_attr
                relation = ET.SubElement(list_relation, tei_tag(list_relation, "relation"), attrib=attrib)
                desc = ET.SubElement(relation, tei_tag(relation, "desc"), attrib={"type": "justification"})
                desc.text = text or None
                ET.SubElement(desc, tei_tag(relation, "date"), attrib={"when": when})
                created.append(relation)
            self.save()
            return SuggestionResult(
                unit_id=str(app),
                suggestion=self.suggestion_info(app, created[0]),
                inverse=self.suggestion_info(app, created[1]),
            )

    def get_suggestions(
        self,
        unit_id:str|None=None,
        active:str|None=None,
        passive:str|None=None,
        source:str|None=None,
    ) -> list[SuggestionInfo]:
        """
        Returns suggestions, optionally for one unit, one pair (in the direction given) and/or one source.
        Without a pair, each suggestion is given once, for the pair in document order.
        """
        with self.lock:
            self.refresh()
            apps = [self.get_app(unit_id)] if unit_id else self.doc.apps
            apps = [app for app in apps if not self.hides(app)]
            if source is not None:
                name, model = self.parse_source(source)
                source = f"{name}:{model}" if model else name
            results = []
            for app in apps:
                order = {reading.n: index for index, reading in enumerate(app.readings)}
                for relation in self.suggestion_relations(app):
                    first, second = relation.get("active"), relation.get("passive")
                    if active is not None or passive is not None:
                        if (first, second) != (active, passive):
                            continue
                    elif order.get(first, 0) > order.get(second, 0):
                        continue
                    info = self.suggestion_info(app, relation)
                    if source is not None and info.source != source:
                        continue
                    results.append(info)
            return results

    def suggestions_for(self, app:App, pair:Pair) -> list[SuggestionInfo]:
        """ Suggestions for a pair in its direction (no file refresh; for use while rendering a unit). """
        if self.hides(app):
            return []
        return [
            self.suggestion_info(app, relation) for relation in self.suggestion_relations(app)
            if (relation.get("active"), relation.get("passive")) == (pair.active.n, pair.passive.n)
        ]

    def clear_suggestions(self, unit_id:str, active:str, passive:str, source:str|None=None):
        """ Removes the suggestions for a pair (both directions), from one source or from all. """
        with self.writing():
            self.check_writable()
            self.refresh()
            app = self.get_app(unit_id)
            self.check_unit_writable(app)
            self.get_pair(app, active, passive)
            if source is not None:
                name, model = self.parse_source(source)
                source = f"{name}:{model}" if model else name
            self.remove_suggestion_relations(app, {active, passive}, source=source)
            self.save()
