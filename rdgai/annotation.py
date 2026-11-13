"""
A service layer for querying and editing the classifications in a TEI file.

This is used by the MCP server (``rdgai.mcp_server``) and is independent of MCP so that it can be tested
and reused directly. The TEI file is the state: every write is saved to disk straight away.
"""
import os
import threading
import tempfile
from pathlib import Path
from datetime import datetime, timezone
from typing import Literal

from lxml import etree as ET
from pydantic import BaseModel, Field

from .apparatus import Doc, App, Pair, RelationType, tei_tag, unique
from .tei import find_element, find_elements, extract_text


DEFAULT_IGNORE_TYPES = ("orthographic", "defective")
CERTAINTY_VALUES = ("high", "medium", "low")
RDGAI_RESP = "rdgai"

DIRECTION_CONVENTION = (
    "A category describes the change from the ACTIVE (first) reading to the PASSIVE (second) reading. "
    "'Addition' means the passive reading has the extra words; 'Omission' means the passive reading lacks them. "
    "Setting a category on a pair automatically sets the inverse category on the reverse pair."
)

UnitStatus = Literal["unclassified", "partial", "classified", "all"]


class AnnotationError(Exception):
    pass


class ReadOnlyError(AnnotationError):
    pass


# ---------------------------------------------------------------------------
# Result models (these become the structured output of the MCP tools)
# ---------------------------------------------------------------------------

class Category(BaseModel):
    name: str
    description: str
    inverse: str = Field(description="The category assigned to the reverse pair. Equal to name if the category is its own inverse.")


class Definitions(BaseModel):
    language: str
    guidance: str = Field(description="Guidance given in the XML comment before the category definitions (e.g. a decision order).")
    direction_convention: str
    categories: list[Category]


class Provenance(BaseModel):
    resp: str = Field(default="", description="Who is responsible, e.g. '#RT' for a human annotator or '#rdgai' for the pipeline.")
    cert: str = Field(default="", description="Certainty: high, medium or low.")
    justification: str = ""
    change: str = Field(default="", description="Pointer to the <change> recording the pipeline run that made this classification, if any.")
    when: str = Field(default="", description="Timestamp of the pipeline run (from the <change> element), if any.")
    model: str = Field(default="", description="The model used by the pipeline run, if any.")


class PairInfo(BaseModel):
    active: str = Field(description="Identifier of the active (first) reading.")
    passive: str = Field(description="Identifier of the passive (second) reading.")
    active_text: str
    passive_text: str
    category: str | None = Field(description="Category for the change from active to passive, or null if unclassified.")
    inverse_category: str | None = Field(description="Category on the reverse pair (passive to active).")
    provenance: Provenance | None = None


class ReadingInfo(BaseModel):
    id: str
    text: str = Field(description="The reading text, or 'OMIT' for an empty reading (an omission).")
    type: str = Field(default="", description="The reading's @type, e.g. orthographic or defective.")
    witness_count: int
    witnesses: list[str]
    excluded: bool = Field(description="True if pairs with this reading are not classified (e.g. orthographic or defective readings).")


class UnitSummary(BaseModel):
    id: str
    verse: str
    lemma: str
    reading_count: int
    pair_count: int = Field(description="Number of unordered pairs of substantive readings to classify.")
    classified_count: int
    status: str


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


class UnitList(BaseModel):
    total: int = Field(description="Number of units matching the filters.")
    offset: int
    units: list[UnitSummary]


class CategoryCount(BaseModel):
    category: str
    total: int = Field(description="Number of directed pairs (active → passive) with this category.")
    human: int = Field(description="Of which classified by a human annotator.")
    pipeline: int = Field(description="Of which classified by the rdgai pipeline (tentative).")


class CategoryCounts(BaseModel):
    pairs_to_classify: int = Field(description="Unordered pairs of substantive readings in the document.")
    classified: int = Field(description="Of which classified.")
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
        ignore_types: Readings with these @type values are excluded from pairs (teiphy collapses them).
    """
    def __init__(
        self,
        path:Path|str,
        annotator:str|None=None,
        annotator_name:str|None=None,
        read_only:bool=False,
        ignore_types:tuple[str,...]|list[str]=DEFAULT_IGNORE_TYPES,
    ):
        self.path = Path(path)
        if not self.path.exists():
            raise FileNotFoundError(self.path)
        self.annotator = annotator.lstrip("#") if annotator else None
        self.annotator_name = annotator_name
        self.read_only = read_only
        self.ignore_types = set(ignore_types)
        self.lock = threading.RLock()
        self.doc: Doc
        self._mtime: float = 0.0
        self.load()

    # -- loading and saving --------------------------------------------------

    def load(self):
        with self.lock:
            self.doc = Doc(self.path)
            self._mtime = self.path.stat().st_mtime

    def refresh(self):
        """ Reloads the file if it has been changed by another program (e.g. the rdgai GUI). """
        with self.lock:
            if self.path.stat().st_mtime != self._mtime:
                self.load()

    def save(self):
        with self.lock:
            fd, tmp = tempfile.mkstemp(dir=self.path.parent, prefix=f".{self.path.name}.", suffix=".tmp")
            os.close(fd)
            try:
                self.doc.write(tmp)
                os.replace(tmp, self.path)
            finally:
                if os.path.exists(tmp):
                    os.remove(tmp)
            self._mtime = self.path.stat().st_mtime

    def check_writable(self):
        if self.read_only:
            raise ReadOnlyError("This server is read-only: classifications cannot be changed.")

    # -- lookups ---------------------------------------------------------------

    def get_app(self, unit_id:str) -> App:
        try:
            return self.doc[unit_id]
        except KeyError:
            raise AnnotationError(f"No variation unit with id '{unit_id}'.")

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

    @staticmethod
    def is_classified(pair:Pair) -> bool:
        return bool(pair.types) or bool(pair.get_inverse().types)

    def status(self, app:App) -> str:
        pairs = self.substantive_pairs(app)
        if not pairs:
            return "nothing_to_classify"
        classified = sum(self.is_classified(pair) for pair in pairs)
        if classified == 0:
            return "unclassified"
        if classified == len(pairs):
            return "classified"
        return "partial"

    def change_info(self, change:str) -> dict:
        """ Looks up the <change> element referenced by a relation's @change attribute. """
        change_id = change.lstrip("#").split()[0] if change else ""
        if not change_id:
            return {}
        for element in unique(find_elements(self.doc.tree, ".//revisionDesc/change")):
            if element.attrib.get("{http://www.w3.org/XML/1998/namespace}id") == change_id:
                model = next((extract_text(name, include_tail=False) for name in unique(find_elements(element, ".//name[@type='model']"))), "")
                return dict(when=element.attrib.get("when", ""), model=model)
        return {}

    # -- serialisation -----------------------------------------------------------

    def pair_info(self, pair:Pair) -> PairInfo:
        category = sorted(pair.relation_type_names())
        inverse_category = sorted(pair.get_inverse().relation_type_names())
        provenance = None
        if pair.relation_element() is not None:
            data = pair.provenance()
            provenance = Provenance(**data, **self.change_info(data.get("change", "")))
        return PairInfo(
            active=pair.active.n,
            passive=pair.passive.n,
            active_text=str(pair.active),
            passive_text=str(pair.passive),
            category=" ".join(category) or None,
            inverse_category=" ".join(inverse_category) or None,
            provenance=provenance,
        )

    def unit_summary(self, app:App) -> UnitSummary:
        pairs = self.substantive_pairs(app)
        lemma = extract_text(find_element(app.element, ".//lem"))
        return UnitSummary(
            id=str(app),
            verse=verse_of(str(app)),
            lemma=lemma or "OMIT",
            reading_count=len(app.readings),
            pair_count=len(pairs),
            classified_count=sum(self.is_classified(pair) for pair in pairs),
            status=self.status(app),
        )

    # -- read operations -----------------------------------------------------------

    def definitions(self) -> Definitions:
        with self.lock:
            self.refresh()
            categories = [
                Category(name=relation_type.name, description=relation_type.description, inverse=relation_type.get_inverse().name)
                for relation_type in self.doc.relation_types.values()
            ]
            return Definitions(
                language=self.doc.language,
                guidance=self.doc.get_definitions_comment(),
                direction_convention=DIRECTION_CONVENTION,
                categories=categories,
            )

    def list_units(self, status:UnitStatus="all", verse:str="", offset:int=0, limit:int=50) -> UnitList:
        with self.lock:
            self.refresh()
            apps = self.doc.apps
            if verse:
                apps = [app for app in apps if matches_verse(str(app), verse)]
            if status != "all":
                apps = [app for app in apps if self.status(app) == status]
            offset = max(offset, 0)
            limit = max(min(limit, 500), 1)
            return UnitList(
                total=len(apps),
                offset=offset,
                units=[self.unit_summary(app) for app in apps[offset:offset+limit]],
            )

    def get_unit(self, unit_id:str) -> UnitDetail:
        with self.lock:
            self.refresh()
            app = self.get_app(unit_id)
            readings = [
                ReadingInfo(
                    id=reading.n,
                    text=str(reading),
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
                pairs=[self.pair_info(pair) for pair in self.substantive_pairs(app)],
                status=self.status(app),
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
            counts = {name: CategoryCount(category=name, total=0, human=0, pipeline=0) for name in self.doc.relation_types}
            for pair in self.directed_pairs():
                for name in pair.relation_type_names():
                    count = counts.setdefault(name, CategoryCount(category=name, total=0, human=0, pipeline=0))
                    count.total += 1
                    if self.is_pipeline(pair):
                        count.pipeline += 1
                    else:
                        count.human += 1
            pairs = [pair for app in self.doc.apps for pair in self.substantive_pairs(app)]
            return CategoryCounts(
                pairs_to_classify=len(pairs),
                classified=sum(self.is_classified(pair) for pair in pairs),
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
            pairs = [pair for pair in self.directed_pairs() if relation_type in pair.types]
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

    # -- write operations -----------------------------------------------------------

    def ensure_resp_stmt(self, xml_id:str, name:str, resp:str):
        """ Adds <respStmt xml:id="..."> to the titleStmt so that @resp pointers resolve. """
        tree = self.doc.tree
        for resp_stmt in unique(find_elements(tree, ".//respStmt")):
            if resp_stmt.attrib.get("{http://www.w3.org/XML/1998/namespace}id") == xml_id:
                return
        title_stmt = find_element(tree, ".//teiHeader/fileDesc/titleStmt")
        if title_stmt is None:
            return
        resp_stmt = ET.SubElement(title_stmt, tei_tag(title_stmt, "respStmt"), attrib={"{http://www.w3.org/XML/1998/namespace}id": xml_id})
        ET.SubElement(resp_stmt, tei_tag(title_stmt, "resp")).text = resp
        ET.SubElement(resp_stmt, tei_tag(title_stmt, "name")).text = name

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
    ) -> WriteResult:
        """
        Sets the category for the change from the active reading to the passive reading.
        Replaces any existing category, sets the inverse on the reverse pair and saves the file.

        resp defaults to the store's annotator.
        """
        with self.lock:
            self.check_writable()
            self.refresh()
            if cert not in CERTAINTY_VALUES:
                raise AnnotationError(f"cert must be one of {', '.join(CERTAINTY_VALUES)}.")
            resp = (resp or self.annotator or "").lstrip("#")
            if not resp:
                raise AnnotationError("No annotator is set: start the server with --annotator.")

            app = self.get_app(unit_id)
            pair = self.get_pair(app, active, passive)
            relation_type = self.get_relation_type(category)

            if resp == RDGAI_RESP:
                self.ensure_resp_stmt(RDGAI_RESP, "rdgai", "automatic classification")
            elif resp == self.annotator:
                self.ensure_resp_stmt(resp, self.annotator_name or resp, "classification of transcriptional changes")

            justification = (justification or "").strip()
            inverse_justification = f"Inverse of {active} → {passive}: {justification}" if justification else ""
            pair.set_type_with_inverse(
                relation_type,
                responsible=f"#{resp}",
                cert=cert,
                description=justification,
                inverse_description=inverse_justification,
                change=f"#{change.lstrip('#')}" if change else None,
            )
            self.save()
            return WriteResult(
                unit_id=str(app),
                pair=self.pair_info(pair),
                inverse=self.pair_info(pair.get_inverse()),
                saved_to=str(self.path),
            )

    def clear_classification(self, unit_id:str, active:str, passive:str) -> WriteResult:
        """ Removes the classification of a pair and its inverse and saves the file. """
        with self.lock:
            self.check_writable()
            self.refresh()
            app = self.get_app(unit_id)
            pair = self.get_pair(app, active, passive)
            pair.clear_types_with_inverse()
            self.save()
            return WriteResult(
                unit_id=str(app),
                pair=self.pair_info(pair),
                inverse=self.pair_info(pair.get_inverse()),
                saved_to=str(self.path),
            )

    def start_run(self, model:str, description:str="") -> str:
        """
        Records a pipeline run as a <change> in the revisionDesc and returns its xml:id.
        Relations written during the run point to it with @change.
        """
        with self.lock:
            self.check_writable()
            self.refresh()
            tree = self.doc.tree
            root = tree.getroot()
            header = find_element(tree, ".//teiHeader")
            revision_desc = find_element(header, ".//revisionDesc")
            if revision_desc is None:
                revision_desc = ET.SubElement(header, tei_tag(header, "revisionDesc"))
            now = datetime.now(timezone.utc).replace(microsecond=0)
            change_id = f"rdgai-run-{now.strftime('%Y%m%dT%H%M%SZ')}"
            existing = {
                element.attrib.get("{http://www.w3.org/XML/1998/namespace}id")
                for element in unique(find_elements(root, ".//revisionDesc/change"))
            }
            suffix = 1
            base = change_id
            while change_id in existing:
                suffix += 1
                change_id = f"{base}-{suffix}"
            self.ensure_resp_stmt(RDGAI_RESP, "rdgai", "automatic classification")
            change = ET.SubElement(
                revision_desc,
                tei_tag(header, "change"),
                attrib={
                    "{http://www.w3.org/XML/1998/namespace}id": change_id,
                    "when": now.isoformat().replace("+00:00", "Z"),
                    "who": f"#{RDGAI_RESP}",
                },
            )
            change.text = (description.strip() + " " if description.strip() else "") + "Classification by rdgai using "
            model_name = ET.SubElement(change, tei_tag(header, "name"), attrib={"type": "model"})
            model_name.text = model
            model_name.tail = "."
            self.save()
            return change_id
