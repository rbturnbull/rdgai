from pathlib import Path
from dataclasses import dataclass, field
from lxml.etree import _Element as Element
from lxml.etree import _ElementTree as ElementTree

from .relations import Relation
from .tei import read_tei, find_elements, extract_text, find_parent

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


@dataclass
class RelationType():
    name: str
    description: str
    inverse: 'RelationType'

    def __str__(self):
        return self.name
    

@dataclass
class Pair():
    active: Reading
    passive: Reading
    types: list[RelationType] = field(default_factory=list)

    def __str__(self):
        return f"{self.active} ➞ {self.passive}"
    

@dataclass
class App():
    element: Element
    readings: list[Reading] = field(default_factory=list)
    pairs: list[Relation] = field(default_factory=list)

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

                types = []
                for relation_element in relation_elements:
                    if relation_element.attrib.get("active") == active.n and relation_element.attrib.get("passive") == passive.n:
                        ana = relation_element.attrib.get("ana", "")
                        if ana:
                            types.append(ana)
        
                pair = Pair(active=active, passive=passive, types=types)
                self.pairs.append(pair)

    def __str__(self):
        # get xml:id attribute
        return f"{self.element.attrib.get('{http://www.w3.org/XML/1998/namespace}id', '')}"

    def ab(self) -> Element:
        return find_parent(self.element, "ab")

    def ab_name(self) -> str:
        ab = self.ab()
        return ab.attrib.get("n", "")


@dataclass
class Doc():
    path: Path
    tree: ElementTree = field(default=None)
    apps: list[App] = field(default_factory=list)
    
    def __post_init__(self):
        self.tree = read_tei(self.path)
        for app_element in find_elements(self.tree, ".//app"):
            app = App(app_element)
            self.apps.append(app)

    def __str__(self):
        return str(self.path)
    

def read_doc(doc_path:Path) -> Doc:
    doc = Doc(doc_path)
    return doc