import argparse
import json
import re
import textwrap
from pathlib import Path

import fitz
import matplotlib.pyplot as plt
import numpy as np
from matplotlib.backends.backend_pdf import PdfPages

EXPECTED_COLUMN_COUNT = 32

DEFAULT_ANALYSIS_OPTIONS = {
    "span_threshold": None,
    "threshold_in_kdac": False,
    "vline_x": None,
    "slope_threshold": -1.0,
    "sustained_slope_threshold": -0.08,
    "min_transition_drop_kdac": 3.0,
    "transition_window": 80,
    "max_bias": 1800,
    "max_gap_points": 1,
    "min_segment_points": 2,
}


# Segmentation Slope Detection

def merge_close_segments(segments, max_gap_points=1):
    """
    Merge contiguous index segments when their gap is tiny
    (<= max_gap_points). Segments are (start_idx, end_idx), inclusive.
    """
    if len(segments) == 0:
        return []

    merged = [list(segments[0])]
    for start_idx, end_idx in segments[1:]:
        gap = start_idx - merged[-1][1] - 1
        if gap <= max_gap_points:
            merged[-1][1] = end_idx
        else:
            merged.append([start_idx, end_idx])

    return [(int(s), int(e)) for s, e in merged]


def find_transition_segments(
    dy,
    bias,
    *,
    sustained_slope_threshold=-0.08,
    max_bias=1800,
    max_gap_points=1,
):
    """
    Build candidate negative-slope segments using dy < sustained_slope_threshold
    for bias < max_bias, then merge tiny internal gaps.
    """
    allowed_mask = bias < max_bias
    candidate_mask = (dy < sustained_slope_threshold) & allowed_mask
    idx = np.flatnonzero(candidate_mask)

    if idx.size == 0:
        return []

    raw_segments = []
    start_idx = int(idx[0])
    prev_idx = int(idx[0])

    for i in idx[1:]:
        i = int(i)
        if i == prev_idx + 1:
            prev_idx = i
            continue
        raw_segments.append((start_idx, prev_idx))
        start_idx = i
        prev_idx = i

    raw_segments.append((start_idx, prev_idx))
    return merge_close_segments(raw_segments, max_gap_points=max_gap_points)


def summarize_segment(trace, dy, bias, start_idx, end_idx):
    """
    Summarize one segment using inclusive index endpoints.
    """
    seg_dy = dy[start_idx : end_idx + 1]
    return {
        "start_index": int(start_idx),
        "end_index": int(end_idx),
        "start_bias": float(bias[start_idx]),
        "end_bias": float(bias[end_idx]),
        "center_bias": float(0.5 * (bias[start_idx] + bias[end_idx])),
        "min_slope": float(np.nanmin(seg_dy)),
        "mean_slope": float(np.nanmean(seg_dy)),
        "total_drop_kdac": float(trace[start_idx] - trace[end_idx]),
        "n_points": int(end_idx - start_idx + 1),
    }


def segment_overlaps_window(segment, center_bias, transition_window=80):
    """
    True when [segment_start, segment_end] overlaps [center_bias - window, center_bias].
    """
    window_start = center_bias - transition_window
    window_end = center_bias
    return (segment["end_bias"] >= window_start) and (segment["start_bias"] <= window_end)


def _transition_reason_from_passes(sharp_pass: bool, sustained_pass: bool):
    if sharp_pass and sustained_pass:
        return "sharp_and_sustained"
    if sharp_pass:
        return "sharp"
    if sustained_pass:
        return "sustained"
    return None


def classify_detector_transition(
    trace,
    dy,
    bias,
    *,
    slope_threshold=-1.0,
    sustained_slope_threshold=-0.08,
    min_transition_drop_kdac=3.0,
    max_bias=1800,
    max_gap_points=1,
    min_segment_points=2,
):
    """
    Classify detector as:
      - none  : zero accepted transition segments
      - clean : exactly one accepted transition segment
      - noise : more than one accepted transition segment

    Status always stays in none/clean/noise.
    Transition reason tracks sharp/sustained acceptance for accepted segments.
    """
    segment_idx_pairs = find_transition_segments(
        dy,
        bias,
        sustained_slope_threshold=sustained_slope_threshold,
        max_bias=max_bias,
        max_gap_points=max_gap_points,
    )

    all_segments = []
    accepted_segments = []

    for start_idx, end_idx in segment_idx_pairs:
        seg = summarize_segment(trace, dy, bias, start_idx, end_idx)

        sharp_pass = seg["min_slope"] <= slope_threshold
        sustained_drop_pass = seg["total_drop_kdac"] >= min_transition_drop_kdac
        sustained_mean_pass = seg["mean_slope"] <= sustained_slope_threshold
        sustained_points_pass = seg["n_points"] >= min_segment_points
        sustained_pass = sustained_drop_pass and sustained_mean_pass and sustained_points_pass

        transition_reason = _transition_reason_from_passes(bool(sharp_pass), bool(sustained_pass))

        seg["sharp_pass"] = bool(sharp_pass)
        seg["sustained_pass"] = bool(sustained_pass)
        seg["sustained_drop_pass"] = bool(sustained_drop_pass)
        seg["sustained_mean_pass"] = bool(sustained_mean_pass)
        seg["sustained_points_pass"] = bool(sustained_points_pass)
        seg["transition_reason"] = transition_reason
        seg["accepted"] = transition_reason is not None
        all_segments.append(seg)

        if seg["accepted"]:
            accepted_segments.append(seg)

    if len(accepted_segments) == 0:
        status = "none"
        transition_reason = None
    elif len(accepted_segments) == 1:
        status = "clean"
        transition_reason = accepted_segments[0].get("transition_reason")
    else:
        status = "noise"
        transition_reason = None

    return {
        "status": status,
        "transition_reason": transition_reason,
        "all_segments": all_segments,
        "accepted_segments": accepted_segments,
    }


# -----------------------------
# FIND BEST BIAS PER COLUMN
# -----------------------------
def find_best_bias(bias, clean_detectors, transition_window=80):
    """
    Scan candidate bias points and count clean detectors whose
    single accepted transition segment overlaps [candidate - window, candidate].
    """
    counts = np.zeros(len(bias), dtype=int)
    detectors_by_candidate = []

    for i, candidate_bias in enumerate(bias):
        matching = [
            det
            for det in clean_detectors
            if segment_overlaps_window(
                det["accepted_segments"][0],
                center_bias=candidate_bias,
                transition_window=transition_window,
            )
        ]
        counts[i] = len(matching)
        detectors_by_candidate.append(matching)

    best_idx = int(np.argmax(counts)) if len(counts) > 0 else 0
    best_bias = float(bias[best_idx]) if len(bias) > 0 else np.nan
    return best_bias, counts, detectors_by_candidate


def add_report_pages(pdf, report_lines, wrap_width=110, lines_per_page=45):
    wrapped_lines = []

    for text in report_lines:
        for line in str(text).splitlines():
            if line:
                wrapped_lines.extend(textwrap.wrap(line, width=wrap_width) or [""])
            else:
                wrapped_lines.append("")

    if not wrapped_lines:
        wrapped_lines.append("No report content available.")

    for start in range(0, len(wrapped_lines), lines_per_page):
        fig = plt.figure(figsize=(13, 9))
        ax = plt.gca()
        ax.axis("off")

        y = 0.98
        for line in wrapped_lines[start : start + lines_per_page]:
            fig.text(0.03, y, line, ha="left", va="top", family="monospace", fontsize=10)
            y -= 0.021

        pdf.savefig(fig)
        plt.close(fig)


def _serialize_options(options: dict):
    serializable = {}
    for key, value in options.items():
        if key == "vline_x" and isinstance(value, dict):
            serializable[key] = {str(int(k)): float(v) for k, v in value.items()}
        else:
            serializable[key] = value
    return serializable


def _normalize_vline_map(vline_x):
    if vline_x is None:
        return None
    if not isinstance(vline_x, dict):
        raise ValueError("options['vline_x'] must be a dict like {column_index: bias_value}.")

    normalized = {}
    for key, value in vline_x.items():
        try:
            col_idx = int(key)
        except (TypeError, ValueError) as exc:
            raise ValueError(f"Invalid vline_x column key: {key!r}") from exc
        normalized[col_idx] = float(value)
    return normalized


def _resolve_analysis_options(options: dict | None):
    merged = DEFAULT_ANALYSIS_OPTIONS.copy()
    if options:
        merged.update(options)
    merged["vline_x"] = _normalize_vline_map(merged.get("vline_x"))
    return merged


def _read_numpy_detector_payload(file_path: Path):
    suffix = file_path.suffix.lower()
    warnings = []

    if suffix == ".npz":
        with np.load(file_path, allow_pickle=False) as loaded:
            missing = [key for key in ("bias", "y") if key not in loaded.files]
            if missing:
                raise ValueError(
                    f"Invalid .npz file: missing required arrays {missing}. Expected keys: 'bias' and 'y'."
                )
            bias = np.asarray(loaded["bias"], dtype=float)
            y = np.asarray(loaded["y"], dtype=float)

    elif suffix == ".npy":
        arr = np.load(file_path, allow_pickle=False)
        if not isinstance(arr, np.ndarray) or arr.ndim != 3:
            raise ValueError(
                "Invalid .npy file: expected a 3D numeric array shaped "
                "(n_rows, n_cols, n_bias_points)."
            )
        y = np.asarray(arr, dtype=float)
        bias = np.arange(y.shape[2], dtype=float)
        warnings.append(
            "Input .npy file has no explicit bias vector; using index-based bias [0..N-1]."
        )

    else:
        raise ValueError("Only .npz and .npy files are supported.")

    if bias.ndim != 1:
        raise ValueError(f"Invalid bias array: expected 1D, got shape {bias.shape}.")

    if y.ndim != 3:
        raise ValueError(f"Invalid y array: expected 3D, got shape {y.shape}.")

    if y.shape[2] != bias.shape[0]:
        raise ValueError(
            "Invalid detector data: y.shape[2] must match len(bias). "
            f"Got y.shape={y.shape} and len(bias)={len(bias)}."
        )

    if not np.isfinite(bias).any():
        raise ValueError("Invalid bias array: no finite values found.")

    if y.shape[1] <= 0:
        raise ValueError("Invalid detector data: y has zero columns.")

    return bias, y, warnings


def validate_uploaded_numpy_file(file_path: str) -> bool:
    """
    Return True only if the uploaded file is .npz or .npy and contains the expected detector data.
    Raise a clear ValueError with a useful message if invalid.
    """
    path = Path(file_path).expanduser()

    if not path.exists():
        raise ValueError(f"Input file does not exist: {path}")
    if not path.is_file():
        raise ValueError(f"Input path is not a file: {path}")
    if path.suffix.lower() not in {".npz", ".npy"}:
        raise ValueError(
            f"Unsupported file extension '{path.suffix}'. Expected .npz or .npy."
        )

    _read_numpy_detector_payload(path)
    return True


def load_numpy_detector_file(file_path: str):
    """
    Load a .npz or .npy detector data file.
    Return data in the internal structure expected by the analysis functions.
    Do not rely on any hardcoded file path.
    """
    path = Path(file_path).expanduser().resolve()
    bias, y, warnings = _read_numpy_detector_payload(path)

    return {
        "source_file_path": str(path),
        "source_file_name": path.name,
        "bias": bias,
        "y": y,
        "warnings": warnings,
    }


def _format_segment(seg):
    return (
        f"[{seg['start_bias']:.1f}, {seg['end_bias']:.1f}] "
        f"min_slope={seg['min_slope']:.3f} "
        f"mean_slope={seg['mean_slope']:.3f} "
        f"drop_kdac={seg['total_drop_kdac']:.3f}"
    )


def _gap_to_window(seg, center_bias, window):
    window_start = center_bias - window
    window_end = center_bias
    if seg["end_bias"] < window_start:
        return float(window_start - seg["end_bias"])
    if seg["start_bias"] > window_end:
        return float(seg["start_bias"] - window_end)
    return 0.0


def analyze_detector_data(data, options: dict | None = None) -> dict:
    """
    Run the detector analysis without plotting.
    Return structured results for all 32 columns.
    """
    opts = _resolve_analysis_options(options)

    bias_raw = np.asarray(data["bias"], dtype=float)
    y = np.asarray(data["y"], dtype=float)

    positive_bias_mask = bias_raw > 0
    bias = bias_raw[positive_bias_mask]

    if bias.size == 0:
        raise ValueError("No positive bias values found after filtering bias > 0.")

    nrow, ncol, _nbias = y.shape

    warnings = list(data.get("warnings", []))
    if ncol < EXPECTED_COLUMN_COUNT:
        warnings.append(
            f"Input has {ncol} columns, fewer than expected {EXPECTED_COLUMN_COUNT}. "
            "Missing columns will be included with empty results."
        )
    elif ncol > EXPECTED_COLUMN_COUNT:
        warnings.append(
            f"Input has {ncol} columns; only the first {EXPECTED_COLUMN_COUNT} columns will be reported."
        )

    report_lines = []
    columns = {}

    for col in range(EXPECTED_COLUMN_COUNT):
        column_report_lines = []

        def log(text):
            line = str(text)
            report_lines.append(line)
            column_report_lines.append(line)

        fig_ready_plot_traces = []

        if col >= ncol:
            log(f"\nColumn {col} - column not present in input")
            columns[str(col)] = {
                "column_index": int(col),
                "graph_page_number": int(col + 1),
                "best_bias": None,
                "max_detectors_in_transition": 0,
                "detector_rows_found": [],
                "detector_columns_found": [],
                "noisy_detector_pairs": [],
                "best_detector_pairs": [],
                "detector_status": "none",
                "transition_reason": None,
                "transition_window": int(opts["transition_window"]),
                "user_bias": None,
                "user_bias_matching_pairs": [],
                "detectors": [],
                "plot_traces": fig_ready_plot_traces,
                "report_summary": "\n".join(column_report_lines),
            }
            continue

        valid_traces = []
        valid_rows = []

        for row in range(nrow):
            trace_dac = y[row, col, :]
            trace_dac = trace_dac[positive_bias_mask]

            # --- SPAN FILTER ---
            if opts["span_threshold"] is not None:
                span = np.nanmax(trace_dac) - np.nanmin(trace_dac)

                if opts["threshold_in_kdac"]:
                    span *= 1e-3

                if not np.isfinite(span) or span >= float(opts["span_threshold"]):
                    continue

            d = (trace_dac * 1e-3).copy()
            valid_traces.append(d)
            valid_rows.append(row)

        if len(valid_traces) == 0:
            log(f"\nColumn {col} - no valid traces after filtering")
            columns[str(col)] = {
                "column_index": int(col),
                "graph_page_number": int(col + 1),
                "best_bias": None,
                "max_detectors_in_transition": 0,
                "detector_rows_found": [],
                "detector_columns_found": [],
                "noisy_detector_pairs": [],
                "best_detector_pairs": [],
                "detector_status": "none",
                "transition_reason": None,
                "transition_window": int(opts["transition_window"]),
                "user_bias": None,
                "user_bias_matching_pairs": [],
                "detectors": [],
                "plot_traces": fig_ready_plot_traces,
                "report_summary": "\n".join(column_report_lines),
            }
            continue

        log(f"\nColumn {col}")

        detector_infos = []
        for i, trace in enumerate(valid_traces):
            dy = np.gradient(trace, bias)
            transition_info = classify_detector_transition(
                trace,
                dy,
                bias,
                slope_threshold=opts["slope_threshold"],
                sustained_slope_threshold=opts["sustained_slope_threshold"],
                min_transition_drop_kdac=opts["min_transition_drop_kdac"],
                max_bias=opts["max_bias"],
                max_gap_points=opts["max_gap_points"],
                min_segment_points=opts["min_segment_points"],
            )
            detector_infos.append(
                {
                    "row": int(valid_rows[i]),
                    "col": int(col),
                    "trace": trace,
                    "dy": dy,
                    **transition_info,
                }
            )

        clean_detectors = [d for d in detector_infos if d["status"] == "clean"]
        plot_detectors = [d for d in detector_infos if d["status"] == "clean"]
        noisy_detectors = [d for d in detector_infos if d["status"] == "noise"]

        noisy_pairs = [(d["row"], d["col"]) for d in noisy_detectors]
        log(f"  Noisy detectors excluded: {noisy_pairs}")
        for det in noisy_detectors:
            for seg in det["accepted_segments"]:
                log(
                    f"    row={det['row']} col={det['col']} "
                    f"noisy_transition={_format_segment(seg)}"
                )

        for det in plot_detectors:
            fig_ready_plot_traces.append(
                {
                    "row": int(det["row"]),
                    "col": int(det["col"]),
                    "trace_kdac": [float(v) for v in det["trace"]],
                    "bias": [float(v) for v in bias],
                    "status": det["status"],
                    "transition_reason": det.get("transition_reason"),
                }
            )

        # --- FIND BEST BIAS ---
        best_bias, counts, detectors_by_candidate = find_best_bias(
            bias,
            clean_detectors,
            transition_window=opts["transition_window"],
        )
        best_idx = int(np.argmax(counts)) if len(counts) > 0 else 0
        best_detectors = detectors_by_candidate[best_idx] if len(detectors_by_candidate) > 0 else []

        log(f"  Best bias = {best_bias}")
        log(
            f"  Max detectors in transition at best bias = "
            f"{int(np.max(counts)) if len(counts) > 0 else 0}"
        )
        best_pairs = [(d["row"], d["col"]) for d in best_detectors]
        log(
            f"  Detectors at best bias in "
            f"[{best_bias - opts['transition_window']:.1f}, {best_bias:.1f}]: {best_pairs}"
        )
        for det in best_detectors:
            seg = det["accepted_segments"][0]
            log(f"    row={det['row']} col={det['col']} transition={_format_segment(seg)}")

        user_bias = None
        user_matches = []
        vline_x = opts.get("vline_x")

        # --- USER DEFINED LINE ---
        if vline_x is not None and col in vline_x:
            user_bias = float(vline_x[col])
            log(f"  User bias = {user_bias}")

            user_matches = [
                det
                for det in clean_detectors
                if segment_overlaps_window(
                    det["accepted_segments"][0],
                    center_bias=user_bias,
                    transition_window=opts["transition_window"],
                )
            ]
            user_pairs = [(d["row"], d["col"]) for d in user_matches]
            log(
                f"  Clean detectors in transition in "
                f"[{user_bias - opts['transition_window']:.1f}, {user_bias:.1f}]: {user_pairs}"
            )
            for det in user_matches:
                seg = det["accepted_segments"][0]
                log(f"    row={det['row']} col={det['col']} transition={_format_segment(seg)}")

            near_miss_lines = []
            for det in detector_infos:
                if det in user_matches:
                    continue

                if det["status"] in ("clean", "noise"):
                    segments_to_check = det["accepted_segments"]
                else:
                    segments_to_check = det["all_segments"]

                for seg in segments_to_check:
                    overlap = segment_overlaps_window(
                        seg,
                        center_bias=user_bias,
                        transition_window=opts["transition_window"],
                    )
                    gap = _gap_to_window(seg, user_bias, opts["transition_window"])

                    # near miss: outside window but close (<= 40 DAC)
                    # plus rejected/noisy overlaps for diagnostics
                    if (not overlap and gap > 40) or (det["status"] == "clean" and overlap):
                        continue

                    if det["status"] == "noise":
                        reason = "excluded as noisy (multiple accepted transitions)"
                    elif seg.get("accepted", False):
                        reason = "accepted transition outside user window"
                    else:
                        reasons = []
                        if seg["min_slope"] > opts["slope_threshold"]:
                            reasons.append("slope not steep enough for sharp rule")
                        if seg["total_drop_kdac"] < opts["min_transition_drop_kdac"]:
                            reasons.append("total drop too small for sustained rule")
                        if seg["mean_slope"] > opts["sustained_slope_threshold"]:
                            reasons.append("mean slope too shallow for sustained rule")
                        if seg["n_points"] < opts["min_segment_points"]:
                            reasons.append("segment too short for sustained rule")
                        reason = ", ".join(reasons) if reasons else "not accepted"

                    near_miss_lines.append(
                        f"    row={det['row']} col={det['col']} "
                        f"transition={_format_segment(seg)} gap_to_window={gap:.1f} reason={reason}"
                    )

            log("  Near misses / diagnostics (<=40 DAC from window, or rejected/noisy overlaps):")
            if len(near_miss_lines) == 0:
                log("    []")
            else:
                for line in near_miss_lines:
                    log(line)

        status_counts = {
            "none": int(sum(1 for d in detector_infos if d["status"] == "none")),
            "clean": int(sum(1 for d in detector_infos if d["status"] == "clean")),
            "noise": int(sum(1 for d in detector_infos if d["status"] == "noise")),
        }

        serialized_detectors = []
        for det in detector_infos:
            serialized_detectors.append(
                {
                    "row": int(det["row"]),
                    "col": int(det["col"]),
                    "status": det["status"],
                    "transition_reason": det.get("transition_reason"),
                    "accepted_segments": det["accepted_segments"],
                    "all_segments": det["all_segments"],
                }
            )

        column_transition_reason = None
        if len(clean_detectors) == 1:
            column_transition_reason = clean_detectors[0].get("transition_reason")

        columns[str(col)] = {
            "column_index": int(col),
            "graph_page_number": int(col + 1),
            "best_bias": float(best_bias) if np.isfinite(best_bias) else None,
            "max_detectors_in_transition": int(np.max(counts)) if len(counts) > 0 else 0,
            "detector_rows_found": [int(r) for r in valid_rows],
            "detector_columns_found": [int(col)] * len(valid_rows),
            "noisy_detector_pairs": [[int(r), int(c)] for r, c in noisy_pairs],
            "best_detector_pairs": [[int(d["row"]), int(d["col"])] for d in best_detectors],
            "detector_status": "none"
            if len(clean_detectors) == 0
            else "clean"
            if len(clean_detectors) == 1
            else "noise",
            "transition_reason": column_transition_reason,
            "transition_window": int(opts["transition_window"]),
            "user_bias": user_bias,
            "user_bias_matching_pairs": [[int(d["row"]), int(d["col"])] for d in user_matches],
            "status_counts": status_counts,
            "detectors": serialized_detectors,
            "plot_traces": fig_ready_plot_traces,
            "report_summary": "\n".join(column_report_lines),
        }

    return {
        "source_file_name": str(data.get("source_file_name", "")),
        "source_file_path": str(data.get("source_file_path", "")),
        "analysis_options": _serialize_options(opts),
        "row_count_found": int(nrow),
        "column_count_found": int(ncol),
        "bias_points_found": int(len(bias)),
        "columns": columns,
        "report_lines": report_lines,
        "warnings": warnings,
    }


def generate_pdf_from_analysis(analysis_result: dict, output_pdf_path: str) -> str:
    """
    Generate a PDF from the structured analysis result.
    The PDF contains graph pages 1-32 and report text pages after that.
    """
    output_path = Path(output_pdf_path).expanduser().resolve()
    output_path.parent.mkdir(parents=True, exist_ok=True)

    columns = analysis_result.get("columns", {})

    with PdfPages(output_path) as pdf:
        for col in range(EXPECTED_COLUMN_COUNT):
            col_data = columns.get(str(col), {})
            fig = plt.figure(figsize=(13, 9))
            ax = plt.gca()

            plot_traces = col_data.get("plot_traces", [])
            for det in plot_traces:
                ax.plot(
                    det.get("bias", []),
                    det.get("trace_kdac", []),
                    alpha=0.7,
                    label=f"c{int(det.get('col', col)):02d}r{int(det.get('row', 0)):02d}",
                )

            best_bias = col_data.get("best_bias")
            if best_bias is not None:
                ax.axvline(
                    float(best_bias),
                    color="red",
                    linestyle="--",
                    linewidth=2,
                    label=f"Best Bias ({float(best_bias):.0f})",
                )

            user_bias = col_data.get("user_bias")
            if user_bias is not None:
                ax.axvline(
                    float(user_bias),
                    color="black",
                    linestyle=":",
                    linewidth=2,
                    label=f"User Bias ({float(user_bias):.0f})",
                )

            if not plot_traces:
                ax.text(0.5, 0.5, "No valid traces", ha="center", va="center", transform=ax.transAxes)

            ax.set_title(f"Column {col}")
            ax.set_xlabel("Bias (DAC)")
            ax.set_ylabel("Response (kDAC)")
            ax.grid(True)

            handles, labels = ax.get_legend_handles_labels()
            if handles and labels:
                ax.legend()

            pdf.savefig(fig)
            plt.close(fig)

        add_report_pages(pdf, analysis_result.get("report_lines", []))

    return str(output_path)


def save_analysis_json(analysis_result: dict, output_json_path: str) -> str:
    """
    Save the full structured analysis result as JSON.
    Return the output JSON path.
    """
    output_path = Path(output_json_path).expanduser().resolve()
    output_path.parent.mkdir(parents=True, exist_ok=True)

    with output_path.open("w", encoding="utf-8") as f:
        json.dump(analysis_result, f, indent=2)

    return str(output_path)


def plot_iv_by_column(
    npz_path,
    out_dir=None,
    *,
    span_threshold=None,
    threshold_in_kdac=False,
    vline_x=None,
    slope_threshold=-1.0,
    sustained_slope_threshold=-0.08,
    min_transition_drop_kdac=3.0,
    transition_window=80,
    max_bias=1800,
    max_gap_points=1,
    min_segment_points=2,
):
    """
    Backward-compatible wrapper for the original script entry.
    Generates the PDF with the same naming convention and returns its path.
    """
    npz_path = str(npz_path)
    if out_dir is None:
        out_dir = str(Path(npz_path).expanduser().resolve().parent)

    loaded_data = load_numpy_detector_file(npz_path)
    options = {
        "span_threshold": span_threshold,
        "threshold_in_kdac": threshold_in_kdac,
        "vline_x": vline_x,
        "slope_threshold": slope_threshold,
        "sustained_slope_threshold": sustained_slope_threshold,
        "min_transition_drop_kdac": min_transition_drop_kdac,
        "transition_window": transition_window,
        "max_bias": max_bias,
        "max_gap_points": max_gap_points,
        "min_segment_points": min_segment_points,
    }

    analysis_result = analyze_detector_data(loaded_data, options=options)
    base_name = Path(npz_path).stem
    pdf_path = Path(out_dir).expanduser().resolve() / f"best_bias_{base_name}.pdf"
    generate_pdf_from_analysis(analysis_result, str(pdf_path))
    return str(pdf_path)


def run_analysis(np_file_path: str, output_dir: str, options: dict | None = None) -> dict:
    """
    Main website-safe entry point.
    """
    validate_uploaded_numpy_file(np_file_path)
    loaded_data = load_numpy_detector_file(np_file_path)
    analysis_result = analyze_detector_data(loaded_data, options=options)

    source_base = Path(np_file_path).stem
    out_dir = Path(output_dir).expanduser().resolve()
    out_dir.mkdir(parents=True, exist_ok=True)

    pdf_path = out_dir / f"best_bias_{source_base}.pdf"
    json_path = out_dir / f"best_bias_{source_base}.json"

    pdf_out = generate_pdf_from_analysis(analysis_result, str(pdf_path))
    json_out = save_analysis_json(analysis_result, str(json_path))

    return {
        "pdf_path": pdf_out,
        "json_path": json_out,
        "source_file_name": loaded_data["source_file_name"],
        "columns": analysis_result["columns"],
        "warnings": analysis_result.get("warnings", []),
    }


def extract_pdf_column_data(pdf_path: str) -> dict:
    """
    For an existing PDF with convention:
      - page 1..32: Column 0..31 graphs
      - page 33+: report text

    Return mapping per column to graph page and extracted column text.
    """
    path = Path(pdf_path).expanduser().resolve()
    if not path.exists() or not path.is_file():
        raise ValueError(f"PDF file not found: {path}")

    columns = {
        str(col): {"graph_page_number": int(col + 1), "text": ""}
        for col in range(EXPECTED_COLUMN_COUNT)
    }

    doc = fitz.open(path)
    try:
        if doc.page_count <= EXPECTED_COLUMN_COUNT:
            return {
                "pdf_path": str(path),
                "page_count": int(doc.page_count),
                "columns": columns,
            }

        report_text_parts = []
        for page_idx in range(EXPECTED_COLUMN_COUNT, doc.page_count):
            report_text_parts.append(doc.load_page(page_idx).get_text("text") or "")
        report_text = "\n".join(report_text_parts)

        heading_pattern = re.compile(r"\bColumn\s+([0-9]|[1-2][0-9]|3[0-1])\b", re.IGNORECASE)
        matches = list(heading_pattern.finditer(report_text))

        if matches:
            sections = {str(col): [] for col in range(EXPECTED_COLUMN_COUNT)}
            for i, match in enumerate(matches):
                col_key = str(int(match.group(1)))
                start = match.start()
                end = matches[i + 1].start() if i + 1 < len(matches) else len(report_text)
                section = report_text[start:end].strip()
                if section:
                    sections[col_key].append(section)

            for col_key in sections:
                columns[col_key]["text"] = "\n\n".join(sections[col_key]).strip()

        return {
            "pdf_path": str(path),
            "page_count": int(doc.page_count),
            "columns": columns,
        }
    finally:
        doc.close()


def render_pdf_page_to_image(pdf_path: str, page_number: int, output_image_path: str) -> str:
    """
    Render a single PDF page to a PNG image preview.
    page_number is 1-based.
    """
    if page_number < 1:
        raise ValueError("page_number must be >= 1 (1-based indexing).")

    pdf = Path(pdf_path).expanduser().resolve()
    out_image = Path(output_image_path).expanduser().resolve()

    if not pdf.exists() or not pdf.is_file():
        raise ValueError(f"PDF file not found: {pdf}")

    out_image.parent.mkdir(parents=True, exist_ok=True)

    doc = fitz.open(pdf)
    try:
        if page_number > doc.page_count:
            raise ValueError(
                f"page_number {page_number} is out of range for PDF with {doc.page_count} pages."
            )

        page = doc.load_page(page_number - 1)
        pix = page.get_pixmap(matrix=fitz.Matrix(2.0, 2.0), alpha=False)
        pix.save(str(out_image))
    finally:
        doc.close()

    return str(out_image)


def _build_cli_parser():
    parser = argparse.ArgumentParser(
        description="Run detector analysis on an uploaded .npz/.npy file and write PDF+JSON outputs."
    )
    parser.add_argument("--input", required=True, help="Path to uploaded .npz or .npy file")
    parser.add_argument("--output", required=True, help="Output directory")
    parser.add_argument(
        "--options-json",
        default=None,
        help="Optional JSON string for analysis options (e.g. '{\"span_threshold\": 120, \"threshold_in_kdac\": true}')",
    )
    return parser


def main():
    parser = _build_cli_parser()
    args = parser.parse_args()

    options = None
    if args.options_json:
        try:
            options = json.loads(args.options_json)
            if not isinstance(options, dict):
                raise ValueError("options-json must decode to a JSON object.")
        except Exception as exc:
            raise ValueError(f"Invalid --options-json value: {exc}") from exc

    result = run_analysis(args.input, args.output, options=options)
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
