import pandas as pd

df = pd.read_excel('data/input.xlsx')
print('Excel rows:', len(df))
print('\n=== FIRST ROW DATA ===')
row0 = df.iloc[0]
for col in ['theme', 'activity_name', 'focus_area', 'activity_type', 'pdi_indicator', 'flagship_scheme', 'select_supported_department', 'activity_output_type', 'training_category', 'training_organized_by', 'training_subject', 'training_total_trainees', 'training_total_duration_days']:
    if col in row0.index:
        val = row0[col]
        print(f'{col}: {repr(val)}')
