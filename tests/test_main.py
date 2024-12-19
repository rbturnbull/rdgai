import re
from typer.testing import CliRunner
import pandas as pd
from unittest.mock import patch
from rdgai.main import app

from .test_classification import mock_llm
from .conftest import TEST_DATA_DIR

runner = CliRunner()


@patch("llmloader.load", lambda *args, **kwargs: mock_llm)
def test_main_classify(tmp_path):
    output = tmp_path / "output.xml"
    
    result = runner.invoke(app, ["classify", str(TEST_DATA_DIR/"minimal.xml"), str(output), "--verbose"])

    assert result.exit_code == 0

    assert "You are an academic who is an expert in textual criticism in Arabic." in result.stdout

    assert output.exists()
    result = output.read_text()
    
    assert '<relation active="1" passive="2" ana="#category1" resp="#rdgai">' in result
    assert '<desc>justification1</desc>' in result
    assert '<relation active="2" passive="1" ana="#category1" resp="#rdgai">' in result
    assert '<desc>c.f. Reading 2 ➞ Reading 1</desc>' in result


def test_main_classified_pairs(capsys):
    result = runner.invoke(app, ["classified-pairs", str(TEST_DATA_DIR/"arb.xml")])
    assert result.exit_code == 0
    assert "Single_Minor_Word_Change ───────────────────────────\nAn addition, omission" in result.stdout
    assert "Jn8_12-7: الدهر بل تكون له ➞ الدهر بل يكون له\n" in result.stdout


def test_main_html(tmp_path):
    output = tmp_path / "output.html"
    result = runner.invoke(app, ["html", str(TEST_DATA_DIR/"minimal.xml"), str(output)])
    assert result.exit_code == 0
    assert output.exists()
    result = output.read_text()
    assert '<h5 class="card-title large">Reading 1</h5>' in result
    assert '<p class="relation"><span>Reading 1</span> &lrm;➜ <span>Reading 2</span></p>' in result


def test_main_clean(tmp_path):
    output = tmp_path / "clean.xml"
    result = runner.invoke(app, ["clean", str(TEST_DATA_DIR/"messy.xml"), str(output)])
    assert result.exit_code == 0
    assert output.exists()
    result = output.read_text()
    assert '<relation active="1" passive="2" ana="#category1 #category2 #category3"/>' in result
    assert '<relation active="1" passive="3" ana="#category2"/>' in result
    assert '<relation active="2" passive="3" ana="#category3"/>' in result
    assert len(re.findall("<relation ", result)) == 3


def test_main_export(tmp_path):
    output = tmp_path / "output.xlsx"
    result = runner.invoke(app, ["export", str(TEST_DATA_DIR/"minimal_output.xml"), str(output)])
    assert result.exit_code == 0
    assert output.exists()

    variants_df = pd.read_excel(output, sheet_name="Variants", engine="openpyxl")
    assert (variants_df.columns == ['App ID', 'Context', 'Active Reading ID', 'Passive Reading ID', 'Active Reading Text', 'Passive Reading Text', 'Relation Type(s)']).all()
    assert len(variants_df) == 3
    assert variants_df.iloc[1]['Relation Type(s)'] == "category2"

    categories_df = pd.read_excel(output, sheet_name="Categories", engine="openpyxl")
    assert len(categories_df) == 3
    assert categories_df.iloc[1]['Description'] == 'Description 2'


def test_main_import_classifications_xlsx(tmp_path):
    output = tmp_path / "output.xml"
    result = runner.invoke(app, ["import-classifications", str(TEST_DATA_DIR/"minimal_output.xml"), str(TEST_DATA_DIR/"ground_truth.xlsx"), str(output)])
    assert result.exit_code == 0

    assert output.exists()
    output_text = output.read_text()
    assert '<relation active="1" passive="2" ana="#category1" resp="#rdgai"/>' in output_text
    assert '<relation active="1" passive="3" ana="#category3" resp="#ground_truth"/>' in output_text
    assert '<relation active="2" passive="3" ana="#category2" resp="#ground_truth"/>' in output_text


def test_main_import_classifications_csv(tmp_path):
    output = tmp_path / "output.xml"
    result = runner.invoke(app, ["import-classifications", str(TEST_DATA_DIR/"minimal_output.xml"), str(TEST_DATA_DIR/"ground_truth.csv"), str(output)])
    assert result.exit_code == 0

    assert output.exists()
    output_text = output.read_text()
    assert '<relation active="1" passive="2" ana="#category1" resp="#rdgai"/>' in output_text
    assert '<relation active="1" passive="3" ana="#category3" resp="#ground_truth"/>' in output_text
    assert '<relation active="2" passive="3" ana="#category2" resp="#ground_truth"/>' in output_text


def test_main_evaluate(tmp_path):
    report = tmp_path / "report.html"
    confusion_matrix = tmp_path / "confusion_matrix.csv"
    confusion_matrix_plot = tmp_path / "confusion_matrix.svg"

    result = runner.invoke(app, [
        "evaluate", str(TEST_DATA_DIR/"minimal_output.xml"), str(TEST_DATA_DIR/"ground_truth.xml"), 
        "--report", str(report),
        "--confusion-matrix", str(confusion_matrix),
        "--confusion-matrix-plot", str(confusion_matrix_plot),
    ])
    assert result.exit_code == 0

    out = result.stdout
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


@patch("rdgai.apparatus.Doc.flask_app")
def test_main_serve(mock_flask_app, tmp_path):
    output = tmp_path / "output"

    # Mock the run method in the Flask app
    mock_run = mock_flask_app.return_value.run

    # Simulate calling the CLI command
    result = runner.invoke(
        app, 
        ["serve", str(TEST_DATA_DIR/"minimal.xml"), str(output), "--debug", "--all-apps"]
    )
    assert result.exit_code == 0

    # Check if the Flask app's `run` method was called with the correct arguments
    mock_run.assert_called_once_with(debug=True, use_reloader=False)
