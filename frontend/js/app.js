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
            $('#delete-project-btn').disabled = false;
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

// ===== Type-to-confirm dialog =====
// Returns a promise that resolves true if the user types `expected` and
// clicks Delete, false if they cancel or close the dialog any other way.
function confirmDelete({ title, message, expected }) {
    return new Promise((resolve) => {
        const dialog = $('#confirm-delete-dialog');
        const input = $('#confirm-delete-input');
        const confirmBtn = $('#confirm-delete-confirm');
        const cancelBtn = $('#confirm-delete-cancel');

        $('#confirm-delete-title').textContent = title;
        $('#confirm-delete-message').textContent = message;
        $('#confirm-delete-expected').textContent = expected;
        input.value = '';
        confirmBtn.disabled = true;

        const onInput = () => {
            confirmBtn.disabled = input.value !== expected;
        };
        const cleanup = (result) => {
            input.removeEventListener('input', onInput);
            confirmBtn.removeEventListener('click', onConfirm);
            cancelBtn.removeEventListener('click', onCancel);
            dialog.removeEventListener('close', onCancel);
            dialog.close();
            resolve(result);
        };
        const onConfirm = () => {
            if (input.value === expected) cleanup(true);
        };
        const onCancel = () => cleanup(false);

        input.addEventListener('input', onInput);
        confirmBtn.addEventListener('click', onConfirm);
        cancelBtn.addEventListener('click', onCancel);
        dialog.addEventListener('close', onCancel);

        dialog.showModal();
        // Focus the input after the dialog is fully open
        setTimeout(() => input.focus(), 0);
    });
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
    $('#delete-project-btn').disabled = !state.currentProject;
}

projectSelect.addEventListener('change', async () => {
    const id = projectSelect.value;
    if (!id) {
        state.currentProject = null;
        state.tree = [];
        renderTree();
        selectNode(null);
        $('#delete-project-btn').disabled = true;
        pushRoute();
        return;
    }
    state.currentProject = await api.getProject(id);
    await loadTree();
    selectNode(null);
    $('#new-root-btn').disabled = false;
    $('#delete-project-btn').disabled = false;
    pushRoute();
});

$('#delete-project-btn').addEventListener('click', async () => {
    if (!state.currentProject) return;
    const project = state.currentProject;
    const nodeCount = countAllNodes(state.tree);
    const suffix = nodeCount > 0
        ? ` This will permanently delete ${nodeCount} prompt node${nodeCount === 1 ? '' : 's'}, all attached images, and all coaching history.`
        : '';
    const ok = await confirmDelete({
        title: 'Delete project',
        message: `You are about to delete the project "${project.name}".${suffix} This cannot be undone.`,
        expected: project.name,
    });
    if (!ok) return;

    try {
        await api.deleteProject(project.id);
    } catch (err) {
        toast(`Delete failed: ${err.message}`);
        return;
    }
    state.currentProject = null;
    state.tree = [];
    state.currentNode = null;
    renderTree();
    selectNode(null);
    await loadProjects();
    projectSelect.value = '';
    $('#new-root-btn').disabled = true;
    $('#delete-project-btn').disabled = true;
    history.pushState(null, '', location.pathname);
    toast('Project deleted');
});

function countAllNodes(treeNodes) {
    let n = 0;
    for (const node of treeNodes) {
        n += 1 + countAllNodes(node.children || []);
    }
    return n;
}

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

    // Prompt text — show name if set, otherwise truncated prompt
    const text = document.createElement('span');
    text.className = 'node-text';
    text.textContent = node.name || (node.prompt_text.length > 60 ? node.prompt_text.slice(0, 60) + '…' : node.prompt_text);
    text.title = node.name ? `${node.name}\n${node.prompt_text}` : node.prompt_text;
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
    $('#prompt-name').value = node.name || '';
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
        $('#image-description').value = node.image.description || '';
        $('#image-description').placeholder = node.image.description
            ? ''
            : 'Generating description... (refresh to check)';
        maybePollDescription(node);
    } else {
        $('#image-preview').hidden = true;
        $('#dropzone-label').hidden = false;
        $('#delete-image-btn').hidden = true;
        $('#image-feedback').value = '';
        $('#image-description').value = '';
        $('#image-description').placeholder = '';
    }

    // Load coaching
    loadCoaching();
}

// ===== Editor actions =====
$('#save-prompt-btn').addEventListener('click', async () => {
    if (!state.currentNode) return;
    const data = {
        name: $('#prompt-name').value || null,
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

function countDescendants(treeNode) {
    let n = 0;
    for (const child of treeNode.children || []) {
        n += 1 + countDescendants(child);
    }
    return n;
}

function findInTree(nodes, id) {
    for (const n of nodes) {
        if (n.id === id) return n;
        const hit = findInTree(n.children || [], id);
        if (hit) return hit;
    }
    return null;
}

$('#delete-node-btn').addEventListener('click', async () => {
    if (!state.currentNode) return;
    const node = state.currentNode;
    const treeNode = findInTree(state.tree, node.id);
    const descendants = treeNode ? countDescendants(treeNode) : 0;
    const label = node.name || (node.prompt_text.length > 50 ? node.prompt_text.slice(0, 50) + '…' : node.prompt_text);
    const childPart = descendants > 0
        ? ` along with ${descendants} child node${descendants === 1 ? '' : 's'}`
        : '';
    // Type the node name if it has one. Otherwise fall back to typing the
    // word "delete" — the prompt text is too long/awkward to retype.
    const expected = node.name || 'delete';
    const ok = await confirmDelete({
        title: 'Delete node',
        message: `You are about to delete "${label}"${childPart}. This will also remove any attached images and coaching history. This cannot be undone.`,
        expected,
    });
    if (!ok) return;

    const parentId = node.parent_id;
    try {
        await api.deleteNode(state.currentProject.id, node.id);
    } catch (err) {
        toast(`Delete failed: ${err.message}`);
        return;
    }
    await loadTree();
    if (parentId) {
        await selectNodeById(parentId);
    } else {
        selectNode(null);
    }
    toast('Deleted');
});

$('#iterate-btn').addEventListener('click', () => {
    if (!state.currentNode) return;
    $('#iterate-dialog-title').textContent = 'Iterate';
    $('#iterate-name').value = '';
    $('#iterate-prompt-text').value = state.currentNode.prompt_text;
    $('#iterate-notes').value = '';
    resetRootImageUi();
    $('#root-image-section').hidden = false;
    $('#iterate-dialog').showModal();
    $('#iterate-dialog').dataset.mode = 'iterate';
});

$('#fork-btn').addEventListener('click', () => {
    if (!state.currentNode) return;
    $('#iterate-dialog-title').textContent = 'Fork';
    $('#iterate-name').value = '';
    $('#iterate-prompt-text').value = state.currentNode.prompt_text;
    $('#iterate-notes').value = '';
    resetRootImageUi();
    $('#root-image-section').hidden = false;
    $('#iterate-dialog').showModal();
    $('#iterate-dialog').dataset.mode = 'fork';
});

$('#iterate-dialog').querySelector('form').addEventListener('submit', async (e) => {
    // Root mode is handled by its own capture-phase listener.
    if ($('#iterate-dialog').dataset.mode === 'root') return;
    if (!state.currentNode) return;
    const data = {
        parent_id: state.currentNode.id,
        name: $('#iterate-name').value || null,
        prompt_text: $('#iterate-prompt-text').value,
        notes: $('#iterate-notes').value || null,
    };
    const newNode = await api.createNode(state.currentProject.id, data);
    if (stagedRootImage) {
        try {
            await api.uploadImage(state.currentProject.id, newNode.id, stagedRootImage);
        } catch (err) {
            toast(`Image upload failed: ${err.message}`);
        }
    }
    resetRootImageUi();
    await loadTree();
    // Refetch so the freshly-attached image is present in state
    const hydrated = await api.getNode(state.currentProject.id, newNode.id);
    selectNode(hydrated);
});

// Staged image for the "new root prompt" dialog. Captured via drop/paste/
// picker while the dialog is open, uploaded to the new node on submit.
let stagedRootImage = null;

function resetRootImageUi() {
    stagedRootImage = null;
    $('#root-image-section').hidden = true;
    $('#root-image-preview').hidden = true;
    $('#root-image-preview').src = '';
    $('#root-image-label').hidden = false;
    $('#root-image-label').textContent = 'Drop, paste, or click to attach an image (optional)';
    $('#root-image-clear').hidden = true;
    $('#root-image-input').value = '';
}

function stageRootImage(file) {
    stagedRootImage = file;
    const preview = $('#root-image-preview');
    preview.src = URL.createObjectURL(file);
    preview.hidden = false;
    $('#root-image-label').hidden = true;
    $('#root-image-clear').hidden = false;
}

$('#new-root-btn').addEventListener('click', () => {
    if (!state.currentProject) return;
    $('#iterate-dialog-title').textContent = 'New root prompt';
    $('#iterate-name').value = '';
    $('#iterate-prompt-text').value = '';
    $('#iterate-notes').value = '';
    resetRootImageUi();
    $('#root-image-section').hidden = false;
    $('#iterate-dialog').showModal();
    $('#iterate-dialog').dataset.mode = 'root';
});

const rootDropzone = $('#root-image-dropzone');
const rootImageInput = $('#root-image-input');

rootDropzone.addEventListener('click', () => rootImageInput.click());
rootDropzone.addEventListener('dragover', (e) => { e.preventDefault(); rootDropzone.classList.add('dragover'); });
rootDropzone.addEventListener('dragleave', () => rootDropzone.classList.remove('dragover'));
rootDropzone.addEventListener('drop', (e) => {
    e.preventDefault();
    rootDropzone.classList.remove('dragover');
    const file = e.dataTransfer.files[0];
    if (file && file.type.startsWith('image/')) stageRootImage(file);
});
rootImageInput.addEventListener('change', () => {
    if (rootImageInput.files[0]) stageRootImage(rootImageInput.files[0]);
});
$('#root-image-clear').addEventListener('click', () => resetRootImageUi());

// Paste handler scoped to the create dialog (root / iterate / fork).
// Whenever the dialog is open and the clipboard holds an image, stage
// it for the new node and stop propagation so the global paste handler
// doesn't upload it to the parent (currently selected) node by mistake.
// Text paste is left untouched so the textareas still work normally.
document.addEventListener('paste', (e) => {
    const dialog = $('#iterate-dialog');
    if (!dialog.open) return;

    let imageFile = null;
    for (const item of e.clipboardData?.items || []) {
        if (item.type.startsWith('image/')) {
            imageFile = item.getAsFile();
            break;
        }
    }
    if (!imageFile) return;

    e.preventDefault();
    e.stopPropagation();
    stageRootImage(imageFile);
}, true);

// Override submit for root mode
const origSubmit = $('#iterate-dialog').querySelector('form');
origSubmit.addEventListener('submit', async function rootHandler(e) {
    if ($('#iterate-dialog').dataset.mode !== 'root') return;
    e.preventDefault();
    const data = {
        name: $('#iterate-name').value || null,
        prompt_text: $('#iterate-prompt-text').value,
        notes: $('#iterate-notes').value || null,
    };
    const newNode = await api.createNode(state.currentProject.id, data);
    if (stagedRootImage) {
        try {
            await api.uploadImage(state.currentProject.id, newNode.id, stagedRootImage);
        } catch (err) {
            toast(`Image upload failed: ${err.message}`);
        }
    }
    resetRootImageUi();
    await loadTree();
    // Refetch the node so the freshly-attached image is present in state
    const hydrated = await api.getNode(state.currentProject.id, newNode.id);
    selectNode(hydrated);
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

    // 1. Local file or browser-provided image blob
    if (e.dataTransfer.files.length) {
        handleImageUpload(e.dataTransfer.files[0]);
        return;
    }

    // 2. Check dataTransfer.items for inline image data
    for (const item of e.dataTransfer.items || []) {
        if (item.type.startsWith('image/')) {
            const file = item.getAsFile();
            if (file) { handleImageUpload(file); return; }
        }
    }

    // 3. Image URL from HTML or URI drag data
    const html = e.dataTransfer.getData('text/html');
    const uri = e.dataTransfer.getData('text/uri-list');
    const plain = e.dataTransfer.getData('text/plain');
    const url = extractImageUrl(html) || extractImageUrl(uri) || extractImageUrl(plain);
    if (url) {
        handleImageUrl(url);
        return;
    }

    // Debug: show what we received so user can report
    const types = [...(e.dataTransfer.types || [])];
    console.log('Drop data types:', types);
    types.forEach(t => console.log(`  ${t}:`, e.dataTransfer.getData(t)?.slice(0, 200)));
    toast('No image found — try copy/paste instead (Cmd+V)');
});

// Paste support: copy image in Discord → Cmd+V here
document.addEventListener('paste', (e) => {
    if (!state.currentNode) return;
    const cb = e.clipboardData;
    if (!cb) return;

    // 1. Raw image data (right-click → Copy Image on some platforms)
    for (const item of cb.items || []) {
        if (item.type.startsWith('image/')) {
            e.preventDefault();
            const file = item.getAsFile();
            if (file) handleImageUpload(file);
            return;
        }
    }

    // 2. HTML with <img> tag or URL pointing to an image
    const html = cb.getData('text/html');
    const plain = cb.getData('text/plain');
    const url = extractImageUrl(html) || extractImageUrl(plain);
    if (url) {
        // Don't hijack paste if user is typing in a text field and it's not an image URL
        const active = document.activeElement?.tagName;
        if (active === 'TEXTAREA' || active === 'INPUT') {
            // Only hijack if it really looks like an image URL
            if (!/\.(png|jpe?g|gif|webp)/i.test(url) &&
                !url.includes('cdn.discordapp.com') &&
                !url.includes('media.discordapp.net')) {
                return;
            }
        }
        e.preventDefault();
        handleImageUrl(url);
        return;
    }

    // Debug: log what's in the clipboard
    const types = [...(cb.types || [])];
    console.log('Paste data types:', types);
    types.forEach(t => console.log(`  ${t}:`, cb.getData(t)?.slice(0, 300)));
});
imageInput.addEventListener('change', () => {
    if (imageInput.files.length) handleImageUpload(imageInput.files[0]);
});

function extractImageUrl(text) {
    if (!text) return null;
    // Decode HTML entities (Discord drag data uses &amp; etc.)
    const decode = (s) => {
        const el = document.createElement('textarea');
        el.innerHTML = s;
        return el.value;
    };
    // Try <img src="...">
    const imgMatch = text.match(/<img[^>]+src=["']([^"']+)["']/i);
    if (imgMatch) return decode(imgMatch[1]);
    // Try bare URL ending in image extension or from known CDNs
    const urlMatch = text.match(/(https?:\/\/[^\s<>"']+)/i);
    if (urlMatch) {
        const u = decode(urlMatch[1]);
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

// ===== Description polling =====
// Descriptions are generated in the background after upload. Poll the
// image endpoint until it populates, then update the textarea — unless the
// user navigated away or has started typing their own edit.
let descriptionPollToken = 0;
async function maybePollDescription(node) {
    const token = ++descriptionPollToken;
    if (!node.image || node.image.description) return;

    const pid = node.project_id;
    const nid = node.id;
    const deadline = Date.now() + 120_000; // give up after 2 minutes

    while (token === descriptionPollToken && Date.now() < deadline) {
        await new Promise(r => setTimeout(r, 3000));
        if (token !== descriptionPollToken) return;
        if (!state.currentNode || state.currentNode.id !== nid) return;

        let fresh;
        try {
            fresh = await api.getImage(pid, nid);
        } catch {
            return;
        }
        if (!fresh?.description) continue;

        // Merge into state + UI only if the user hasn't typed over it
        const ta = $('#image-description');
        if (ta.value.trim() === '') {
            ta.value = fresh.description;
            ta.placeholder = '';
        }
        if (state.currentNode && state.currentNode.id === nid) {
            state.currentNode.image = fresh;
        }
        return;
    }
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
        label.textContent = 'Drop, paste, or click to upload';
    }
}

async function refreshNodeAndTree() {
    const node = await api.getNode(state.currentProject.id, state.currentNode.id);
    state.currentNode = node;
    selectNode(node);
    await loadTree();
}

$('#save-image-btn').addEventListener('click', async () => {
    if (!state.currentNode?.image) return;
    await api.updateImage(state.currentProject.id, state.currentNode.id, {
        feedback: $('#image-feedback').value,
        description: $('#image-description').value,
    });
    // Editing the description should cancel any in-flight auto-description
    // poll so the background task doesn't clobber the user's edits.
    descriptionPollToken++;
    toast('Saved');
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
