import math
from typing import Optional
from pathlib import Path
from dataclasses import dataclass, field
from lxml.etree import _Element as Element
from lxml.etree import _ElementTree as ElementTree
from lxml import etree as ET
from rich.console import Console
import textwrap
from collections import defaultdict

# from .relations import Relation, get_reading_identifier
from .tei import read_tei, find_elements, extract_text, find_parent, find_element, write_tei, make_nc_name, get_language, get_reading_identifier, extract_text_siblings
from .notes import parse_reading_notes


def unique(elements:list[Element]) -> list[Element]:
    """ Removes duplicates (find_elements can return the same element for namespaced and plain queries). """
    seen = set()
    result = []
    for element in elements:
        if id(element) not in seen:
            seen.add(id(element))
            result.append(element)
    return result


def relation_provenance(relation:Element) -> dict:
    """ Reads resp, cert, change, the justification and the time saved from a <relation> element. """
    justification_parts = []
    when = ""
    justification_source = ""
    for desc in unique(find_elements(relation, "./desc")):
        if desc.attrib.get("type", "justification") != "justification":
            continue
        justification_source = justification_source or desc.attrib.get("resp", "").lstrip("#")
        text = desc.text or ""
        for child in desc:
            if isinstance(child.tag, str) and ET.QName(child).localname == "date":
                when = when or child.attrib.get("when", "")
            else:
                text += " " + extract_text(child, include_tail=False)
            text += child.tail or ""
        justification_parts.append(" ".join(text.split()))
    return dict(
        resp=relation.attrib.get("resp", ""),
        cert=relation.attrib.get("cert", ""),
        change=relation.attrib.get("change", ""),
        justification="\n".join(part for part in justification_parts if part).strip(),
        when=when,
        justification_source=justification_source,
    )


def prune_empty_list(list_relation:Element|None):
    """
    Removes a <listRelation> with no <relation> left (TEI requires at least one), and its <note> if that is now empty.
    """
    if list_relation is None or len(list_relation) > 0:
        return
    parent = list_relation.getparent()
    if parent is None:
        return
    parent.remove(list_relation)
    if ET.QName(parent).localname == "note" and len(parent) == 0 and not (parent.text or "").strip():
        grandparent = parent.getparent()
        if grandparent is not None:
            grandparent.remove(parent)


def tei_tag(reference:Element, tag:str) -> str:
    """ Returns the tag name in the same namespace as the reference element. """
    namespace = ET.QName(reference).namespace
    return f"{{{namespace}}}{tag}" if namespace else tag

@dataclass
class Reading():
    element: Element
    app:"App"
    n: str  = field(default=None)
    text: str  = field(default=None)
    witnesses: list[str] = field(default_factory=list)
    type: str = field(default="")
    raw_text: str = field(default="")
    notes: list[str] = field(default_factory=list)

    def __post_init__(self):
        self.n = get_reading_identifier(self.element)
        self.raw_text = extract_text(self.element).strip()
        self.text = self.raw_text
        if self.app.doc.reading_notes:
            self.text, self.notes = parse_reading_notes(self.raw_text)
        self.witnesses = self.element.attrib.get("wit", "").split()
        self.type = self.element.attrib.get("type", "")

    def __str__(self):
        return self.text or 'OMIT'
    
    def witnesses_str(self) -> str:
        return " ".join(self.witnesses)

    def __hash__(self):
        return hash(self.element)

    def text_in_context(self, text:str|None=None) -> str:
        text = self.text if text is None else text
        return self.app.text_in_context(text)


@dataclass
class RelationType():
    element: Element
    name: str
    description: str
    inverse: Optional['RelationType'] = None
    pairs: set['Pair'] = field(default_factory=set)

    def __str__(self):
        return self.name
    
    def __repr__(self) -> str:
        return str(self)

    def __eq__(self, other):
        if isinstance(other, RelationType):
            return (self.name, self.element, self.description) == (other.name, other.element, other.description)
        return False

    def __hash__(self):
        return hash((self.name, self.element, self.description))
    
    def str_with_description(self) -> str:
        result = self.name
        if self.description:
            result += f": {self.description}"
        return result

    def pairs_sorted(self, exclude_rdgai:bool = False) -> list['Pair']:
        pairs = sorted(self.pairs, key=lambda pair: (str(pair.active.app), pair.active.n, pair.passive.n))
        if exclude_rdgai:
            pairs = [pair for pair in pairs if not pair.rdgai_responsible()]
        return pairs

    def get_inverse(self) -> 'RelationType':
        return self.inverse if self.inverse else self


@dataclass
class Pair():
    active: Reading
    passive: Reading
    types: set[RelationType] = field(default_factory=set)

    def __post_init__(self):
        for relation_type in self.types:
            relation_type.pairs.add(self)

    def __str__(self):
        return f"{self.active} ➞ {self.passive}"
    
    def print(self, console):
        console.print(f"[bold red]{self.app}[/bold red]: [green]{self.active}[/green] [red]➞[/red] [green]{self.passive}[/green]")

    def reading_transition_str(self) -> str:
        return f"{self.active or 'OMISSION'} → {self.passive or 'OMISSION'}"
    
    def __repr__(self) -> str:
        return str(self)

    @property
    def app(self) -> "App":
        # assert self.active.app == self.passive.app
        return self.active.app
    
    def __hash__(self):
        return hash((self.active, self.passive))
    
    def app_element(self) -> Element:
        return find_parent(self.active.element, "app")
    
    def relation_elements(self) -> list[Element]:
        list_relation = find_element(self.app_element(), ".//listRelation[@type='transcriptional']")
        if list_relation is None:
            return []

        return [
            relation for relation in unique(find_elements(list_relation, ".//relation"))
            if relation.attrib.get("active") == self.active.n and relation.attrib.get("passive") == self.passive.n
        ]
    
    def element_for_type(self, type:RelationType) -> Element|None:        
        for relation in self.relation_elements():
            if f"#{type.name}" in relation.attrib.get("ana").split():
                return relation
        return None
    
    def get_inverse(self) -> "Pair":
        found_pair = None
        for pair in self.app.pairs:
            if pair.active == self.passive and pair.passive == self.active:
                found_pair = pair
                break
        assert found_pair is not None, f"No inverse pair found for {self}"
        return found_pair
    
    def add_type_with_inverse(self, type:RelationType, responsible:str|None=None, description:str="", inverse_description:str="") -> Element:
        relation = self.add_type(type, responsible=responsible, description=description)
        inverse = self.get_inverse()
        inverse.add_type(type.get_inverse(), responsible=responsible, description=inverse_description)
        return relation

    def add_type(self, type:RelationType, responsible:str|None=None, description:str="") -> Element:
        self.types.add(type)
        type.pairs.add(self)

        # Check if the relation already exists
        relation = self.element_for_type(type)
        if relation is not None:
            return relation

        list_relation = self.list_relation_element()
        
        relation = find_element(list_relation, f".//relation[@active='{self.active.n}'][@passive='{self.passive.n}']")
        if relation is not None:
            if type.name not in relation.attrib.get("ana").split():
                relation.attrib["ana"] += f" #{type.name}"
        else:
            relation = ET.SubElement(list_relation, "relation", attrib={"active":self.active.n, "passive":self.passive.n, "ana":f"#{type.name}"})

        if responsible is not None:
            relation.set("resp", responsible)

        self.add_description(description, relation)

        return relation
    
    def list_relation_element(self, create:bool=True) -> Element|None:
        """ Returns the <listRelation type="transcriptional"> of this pair's app, creating it if necessary. """
        app_element = self.app_element()
        list_relation = find_element(app_element, ".//listRelation[@type='transcriptional']")
        if list_relation is None and create:
            # TEI does not allow <listRelation> directly in <app>, so it goes in a <note> (as teiphy expects).
            note = ET.SubElement(app_element, tei_tag(app_element, "note"))
            list_relation = ET.SubElement(note, tei_tag(app_element, "listRelation"), attrib={"type":"transcriptional"})
        return list_relation

    def set_type(
        self,
        type:RelationType,
        responsible:str|None=None,
        cert:str|None=None,
        description:str="",
        change:str|None=None,
        when:str|None=None,
        description_resp:str|None=None,
    ) -> Element:
        """
        Sets the classification of this pair to a single relation type, replacing any existing classification.

        The justification and the time it was saved (``when``) go in a single <desc type="justification">,
        as TEI allows only one <desc> in a <relation>.

        Any existing <relation> elements for this pair are removed so that the pair has exactly one category
        (teiphy adds the rates of multiple categories on one pair).
        """
        self.clear_types()
        self.types = {type}
        type.pairs.add(self)

        list_relation = self.list_relation_element()
        attrib = {"active":self.active.n, "passive":self.passive.n, "ana":f"#{type.name}"}
        if responsible:
            attrib["resp"] = responsible
        if cert:
            attrib["cert"] = cert
        if change:
            attrib["change"] = change
        relation = ET.SubElement(list_relation, tei_tag(list_relation, "relation"), attrib=attrib)

        description = (description or "").strip()
        if description or when:
            desc_attrib = {"type":"justification"}
            if description and description_resp:
                desc_attrib["resp"] = description_resp  # whose wording the justification is, if not the label's resp
            desc = ET.SubElement(relation, tei_tag(relation, "desc"), attrib=desc_attrib)
            desc.text = description or None
            if when:
                ET.SubElement(desc, tei_tag(relation, "date"), attrib={"when": when})

        return relation

    def set_type_with_inverse(
        self,
        type:RelationType,
        responsible:str|None=None,
        cert:str|None=None,
        description:str="",
        inverse_description:str|None=None,
        change:str|None=None,
        when:str|None=None,
        description_resp:str|None=None,
    ) -> Element:
        """ Sets the classification of this pair and sets the inverse category on the inverse pair. """
        relation = self.set_type(
            type, responsible=responsible, cert=cert, description=description, change=change, when=when, description_resp=description_resp,
        )
        inverse_description = description if inverse_description is None else inverse_description
        self.get_inverse().set_type(
            type.get_inverse(), responsible=responsible, cert=cert, description=inverse_description, change=change, when=when,
            description_resp=description_resp,
        )
        return relation

    def clear_types(self):
        """ Removes all classifications (and their <relation> elements) for this pair. """
        for relation_type in list(self.types):
            relation_type.pairs.discard(self)
        self.types = set()
        for relation in self.relation_elements():
            list_relation = relation.getparent()
            list_relation.remove(relation)
            prune_empty_list(list_relation)

    def clear_types_with_inverse(self):
        self.clear_types()
        self.get_inverse().clear_types()

    def relation_element(self) -> Element|None:
        elements = self.relation_elements()
        return elements[0] if elements else None

    def provenance(self) -> dict:
        """ Returns the responsibility, certainty, change and justification recorded on this pair's relation. """
        relation = self.relation_element()
        if relation is None:
            return {}
        return relation_provenance(relation)

    def remove_description(self):
        for relation in self.relation_elements():
            for desc in find_elements(relation, ".//desc"):
                relation.remove(desc)
                
    def add_description(self, description:str, relation:Element|None=None):
        if relation is None:
            relation_elements = self.relation_elements()

            if len(relation_elements) == 0:
                list_relation = self.list_relation_element()
                relation = ET.SubElement(list_relation, "relation", attrib={"active":self.active.n, "passive":self.passive.n})
            else:        
                relation = relation_elements[0]

        description = description.strip()
        if description:
            description_element = find_element(relation, ".//desc")
            if description_element is None:
                description_element = ET.SubElement(relation, "desc")
                
            description_element.text = description

    def remove_type(self, relation_type:RelationType):
        if relation_type in self.types:
            self.types.remove(relation_type)

        if self in relation_type.pairs:    
            relation_type.pairs.remove(self)

        list_relation = find_element(self.app_element(), ".//listRelation[@type='transcriptional']")
        for relation in find_elements(list_relation, f".//relation[@active='{self.active.n}'][@passive='{self.passive.n}']"):
            if f"#{relation_type.name}" in relation.attrib.get("ana").split():
                relation.attrib['ana'] = " ".join([ana for ana in relation.attrib.get("ana").split() if ana != f"#{relation_type.name}"])
            if not relation.attrib.get("ana"):
                relation.getparent().remove(relation)

    def remove_type_with_inverse(self, relation_type:RelationType):
        self.remove_type(relation_type)
        inverse = self.get_inverse()
        inverse.remove_type(relation_type.get_inverse())

    def remove_all_types(self):
        for relation_type in set(self.types):
            self.remove_type_with_inverse(relation_type)

    def rdgai_responsible(self) -> bool:
        for element in self.relation_elements():
            if element.attrib.get('resp', '') == '#rdgai':
                return True
        return False

    def relation_type_names(self) -> set[str]:
        return set(type.name for type in self.types)
    
    def has_description(self) -> bool:
        for relation in self.relation_elements():
            if find_element(relation, ".//desc") is not None:
                return True
        return False

    def get_description(self) -> str:
        description = ""
        for relation_element in self.relation_elements():
            for desc in find_elements(relation_element, ".//desc"):
                description += "\n" + extract_text(desc)
        return description.strip()


@dataclass
class App():
    element: Element
    doc: "Doc"
    readings: list[Reading] = field(default_factory=list)
    pairs: list[Pair] = field(default_factory=list)
    non_redundant_pairs: list[Pair] = field(default_factory=list)
    id_to_reading: dict[str,Reading] = field(default_factory=dict)

    def __post_init__(self):
        for reading in find_elements(self.element, ".//rdg"):
            self.readings.append(Reading(reading, app=self))

        # Index relation elements by (active, passive)
        relation_elements = defaultdict(list)
        for list_relation in unique(find_elements(self.element, ".//listRelation[@type='transcriptional']")):
            for relation_element in unique(find_elements(list_relation, ".//relation")):
                key = (relation_element.attrib.get("active"), relation_element.attrib.get("passive"))
                relation_elements[key].append(relation_element)
        
        # Build list of relation pairs
        active_visited = set()
        for active in self.readings:
            active_visited.add(active)
            for passive in self.readings:
                if active == passive:
                    continue

                types = set()
                for relation_element in relation_elements.get((active.n, passive.n), []):
                    for ana in relation_element.attrib.get("ana", "").split():
                        if ana.startswith("#"):
                            ana = ana[1:]
                        if ana:
                            types.add(ana)

                pair_relation_types = set()
                for type_name in types:
                    relation_type = self.doc.relation_types[type_name] if type_name in self.doc.relation_types else self.doc.add_relation_type(type_name)                    
                    pair_relation_types.add(relation_type)

                pair = Pair(active=active, passive=passive, types=pair_relation_types)
                self.pairs.append(pair)
                if passive not in active_visited:
                    self.non_redundant_pairs.append(pair)

                for relation_type in pair_relation_types:
                    relation_type.pairs.add(pair)

        assert len(self.pairs) == len(self.non_redundant_pairs) * 2

        self.id_to_reading = {reading.n: reading for reading in self.readings}

    def __getitem__(self, key):
        return self.id_to_reading[key]

    def __len__(self):
        return len(self.readings)

    def get_classified_pairs(self, redundant:bool=True) -> list[Pair]:
        pairs = self.pairs if redundant else self.non_redundant_pairs
        return [pair for pair in pairs if len(pair.types) > 0]

    def get_unclassified_pairs(self, redundant:bool=True) -> list[Pair]:
        pairs = self.pairs if redundant else self.non_redundant_pairs
        return [pair for pair in pairs if len(pair.types) == 0]

    def __hash__(self):
        return hash(self.element)

    def __str__(self):
        name = self.element.attrib.get('{http://www.w3.org/XML/1998/namespace}id', '')
        if not name:
            name = self.element.attrib.get('n', '')

        if not name:
            ab = self.ab()
            if ab is not None:
                for index, app in enumerate(find_elements(ab, ".//app")):
                    if app == self.element:
                        name = make_nc_name(f"{self.ab_name()}-{index+1}")
                        self.element.attrib['{http://www.w3.org/XML/1998/namespace}id'] = name
                        break
            if not name:
                root = self.element.getroottree().getroot()
                for index, app in enumerate(find_elements(root, ".//app")):
                    if app == self.element:
                        name = make_nc_name(f"app-{index+1}")
                        self.element.attrib['{http://www.w3.org/XML/1998/namespace}id'] = name
                        break
        return str(name).replace(" ", "_").replace(":", "_")

    def ab(self) -> Element|None:
        return find_parent(self.element, "ab")

    def lacunae(self) -> list[str]:
        """ Returns the witnesses recorded as lacunose for this unit in <witDetail type="lac">. """
        witnesses = []
        for wit_detail in unique(find_elements(self.element, ".//witDetail[@type='lac']")):
            witnesses.extend(wit_detail.attrib.get("wit", "").split())
        return witnesses

    def ab_name(self) -> str:
        ab = self.ab()
        if ab is None:
            return ""
        return ab.attrib.get("n", "")
    
    def text_before(self) -> str:
        ab = self.ab()
        if ab is None:
            return extract_text_siblings(self.element, "milestone", truncate=100, preceding=True)
        
        items = []
        for child in ab:
            if child == self.element:
                break
            child_text = extract_text(child)
            if child_text:
                items.append(child_text)

        text = " ".join(items)
        return text.strip()
    
    def text_in_context(self, text:str|None=None) -> str:
        return f"{self.text_before()} {self.text_with_signs(text)} {self.text_after()}".strip()

    def text(self) -> str:
        text = extract_text(self.element)
        if self.doc.reading_notes:
            text, _ = parse_reading_notes(text)
        return text

    def text_with_signs(self, text:str|None=None) -> str:
        text = self.text() if text is None else text
        if not text:
            return "⸆"
        return f"⸂{text}⸃"

    def text_after(self) -> str:
        ab = self.ab()
        if ab is None:
            return extract_text_siblings(self.element, "milestone", truncate=100)
        
        items = []
        reached_element = False
        for child in ab:
            if reached_element:
                child_text = extract_text(child)
                if child_text:
                    items.append(child_text)
            if child == self.element:
                reached_element = True

        text = " ".join(items)
        return text.strip()

    def entropy(self) -> float:
        counts = [len(reading.witnesses) for reading in self.readings if len(reading.witnesses) > 0]
        total = sum(counts)
        probabilities = [count / total for count in counts]
        entropy = sum(-p * math.log2(p) for p in probabilities)

        return entropy
        

@dataclass
class Doc():
    path: Path
    tree: ElementTree = field(default=None)
    apps: list[App] = field(default_factory=list)
    relation_types: dict[str,RelationType] = field(default_factory=dict)
    id_to_app: dict[str,App] = field(default_factory=dict)
    reading_notes: bool = True
    
    def __post_init__(self):
        self.tree = read_tei(self.path)
        self.relation_types = self.get_relation_types()

        for app_element in find_elements(self.tree, ".//app"):
            app = App(app_element, doc=self)
            self.apps.append(app)

        self.id_to_app = {app.__str__(): app for app in self.apps}        

    def __getitem__(self, key):
        return self.id_to_app[key]

    def __len__(self):
        return len(self.apps)

    def get_interpgrp(self) -> Element:
        text = find_element(self.tree, ".//text") 
        interp_group = find_element(text, ".//interpGrp[@type='transcriptional']") 
        if interp_group is None: 
            interp_group = ET.Element("interpGrp", attrib={"type":"transcriptional"}) 
            text.insert(0, interp_group) 

        return interp_group

    def get_definitions_comment(self) -> str:
        """
        Returns the text of the XML comment(s) immediately preceding the <interpGrp type="transcriptional">.

        This is where guidance such as a decision order for the categories is kept.
        """
        interp_group = find_element(find_element(self.tree, ".//text"), ".//interpGrp[@type='transcriptional']")
        if interp_group is None:
            return ""
        comments = []
        for sibling in interp_group.itersiblings(preceding=True):
            if not isinstance(sibling, ET._Comment):
                break
            comments.append(textwrap.dedent(sibling.text or "").strip())
        return "\n\n".join(reversed(comments))

    def add_relation_type(self, name:str, description:str="") -> RelationType:
        if name in self.relation_types:
            assert self.relation_types[name].description == description, f"RelationType {name} already exists with a different description."
            return self.relation_types[name]
    
        interp_group = self.get_interpgrp()
        interp = find_element(interp_group, f".//interp[@xml:id='{name}']")
        if interp is None:
            interp = ET.Element("interp", attrib={"{http://www.w3.org/XML/1998/namespace}id":name})
            interp_group.append(interp)

        relation_type = RelationType(name=name, element=interp, description="")
        self.relation_types[name] = relation_type
        return relation_type

    def __str__(self):
        return str(self.path)

    def __repr__(self) -> str:
        return str(self)

    def write(self, output:str|Path):
        write_tei(self.tree, output)

    @property
    def language(self):
        return get_language(self.tree)
    
    def get_relation_types(self, categories_to_ignore:list[str]|None=None) -> list[RelationType]:
        interp_group = self.get_interpgrp()
        categories_to_ignore = categories_to_ignore or []
        
        relation_types = dict()
        assert interp_group is not None, "No interpGrp of type='transcriptional' found in TEI file."
        
        for interp in find_elements(interp_group, "./interp"):
            name = interp.attrib.get("{http://www.w3.org/XML/1998/namespace}id", "")
            if name in categories_to_ignore: continue

            description = extract_text(interp).strip()
            relation_types[name] = RelationType(name=name, element=interp, description=description)

        # get corresponding relations
        for category in relation_types.values():
            inverse_name = category.element.attrib.get("corresp", "")
            if inverse_name.startswith("#"):
                inverse_name = inverse_name[1:]

            if inverse_name in relation_types:
                inverse = relation_types[inverse_name]
                category.inverse = inverse
                if inverse.inverse is None:
                    inverse.inverse = category
                else:
                    assert inverse.inverse == category, f"Inverse category {inverse} already has an inverse {inverse.inverse}."

        return relation_types

    def get_classified_pairs(self, redundant:bool=True) -> list[Pair]:
        pairs = []
        for app in self.apps:
            pairs.extend(app.get_classified_pairs(redundant=redundant))

        return pairs

    def get_unclassified_pairs(self, redundant:bool=True) -> list[Pair]:
        pairs = []
        for app in self.apps:
            pairs.extend(app.get_unclassified_pairs(redundant=redundant))

        return pairs

    def print_classified_pairs(self, console:Console|None=None) -> None:
        console = console or Console()
        for relation_type in self.relation_types.values():
            console.rule(str(relation_type))
            console.print(relation_type.description, style="grey46")
            for pair in relation_type.pairs_sorted():
                pair.print(console)

            console.print("")

    def clean(self, output:Path|None=None):
        """ Cleans a TEI XML file for common errors. """

        # find all transcriptional listRelation elements (not suggestions or intrinsic relations)
        list_relations = unique(find_elements(self.tree, ".//listRelation[@type='transcriptional']"))
        for list_relation in list_relations:
            relations_so_far = set()
            for relation in find_elements(list_relation, ".//relation"):
                # make sure that relation elements have a # at the start of the ana attribute
                if not relation.attrib['ana'].startswith("#"):
                    relation.attrib['ana'] = f"#{relation.attrib['ana']}"
                
                relations_so_far.add( (relation.attrib['active'], relation.attrib['passive']) )
            
            # consolidate duplicate relations
            for active, passive in relations_so_far:
                relations = find_elements(list_relation, f".//relation[@active='{active}'][@passive='{passive}']")
                if len(relations) > 1:
                    analytic_set = set()
                    for relation in relations:
                        analytic_set.update(relation.attrib['ana'].split())

                    for relation in relations[1:]:
                        list_relation.remove(relation)

                    relations[0].attrib['ana'] = " ".join(sorted(analytic_set))
        
        if output:
            output = Path(output)
            output.parent.mkdir(parents=True, exist_ok=True)
            print("Writing to", output)
            self.write(output)     
