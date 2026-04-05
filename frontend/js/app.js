import { api } from './api.js';

// ===== State =====
const state = {
    projects: [],
    currentProject: null,
    tree: [],
    currentNode: null,
    coachingMessages: [],
};

// ===== DOM refs =====
const $ = (sel) => document.querySelector(sel);
const projectSelect = $('#project-select');
const promptTree = $('#prompt-tree');
const emptyState = $('#empty-state');
const editorPanel = $('#editor-panel');
const imagePanel = $('#image-panel');
const coachingMessages = $('#coaching-messages');

// ===== Routing =====
function pushRoute() {
    const pid = state.currentProject?.id;
    const nid = state.currentNode?.id;
    let hash = '';
    if (pid && nid) hash = `#/projects/${pid}/nodes/${nid}`;
    else if (pid) hash = `#/projects/${pid}`;
    if (location.hash !== hash) history.pushState(null, '', hash || location.pathname);
}

function parseRoute() {
    const m = location.hash.match(/^#\/projects\/(\d+)(?:\/nodes\/(\d+))?$/);
    if (!m) return { projectId: null, nodeId: null };
    return { projectId: parseInt(m[1]), nodeId: m[2] ? parseInt(m[2]) : null };
}

async function navigateToRoute() {
    const { projectId, nodeId } = parseRoute();
    await loadProjects();

    if (projectId) {
        const project = state.projects.find(p => p.id === projectId);
        if (project) {
            state.currentProject = project;
            projectSelect.value = projectId;
            $('#new-root-btn').disabled = false;
            await loadTree();
            if (nodeId) {
                await selectNodeById(nodeId, true);
            } else {
                selectNode(null, true);
            }
            return;
        }
    }
    state.currentProject = null;
    selectNode(null);
}

window.addEventListener('popstate', () => navigateToRoute());

// ===== Toast =====
function toast(msg) {
    const el = document.createElement('div');
    el.className = 'toast';
    el.textContent = msg;
    document.body.appendChild(el);
    setTimeout(() => el.remove(), 2200);
}

// ===== Projects =====
async function loadProjects() {
    state.projects = await api.listProjects();
    projectSelect.innerHTML = '<option value="">Select project...</option>';
    for (const p of state.projects) {
        const opt = document.createElement('option');
        opt.value = p.id;
        opt.textContent = p.name;
        if (state.currentProject && p.id === state.currentProject.id) opt.selected = true;
        projectSelect.appendChild(opt);
    }
    $('#new-root-btn').disabled = !state.currentProject;
}

projectSelect.addEventListener('change', async () => {
    const id = projectSelect.value;
    if (!id) { state.currentProject = null; state.tree = []; renderTree(); selectNode(null); pushRoute(); return; }
    state.currentProject = await api.getProject(id);
    await loadTree();
    selectNode(null);
    $('#new-root-btn').disabled = false;
    pushRoute();
});

$('#new-project-btn').addEventListener('click', () => {
    $('#new-project-name').value = '';
    $('#new-project-desc').value = '';
    $('#new-project-dialog').showModal();
});

$('#new-project-dialog').querySelector('form').addEventListener('submit', async (e) => {
    const name = $('#new-project-name').value.trim();
    if (!name) return;
    const project = await api.createProject({ name, description: $('#new-project-desc').value.trim() || null });
    state.currentProject = project;
    await loadProjects();
    projectSelect.value = project.id;
    await loadTree();
    selectNode(null);
    pushRoute();
});

// ===== Tree =====
async function loadTree() {
    if (!state.currentProject) { state.tree = []; renderTree(); return; }
    state.tree = await api.getTree(state.currentProject.id);
    renderTree();
}

function renderTree() {
    promptTree.innerHTML = '';
    if (!state.tree.length) {
        promptTree.innerHTML = '<div style="padding:12px 8px;color:var(--text-muted);font-size:12px;">No prompts yet.</div>';
        return;
    }
    for (const node of state.tree) {
        promptTree.appendChild(renderTreeNode(node, true));
    }
}

function renderTreeNode(node, isRoot = false) {
    const container = document.createElement('div');
    container.className = `tree-node${isRoot ? ' root' : ''}`;
    container.dataset.nodeId = node.id;

    const item = document.createElement('div');
    item.className = 'tree-item' + (state.currentNode && state.currentNode.id === node.id ? ' selected' : '');

    // Toggle for children
    if (node.children && node.children.length > 0) {
        const toggle = document.createElement('button');
        toggle.className = 'tree-toggle';
        toggle.textContent = '\u25BE';
        toggle.addEventListener('click', (e) => {
            e.stopPropagation();
            const childrenEl = container.querySelector(':scope > .tree-children');
            if (childrenEl) {
                childrenEl.classList.toggle('collapsed');
                toggle.textContent = childrenEl.classList.contains('collapsed') ? '\u25B8' : '\u25BE';
            }
        });
        item.appendChild(toggle);
    } else {
        const spacer = document.createElement('span');
        spacer.style.width = '12px';
        spacer.style.flexShrink = '0';
        item.appendChild(spacer);
    }

    // Star indicator
    if (node.is_starred) {
        const star = document.createElement('span');
        star.className = 'star active';
        star.textContent = '\u2605';
        item.appendChild(star);
    }

    // Image indicator
    if (node.has_image) {
        const dot = document.createElement('span');
        dot.className = 'has-image';
        item.appendChild(dot);
    }

    // Prompt text
    const text = document.createElement('span');
    text.className = 'node-text';
    text.textContent = node.prompt_text;
    text.title = node.prompt_text;
    item.appendChild(text);

    item.addEventListener('click', () => selectNodeById(node.id));
    container.appendChild(item);

    // Children
    if (node.children && node.children.length > 0) {
        const childrenEl = document.createElement('div');
        childrenEl.className = 'tree-children';
        for (const child of node.children) {
            childrenEl.appendChild(renderTreeNode(child));
        }
        container.appendChild(childrenEl);
    }

    return container;
}

async function selectNodeById(nodeId, skipRoute = false) {
    if (!state.currentProject) return;
    const node = await api.getNode(state.currentProject.id, nodeId);
    selectNode(node, skipRoute);
}

function selectNode(node, skipRoute = false) {
    state.currentNode = node;

    // Update tree selection
    promptTree.querySelectorAll('.tree-item').forEach(el => el.classList.remove('selected'));
    if (node) {
        const container = promptTree.querySelector(`[data-node-id="${node.id}"]`);
        if (container) container.querySelector('.tree-item').classList.add('selected');
    }
    if (!skipRoute) pushRoute();

    if (!node) {
        emptyState.hidden = false;
        editorPanel.hidden = true;
        imagePanel.hidden = true;
        coachingMessages.innerHTML = '';
        return;
    }

    emptyState.hidden = true;
    editorPanel.hidden = false;
    imagePanel.hidden = false;

    // Populate editor
    $('#prompt-text').value = node.prompt_text;
    $('#prompt-notes').value = node.notes || '';
    $('#star-checkbox').checked = node.is_starred;
    renderNodeTags(node.tags || []);

    // Populate image
    if (node.image) {
        const img = $('#image-preview');
        img.src = `/images/${node.project_id}/${node.image.filename}`;
        img.hidden = false;
        $('#dropzone-label').hidden = true;
        $('#delete-image-btn').hidden = false;
        $('#image-feedback').value = node.image.feedback || '';
    } else {
        $('#image-preview').hidden = true;
        $('#dropzone-label').hidden = false;
        $('#delete-image-btn').hidden = true;
        $('#image-feedback').value = '';
    }

    // Load coaching
    loadCoaching();
}

// ===== Editor actions =====
$('#save-prompt-btn').addEventListener('click', async () => {
    if (!state.currentNode) return;
    const data = {
        prompt_text: $('#prompt-text').value,
        notes: $('#prompt-notes').value || null,
        is_starred: $('#star-checkbox').checked,
    };
    await api.updateNode(state.currentProject.id, state.currentNode.id, data);
    toast('Saved');
    await loadTree();
});

$('#copy-prompt-btn').addEventListener('click', () => {
    navigator.clipboard.writeText($('#prompt-text').value);
    toast('Copied to clipboard');
});

$('#iterate-btn').addEventListener('click', () => {
    if (!state.currentNode) return;
    $('#iterate-dialog-title').textContent = 'Iterate';
    $('#iterate-prompt-text').value = state.currentNode.prompt_text;
    $('#iterate-notes').value = '';
    $('#iterate-dialog').showModal();
    $('#iterate-dialog').dataset.mode = 'iterate';
});

$('#fork-btn').addEventListener('click', () => {
    if (!state.currentNode) return;
    $('#iterate-dialog-title').textContent = 'Fork';
    $('#iterate-prompt-text').value = state.currentNode.prompt_text;
    $('#iterate-notes').value = '';
    $('#iterate-dialog').showModal();
    $('#iterate-dialog').dataset.mode = 'fork';
});

$('#iterate-dialog').querySelector('form').addEventListener('submit', async (e) => {
    if (!state.currentNode) return;
    const data = {
        parent_id: state.currentNode.id,
        prompt_text: $('#iterate-prompt-text').value,
        notes: $('#iterate-notes').value || null,
    };
    const newNode = await api.createNode(state.currentProject.id, data);
    await loadTree();
    selectNode(newNode);
});

$('#new-root-btn').addEventListener('click', () => {
    if (!state.currentProject) return;
    $('#iterate-dialog-title').textContent = 'New root prompt';
    $('#iterate-prompt-text').value = '';
    $('#iterate-notes').value = '';
    $('#iterate-dialog').showModal();
    $('#iterate-dialog').dataset.mode = 'root';
});

// Override submit for root mode
const origSubmit = $('#iterate-dialog').querySelector('form');
origSubmit.addEventListener('submit', async function rootHandler(e) {
    if ($('#iterate-dialog').dataset.mode !== 'root') return;
    e.preventDefault();
    const data = {
        prompt_text: $('#iterate-prompt-text').value,
        notes: $('#iterate-notes').value || null,
    };
    const newNode = await api.createNode(state.currentProject.id, data);
    await loadTree();
    selectNode(newNode);
    $('#iterate-dialog').close();
}, true);

// ===== Tags =====
function renderNodeTags(tags) {
    const list = $('#node-tag-list');
    list.innerHTML = '';
    for (const tag of tags) {
        const el = document.createElement('span');
        el.className = 'tag';
        el.innerHTML = `${tag.name} <button class="remove-tag">&times;</button>`;
        el.querySelector('.remove-tag').addEventListener('click', async () => {
            await api.removeNodeTag(state.currentProject.id, state.currentNode.id, tag.name);
            const node = await api.getNode(state.currentProject.id, state.currentNode.id);
            renderNodeTags(node.tags);
        });
        list.appendChild(el);
    }
}

$('#node-tag-input').addEventListener('keydown', async (e) => {
    if (e.key !== 'Enter') return;
    e.preventDefault();
    const name = e.target.value.trim();
    if (!name || !state.currentNode) return;
    await api.addNodeTag(state.currentProject.id, state.currentNode.id, name);
    e.target.value = '';
    const node = await api.getNode(state.currentProject.id, state.currentNode.id);
    renderNodeTags(node.tags);
});

// ===== Image upload =====
const dropzone = $('#image-dropzone');
const imageInput = $('#image-input');

dropzone.addEventListener('click', () => imageInput.click());
dropzone.addEventListener('dragover', (e) => { e.preventDefault(); dropzone.classList.add('dragover'); });
dropzone.addEventListener('dragleave', () => dropzone.classList.remove('dragover'));
dropzone.addEventListener('drop', async (e) => {
    e.preventDefault();
    dropzone.classList.remove('dragover');

    // 1. Local file drag
    if (e.dataTransfer.files.length) {
        handleImageUpload(e.dataTransfer.files[0]);
        return;
    }

    // 2. Image URL drag (e.g. from Discord, browser)
    const html = e.dataTransfer.getData('text/html');
    const uri = e.dataTransfer.getData('text/uri-list');
    const url = extractImageUrl(html) || extractImageUrl(uri);
    if (url) {
        handleImageUrl(url);
        return;
    }

    toast('No image found in drop');
});
imageInput.addEventListener('change', () => {
    if (imageInput.files.length) handleImageUpload(imageInput.files[0]);
});

function extractImageUrl(text) {
    if (!text) return null;
    // Try <img src="...">
    const imgMatch = text.match(/<img[^>]+src=["']([^"']+)["']/i);
    if (imgMatch) return imgMatch[1];
    // Try bare URL ending in image extension or from known CDNs
    const urlMatch = text.match(/(https?:\/\/[^\s<>"']+)/i);
    if (urlMatch) {
        const u = urlMatch[1];
        if (/\.(png|jpe?g|gif|webp)/i.test(u) ||
            u.includes('cdn.discordapp.com') ||
            u.includes('media.discordapp.net')) {
            return u;
        }
    }
    return null;
}

async function handleImageUpload(file) {
    if (!state.currentNode) return;
    await api.uploadImage(state.currentProject.id, state.currentNode.id, file);
    await refreshNodeAndTree();
    toast('Image uploaded');
}

async function handleImageUrl(url) {
    if (!state.currentNode) return;
    const label = $('#dropzone-label');
    label.textContent = 'Downloading...';
    label.hidden = false;
    try {
        await api.uploadImageFromUrl(state.currentProject.id, state.currentNode.id, url);
        await refreshNodeAndTree();
        toast('Image uploaded');
    } catch (err) {
        toast(`Failed to fetch image: ${err.message}`);
        label.textContent = 'Drop image here or click to upload';
    }
}

async function refreshNodeAndTree() {
    const node = await api.getNode(state.currentProject.id, state.currentNode.id);
    state.currentNode = node;
    selectNode(node);
    await loadTree();
}

$('#save-feedback-btn').addEventListener('click', async () => {
    if (!state.currentNode?.image) return;
    await api.updateImageFeedback(state.currentProject.id, state.currentNode.id, $('#image-feedback').value);
    toast('Feedback saved');
});

$('#delete-image-btn').addEventListener('click', async () => {
    if (!state.currentNode?.image) return;
    await api.deleteImage(state.currentProject.id, state.currentNode.id);
    await refreshNodeAndTree();
    toast('Image removed');
});

// ===== Coaching =====
async function loadCoaching() {
    if (!state.currentNode) { coachingMessages.innerHTML = ''; return; }
    const messages = await api.getCoachingHistory(state.currentProject.id, state.currentNode.id);
    state.coachingMessages = messages;
    renderCoachingMessages();
}

function renderCoachingMessages() {
    coachingMessages.innerHTML = '';
    if (!state.coachingMessages.length) {
        coachingMessages.innerHTML = '<div style="color:var(--text-muted);font-size:12px;text-align:center;padding:20px;">Ask for prompt suggestions, or describe what to change after uploading a result.</div>';
        return;
    }
    for (const msg of state.coachingMessages) {
        appendCoachingMessage(msg.role, msg.content);
    }
    coachingMessages.scrollTop = coachingMessages.scrollHeight;
}

function appendCoachingMessage(role, content) {
    const el = document.createElement('div');
    el.className = `coaching-msg ${role}`;
    if (role === 'assistant') {
        el.innerHTML = renderMarkdown(content);
        el.querySelectorAll('pre').forEach(pre => {
            const btn = document.createElement('button');
            btn.className = 'copy-code-btn';
            btn.textContent = 'copy';
            btn.addEventListener('click', () => {
                navigator.clipboard.writeText(pre.textContent.replace('copy', '').trim());
                btn.textContent = 'copied';
                setTimeout(() => btn.textContent = 'copy', 1500);
            });
            pre.style.position = 'relative';
            pre.appendChild(btn);
        });
    } else {
        el.textContent = content;
    }
    coachingMessages.appendChild(el);
    return el;
}

function renderMarkdownStreaming(text) {
    // Lightweight renderer for mid-stream — no block elements, just inline formatting
    return text
        .replace(/```(\w*)\n([\s\S]*?)```/g, (_, lang, code) =>
            `<pre><code>${code.replace(/</g, '&lt;')}</code></pre>`)
        .replace(/`([^`]+)`/g, '<code>$1</code>')
        .replace(/\*\*(.+?)\*\*/g, '<strong>$1</strong>')
        .replace(/^### (.+)$/gm, '<strong style="color:var(--accent);text-transform:uppercase;font-size:11px;letter-spacing:0.04em">$1</strong>')
        .replace(/\n/g, '<br>');
}

function renderMarkdown(text) {
    // Fenced code blocks first (protect from other transforms)
    const codeBlocks = [];
    let html = text.replace(/```(\w*)\n([\s\S]*?)```/g, (_, lang, code) => {
        const idx = codeBlocks.length;
        codeBlocks.push(`<pre><code>${code.replace(/</g, '&lt;')}</code></pre>`);
        return `\x00CODE${idx}\x00`;
    });

    // Process line by line for block elements
    const lines = html.split('\n');
    const out = [];
    let inList = false;

    for (const line of lines) {
        const trimmed = line.trim();

        // Headers
        if (trimmed.startsWith('### ')) {
            if (inList) { out.push('</ul>'); inList = false; }
            out.push(`<h4>${trimmed.slice(4)}</h4>`);
        } else if (trimmed.startsWith('## ')) {
            if (inList) { out.push('</ul>'); inList = false; }
            out.push(`<h3>${trimmed.slice(3)}</h3>`);
        // Horizontal rule
        } else if (/^---+$/.test(trimmed)) {
            if (inList) { out.push('</ul>'); inList = false; }
            out.push('<hr>');
        // Bullet list
        } else if (/^[-*] /.test(trimmed)) {
            if (!inList) { out.push('<ul>'); inList = true; }
            out.push(`<li>${trimmed.slice(2)}</li>`);
        // Empty line
        } else if (trimmed === '') {
            if (inList) { out.push('</ul>'); inList = false; }
            out.push('<br>');
        // Code block placeholder or regular text
        } else {
            if (inList) { out.push('</ul>'); inList = false; }
            out.push(`<p>${trimmed}</p>`);
        }
    }
    if (inList) out.push('</ul>');

    html = out.join('\n');

    // Inline formatting
    html = html
        .replace(/`([^`]+)`/g, '<code>$1</code>')
        .replace(/\*\*(.+?)\*\*/g, '<strong>$1</strong>');

    // Restore code blocks
    html = html.replace(/\x00CODE(\d+)\x00/g, (_, idx) => codeBlocks[parseInt(idx)]);

    return html;
}

$('#coaching-send-btn').addEventListener('click', sendCoaching);
$('#coaching-input').addEventListener('keydown', (e) => {
    if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); sendCoaching(); }
});

async function sendCoaching() {
    const input = $('#coaching-input');
    const sendBtn = $('#coaching-send-btn');
    const message = input.value.trim();
    if (!message || !state.currentNode) return;

    input.value = '';
    input.disabled = true;
    sendBtn.disabled = true;
    sendBtn.textContent = 'Thinking...';

    appendCoachingMessage('user', message);

    const assistantEl = document.createElement('div');
    assistantEl.className = 'coaching-msg assistant coaching-streaming';
    coachingMessages.appendChild(assistantEl);
    coachingMessages.scrollTop = coachingMessages.scrollHeight;

    const cleanup = () => {
        assistantEl.classList.remove('coaching-streaming');
        input.disabled = false;
        sendBtn.disabled = false;
        sendBtn.textContent = 'Send';
        input.focus();
    };

    try {
        const controller = new AbortController();
        const timeout = setTimeout(() => controller.abort(), 120_000);

        const res = await api.streamCoaching(
            state.currentProject.id, state.currentNode.id, message, controller.signal
        );
        clearTimeout(timeout);

        if (!res.ok) {
            const errBody = await res.text();
            throw new Error(`Server error (${res.status}): ${errBody.slice(0, 200)}`);
        }

        const reader = res.body.getReader();
        const decoder = new TextDecoder();
        let fullText = '';

        while (true) {
            const { done, value } = await reader.read();
            if (done) break;
            const chunk = decoder.decode(value, { stream: true });
            const lines = chunk.split('\n');
            for (const line of lines) {
                if (line.startsWith('data: ')) {
                    const raw = line.slice(6);
                    if (raw === '[DONE]') continue;
                    try { fullText += JSON.parse(raw); } catch { fullText += raw; }
                    assistantEl.innerHTML = renderMarkdownStreaming(fullText);
                    coachingMessages.scrollTop = coachingMessages.scrollHeight;
                }
            }
        }

        cleanup();

        if (!fullText.trim()) {
            assistantEl.innerHTML = '<em style="color:var(--text-muted)">No response received. The coach may be unavailable.</em>';
        } else {
            assistantEl.innerHTML = renderMarkdown(fullText);
            addCopyButtons(assistantEl);
        }

        state.coachingMessages.push({ role: 'user', content: message });
        state.coachingMessages.push({ role: 'assistant', content: fullText });
    } catch (err) {
        cleanup();
        const msg = err.name === 'AbortError'
            ? 'Request timed out. The coach took too long to respond.'
            : err.message;
        assistantEl.innerHTML = `<em style="color:var(--danger)">${msg}</em>`;
    }
}

function addCopyButtons(container) {
    container.querySelectorAll('pre').forEach(pre => {
        const btn = document.createElement('button');
        btn.className = 'copy-code-btn';
        btn.textContent = 'copy';
        btn.addEventListener('click', () => {
            navigator.clipboard.writeText(pre.textContent.replace('copy', '').trim());
            btn.textContent = 'copied';
            setTimeout(() => btn.textContent = 'copy', 1500);
        });
        pre.style.position = 'relative';
        pre.appendChild(btn);
    });
}

$('#clear-coaching-btn').addEventListener('click', () => {
    state.coachingMessages = [];
    renderCoachingMessages();
});

// ===== Star toggle =====
$('#star-checkbox').addEventListener('change', async () => {
    if (!state.currentNode) return;
    await api.updateNode(state.currentProject.id, state.currentNode.id, {
        is_starred: $('#star-checkbox').checked,
    });
    await loadTree();
});

// ===== Init =====
navigateToRoute();
