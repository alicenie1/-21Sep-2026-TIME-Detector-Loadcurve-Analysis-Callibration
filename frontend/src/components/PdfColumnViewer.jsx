export default function PdfColumnViewer({ columnData, loading, error }) {
  if (loading) {
    return <div className="panel status">Loading selected column...</div>;
  }

  if (error) {
    return <div className="panel status error">{error}</div>;
  }

  if (!columnData) {
    return <div className="panel status">Pick a file and a column to begin.</div>;
  }

  return (
    <div className="viewer">
      <div className="panel">
        <div className="label">
          Graph Page {columnData.graph_page_number} (Column {columnData.column})
        </div>
        <img
          src={columnData.graph_image_url}
          alt={`Column ${columnData.column} graph`}
          className="graph-image"
        />
      </div>
      <div className="panel">
        <div className="label">Text Report</div>
        <pre className="report-text">{columnData.text}</pre>
      </div>
    </div>
  );
}
