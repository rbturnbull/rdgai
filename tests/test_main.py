from typer.testing import CliRunner
from rdgai.main import app
from unittest.mock import patch

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
