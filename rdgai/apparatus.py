from pathlib import Path
from dataclasses import dataclass, field
from lxml.etree import _Element as Element
from lxml.etree import _ElementTree as ElementTree
from lxml import etree as ET

from .relations import Relation
from .tei import read_tei, find_elements, extract_text, find_parent, find_element, write_tei

@dataclass
class Reading():
    element: Element
    n: str  = field(default=None)
    text: str  = field(default=None)
    witnesses: list[str] = field(default_factory=list)


    def __post_init__(self):
        self.n = self.element.attrib.get("n", "")
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
    inverse: 'RelationType' = None

    def __str__(self):
        return self.name
    
    def __hash__(self):
        return hash(self.element)


@dataclass
class Pair():
    active: Reading
    passive: Reading
    types: set[RelationType] = field(default_factory=set)

    def __str__(self):
        return f"{self.active} ➞ {self.passive}"
    
    def __hash__(self):
        return hash((self.active, self.passive))
    
    def app(self) -> Element:
        return find_parent(self.active.element, "app")
    
    def element_for_type(self, type:RelationType) -> Element|None:
        list_relation = find_element(self.app(), ".//listRelation[@type='transcriptional']")
        
        if list_relation is None:
            return None
        
        for relation in find_elements(list_relation, ".//relation"):
            if relation.attrib.get("active") == self.active.n and relation.attrib.get("passive") == self.passive.n and relation.attrib.get("ana") == f"#{type.name}":
                return relation
        return None
    
    def add_type(self, type:RelationType) -> Element|None:
        self.types.add(type.name)

        # Check if the relation already exists
        relation = self.element_for_type(type)
        if relation:
            return relation

        list_relation = find_element(self.app(), ".//listRelation[@type='transcriptional']")
        if list_relation is None:
            list_relation = ET.SubElement("listRelation", attrib={"type":"transcriptional"})
        
        relation = ET.SubElement(list_relation, "relation", attrib={"active":self.active.n, "passive":self.passive.n, "ana":f"#{type.name}"})
        return relation

    def remove_type(self, type:RelationType):
        self.types.remove(type.name)
        relation = self.element_for_type(type)
        if relation is not None:
            relation.getparent().remove(relation)


@dataclass
class App():
    element: Element
    readings: list[Reading] = field(default_factory=list)
    pairs: list[Relation] = field(default_factory=list)
    relation_types: list[RelationType] = field(default_factory=list)

    def __post_init__(self):
        for reading in find_elements(self.element, ".//rdg"):
            self.readings.append(Reading(reading))

        # Build list of relation elements
        relation_elements = []
        for list_relation in find_elements(self.element, ".//listRelation[@type='transcriptional']"):
            for relation_element in find_elements(list_relation, ".//relation"):
                relation_elements.append(relation_element)
        
        # Build list of relation pairs
        for active in self.readings:
            for passive in self.readings:
                if active == passive:
                    continue

                types = set()
                for relation_element in relation_elements:
                    if relation_element.attrib.get("active") == active.n and relation_element.attrib.get("passive") == passive.n:
                        ana = relation_element.attrib.get("ana", "")
                        if ana.startswith("#"):
                            ana = ana[1:]
                        if ana:
                            types.add(ana)


                for type in types:
                    in_list = False
                    for relation_type in self.relation_types:
                        if relation_type.name == type:
                            in_list = True
                            break
                    if not in_list:
                        # See if interpGrp exists
                        text = find_parent(self.element, "text")
                        interp_group = find_element(text, ".//interpGrp[@type='transcriptional']")
                        if interp_group is None:
                            interp_group = ET.Element("interpGrp", attrib={"type":"transcriptional"})
                            text.insert(0, interp_group)
                        
                        interp = ET.Element("interp", attrib={"{http://www.w3.org/XML/1998/namespace}id":type})
                        interp_group.append(interp)

                        self.relation_types.append(RelationType(name=type, element=interp, description=""))

                pair = Pair(active=active, passive=passive, types=types)
                self.pairs.append(pair)

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
                    name = f"{self.ab_name()}-{index+1}"
                    break
        return str(name).replace(" ", "_").replace(":", "_")

    def ab(self) -> Element:
        return find_parent(self.element, "ab")

    def ab_name(self) -> str:
        ab = self.ab()
        return ab.attrib.get("n", "")


def get_relation_types(doc:ElementTree|Element, categories_to_ignore:list[str]|None=None) -> list[RelationType]:
    interp_group = find_element(doc, ".//interpGrp[@type='transcriptional']")
    categories_to_ignore = categories_to_ignore or []
    
    relation_categories = []
    if interp_group is None:
        print("No interpGrp of type='transcriptional' found in TEI file.")
        return relation_categories
    
    for interp in find_elements(interp_group, "./interp"):
        name = interp.attrib.get("{http://www.w3.org/XML/1998/namespace}id", "")
        if name in categories_to_ignore:
            continue
        description = extract_text(interp).strip()
        relation_categories.append(RelationType(name=name, element=interp, description=description))

    return relation_categories


@dataclass
class Doc():
    path: Path
    tree: ElementTree = field(default=None)
    apps: list[App] = field(default_factory=list)
    relation_types: list[RelationType] = field(default_factory=list)
    
    def __post_init__(self):
        self.tree = read_tei(self.path)
        self.relation_types = get_relation_types(self.tree)

        for app_element in find_elements(self.tree, ".//app"):
            app = App(app_element, relation_types=self.relation_types)
            self.apps.append(app)


    def __str__(self):
        return str(self.path)
    
    def write(self, output:str|Path):
        write_tei(self.tree, output)
    

def read_doc(doc_path:Path) -> Doc:
    doc = Doc(doc_path)
    return doc