const BASE = '/api';

async function request(path, options = {}) {
    const res = await fetch(`${BASE}${path}`, {
        headers: { 'Content-Type': 'application/json', ...options.headers },
        ...options,
    });
    if (!res.ok) {
        const body = await res.text();
        throw new Error(`${res.status}: ${body}`);
    }
    if (res.status === 204) return null;
    return res.json();
}

export const api = {
    // Projects
    listProjects: () => request('/projects'),
    createProject: (data) => request('/projects', { method: 'POST', body: JSON.stringify(data) }),
    getProject: (id) => request(`/projects/${id}`),
    updateProject: (id, data) => request(`/projects/${id}`, { method: 'PATCH', body: JSON.stringify(data) }),
    deleteProject: (id) => request(`/projects/${id}`, { method: 'DELETE' }),

    // Prompt tree
    getTree: (pid) => request(`/projects/${pid}/tree`),
    createNode: (pid, data) => request(`/projects/${pid}/nodes`, { method: 'POST', body: JSON.stringify(data) }),
    getNode: (pid, nid) => request(`/projects/${pid}/nodes/${nid}`),
    updateNode: (pid, nid, data) => request(`/projects/${pid}/nodes/${nid}`, { method: 'PATCH', body: JSON.stringify(data) }),
    deleteNode: (pid, nid) => request(`/projects/${pid}/nodes/${nid}`, { method: 'DELETE' }),
    getAncestors: (pid, nid) => request(`/projects/${pid}/nodes/${nid}/ancestors`),

    // Images
    uploadImage: async (pid, nid, file) => {
        const form = new FormData();
        form.append('file', file);
        const res = await fetch(`${BASE}/projects/${pid}/nodes/${nid}/image`, { method: 'POST', body: form });
        if (!res.ok) throw new Error(`${res.status}: ${await res.text()}`);
        return res.json();
    },
    uploadImageFromUrl: (pid, nid, url) =>
        request(`/projects/${pid}/nodes/${nid}/image/from-url`, { method: 'POST', body: JSON.stringify({ url }) }),
    getImage: (pid, nid) => request(`/projects/${pid}/nodes/${nid}/image`),
    updateImage: (pid, nid, patch) =>
        request(`/projects/${pid}/nodes/${nid}/image`, { method: 'PATCH', body: JSON.stringify(patch) }),
    deleteImage: (pid, nid) => request(`/projects/${pid}/nodes/${nid}/image`, { method: 'DELETE' }),

    // Tags
    addNodeTag: (pid, nid, name) =>
        request(`/projects/${pid}/nodes/${nid}/tags`, { method: 'POST', body: JSON.stringify({ name }) }),
    removeNodeTag: (pid, nid, name) =>
        request(`/projects/${pid}/nodes/${nid}/tags/${encodeURIComponent(name)}`, { method: 'DELETE' }),

    // Coaching
    getCoachingHistory: (pid, nid) => request(`/projects/${pid}/nodes/${nid}/coaching`),
    streamCoaching: (pid, nid, message, signal) => {
        return fetch(`${BASE}/projects/${pid}/nodes/${nid}/coaching`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ message }),
            signal,
        });
    },
};
