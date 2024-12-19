from rdgai.classification import classify
from langchain_core.runnables import RunnableLambda

mock_llm = RunnableLambda(lambda *x, **kwargs: "1 → 2 = category1: justification1")


def test_classify_minimal_prompt_only(minimal, capsys, tmp_path):
    classify(
        minimal, 
        tmp_path,
        llm=mock_llm,
        prompt_only=True,
    )
    captured = capsys.readouterr()
    response = captured.out
    assert "You are an academic who is an expert in textual criticism in Arabic." in response
    assert "Here are 3 possible categories for the types of changes in the text:" in response
    assert "1: Reading 1"
    assert "2: Reading 2"
    assert "3: Reading 3"
    assert "1 → 2" in response
    assert "1 → 3" in response
    assert "When you are finished, output 5 hyphens: '-----'." in response
    assert "Certainly, classifications for combinations of the readings are:" in response


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
    assert '<desc>c.f. Reading 2 ➞ Reading 1</desc>' in result
    
    