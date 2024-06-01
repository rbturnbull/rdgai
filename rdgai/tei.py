import re
from lxml.etree import _ElementTree as ElementTree
from lxml.etree import _Element as Element
from vorlagellm.tei import find_element, find_elements, extract_text
from rich.console import Console

from dataclasses import dataclass

error_console = Console(stderr=True, style="bold red")


@dataclass
class RelationCategory():
    name:str
    element:Element
    description:str=""
    

def get_relation_categories(doc:ElementTree|Element) -> list[RelationCategory]:
    interp_group = find_element(doc, ".//interpGrp[@type='transcriptional']")
    
    relation_categories = []
    if interp_group is None:
        error_console.print("No interpGrp of type='transcriptional' found in TEI file.")
        return relation_categories
    
    for interp in find_elements(interp_group, "./interp"):
        name = interp.attrib.get("{http://www.w3.org/XML/1998/namespace}id", "")
        description = extract_text(interp).strip()
        relation_categories.append(RelationCategory(name=name, element=interp, description=description))

    return relation_categories