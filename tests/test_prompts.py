from rdgai.prompts import build_template


def test_build_template(minimal):
    template = build_template(app=minimal.apps[0])
    assert len(template.messages) == 3
    response = template.invoke({}).to_string()
    assert "System: You are an academic who is an expert in textual criticism in Arabic." in response
    assert "Here are 3 possible categories for the types of changes in the text:" in response
    assert "1: Reading 1"
    assert "2: Reading 2"
    assert "3: Reading 3"
    assert "1 → 2" in response
    assert "1 → 3" in response
    assert "When you are finished, output 5 hyphens: '-----'." in response
    assert "AI: Certainly, classifications for combinations of the readings are:" in response
