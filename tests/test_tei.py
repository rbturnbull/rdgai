import pytest
from lxml import etree as ET
from rdgai.tei import get_language_code, extract_text, get_reading_identifier



def test_get_language_code_with_lang_attribute():
    # XML structure with <text> element and xml:lang attribute
    root = ET.Element("root")
    text_element = ET.Element("text", attrib={"{http://www.w3.org/XML/1998/namespace}lang": "en"})
    root.append(text_element)
    doc = ET.ElementTree(root)

    assert get_language_code(doc) == "en"

def test_get_language_code_without_lang_attribute():
    # XML structure with <text> element but no xml:lang attribute
    root = ET.Element("root")
    text_element = ET.Element("text")
    root.append(text_element)
    doc = ET.ElementTree(root)

    assert get_language_code(doc) == ""


def test_get_language_code_no_text_element():
    # XML structure without <text> element
    root = ET.Element("root")
    doc = ET.ElementTree(root)

    assert get_language_code(doc) == ""



def test_extract_text_simple_node():
    node = ET.Element("text")
    node.text = "Hello"
    assert extract_text(node) == "Hello"

def test_extract_text_with_children():
    node = ET.Element("text")
    node.text = "Hello"
    child = ET.Element("child")
    child.text = "World"
    node.append(child)
    assert extract_text(node) == "Hello World"

def test_extract_text_with_tail():
    node = ET.Element("text")
    node.text = "Hello"
    node.tail = "Tail"
    assert extract_text(node) == "Hello Tail"

def test_extract_text_with_excluded_tags():
    for tag in ["pc", "witDetail", "note"]:
        node = ET.Element(tag)
        node.text = "Should be ignored"
        assert extract_text(node) == ""

def test_extract_text_app_with_lem():
    node = ET.Element("app")
    lem = ET.Element("lem")
    lem.text = "Lemma text"
    node.append(lem)
    assert extract_text(node) == "Lemma text"

def test_extract_text_app_with_rdg():
    node = ET.Element("app")
    rdg = ET.Element("rdg")
    rdg.text = "Reading text"
    node.append(rdg)
    assert extract_text(node) == "Reading text"

def test_extract_text_app_no_lem_or_rdg():
    node = ET.Element("app")
    assert extract_text(node) == ""

def test_extract_text_none_node():
    assert extract_text(None) == ""


def test_get_reading_identifier_with_xml_id():
    reading = ET.Element("rdg", attrib={"{http://www.w3.org/XML/1998/namespace}id": "r1"})
    assert get_reading_identifier(reading) == "r1"

def test_get_reading_identifier_with_n_attribute():
    reading = ET.Element("rdg", attrib={"n": "r2"})
    assert get_reading_identifier(reading) == "r2"

def test_get_reading_identifier_create_new_identifier():
    app = ET.Element("app")
    existing_reading = ET.Element("rdg", attrib={"n": "1"})
    app.append(existing_reading)
    new_reading = ET.Element("rdg")
    app.append(new_reading)

    assert get_reading_identifier(new_reading) == "2"
    assert new_reading.attrib["n"] == "2"

def test_get_reading_identifier_assert_check():
    app = ET.Element("app")
    reading = ET.SubElement(app, "rdg")
    with pytest.raises(AssertionError, match="must have a name attribute 'xml:id' or 'n'"):
        get_reading_identifier(reading, check=True, create_if_necessary=False)

def test_get_reading_identifier_no_creation():
    reading = ET.Element("rdg")
    assert get_reading_identifier(reading, create_if_necessary=False) == ""

def test_get_reading_identifier_with_both_attributes():
    reading = ET.Element("rdg", attrib={"{http://www.w3.org/XML/1998/namespace}id": "r1", "n": "r2"})
    assert get_reading_identifier(reading) == "r1"    