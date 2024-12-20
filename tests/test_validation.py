from langchain_core.runnables import RunnableLambda
from rdgai.validation import validate

mock_llm = RunnableLambda(lambda *x, **kwargs: "Multiple_Word_Changes\njustification1")


def test_validate(arb, tmp_path, capsys):
    output = tmp_path / "output.xml"
    report = tmp_path / "report.html"
    confusion_matrix = tmp_path / "confusion_matrix.csv"
    confusion_matrix_plot = tmp_path / "confusion_matrix.svg"

    validate(
        arb,
        output,
        report=report,
        confusion_matrix=confusion_matrix,
        confusion_matrix_plot=confusion_matrix_plot,
        proportion=0.05,
        llm=mock_llm,
    )

    assert output.exists()
    output_text = output.read_text()
    assert '<relation active="2" passive="4" ana="#Multiple_Word_Changes" resp="#rdgai">' in output_text
    assert '<desc>justification1</desc>' in output_text
    assert '<desc>c.f. الدهر بل تكون له ➞ الدهر بل تكون معه</desc>' in output_text


    out = capsys.readouterr().out

    assert "No rdgai relations found in predicted document" not in out
    assert "recall 25" in out
    assert "f1 12" in out
    assert "accuracy 33" in out

    assert report.exists()
    report_text = report.read_text()
    assert "Incorrect (10)" in report_text
    assert "Correct (5)" in report_text

    assert confusion_matrix.exists()
    confusion_matrix_text = confusion_matrix.read_text()
    assert ',Orthography,Single_Minor_Word_Change,Single_Major_Word_Change,Multiple_Word_Changes,Transposition' in confusion_matrix_text
    assert 'Orthography,0,0,0,1,0' in confusion_matrix_text
    assert 'Single_Minor_Word_Change,0,0,0,6,0' in confusion_matrix_text
    assert 'Single_Major_Word_Change,0,0,0,3,0' in confusion_matrix_text
    assert 'Multiple_Word_Changes,0,0,0,5,0' in confusion_matrix_text

    assert confusion_matrix_plot.exists()



    