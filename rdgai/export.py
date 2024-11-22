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

    headers = ['App ID', 'Context', 'Active Reading ID', 'Passive Reading ID',
               'Active Reading Text', 'Passive Reading Text', 'Relation Type(s)']

    for col_num, header in enumerate(headers, start=1):  # Start from column A (1)
        cell = ws.cell(row=1, column=col_num)
        cell.value = header
        cell.font = header_font

    current_row = 2
    for app in doc.apps:
        for pair in app.non_redundant_pairs:
            ws[f'A{current_row}'] = str(app)
            ws[f'B{current_row}'] = app.text_in_context()
            ws[f'C{current_row}'] = pair.active.n
            ws[f'D{current_row}'] = pair.passive.n
            ws[f'E{current_row}'] = pair.active.text
            ws[f'F{current_row}'] = pair.passive.text
            
            for relation_type_index, relation_type in enumerate(pair.types):
                column = ord('G') + relation_type_index
                ws[f'{chr(column)}{current_row}'] = str(relation_type)
            
            current_row += 1

    data_val = DataValidation(type="list",formula1=f'"{",".join(relation_category_dict.keys())}"')
    ws.add_data_validation(data_val)

    max_relation_types = max(10, max(len(pair.types) for app in doc.apps for pair in app.pairs))
    end_column = chr(ord('G') + max_relation_types - 1)

    data_val.add(f"G2:{end_column}{current_row}")

    # Create new sheet with descriptions of categories and counts
    categories_worksheet = wb.create_sheet('Categories')

    # Add a header to the "Category" column
    headers = ['Category', 'Inverse', 'Count', 'Inverse Count', 'Total', 'Description']
    for col_num, header in enumerate(headers, start=1): 
        cell = categories_worksheet.cell(row=1, column=col_num)
        cell.value = header
        cell.font = header_font

    # Populate the categories from relation_category_dict.keys()
    for idx, category in enumerate(relation_category_dict.values(), start=2):  # Start from row 2
        category_name = str(category)
        inverse_name = str(category.inverse) if category.inverse else category_name
        categories_worksheet[f'A{idx}'] = category_name
        categories_worksheet[f'B{idx}'] = inverse_name
        categories_worksheet[f'C{idx}'] = f'=COUNTIF(Variants!G:{end_column}, "{category_name}")'
        categories_worksheet[f'D{idx}'] = f'=COUNTIF(Variants!G:{end_column}, "{inverse_name}")'
        categories_worksheet[f'E{idx}'] = f'=SUM(C{idx}:D{idx})'
        categories_worksheet[f'F{idx}'] = category.description

    wb.save(output)

