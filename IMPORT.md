Importing the All India Drug Bank dataset

1) Obtain the CSV
- If you have the CSV file already, place it at: `backend/all_india_drug_bank.csv`.
- To download from Kaggle (example):
  - Install kaggle CLI: `pip install kaggle`
  - Place your `kaggle.json` in `%USERPROFILE%\.kaggle\kaggle.json` (Windows)
  - Download: `kaggle datasets download owner/dataset-name -f filename.csv -p backend --unzip`

2) Run the importer (from project root):

```powershell
python backend/import_all_india.py backend/all_india_drug_bank.csv
```

3) Outcome
- The importer inserts rows into `drugs`, `drug_aliases`, `drug_side_effects`, and `drug_uses`.
- A file `backend/brand_to_generic.json` will be written with brand→generic mappings.
- The importer prints an import summary (Imported / Skipped).

4) Notes
- The importer uses the existing `normalize_name()` (OpenFDA/RxNorm best-effort). If you want stricter mapping, run post-processing to review `brand_to_generic.json`.
- Ensure the dataset license permits your intended use; the importer records `source='all_india_drug_bank'` for provenance.
