import os
import json
from datetime import datetime, timezone
from pathlib import Path
import sys


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

_mpl_config_dir = (PROJECT_ROOT / "backend" / "server_storage" / ".matplotlib").resolve()
_mpl_config_dir.mkdir(parents=True, exist_ok=True)
os.environ.setdefault("MPLBACKEND", "Agg")
os.environ.setdefault("MPLCONFIGDIR", str(_mpl_config_dir))

from detector_analysis import (  # noqa: E402
    EXPECTED_COLUMN_COUNT,
    extract_pdf_column_data,
    render_pdf_page_to_image,
)


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def build_pdf_metadata(
    file_id: str,
    file_name: str,
    pdf_path: Path,
    metadata_path: Path,
) -> dict:
    extracted = extract_pdf_column_data(str(pdf_path))
    columns: dict[str, dict] = {}

    for column in range(EXPECTED_COLUMN_COUNT):
        key = str(column)
        extracted_column = extracted.get("columns", {}).get(key, {})
        graph_page = int(extracted_column.get("graph_page_number", column + 1))
        text = (extracted_column.get("text") or "").strip()
        if not text:
            text = "No text report found for this column."

        columns[key] = {
            "column": column,
            "graph_page_number": graph_page,
            "graph_image_url": f"/api/files/{file_id}/pages/{graph_page}/image",
            "text": text,
            "source_pdf_name": file_name,
            "download_url": f"/api/files/{file_id}/download",
        }

    metadata = {
        "file_id": file_id,
        "file_name": file_name,
        "pdf_path": str(pdf_path.resolve()),
        "page_count": int(extracted.get("page_count", 0)),
        "columns": columns,
        "updated_at": _utc_now_iso(),
    }

    metadata_path.parent.mkdir(parents=True, exist_ok=True)
    metadata_path.write_text(json.dumps(metadata, indent=2), encoding="utf-8")
    return metadata


def load_pdf_metadata(metadata_path: Path) -> dict:
    return json.loads(metadata_path.read_text(encoding="utf-8"))


def ensure_page_preview(
    file_id: str,
    pdf_path: Path,
    previews_root: Path,
    page_number: int,
) -> Path:
    preview_dir = previews_root / file_id
    preview_dir.mkdir(parents=True, exist_ok=True)
    image_path = preview_dir / f"page_{page_number}.png"

    if image_path.exists():
        return image_path

    render_pdf_page_to_image(str(pdf_path), page_number, str(image_path))
    return image_path
