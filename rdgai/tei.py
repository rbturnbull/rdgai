import re
from pathlib import Path
from lxml import etree as ET
from lxml.etree import _ElementTree as ElementTree
from lxml.etree import _Element as Element

from .languages import convert_language_code


def get_language_code(doc:ElementTree|Element) -> str:
    """ Reads the element <text> and returns the value of the xml:lang attribute."""
    text = find_element(doc, ".//text")
    if text is None:
        return ""
    
    return text.attrib.get("{http://www.w3.org/XML/1998/namespace}lang", "")


def get_language(doc:ElementTree|Element) -> str:
    code = get_language_code(doc)
    return convert_language_code(code)
    

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


def make_nc_name(string):
    invalid_chars = "!\"#$%&'()*+/:;<=>?@[\]^,{|}~` "
    result = string.translate(str.maketrans(invalid_chars, '_' * len(invalid_chars)))
    # if result[0].isdigit or result[0] in [".", "-"]:
    #     result = "id-" + result

    return result


def extract_text(node:Element, include_tail:bool=True) -> str:
    if node is None:
        return ""
    
    tag = re.sub(r"{.*}", "", node.tag)

    if tag in ["pc", "witDetail", "note"]:
        return ""
    if tag == "app":            
        lemma = find_element(node, ".//lem")
        if lemma is None:
            lemma = find_element(node, ".//rdg")
        return extract_text(lemma) or ""


    text = node.text or ""
    for child in node:
        text += " " + extract_text(child)

    if include_tail:
        text += node.tail or ""

    return text.strip()




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



def find_parent(element:Element, tag:str) -> Element|None:
    """
    Finds the nearest ancestor of the given element with the specified tag.

    Args:
        element (Element): The starting XML element from which to search upward.
        tag (str): The tag name of the ancestor element to find.

    Returns:
        Optional[Element]: The nearest ancestor element with the specified tag, or None if no such element is found.

    Example:
        >>> from xml.etree.ElementTree import Element
        >>> root = Element('root')
        >>> ab = Element('ab')
        >>> section = Element('section')
        >>> target = Element('target')
        >>> root.append(ab)
        >>> ab.append(section)
        >>> section.append(target)
        >>> result = find_parent(target, 'ab')
        >>> assert result == ab

        This will find the <ab> ancestor of the <target> element.
    """
    while element is not None:
        element_tag = re.sub(r"{.*}", "", element.tag)
        if element_tag == tag:
            return element
        element = element.getparent()
    return None


def write_tei(doc:ElementTree, path:Path|str) -> None:
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    doc.write(str(path), encoding="utf-8", xml_declaration=True, pretty_print=True)


