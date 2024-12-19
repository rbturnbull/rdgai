from rdgai.parsers import parse_category_and_justification

def test_parse_category_and_justification():
    assert parse_category_and_justification("category1\njustification1") == ("category1", "justification1")
    assert parse_category_and_justification("category1") == ("category1", "")
