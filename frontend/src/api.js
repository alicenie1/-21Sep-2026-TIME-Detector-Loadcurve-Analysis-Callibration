export const API_BASE = import.meta.env.VITE_API_BASE_URL || "";
const API_BASE = import.meta.env.VITE_API_BASE_URL || "";

async function request(path, options = {}) {
  const response = await fetch(`${API_BASE}${path}`, options);
  if (!response.ok) {
    let message = `Request failed with ${response.status}`;
    try {
      const data = await response.json();
      if (data?.detail) {
        message = data.detail;
      }
    } catch (_) {
      // Keep default error message.
    }
    throw new Error(message);
  }
  return response.json();
}

export function listFiles() {
  return request("/api/files");
}

export function getColumn(fileId, column) {
  return request(`/api/files/${encodeURIComponent(fileId)}/columns/${column}`);
}

export function runAnalysis(file) {
  const formData = new FormData();
  formData.append("upload", file);
  return request("/api/run-analysis", {
    method: "POST",
    body: formData
  });
}
