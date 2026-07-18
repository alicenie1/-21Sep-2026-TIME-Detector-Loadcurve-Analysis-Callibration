import { useEffect, useMemo, useState } from "react";
import { API_BASE, getColumn, listFiles, runAnalysis } from "./api";
...
          <a className="download-button" href={`${API_BASE}${selectedFile.download_url}`}>
import ColumnSelector from "./components/ColumnSelector";
import FileSelector from "./components/FileSelector";
import PdfColumnViewer from "./components/PdfColumnViewer";
import UploadAnalysis from "./components/UploadAnalysis";

export default function App() {
  const [files, setFiles] = useState([]);
  const [filesLoading, setFilesLoading] = useState(true);
  const [filesError, setFilesError] = useState("");
  const [selectedFileId, setSelectedFileId] = useState("");
  const [selectedColumn, setSelectedColumn] = useState(0);

  const [columnData, setColumnData] = useState(null);
  const [columnLoading, setColumnLoading] = useState(false);
  const [columnError, setColumnError] = useState("");

  const selectedFile = useMemo(
    () => files.find((file) => file.file_id === selectedFileId) || null,
    [files, selectedFileId]
  );

  async function refreshFiles(preferredFileId) {
    setFilesLoading(true);
    setFilesError("");
    try {
      const apiFiles = await listFiles();
      setFiles(apiFiles);
      if (apiFiles.length === 0) {
        setSelectedFileId("");
        return;
      }
      const nextFileId =
        preferredFileId && apiFiles.some((file) => file.file_id === preferredFileId)
          ? preferredFileId
          : selectedFileId && apiFiles.some((file) => file.file_id === selectedFileId)
            ? selectedFileId
            : apiFiles[0].file_id;
      setSelectedFileId(nextFileId);
    } catch (error) {
      setFilesError(error.message || "Failed to load files.");
    } finally {
      setFilesLoading(false);
    }
  }

  useEffect(() => {
    refreshFiles();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  useEffect(() => {
    async function loadColumnData() {
      if (!selectedFileId) {
        setColumnData(null);
        return;
      }
      setColumnLoading(true);
      setColumnError("");
      try {
        const data = await getColumn(selectedFileId, selectedColumn);
        setColumnData(data);
      } catch (error) {
        setColumnData(null);
        setColumnError(error.message || "Failed to load column.");
      } finally {
        setColumnLoading(false);
      }
    }
    loadColumnData();
  }, [selectedFileId, selectedColumn]);

  async function handleRunAnalysis(file) {
    const result = await runAnalysis(file);
    await refreshFiles(result.file_id);
    setSelectedColumn(0);
  }

  return (
    <main className="app">
      <header className="header panel">
        <h1>Detector Analysis Viewer</h1>
        <p>
          View graph pages and report text by column, download PDFs, and run analysis on uploaded
          NumPy files.
        </p>
      </header>

      <section className="controls">
        <FileSelector
          files={files}
          selectedFileId={selectedFileId}
          onChange={setSelectedFileId}
          disabled={filesLoading}
        />
        <ColumnSelector
          selectedColumn={selectedColumn}
          onChange={setSelectedColumn}
          disabled={!selectedFileId || columnLoading}
        />
        <UploadAnalysis onRun={handleRunAnalysis} />
      </section>

      {filesError ? <div className="panel status error">{filesError}</div> : null}
      {filesLoading ? <div className="panel status">Loading files...</div> : null}

      <section className="panel download-panel">
        <div>
          Selected File: <strong>{selectedFile ? selectedFile.name : "None"}</strong>
        </div>
        {selectedFile ? (
          <a className="download-button" href={selectedFile.download_url}>
            Download PDF
          </a>
        ) : null}
      </section>

      <PdfColumnViewer columnData={columnData} loading={columnLoading} error={columnError} />
    </main>
  );
}
