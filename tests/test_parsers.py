from rdgai.parsers import read_output, Result

def test_basic_functionality():
    llm_output = "r1 → r2 = cat1: justification1"
    expected = [Result("r1", "r2", "cat1", "justification1")]
    assert read_output(llm_output) == expected

def test_no_arrow():
    llm_output = "r1 r2 = cat1: justification1"
    expected = []
    assert read_output(llm_output) == expected

def test_no_equals():
    llm_output = "r1 → r2 cat1: justification1"
    expected = []
    assert read_output(llm_output) == expected

def test_no_colon_in_category():
    llm_output = "r1 → r2 = cat1"
    expected = [Result("r1", "r2", "cat1", "")]
    assert read_output(llm_output) == expected

def test_empty_lines():
    llm_output = "\n\nr1 → r2 = cat1: justification1\n\n"
    expected = [Result("r1", "r2", "cat1", "justification1")]
    assert read_output(llm_output) == expected
