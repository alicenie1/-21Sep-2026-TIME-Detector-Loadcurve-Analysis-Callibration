export default function FileSelector({ files, selectedFileId, onChange, disabled }) {
  return (
    <div className="panel">
      <label className="label" htmlFor="file-select">
        Select PDF
      </label>
      <select
        id="file-select"
        className="select"
        value={selectedFileId || ""}
        onChange={(event) => onChange(event.target.value)}
        disabled={disabled || files.length === 0}
      >
        {files.length === 0 ? (
          <option value="">No files available</option>
        ) : null}
        {files.map((file) => (
          <option key={file.file_id} value={file.file_id}>
            {file.name} ({file.kind})
          </option>
        ))}
      </select>
    </div>
  );
}
