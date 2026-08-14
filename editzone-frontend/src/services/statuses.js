import api from "./api";

export const statusApi = {
  list: () => api.get("/statuses"),
  mine: () => api.get("/statuses/mine"),
  forEditor: (editorId) => api.get(`/statuses/editor/${editorId}`),
  create: (uploadId, caption) => api.post("/statuses", { upload_id: uploadId, caption }),
  view: (id) => api.post(`/statuses/${id}/view`),
  like: (id) => api.put(`/statuses/${id}/like`),
  unlike: (id) => api.delete(`/statuses/${id}/like`),
  remove: (id) => api.delete(`/statuses/${id}`),
  people: (id, type) => api.get(`/statuses/${id}/${type === "views" ? "viewers" : "likes"}`),
  cancelMedia: (id) => api.delete(`/media/${encodeURIComponent(id)}`),
};

export function groupStatuses(statuses = []) {
  const groups = new Map();
  statuses.forEach((status) => {
    const key = status.editor.id;
    if (!groups.has(key)) groups.set(key, { editor: status.editor, statuses: [] });
    groups.get(key).statuses.push(status);
  });
  return [...groups.values()];
}
