import os
from pathlib import Path
import sys


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

_mpl_config_dir = (PROJECT_ROOT / "backend" / "server_storage" / ".matplotlib").resolve()
_mpl_config_dir.mkdir(parents=True, exist_ok=True)
os.environ.setdefault("MPLBACKEND", "Agg")
os.environ.setdefault("MPLCONFIGDIR", str(_mpl_config_dir))

from detector_analysis import run_analysis  # noqa: E402


def run_uploaded_analysis(np_file_path: Path, output_dir: Path, options: dict | None = None) -> dict:
    output_dir.mkdir(parents=True, exist_ok=True)
    return run_analysis(str(np_file_path), str(output_dir), options=options)
