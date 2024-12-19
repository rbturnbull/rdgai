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

