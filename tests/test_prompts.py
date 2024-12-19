from rdgai.prompts import build_template, select_spaced_elements, build_template_pair


# def test_build_template(minimal):
#     template = build_template(app=minimal.apps[0])
#     assert len(template.messages) == 3
#     response = template.invoke({}).to_string()
#     assert "System: You are an academic who is an expert in textual criticism in Arabic." in response
#     assert "Here are 3 possible categories for the types of changes in the text:" in response
#     assert "1: Reading 1"
#     assert "2: Reading 2"
#     assert "3: Reading 3"
#     assert "1 → 2" in response
#     assert "1 → 3" in response
#     assert "When you are finished, output 5 hyphens: '-----'." in response
#     assert "AI: Certainly, classifications for combinations of the readings are:" in response


def test_build_template_pair_minimal(minimal):
    pair = minimal.apps[0].pairs[0]
    template = build_template_pair(pair=pair)
    assert len(template.messages) == 3
    response = template.invoke({}).to_string()
    assert "System: You are an academic who is an expert in textual criticism in Arabic." in response
    assert "Human: I am analyzing textual variants in a document written in Arabic." in response
    assert "category1: Description 1" in response
    assert "Respond with one of these categories: category1, category2, category3" in response
    assert "AI: Certainly, the category for changing from ⸂Reading 1⸃ to ⸂Reading 2⸃ is: " in response


def test_build_template_pair_arb(arb):
    pair = arb.apps[0].pairs[0]
    template = build_template_pair(pair=pair)
    assert len(template.messages) == 3
    response = template.invoke({}).to_string()
    breakpoint()
    assert "System: You are an academic who is an expert in textual criticism in Arabic." in response
    assert "Human: I am analyzing textual variants in a document written in Arabic." in response
    assert "Orthography: Changes in spelling, diacritic" in response
    assert "Multiple_Word_Changes: Changes across more than one word." in response
    assert "e.g. شي → شيا" in response
    assert "The variation unit you need to classify is marked as ⸆ in this text:" in response
    assert "What category would best describe a change from ⸂OMIT⸃ to ⸂قال الرب  للذين اتوا اليه من اليهود⸃?" in response

    assert "Respond with one of these categories: Orthography, Single_Minor_Word_Change, Single_Major_Word_Change, Multiple_Word_Changes, Transposition" in response
    assert "AI: Certainly, the category for changing from ⸂OMIT⸃ to ⸂قال الرب  للذين اتوا اليه من اليهود⸃ is: " in response


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
