import pandas as pd
from rdgai.export import export_variants_to_excel, import_classifications_from_dataframe

from .conftest import TEST_DATA_DIR

def test_export_variants_to_excel(minimal_output, tmp_path):
    output = tmp_path / "output.xlsx"
    export_variants_to_excel(minimal_output, output)
    assert output.exists()

    variants_df = pd.read_excel(output, sheet_name="Variants", engine="openpyxl")
    assert (variants_df.columns == ['App ID', 'Context', 'Active Reading ID', 'Passive Reading ID', 'Active Reading Text', 'Passive Reading Text', 'Relation Type(s)']).all()
    assert len(variants_df) == 3
    assert variants_df.iloc[1]['Relation Type(s)'] == "category2"

    categories_df = pd.read_excel(output, sheet_name="Categories", engine="openpyxl")
    assert len(categories_df) == 3
    assert categories_df.iloc[1]['Description'] == 'Description 2'


def test_import_classifications_from_dataframe(minimal_output, tmp_path):
    output = tmp_path / "output.xml"
    responsible = "#import"
    variants_df = pd.read_excel(TEST_DATA_DIR/"ground_truth.xlsx", sheet_name="Variants", engine="openpyxl")
    import_classifications_from_dataframe(minimal_output, variants_df, output, responsible=responsible)
    assert output.exists()
    output_text = output.read_text()
    assert '<relation active="1" passive="2" ana="#category1" resp="#rdgai"/>' in output_text
    assert '<relation active="1" passive="3" ana="#category3" resp="#import"/>' in output_text
    assert '<relation active="2" passive="3" ana="#category2" resp="#import"/>' in output_text



