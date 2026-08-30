from openpyxl import load_workbook

wb = load_workbook(r'D:\Python Automation\egram_bot\data\input.xlsx')

print("=== Activities Sheet ===")
acts = wb['Activities']
print("Headers:", [cell.value for cell in next(acts.iter_rows(min_row=1, max_row=1, min_col=1, max_col=4))])
print("\nFirst 5 data rows (Activity_Key, Activity Name, Activity Output):")
for row in acts.iter_rows(min_row=2, max_row=6, min_col=1, max_col=4, values_only=True):
    print(row)

print("\n=== Asset Sheet ===")
asset = wb['Asset']
print("Headers:", [cell.value for cell in next(asset.iter_rows(min_row=1, max_row=1))])
print("\nAll data rows in Asset sheet:")
for i, row in enumerate(asset.iter_rows(min_row=2, max_row=asset.max_row, values_only=True)):
    print(f"Row {i+2}: {row}")
    if i > 5:  # Just show first few rows
        print("...")
        break
