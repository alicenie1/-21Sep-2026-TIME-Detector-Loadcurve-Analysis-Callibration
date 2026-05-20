import hashlib
import json
import re
from datetime import datetime, timezone
from pathlib import Path
from threading import Lock
from typing import Any
from uuid import uuid4


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def sanitize_filename(filename: str, allowed_extensions: tuple[str, ...]) -> str:
    base_name = Path(filename or "").name
    safe_name = re.sub(r"[^A-Za-z0-9._-]", "_", base_name).strip("._")
    if not safe_name:
        safe_name = "upload"

    suffix = Path(safe_name).suffix.lower()
    if suffix not in allowed_extensions:
        raise ValueError(f"Unsupported file extension: {suffix or '(none)'}")

    return safe_name


class StorageManager:
    def __init__(self, base_dir: Path | None = None) -> None:
        backend_dir = Path(__file__).resolve().parent
        self.base_dir = (base_dir or (backend_dir / "server_storage")).resolve()
        self.uploads_dir = self.base_dir / "uploads"
        self.pdfs_dir = self.base_dir / "pdfs"
        self.generated_dir = self.base_dir / "generated"
        self.previews_dir = self.base_dir / "previews"
        self.metadata_dir = self.base_dir / "metadata"
        self.index_path = self.base_dir / "file_index.json"
        self._lock = Lock()
        self._index: dict[str, list[dict[str, Any]]] = {"files": []}
        self.ensure_layout()
        self._load_index()

    def ensure_layout(self) -> None:
        for folder in (
            self.base_dir,
            self.uploads_dir,
            self.pdfs_dir,
            self.generated_dir,
            self.previews_dir,
            self.metadata_dir,
        ):
            folder.mkdir(parents=True, exist_ok=True)

    def _load_index(self) -> None:
        if self.index_path.exists():
            try:
                data = json.loads(self.index_path.read_text(encoding="utf-8"))
                if isinstance(data, dict) and isinstance(data.get("files"), list):
                    self._index = {"files": data["files"]}
                else:
                    self._index = {"files": []}
            except json.JSONDecodeError:
                self._index = {"files": []}
        else:
            self._index = {"files": []}
            self._write_index_locked()

        self._prune_missing_files_locked()
        self._write_index_locked()

    def _write_index_locked(self) -> None:
        temp_path = self.index_path.with_suffix(".tmp")
        temp_path.write_text(json.dumps(self._index, indent=2), encoding="utf-8")
        temp_path.replace(self.index_path)

    def _prune_missing_files_locked(self) -> None:
        valid_files = []
        for record in self._index.get("files", []):
            pdf_path = Path(record.get("pdf_path", ""))
            if pdf_path.exists() and pdf_path.suffix.lower() == ".pdf":
                valid_files.append(record)
        self._index["files"] = valid_files

    def list_files(self) -> list[dict[str, Any]]:
        with self._lock:
            rows = list(self._index["files"])
        return sorted(rows, key=lambda r: r.get("created_at", ""), reverse=True)

    def get_file(self, file_id: str) -> dict[str, Any] | None:
        with self._lock:
            for record in self._index["files"]:
                if record.get("file_id") == file_id:
                    return dict(record)
        return None

    def _existing_record_by_pdf_path_locked(self, pdf_path: Path) -> dict[str, Any] | None:
        pdf_resolved = str(pdf_path.resolve())
        for record in self._index["files"]:
            if record.get("pdf_path") == pdf_resolved:
                return record
        return None

    def _existing_record_by_id_locked(self, file_id: str) -> dict[str, Any] | None:
        for record in self._index["files"]:
            if record.get("file_id") == file_id:
                return record
        return None

    def _build_stable_pdf_id(self, pdf_path: Path) -> str:
        digest = hashlib.sha1(str(pdf_path.resolve()).encode("utf-8")).hexdigest()[:12]
        return f"pdf_{digest}"

    def _build_generated_id(self) -> str:
        return f"generated_{uuid4().hex[:10]}"

    def register_pdf(
        self,
        pdf_path: str | Path,
        *,
        kind: str,
        file_id: str | None = None,
        display_name: str | None = None,
    ) -> dict[str, Any]:
        pdf = Path(pdf_path).resolve()
        if pdf.suffix.lower() != ".pdf":
            raise ValueError("Only .pdf files can be registered.")
        if not pdf.exists() or not pdf.is_file():
            raise ValueError(f"PDF not found: {pdf}")

        with self._lock:
            existing = self._existing_record_by_pdf_path_locked(pdf)
            if existing:
                return dict(existing)

            candidate_id = file_id
            if not candidate_id:
                candidate_id = (
                    self._build_stable_pdf_id(pdf)
                    if kind == "existing_pdf"
                    else self._build_generated_id()
                )

            while self._existing_record_by_id_locked(candidate_id):
                candidate_id = f"{candidate_id}_{uuid4().hex[:4]}"

            record = {
                "file_id": candidate_id,
                "name": display_name or pdf.name,
                "kind": kind,
                "pdf_path": str(pdf),
                "metadata_path": str((self.metadata_dir / f"{candidate_id}.json").resolve()),
                "created_at": utc_now_iso(),
            }
            self._index["files"].append(record)
            self._write_index_locked()
            return dict(record)

    def scan_initial_pdfs(self) -> list[dict[str, Any]]:
        registered: list[dict[str, Any]] = []
        for pdf_path in sorted(self.pdfs_dir.glob("*.pdf")):
            record = self.register_pdf(pdf_path, kind="existing_pdf")
            registered.append(record)
        return registered

    def update_metadata_path(self, file_id: str, metadata_path: str | Path) -> None:
        meta = str(Path(metadata_path).resolve())
        with self._lock:
            for record in self._index["files"]:
                if record.get("file_id") == file_id:
                    record["metadata_path"] = meta
                    self._write_index_locked()
                    return
        raise ValueError(f"Unknown file_id: {file_id}")
