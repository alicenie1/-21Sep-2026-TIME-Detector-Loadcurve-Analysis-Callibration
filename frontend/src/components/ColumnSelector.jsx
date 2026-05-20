const columns = Array.from({ length: 32 }, (_, i) => i);

export default function ColumnSelector({ selectedColumn, onChange, disabled }) {
  return (
    <div className="panel">
      <div className="label">Select Column</div>
      <div className="column-grid">
        {columns.map((column) => (
          <button
            key={column}
            type="button"
            className={`column-button ${selectedColumn === column ? "active" : ""}`}
            onClick={() => onChange(column)}
            disabled={disabled}
          >
            {column}
          </button>
        ))}
      </div>
    </div>
  );
}
