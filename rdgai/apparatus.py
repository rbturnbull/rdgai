from typing import Optional
from pathlib import Path
from dataclasses import dataclass, field
from lxml.etree import _Element as Element
from lxml.etree import _ElementTree as ElementTree
from lxml import etree as ET
from rich.console import Console

# from .relations import Relation, get_reading_identifier
from .tei import read_tei, find_elements, extract_text, find_parent, find_element, write_tei, make_nc_name, get_language, get_reading_identifier


@dataclass
class Reading():
    element: Element
    app:"App"
    n: str  = field(default=None)
    text: str  = field(default=None)
    witnesses: list[str] = field(default_factory=list)

    def __post_init__(self):
        self.n = get_reading_identifier(self.element)
        self.text = extract_text(self.element).strip()
        self.witnesses = self.element.attrib.get("wit", "").split()

    def __str__(self):
        return self.text or 'OMIT'
    
    def witnesses_str(self) -> str:
        return " ".join(self.witnesses)

    def __hash__(self):
        return hash(self.element)


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

    def pairs_sorted(self) -> list['Pair']:
        return sorted(self.pairs, key=lambda pair: (str(pair.active.app), pair.active.n, pair.passive.n))


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
    
    def element_for_type(self, type:RelationType) -> Element|None:
        list_relation = find_element(self.app_element(), ".//listRelation[@type='transcriptional']")
        
        if list_relation is None:
            return None
        
        for relation in find_elements(list_relation, f".//relation[@active='{self.active.n}'][@passive='{self.passive.n}']"):
            if f"#{type.name}" in relation.attrib.get("ana").split():
                return relation
        return None
    
    def add_type(self, type:RelationType) -> Element|None:
        self.types.add(type.name)
        type.pairs.add(self)

        # Check if the relation already exists
        relation = self.element_for_type(type)
        if relation:
            return relation

        list_relation = find_element(self.app_element(), ".//listRelation[@type='transcriptional']")
        if list_relation is None:
            list_relation = ET.SubElement(self.app_element(), "listRelation", attrib={"type":"transcriptional"})
        
        relation = find_element(list_relation, f".//relation[@active='{self.active.n}'][@passive='{self.passive.n}']")
        if relation is not None:
            if type.name not in relation.attrib.get("ana").split():
                relation.attrib["ana"] += f" #{type.name}"
        else:
            relation = ET.SubElement(list_relation, "relation", attrib={"active":self.active.n, "passive":self.passive.n, "ana":f"#{type.name}"})

        return relation

    def remove_type(self, relation_type:RelationType):
        self.types.remove(relation_type.name)
        relation_type.pairs.remove(self)

        list_relation = find_element(self.app_element(), ".//listRelation[@type='transcriptional']")
        for relation in find_elements(list_relation, f".//relation[@active='{self.active.n}'][@passive='{self.passive.n}']"):
            if f"#{relation_type.name}" in relation.attrib.get("ana").split():
                relation.attrib['ana'] = " ".join([ana for ana in relation.attrib.get("ana").split() if ana != f"#{relation_type.name}"])
            if not relation.attrib.get("ana"):
                relation.getparent().remove(relation)


@dataclass
class App():
    element: Element
    doc: "Doc"
    readings: list[Reading] = field(default_factory=list)
    pairs: list[Pair] = field(default_factory=list)
    non_redundant_pairs: list[Pair] = field(default_factory=list)

    def __post_init__(self):
        for reading in find_elements(self.element, ".//rdg"):
            self.readings.append(Reading(reading, app=self))

        # Build list of relation elements
        relation_elements = []
        for list_relation in find_elements(self.element, ".//listRelation[@type='transcriptional']"):
            for relation_element in find_elements(list_relation, ".//relation"):
                relation_elements.append(relation_element)
        
        # Build list of relation pairs
        active_visited = set()
        for active in self.readings:
            active_visited.add(active)
            for passive in self.readings:
                if active == passive:
                    continue

                types = set()
                for relation_element in relation_elements:
                    if relation_element.attrib.get("active") == active.n and relation_element.attrib.get("passive") == passive.n:
                        for ana in relation_element.attrib.get("ana", "").split():
                            if ana.startswith("#"):
                                ana = ana[1:]
                            if ana:
                                types.add(ana)

                pair_relation_types = set()
                for type_name in types:
                    if type_name in self.doc.relation_types:
                        relation_type = self.doc.relation_types[type_name]
                    else:
                        # build RelationType if necessary
                        text = find_parent(self.element, "text")
                        interp_group = find_element(text, ".//interpGrp[@type='transcriptional']")
                        if interp_group is None:
                            interp_group = ET.Element("interpGrp", attrib={"type":"transcriptional"})
                            text.insert(0, interp_group)
                        
                        interp = find_element(interp_group, f".//interp[@xml:id='{type_name}']")
                        if interp is None:
                            interp = ET.Element("interp", attrib={"{http://www.w3.org/XML/1998/namespace}id":type_name})
                            interp_group.append(interp)

                        relation_type = RelationType(name=type_name, element=interp, description="")
                        self.doc.relation_types[type_name] = relation_type
                    
                    pair_relation_types.add(relation_type)

                pair = Pair(active=active, passive=passive, types=pair_relation_types)
                self.pairs.append(pair)
                if passive not in active_visited:
                    self.non_redundant_pairs.append(pair)

                for relation_type in pair_relation_types:
                    relation_type.pairs.add(pair)

        assert len(self.pairs) == len(self.non_redundant_pairs) * 2

    def get_classified_pairs(self) -> list[Pair]:
        return [pair for pair in self.pairs if len(pair.types) > 0]

    def get_unclassified_pairs(self) -> list[Pair]:
        return [pair for pair in self.pairs if len(pair.types) == 0]

    def __hash__(self):
        return hash(self.element)

    def __str__(self):
        name = self.element.attrib.get('{http://www.w3.org/XML/1998/namespace}id', '')
        if not name:
            name = self.element.attrib.get('n', '')

        if not name:
            ab = self.ab()
            for index, app in enumerate(find_elements(ab, ".//app")):
                if app == self.element:
                    name = make_nc_name(f"{self.ab_name()}-{index+1}")
                    self.element.attrib['{http://www.w3.org/XML/1998/namespace}id'] = name
                    break
        return str(name).replace(" ", "_").replace(":", "_")

    def ab(self) -> Element|None:
        return find_parent(self.element, "ab")

    def ab_name(self) -> str:
        ab = self.ab()
        return ab.attrib.get("n", "")
    
    def text_before(self) -> str:
        ab = self.ab()
        if ab is None:
            return ""
        
        text = ""
        for child in ab:
            if child == self.element:
                break
            text += " " + extract_text(child)

        return text.strip()
    
    def text_in_context(self) -> str:
        return f"{self.text_before()} {self.text_with_signs()} {self.text_after()}"

    def text(self) -> str:
        return extract_text(self.element)

    def text_with_signs(self) -> str:
        text = self.text()
        if not text:
            return "⸆"
        return f"⸂{text}⸃"

    def text_after(self) -> str:
        ab = self.ab()
        if ab is None:
            return ""
        
        text = ""
        reached_element = False
        for child in ab:
            if reached_element:
                text += " " + extract_text(child)
            if child == self.element:
                reached_element = True

        return text.strip()


@dataclass
class Doc():
    path: Path
    tree: ElementTree = field(default=None)
    apps: list[App] = field(default_factory=list)
    relation_types: dict[str,RelationType] = field(default_factory=dict)
    
    def __post_init__(self):
        self.tree = read_tei(self.path)
        self.relation_types = self.get_relation_types()

        for app_element in find_elements(self.tree, ".//app"):
            app = App(app_element, doc=self)
            self.apps.append(app)

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
        interp_group = find_element(self.tree, ".//interpGrp[@type='transcriptional']")
        categories_to_ignore = categories_to_ignore or []
        
        relation_types = dict()
        if interp_group is None:
            print("No interpGrp of type='transcriptional' found in TEI file.")
            return relation_types
        
        for interp in find_elements(interp_group, "./interp"):
            name = interp.attrib.get("{http://www.w3.org/XML/1998/namespace}id", "")
            if name in categories_to_ignore:
                continue
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

    def get_classified_pairs(self) -> list[Pair]:
        pairs = []
        for app in self.apps:
            pairs.extend(app.get_classified_pairs())

        return pairs

    def get_unclassified_pairs(self) -> list[Pair]:
        pairs = []
        for app in self.apps:
            pairs.extend(app.get_unclassified_pairs())

        return pairs

    def print_classified_pairs(self, console:Console) -> None:
        for relation_type in self.relation_types.values():
            console.rule(str(relation_type))
            console.print(relation_type.description)
            for pair in relation_type.pairs_sorted():
                pair.print(console)

            console.print("")


def read_doc(doc_path:Path) -> Doc:
    doc = Doc(doc_path)
    return doc