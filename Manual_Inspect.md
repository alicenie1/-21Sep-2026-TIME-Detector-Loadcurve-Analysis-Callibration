# Detector Analysis Website

This repository now includes a full-stack website for:

1. Viewing detector-analysis PDFs by column.
2. Downloading full PDFs.
3. Uploading `.npz` / `.npy` files and running `run_analysis(np_file_path, output_dir, options=None)`.
4. Viewing generated analysis outputs in the same UI as existing PDFs.

## Project layout

```text
backend/
  main.py
  storage.py
  pdf_utils.py
  analysis_adapter.py
  requirements.txt
  server_storage/
    uploads/
    pdfs/
    generated/
    previews/
    metadata/
    file_index.json
frontend/
  package.json
  vite.config.js
  src/
    App.jsx
    api.js
    components/
detector_analysis.py
```

## Backend setup (FastAPI)

1. Create/activate a virtual environment.
2. Install dependencies:

```bash
pip install -r backend/requirements.txt
```

3. Place your initial PDFs (the six existing reports) in:

```text
backend/server_storage/pdfs/
```

4. Run backend from repository root:

```bash
uvicorn backend.main:app --reload
```

Backend runs at `http://127.0.0.1:8000`.

## Frontend setup (React + Vite)

1. Install frontend dependencies:

```bash
cd frontend
npm install
```

2. Start frontend:

```bash
npm run dev
```

Frontend runs at `http://127.0.0.1:5173` and proxies `/api` to the FastAPI backend.

## API endpoints

- `GET /api/files`
- `GET /api/files/{file_id}`
- `GET /api/files/{file_id}/columns/{column}` (`column` must be 0..31)
- `GET /api/files/{file_id}/pages/{page_number}/image`
- `GET /api/files/{file_id}/download`
- `POST /api/run-analysis` (`multipart/form-data`, field name: `upload`)

## Storage behavior

- All files are server-side only. No frontend local filesystem paths are used.
- Uploads are saved in `backend/server_storage/uploads/`.
- Generated analysis output is saved in `backend/server_storage/generated/`.
- Page previews are rendered/cached in `backend/server_storage/previews/`.
- Per-PDF column metadata JSON is saved in `backend/server_storage/metadata/`.
- Persistent file registry is saved in `backend/server_storage/file_index.json`.

## Notes

- Existing PDFs are scanned from `backend/server_storage/pdfs/` on backend startup.
- Generated analysis PDFs are registered as `generated_analysis`.
- Existing scanned PDFs are registered as `existing_pdf`.
