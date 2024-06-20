import re
from pathlib import Path
from lxml import etree as ET
from lxml.etree import _ElementTree as ElementTree
from lxml.etree import _Element as Element


def extract_text(node:Element, include_tail:bool=True) -> str:
    text = node.text or ""
    for child in node:
        tag = re.sub(r"{.*}", "", child.tag)
        if tag in ["pc", "witDetail", "note"]:
            continue
        if tag == "app":            
            lemma = find_element(child, ".//lem")
            if lemma is None:
                lemma = find_element(child, ".//rdg")
            text += extract_text(lemma) or ""
            text += " "
        else:
            text += extract_text(child) or ""
        
            if tag == "w":
                text += " "

    if include_tail:
        text += node.tail or ""

    return text



def read_tei(path:Path) -> ElementTree:
    parser = ET.XMLParser(remove_blank_text=True)
    with open(path, 'r') as f:
        return ET.parse(f, parser)


def find_element(doc:ElementTree|Element, xpath:str) -> Element|None:
    if isinstance(doc, ElementTree):
        doc = doc.getroot()
    element = doc.find(xpath, namespaces=doc.nsmap)
    if element is None:
        element = doc.find(xpath)
    return element


def find_elements(doc:ElementTree|Element, xpath:str) -> Element|None:
    if isinstance(doc, ElementTree):
        doc = doc.getroot()
    return doc.findall(xpath, namespaces=doc.nsmap)


