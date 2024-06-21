from rdgai.relations import RelationCategory
from rdgai.prompts import build_template, Reading


def test_build_template():
    relation_categories = [
        RelationCategory(name="category1", element=None, description="Description 1"),
        RelationCategory(name="category2", element=None, description="Description 2"),
        RelationCategory(name="category3", element=None),
    ]

    readings = [
        Reading(id="1", text="Reading 1"),
        Reading(id="2", text="Reading 2"),
        Reading(id="3", text="Reading 3"),
    ]
    
    template = build_template(relation_categories=relation_categories, readings=readings, language="Greek")
    assert len(template.messages) == 3
    response = template.invoke({}).to_string()
    assert "System: You are an academic who is an expert in textual criticism in Greek." in response
    assert "Here are the types of 3 categories for the types of changes in the text:" in response
    assert "1: Reading 1"
    assert "2: Reading 2"
    assert "3: Reading 3"
    assert "1 → 2" in response
    assert "2 → 1" in response
    assert "3 → 1" in response
    assert "3 → 2" in response
    assert "When you are finished, output 5 hyphens: '-----'." in response
    assert "AI: Certainly, classifications for combinations of the readings are:" in response
