import { useState } from "react";

export default function UploadAnalysis({ onRun }) {
  const [file, setFile] = useState(null);
  const [running, setRunning] = useState(false);
  const [error, setError] = useState("");

  async function handleRun() {
    if (!file) {
      setError("Choose a .npz or .npy file first.");
      return;
    }
    setRunning(true);
    setError("");
    try {
      await onRun(file);
      setFile(null);
    } catch (runError) {
      setError(runError.message || "Run analysis failed.");
    } finally {
      setRunning(false);
    }
  }

  return (
    <div className="panel">
      <div className="label">Upload NumPy and Run Analysis</div>
      <input
        type="file"
        accept=".npz,.npy"
        onChange={(event) => setFile(event.target.files?.[0] || null)}
        disabled={running}
      />
      <button type="button" className="run-button" onClick={handleRun} disabled={running}>
        {running ? "Running..." : "Run Analysis"}
      </button>
      {file ? <div className="hint">Selected: {file.name}</div> : null}
      {error ? <div className="status error">{error}</div> : null}
    </div>
  );
}
