"""
A service layer for querying and editing the classifications in a TEI file.

This is used by the MCP server (``rdgai.mcp_server``) and is independent of MCP so that it can be tested
and reused directly. The TEI file is the state: every write is saved to disk straight away.
"""
import os
import re
import json
import hashlib
import threading
import tempfile
from pathlib import Path
from datetime import datetime, timezone
from typing import Literal

from lxml import etree as ET
from pydantic import BaseModel, Field

from .apparatus import Doc, App, Pair, RelationType, tei_tag, unique
from .tei import find_element, find_elements, extract_text
from . import sampling
from .sampling import Sample, default_sample_path


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


NC_NAME = re.compile(r"^[A-Za-z_][A-Za-z0-9_.\-]*$")
XML_ID = "{http://www.w3.org/XML/1998/namespace}id"


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
    definitions_hash: str = Field(description="SHA-256 of the guidance and the categories (name, description, inverse).")


class DefinitionsChange(BaseModel):
    message: str
    relations_updated: int = Field(description="Number of <relation> elements whose category was renamed or reassigned.")
    definitions: Definitions


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
    excluded: bool = Field(description="True if pairs with this reading are not classified (by default, defective readings).")


class UnitSummary(BaseModel):
    id: str
    verse: str
    lemma: str
    reading_count: int
    pair_count: int = Field(description="Number of unordered pairs of substantive readings to classify.")
    classified_count: int
    status: str
    sample_role: str | None = Field(default=None, description="'example' or 'test' if the unit is in the sample.")


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


class SampleProgress(BaseModel):
    role: str
    units: int
    units_classified: int = Field(description="Units whose pairs are all classified.")
    pairs: int
    pairs_classified: int


class SampleUnitStatus(BaseModel):
    id: str
    role: str
    status: str
    pair_count: int
    classified_count: int


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
        check_sample: Check on loading that an existing sample was drawn with the same ignore_types.
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
        check_sample:bool=True,
    ):
        self.path = Path(path)
        if not self.path.exists():
            raise FileNotFoundError(self.path)
        self.sample_path = Path(sample_path) if sample_path else default_sample_path(self.path)
        self.annotator = annotator.lstrip("#") if annotator else None
        self.annotator_name = annotator_name
        self.read_only = read_only
        self.lock_definitions = lock_definitions
        self.ignore_types = set(ignore_types)
        self.lock = threading.RLock()
        self.doc: Doc
        self._mtime: float = 0.0
        self.load()
        if check_sample:
            self.load_sample()  # fails early if the sample was drawn with different settings

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
            sample_role=self.sample_role(str(app)),
        )

    # -- read operations -----------------------------------------------------------

    def definitions(self) -> Definitions:
        with self.lock:
            self.refresh()
            categories = [
                Category(name=relation_type.name, description=relation_type.description, inverse=relation_type.get_inverse().name)
                for relation_type in self.doc.relation_types.values()
            ]
            guidance = self.doc.get_definitions_comment()
            canonical = json.dumps(
                dict(guidance=guidance, categories=[[c.name, c.description, c.inverse] for c in categories]),
                ensure_ascii=False,
            )
            return Definitions(
                language=self.doc.language,
                guidance=guidance,
                direction_convention=DIRECTION_CONVENTION,
                categories=categories,
                definitions_hash=hashlib.sha256(canonical.encode("utf-8")).hexdigest(),
            )

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
                sample_role=self.sample_role(str(app)),
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

    def transcriptional_relations(self) -> list:
        relations = []
        for list_relation in unique(find_elements(self.doc.tree, ".//listRelation[@type='transcriptional']")):
            relations.extend(unique(find_elements(list_relation, ".//relation")))
        return relations

    def relations_using(self, names:set[str]) -> list:
        pointers = {f"#{name}" for name in names}
        return [
            relation for relation in self.transcriptional_relations()
            if pointers & set(relation.attrib.get("ana", "").split())
        ]

    def rename_in_relations(self, mapping:dict[str, str]) -> int:
        """ Replaces category pointers in @ana (old name -> new name). Returns the number of relations changed. """
        pointers = {f"#{old}": f"#{new}" for old, new in mapping.items()}
        changed = 0
        for relation in self.relations_using(set(mapping)):
            tokens = []
            for token in relation.attrib.get("ana", "").split():
                token = pointers.get(token, token)
                if token not in tokens:
                    tokens.append(token)
            relation.set("ana", " ".join(tokens))
            changed += 1
        return changed

    @staticmethod
    def set_interp_text(interp, text:str):
        for child in list(interp):
            interp.remove(child)
        interp.text = text

    def commit_definitions(self, message:str, relations_updated:int=0) -> DefinitionsChange:
        """ Saves the file and reloads it so that categories and pairs are rebuilt from the TEI. """
        self.save()
        self.load()
        return DefinitionsChange(message=message, relations_updated=relations_updated, definitions=self.definitions())

    def update_category(self, name:str, description:str|None=None, new_name:str|None=None) -> DefinitionsChange:
        """
        Changes the description and/or the name of a category.
        Renaming also updates the inverse's @corresp and every classification that uses the category.
        """
        with self.lock:
            self.check_definitions_editable()
            self.refresh()
            relation_type = self.get_relation_type(name)
            name = relation_type.name
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
            return self.commit_definitions(message, relations_updated)

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
        with self.lock:
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
                return self.commit_definitions(f"Added '{name}' and its inverse '{inverse_name}'.")
            return self.commit_definitions(f"Added '{name}' (its own inverse).")

    def remove_category(self, name:str, reassign_to:str|None=None) -> DefinitionsChange:
        """
        Removes a category together with its inverse. If any pairs use them, reassign_to must name a
        category with a corresponding inverse (the pairs' labels are moved to it, keeping their provenance).
        """
        with self.lock:
            self.check_definitions_editable()
            self.refresh()
            relation_type = self.get_relation_type(name)
            inverse = relation_type.get_inverse()
            removed = {relation_type.name, inverse.name}
            removed_text = f"'{relation_type.name}'" + (f" and its inverse '{inverse.name}'" if inverse is not relation_type else "")

            relations_updated = 0
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

            for removed_name in removed:
                element = self.doc.relation_types[removed_name].element
                element.getparent().remove(element)

            message = f"Removed {removed_text}."
            if reassign_to is not None:
                message += f" Reassigned {relations_updated} pairs to '{target.name}'."
            return self.commit_definitions(message, relations_updated)

    def update_guidance(self, guidance:str) -> DefinitionsChange:
        """ Replaces the guidance comment (e.g. the decision order) placed before the category definitions. """
        with self.lock:
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
            return self.commit_definitions("Updated the guidance.")

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
                pairs = self.substantive_pairs(app)
                units.append(SampleUnitStatus(
                    id=unit.id,
                    role=unit.role,
                    status=self.status(app),
                    pair_count=len(pairs),
                    classified_count=sum(self.is_classified(pair) for pair in pairs),
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
        with self.lock:
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
