from rdgai.apparatus import Pair, RelationType
from lxml.etree import _Element as Element


def test_doc_print_classified_pairs(arb, capsys):
    arb.print_classified_pairs()
    captured = capsys.readouterr()
    assert "Single_Minor_Word_Change ───────────────────────────\nAn addition, omission" in captured.out
    assert "Jn8_12-7: الدهر بل تكون له ➞ الدهر بل يكون له\n" in captured.out


def test_doc_get_classified_pairs_arb(arb):
    result = arb.get_classified_pairs()
    assert len(result) == 628
    for pair in result:
        assert isinstance(pair, Pair)
        assert len(pair.types) > 0


def test_doc_get_classified_pairs_minimal_doc(minimal_doc):
    result = minimal_doc.get_classified_pairs()
    assert len(result) == 0


def test_doc_get_unclassified_pairs_arb(arb):
    result = arb.get_unclassified_pairs()
    assert len(result) == 706
    for pair in result:
        assert isinstance(pair, Pair)
        assert len(pair.types) == 0


def test_doc_get_unclassified_pairs_minimal_doc(minimal_doc):
    result = minimal_doc.get_unclassified_pairs()
    assert len(result) == 6
    for pair in result:
        assert isinstance(pair, Pair)
        assert len(pair.types) == 0


def test_doc_str(minimal_doc):
    assert "minimal.xml" in str(minimal_doc)


def test_doc_repr(minimal_doc):
    assert "minimal.xml" in repr(minimal_doc)    


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


def test_pair_str(minimal_doc):
    assert str(minimal_doc.apps[0].pairs[0]) == 'Reading 1 ➞ Reading 2'


def test_pair_repr(minimal_doc):
    assert repr(minimal_doc.apps[0].pairs[0]) == 'Reading 1 ➞ Reading 2'
    

def test_pair_app_element(minimal_doc):
    element = minimal_doc.apps[0].pairs[0].app_element()
    assert isinstance(element, Element)
    assert element.tag.endswith("app")


def test_pair_element_for_type(arb):
    pair = arb.apps[0].pairs[0]

    relation_type = list(pair.types)[0]

    element = pair.element_for_type(relation_type)
    assert element.tag.endswith("relation")
    assert element.attrib == {'active': '1', 'passive': '2', 'ana': '#Multiple_Word_Changes'}

    assert pair.element_for_type(arb.relation_types['Orthography']) == None
