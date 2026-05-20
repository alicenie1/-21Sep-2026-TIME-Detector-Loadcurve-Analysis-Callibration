from pathlib import Path
import shutil
from uuid import uuid4

from fastapi import FastAPI, File, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse

from backend.analysis_adapter import run_uploaded_analysis
from backend.pdf_utils import build_pdf_metadata, ensure_page_preview, load_pdf_metadata
from backend.storage import StorageManager, sanitize_filename


app = FastAPI(title="Detector Analysis Viewer")
storage = StorageManager()

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)


def ensure_metadata(record: dict) -> dict:
    metadata_path = Path(record["metadata_path"]).resolve()
    pdf_path = Path(record["pdf_path"]).resolve()

    if metadata_path.exists():
        try:
            return load_pdf_metadata(metadata_path)
        except Exception:
            pass

    metadata = build_pdf_metadata(
        file_id=record["file_id"],
        file_name=record["name"],
        pdf_path=pdf_path,
        metadata_path=metadata_path,
    )
    storage.update_metadata_path(record["file_id"], metadata_path)
    return metadata


def get_record_or_404(file_id: str) -> dict:
    record = storage.get_file(file_id)
    if not record:
        raise HTTPException(status_code=404, detail="Unknown file_id")
    return record


@app.on_event("startup")
def on_startup() -> None:
    storage.scan_initial_pdfs()
    for record in storage.list_files():
        ensure_metadata(record)


@app.get("/api/files")
def list_files() -> list[dict]:
    files = []
    for record in storage.list_files():
        files.append(
            {
                "file_id": record["file_id"],
                "name": record["name"],
                "kind": record["kind"],
                "download_url": f"/api/files/{record['file_id']}/download",
            }
        )
    return files


@app.get("/api/files/{file_id}")
def get_file(file_id: str) -> dict:
    record = get_record_or_404(file_id)
    return {
        "file_id": record["file_id"],
        "name": record["name"],
        "kind": record["kind"],
        "download_url": f"/api/files/{record['file_id']}/download",
        "columns_url_template": f"/api/files/{record['file_id']}/columns/{{column}}",
    }


@app.get("/api/files/{file_id}/columns/{column}")
def get_column(file_id: str, column: int) -> dict:
    if column < 0 or column > 31:
        raise HTTPException(status_code=400, detail="column must be between 0 and 31")

    record = get_record_or_404(file_id)
    metadata = ensure_metadata(record)
    column_entry = metadata.get("columns", {}).get(str(column))
    if not column_entry:
        raise HTTPException(status_code=404, detail="Column metadata not found")

    return {
        "file_id": record["file_id"],
        "file_name": record["name"],
        "column": column,
        "graph_page_number": column_entry["graph_page_number"],
        "graph_image_url": column_entry["graph_image_url"],
        "text": column_entry.get("text") or "No text report found for this column.",
        "download_url": column_entry["download_url"],
    }


@app.get("/api/files/{file_id}/pages/{page_number}/image")
def get_page_image(file_id: str, page_number: int):
    if page_number < 1:
        raise HTTPException(status_code=400, detail="page_number must be >= 1")

    record = get_record_or_404(file_id)
    pdf_path = Path(record["pdf_path"]).resolve()
    try:
        image_path = ensure_page_preview(file_id, pdf_path, storage.previews_dir, page_number)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return FileResponse(str(image_path), media_type="image/png")


@app.get("/api/files/{file_id}/download")
def download_pdf(file_id: str):
    record = get_record_or_404(file_id)
    pdf_path = Path(record["pdf_path"]).resolve()
    if not pdf_path.exists():
        raise HTTPException(status_code=404, detail="PDF not found on server")
    return FileResponse(str(pdf_path), media_type="application/pdf", filename=record["name"])


@app.post("/api/run-analysis")
async def run_analysis_api(upload: UploadFile = File(...)) -> dict:
    try:
        safe_name = sanitize_filename(upload.filename or "upload.npz", (".npz", ".npy"))
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    upload_token = uuid4().hex[:10]
    upload_path = storage.uploads_dir / f"{upload_token}_{safe_name}"
    with upload_path.open("wb") as out_file:
        shutil.copyfileobj(upload.file, out_file)
    await upload.close()

    output_dir = storage.generated_dir / f"analysis_{upload_token}"
    try:
        result = run_uploaded_analysis(upload_path, output_dir, options=None)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Analysis failed: {exc}") from exc

    generated_pdf = Path(result["pdf_path"]).resolve()
    if generated_pdf.suffix.lower() != ".pdf" or not generated_pdf.exists():
        raise HTTPException(status_code=500, detail="Analysis did not produce a valid PDF")

    record = storage.register_pdf(
        generated_pdf,
        kind="generated_analysis",
        display_name=generated_pdf.name,
    )
    ensure_metadata(record)

    return {
        "file_id": record["file_id"],
        "name": record["name"],
        "status": "complete",
        "view_url": f"/api/files/{record['file_id']}",
        "download_url": f"/api/files/{record['file_id']}/download",
    }
