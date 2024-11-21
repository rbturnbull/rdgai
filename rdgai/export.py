from pathlib import Path
from openpyxl import Workbook
from openpyxl.worksheet.datavalidation import DataValidation
from openpyxl.styles import Font

from .apparatus import read_doc, RelationType, Pair, App, Doc
from .relations import get_relation_categories, get_relation_categories_dict, get_classified_relations, get_apparatus_unclassified_relations, make_readings_list


def export_variants_to_excel(doc:Doc, output:Path):
    """ Export the variants to an Excel file."""
    wb = Workbook()
    header_font = Font(bold=True)

    relation_category_dict = get_relation_categories_dict(doc.tree)

    # Rename the default sheet
    ws = wb.active
    ws.title = 'Variants'

    headers = ['App ID', 'Active Reading ID', 'Passive Reading ID',
               'Active Reading Text', 'Passive Reading Text', 'Relation Type(s)']

    for col_num, header in enumerate(headers, start=1):  # Start from column A (1)
        cell = ws.cell(row=1, column=col_num)
        cell.value = header
        cell.font = header_font

    current_row = 2
    for app in doc.apps:
        for pair in app.pairs:
            ws[f'A{current_row}'] = str(app)
            ws[f'B{current_row}'] = pair.active.n
            ws[f'C{current_row}'] = pair.passive.n
            ws[f'D{current_row}'] = pair.active.text
            ws[f'E{current_row}'] = pair.passive.text
            
            for relation_type_index, relation_type in enumerate(pair.types):
                column = ord('F') + relation_type_index
                ws[f'{chr(column)}{current_row}'] = str(relation_type)
            
            current_row += 1

    data_val = DataValidation(type="list",formula1=f'"{",".join(relation_category_dict.keys())}"')
    ws.add_data_validation(data_val)

    max_relation_types = max(5, max(len(pair.types) for app in doc.apps for pair in app.pairs))
    end_column = chr(ord('F') + max_relation_types - 1)

    data_val.add(f"F2:{end_column}{current_row}")

    # Create new sheet with descriptions of categories and counts
    categories_worksheet = wb.create_sheet('Categories')

    # Add a header to the "Category" column
    categories_worksheet['A1'] = 'Category'
    categories_worksheet['B1'] = 'Description'
    categories_worksheet['A1'].font = header_font
    categories_worksheet['B1'].font = header_font

    # Populate the categories from relation_category_dict.keys()
    for idx, category in enumerate(relation_category_dict.values(), start=2):  # Start from row 2
        categories_worksheet[f'A{idx}'] = str(category)
        categories_worksheet[f'B{idx}'] = category.description

    wb.save(output)

