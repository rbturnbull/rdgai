from rdgai.classification import classify
from langchain_core.runnables import RunnableLambda

mock_llm = RunnableLambda(lambda *x, **kwargs: "category1\njustification1")
mock_llm_dodgy = RunnableLambda(lambda *x, **kwargs: "category_missing\njustification1")


def test_classify_minimal_prompt_only(minimal, capsys, tmp_path):
    classify(
        minimal, 
        tmp_path,
        llm=mock_llm,
        prompt_only=True,
    )
    captured = capsys.readouterr()
    response = captured.out
    assert "There are 3 possible categories for the types of changes in the text that a scribe could make to change one variant reading to another" in response
    assert "The variation unit you need to classify is marked as ⸂Reading 3⸃ in this text:"
    assert "When you are finished, output 5 hyphens: '-----'." in response
    assert "Certainly, the category for changing from ⸂Reading 2⸃ to ⸂Reading 3⸃ is:" in response


def test_classify_minimal_verbose(minimal, capsys, tmp_path):
    output = tmp_path / "output.xml"
    assert not output.exists()

    classify(
        minimal, 
        output,
        llm=mock_llm,
        verbose=True,
    )
    captured = capsys.readouterr()
    response = captured.out
    assert "You are an academic who is an expert in textual criticism in Arabic." in response

    assert output.exists()
    result = output.read_text()
    
    assert '<relation active="1" passive="2" ana="#category1" resp="#rdgai">' in result
    assert '<desc>justification1</desc>' in result
    assert '<relation active="2" passive="1" ana="#category1" resp="#rdgai">' in result
    assert '<desc>c.f. Reading 1 ➞ Reading 2</desc>' in result
    
    
def test_classify_minimal_missing_category(minimal, capsys, tmp_path):
    output = tmp_path / "output.xml"
    assert not output.exists()

    classify(
        minimal, 
        output,
        llm=mock_llm_dodgy,
        verbose=True,
    )
    captured = capsys.readouterr()
    response = captured.out
    assert "You are an academic who is an expert in textual criticism in Arabic." in response

    assert output.exists()
    result = output.read_text()
    
    assert '<relation active="1" passive="2" ana="#category1" resp="#rdgai">' not in result
    assert '<desc>justification1</desc>' not in result
    assert '<relation active="2" passive="1" ana="#category1" resp="#rdgai">' not in result
    assert '<desc>c.f. Reading 1 ➞ Reading 2</desc>' not in result
    
        