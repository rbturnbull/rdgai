from pathlib import Path
from dataclasses import dataclass
from lxml.etree import _Element as Element
from lxml.etree import _ElementTree as ElementTree

from .relations import Relation
from .tei import read_tei, find_elements, extract_text, find_parent

@dataclass
class Reading():
    element: Element
    n: str
    text: str

    def __str__(self):
        return f"{self.n}: {self.text}"


@dataclass
class App():
    element: Element
    readings: list[Reading]
    relations: list[Relation]

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
    tree: ElementTree    
    apps: list[App]
    
    def __str__(self):
        return str(self.path)
    
    
def read_doc(doc_path:Path) -> Doc:
    doc_tree = read_tei(doc_path)
    apps = []
    for app_element in find_elements(doc_tree, ".//app"):
        app = App(element=app_element, readings=[], relations=[])
        for reading in find_elements(app_element, ".//rdg"):
            n = reading.attrib.get("n", "")
            text = extract_text(reading).strip()
            app.readings.append(Reading(element=reading, n=n, text=text))
        apps.append(app)
    doc = Doc(tree=doc_tree, apps=apps, path=doc_path)
    return doc