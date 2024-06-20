import lxml.etree as ET
from rdgai.relations import RelationCategory, Relation, make_readings_dict


def test_make_readings_dict_empty_apparatus():
    apparatus = ET.Element('apparatus')
    result = make_readings_dict(apparatus)
    assert result == {}


def test_make_readings_dict_apparatus_with_readings():
    apparatus = ET.Element('apparatus')
    ET.SubElement(apparatus, 'rdg', attrib={'n': '1'})
    ET.SubElement(apparatus, 'rdg', attrib={'n': '2'})

    result = make_readings_dict(apparatus)
    expected = {
        '1': ET.Element('rdg', attrib={'n': '1'}),
        '2': ET.Element('rdg', attrib={'n': '2'})
    }
    assert result.keys() == expected.keys()
    for key in expected:
        assert result[key].attrib == expected[key].attrib


def test_relation_category():
    element = ET.Element("element")
    category = RelationCategory(name="test_category", element=element, description="Test description")
    
    assert category.name == "test_category"
    assert category.element == element
    assert category.description == "Test description"
    assert category.instances == []


def test_relation_set_category():
    apparatus = ET.Element("apparatus")
    active_element = ET.Element("active", n="A")
    passive_element = ET.Element("passive", n="P")
    relation = Relation(
        active="A",
        passive="P",
        location="loc",
        apparatus=apparatus,
        active_element=active_element,
        passive_element=passive_element,
    )
    category_element = ET.Element("category")
    category = RelationCategory(name="category1", element=category_element)

    relation.add_category(category)

    assert relation.categories == {category}
    assert relation.relation_element is not None
    assert relation.relation_element.attrib["ana"] == "#category1"


def test_relation_set_description():
    apparatus = ET.Element("apparatus")
    active_element = ET.Element("active", n="A")
    passive_element = ET.Element("passive", n="P")
    relation = Relation(
        active="A",
        passive="P",
        location="loc",
        apparatus=apparatus,
        active_element=active_element,
        passive_element=passive_element,
    )

    description = "Test description"
    description_element = relation.set_description(description)

    assert description_element.text == description

def test_relation_get_list_relation():
    apparatus = ET.Element("apparatus")
    active_element = ET.Element("active", n="A")
    passive_element = ET.Element("passive", n="P")
    relation = Relation(
        active="A",
        passive="P",
        location="loc",
        apparatus=apparatus,
        active_element=active_element,
        passive_element=passive_element,
    )

    list_relation = relation.get_list_relation()

    assert list_relation.tag == "listRelation"
    assert list_relation.attrib["type"] == "transcriptional"

def test_relation_active_name():
    apparatus = ET.Element("apparatus")
    active_element = ET.Element("active", n="A")
    passive_element = ET.Element("passive", n="P")
    relation = Relation(
        active="A",
        passive="P",
        location="loc",
        apparatus=apparatus,
        active_element=active_element,
        passive_element=passive_element,
    )

    assert relation.active_name == "A"

def test_relation_passive_name():
    apparatus = ET.Element("apparatus")
    active_element = ET.Element("active", n="A")
    passive_element = ET.Element("passive", n="P")
    relation = Relation(
        active="A",
        passive="P",
        location="loc",
        apparatus=apparatus,
        active_element=active_element,
        passive_element=passive_element,
    )

    assert relation.passive_name == "P"

def test_relation_create_relation_element():
    apparatus = ET.Element("apparatus")
    active_element = ET.Element("active", n="A")
    passive_element = ET.Element("passive", n="P")
    relation = Relation(
        active="A",
        passive="P",
        location="loc",
        apparatus=apparatus,
        active_element=active_element,
        passive_element=passive_element,
    )

    relation_element = relation.create_relation_element()

    assert relation.relation_element is not None
    assert relation.relation_element.tag == "relation"
    assert relation.relation_element.attrib["active"] == "A"
    assert relation.relation_element.attrib["passive"] == "P"
