from rdgai.prompts import build_template, select_spaced_elements


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


def test_select_spaced_elements_basic():
    assert select_spaced_elements([1, 2, 3, 4, 5], 3) == [1, 3, 5]
    assert select_spaced_elements([1, 2, 3, 4, 5], 2) == [1, 5]
    assert select_spaced_elements([1, 2, 3, 4, 5], 1) == [1]

def test_select_spaced_elements_k_greater_than_n():
    assert select_spaced_elements([1, 2, 3], 5) == [1, 2, 3]

def test_select_spaced_elements_k_equals_n():
    assert select_spaced_elements([1, 2, 3], 3) == [1, 2, 3]

def test_select_spaced_elements_large_list():
    lst = list(range(1, 101))
    assert select_spaced_elements(lst, 5) == [1, 25, 50, 75, 100]

def test_select_spaced_elements_single_element():
    assert select_spaced_elements([42], 1) == [42]
    assert select_spaced_elements([42], 2) == [42]

def test_select_spaced_elements_edge_case():
    assert select_spaced_elements([], 3) == []
