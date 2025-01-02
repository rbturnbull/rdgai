import re
import pytest
from rdgai.apparatus import Pair, RelationType
from lxml.etree import _Element as Element


def test_doc_print_classified_pairs(arb, capsys):
    arb.print_classified_pairs()
    captured = capsys.readouterr()
    assert "Single_Minor_Word_Change ───────────────────────────\nAn addition, omission" in captured.out
    assert "Jn8_12-7: الدهر بل تكون له ➞ الدهر بل يكون له\n" in captured.out


def test_doc_get_classified_pairs_arb(arb):
    result = arb.get_classified_pairs()
    assert len(result) == 836
    for pair in result:
        assert isinstance(pair, Pair)
        assert len(pair.types) > 0


def test_relation_type_representative_examples(arb):
    k = 5
    relation_type = arb.relation_types['Orthography']
    examples = relation_type.representative_examples(k)
    assert len(examples) == k
    assert str(examples[0]) == "اللاه ➞ الاله"

    k = 200
    relation_type = arb.relation_types['Single_Major_Word_Change']
    examples = relation_type.representative_examples(k)
    assert len(examples) == 125


def test_doc_get_classified_pairs_minimal(minimal):
    result = minimal.get_classified_pairs()
    assert len(result) == 0


def test_doc_get_classified_pairs_minimal(no_interpgrp):
    assert len(no_interpgrp.relation_types) == 3
    result = no_interpgrp.get_classified_pairs()
    assert len(result) == 3


def test_doc_get_unclassified_pairs_arb(arb):
    result = arb.get_unclassified_pairs()
    assert len(result) == 498
    for pair in result:
        assert isinstance(pair, Pair)
        assert len(pair.types) == 0


def test_doc_get_unclassified_pairs_minimal(minimal):
    result = minimal.get_unclassified_pairs()
    assert len(result) == 6
    for pair in result:
        assert isinstance(pair, Pair)
        assert len(pair.types) == 0


def test_doc_str(minimal):
    assert "minimal.xml" in str(minimal)


def test_doc_repr(minimal):
    assert "minimal.xml" in repr(minimal)    


def test_reading_witnesses_str(arb):
    assert arb.apps[0].readings[0].witnesses_str() == 'CSA S71+'
    assert arb.apps[0].readings[1].witnesses_str() == 'J30 S118 S120 S122 S128 S137 S138'


def test_relationtype_repr(arb):
    for relation_type in arb.relation_types.values():
        assert str(relation_type) == repr(relation_type)


def test_relationtype_str(arb):
    for relation_type in arb.relation_types.values():
        assert str(relation_type) == relation_type.name


def test_relationtype_eq(arb):
    for relation_type in arb.relation_types.values():
        assert relation_type != ""
        assert relation_type == relation_type
        assert relation_type == RelationType(name=relation_type.name, element=relation_type.element, description=relation_type.description)


def test_pair_str(minimal):
    assert str(minimal.apps[0].pairs[0]) == 'Reading 1 ➞ Reading 2'


def test_pair_repr(minimal):
    assert repr(minimal.apps[0].pairs[0]) == 'Reading 1 ➞ Reading 2'
    

def test_pair_app_element(minimal):
    element = minimal.apps[0].pairs[0].app_element()
    assert isinstance(element, Element)
    assert element.tag.endswith("app")


def test_pair_element_for_type(arb):
    pair = arb.apps[0].pairs[0]

    relation_type = list(pair.types)[0]

    element = pair.element_for_type(relation_type)
    assert element.tag.endswith("relation")
    assert element.attrib == {'active': '1', 'passive': '2', 'ana': '#Multiple_Word_Changes'}

    assert pair.element_for_type(arb.relation_types['Orthography']) == None


def test_pair_element_for_type_list_relation_none(minimal):
    pair = minimal.apps[0].pairs[0]
    assert pair.element_for_type(None) == None


def test_pair_add_type(minimal):
    pair = minimal.apps[0].pairs[0]
    relation_type = list(minimal.relation_types.values())[0]
    assert len(relation_type.pairs) == 0
    relation_element = pair.add_type(relation_type)
    assert len(relation_type.pairs) == 1
    assert isinstance(relation_element, Element)
    assert relation_element.tag.endswith("relation")
    assert relation_element.attrib == {'active': '1', 'passive': '2', 'ana': '#category1'}
    assert relation_element.getparent().tag.endswith("listRelation")
    assert minimal.apps[0].element == relation_element.getparent().getparent()

    assert relation_type in pair.types

    assert relation_element == pair.element_for_type(relation_type)

    assert relation_element == pair.add_type(relation_type)
    assert relation_element.attrib == {'active': '1', 'passive': '2', 'ana': '#category1'}

    # Add second type
    relation_type2 = list(minimal.relation_types.values())[1]
    relation_element2 = pair.add_type(relation_type2)
    assert relation_element2 == relation_element
    assert relation_element.attrib == {'active': '1', 'passive': '2', 'ana': '#category1 #category2'}
    

def test_pair_remove_type(minimal):
    pair = minimal.apps[0].pairs[0]
    relation_type = list(minimal.relation_types.values())[0]
    relation_element = pair.add_type(relation_type)
    assert relation_type in pair.types
    assert relation_element == pair.element_for_type(relation_type)
    assert len(relation_type.pairs) == 1
    pair.remove_type(relation_type)
    assert relation_type not in pair.types
    assert len(relation_type.pairs) == 0
    assert pair.element_for_type(relation_type) == None


def test_pair_remove_type2(minimal):
    pair = minimal.apps[0].pairs[0]
    relation_type = list(minimal.relation_types.values())[0]
    relation_type2 = list(minimal.relation_types.values())[1]

    relation_element = pair.add_type(relation_type)
    relation_element2 = pair.add_type(relation_type2)

    assert relation_type in pair.types
    assert relation_type2 in pair.types
    assert relation_element == pair.element_for_type(relation_type)
    assert relation_element2 == pair.element_for_type(relation_type2)

    assert len(relation_type.pairs) == 1
    assert len(relation_type2.pairs) == 1

    pair.remove_type(relation_type)
    assert relation_type not in pair.types
    assert relation_type2 in pair.types

    assert relation_element.attrib == {'active': '1', 'passive': '2', 'ana': '#category2'}


def test_app_hash(arb):
    assert hash(arb.apps[0]) == hash(arb.apps[0])
    assert hash(arb.apps[0]) != hash(arb.apps[1])
    assert hash(arb.apps[0]) == hash(arb.apps[0].element)


def test_app_str(arb):
    assert str(arb.apps[0]) == "Jn8_12-1"


def test_app_str_fallbacks(app_names):
    assert str(app_names.apps[2]) == "ab-3"
    assert str(app_names.apps[0]) == "app"
    assert str(app_names.apps[1]) == "app2"
    assert str(app_names.apps[3]) == "NoAB"
    

def test_app_text_in_context(app_names):
    assert app_names.apps[0].text_in_context() == 'Word1 ⸂Reading 1⸃ Word2 Reading 1 Word3 Word4'
    assert app_names.apps[1].text_in_context() == 'Word1 Reading 1 Word2 ⸂Reading 1⸃ Word3 Word4'
    assert app_names.apps[2].text_in_context() == 'Word1 Reading 1 Word2 Reading 1 Word3 ⸆ Word4'
    assert app_names.apps[3].text_in_context() == '⸂Reading 1⸃'


def test_doc_add_relation_type_existing(minimal):
    relation_type = list(minimal.relation_types.values())[0]
    assert len(relation_type.pairs) == 0
    assert len(minimal.relation_types) == 3
    result = minimal.add_relation_type(relation_type.name, relation_type.description)
    assert result == relation_type
    assert len(minimal.relation_types) == 3


def test_doc_write(minimal, tmp_path):
    file = tmp_path / "minimal.xml"
    minimal.write(file)
    assert file.exists()
    text = file.read_text()
    assert '<interp xml:id="category1">Description 1</interp>' in text
    assert '<app xml:id="app">' in text
    assert '<rdg n="1">Reading 1</rdg>' in text


def test_doc_inverse(inverses):
    assert inverses.relation_types['Addition'].inverse == inverses.relation_types['Omission']
    assert inverses.relation_types['Omission'].inverse == inverses.relation_types['Addition']
    assert inverses.relation_types['Major_Addition'].inverse == inverses.relation_types['Major_Omission']
    assert inverses.relation_types['Major_Omission'].inverse == inverses.relation_types['Major_Addition']
    assert inverses.relation_types['Substitution'].inverse == None


def test_doc_render_html(minimal, tmp_path):
    file = tmp_path / "minimal.html"
    result = minimal.render_html(output=file)
    assert file.exists()
    assert result == file.read_text()
    assert '<h5 class="card-title large">Reading 1</h5>' in result
    assert '<p class="relation"><span>Reading 1</span> &lrm;➜ <span>Reading 2</span></p>' in result


@pytest.fixture
def minimal_flask_test_client(minimal, tmp_path):
    output = tmp_path / "minimal.xml"
    flask_app = minimal.flask_app(output)
    client = flask_app.test_client()
    client.output = output
    return client


def test_doc_flask_app(minimal_flask_test_client):
    response = minimal_flask_test_client.get("/")
    assert response.status_code == 200
    assert '<h5 class="card-title large">Reading 1</h5>' in response.data.decode()
    assert '<p class="relation"><span>Reading 1</span> &lrm;➜ <span>Reading 2</span></p>' in response.data.decode()
    

def test_doc_flask_app_add_remove(minimal_flask_test_client):
    data = {
        "relation_type": "category1",
        "pair": "Reading 1 ➞ Reading 2",
        "operation": "add"
    }
    response = minimal_flask_test_client.get("/")

    response = minimal_flask_test_client.post("/api/relation-type", json=data)
    assert response.status_code == 200
    assert response.data == b"Success"
    output_xml = minimal_flask_test_client.output.read_text()
    assert '<relation active="1" passive="2" ana="#category1"/>' in output_xml

    data['operation'] = 'remove'
    response = minimal_flask_test_client.post("/api/relation-type", json=data)
    assert response.status_code == 200
    assert response.data == b"Success"
    output_xml = minimal_flask_test_client.output.read_text()
    assert '<relation active="1" passive="2" ana="#category1"/>' not in output_xml


def test_doc_flask_app_add_remove_description(minimal_flask_test_client):
    data = {
        "description": "Justification",
        "pair": "Reading 1 ➞ Reading 2",
        "operation": "add"
    }
    response = minimal_flask_test_client.get("/")

    response = minimal_flask_test_client.post("/api/desc", json=data)
    assert response.status_code == 200
    assert response.data == b"Success"
    output_xml = minimal_flask_test_client.output.read_text()
    assert '<relation active="1" passive="2">' in output_xml
    assert '<desc>Justification</desc>' in output_xml

    data['operation'] = 'remove'
    response = minimal_flask_test_client.post("/api/desc", json=data)
    assert response.status_code == 200
    assert response.data == b"Success"
    output_xml = minimal_flask_test_client.output.read_text()
    assert '<desc>Justification</desc>' not in output_xml


def test_doc_clean(messy, tmp_path):
    clean_path = tmp_path/"clean.xml"
    messy.clean(clean_path)
    assert clean_path.exists()
    result = clean_path.read_text()
    assert '<relation active="1" passive="2" ana="#category1 #category2 #category3"/>' in result
    assert '<relation active="1" passive="3" ana="#category2"/>' in result
    assert '<relation active="2" passive="3" ana="#category3"/>' in result
    assert len(re.findall("<relation ", result)) == 3



