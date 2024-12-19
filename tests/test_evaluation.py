from rdgai.evaluation import evaluate_docs


def test_evaluate_docs_no_rdgai(no_interpgrp, capsys):
    evaluate_docs(no_interpgrp, no_interpgrp)
    assert "No rdgai relations found in predicted document" in capsys.readouterr().out


def test_evaluate_docs_minimal(minimal_output, ground_truth, capsys):
    evaluate_docs(minimal_output, ground_truth)
    out = capsys.readouterr().out
    assert "No rdgai relations found in predicted document" not in out
    assert "recall 33" in out
    assert "f1 33" in out
    assert "accuracy 50" in out


def test_evaluate_docs_report(minimal_output, ground_truth, capsys, tmp_path):
    report = tmp_path / "report.html"
    confusion_matrix = tmp_path / "confusion_matrix.csv"
    confusion_matrix_plot = tmp_path / "confusion_matrix.svg"

    evaluate_docs(minimal_output, ground_truth, report=report, confusion_matrix=confusion_matrix, confusion_matrix_plot=confusion_matrix_plot)
    out = capsys.readouterr().out
    assert "No rdgai relations found in predicted document" not in out
    assert "recall 33" in out
    assert "f1 33" in out
    assert "accuracy 50" in out

    assert report.exists()
    report_text = report.read_text()
    assert '"y":["category1","category2","category3"]' in report_text
    assert '<p class="card-text small">Ground Truth</p>' in report_text

    assert confusion_matrix.exists()
    confusion_matrix_text = confusion_matrix.read_text()
    assert ',category1,category2,category3' in confusion_matrix_text
    assert 'category1,1,0,0' in confusion_matrix_text
    assert 'category2,0,0,0' in confusion_matrix_text
    assert 'category3,0,1,0' in confusion_matrix_text

    assert confusion_matrix_plot.exists()

