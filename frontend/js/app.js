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

    // Show/hide the generation status pill based on whether the
    // in-flight generation (if any) belongs to this node.
    renderGenerationStatus();

    // Populate editor
    $('#prompt-name').value = node.name || '';
    $('#prompt-text').value = node.prompt_text;
    $('#prompt-notes').value = node.notes || '';
    $('#star-checkbox').checked = node.is_starred;
    renderNodeTags(node.tags || []);

    // Populate image
    if (node.image) {
        const img = $('#image-preview');
        // Force the browser to drop any cached bitmap for this element by
        // clearing src first, then setting the new (cache-busted) URL.
        // Belt-and-suspenders: the /images mount also sends no-cache headers.
        img.src = '';
        img.src = `/images/${node.project_id}/${node.image.filename}?t=${Date.now()}`;
        img.hidden = false;
        $('#image-dropzone').classList.add('has-image');
        $('#dropzone-label').hidden = true;
        $('#delete-image-btn').hidden = false;
        $('#replace-image-btn').hidden = false;
        $('#image-feedback').value = node.image.feedback || '';
        $('#image-description').value = node.image.description || '';
        $('#image-description').placeholder = node.image.description
            ? ''
            : 'Generating description...';
        $('#regen-description-btn').hidden = false;
        $('#regen-description-btn').disabled = false;
        $('#regen-description-btn').textContent = node.image.description ? 'Regenerate' : 'Generate';
        maybePollDescription(node);
    } else {
        $('#image-preview').hidden = true;
        $('#image-dropzone').classList.remove('has-image');
        $('#dropzone-label').hidden = false;
        $('#delete-image-btn').hidden = true;
        $('#replace-image-btn').hidden = true;
        $('#image-feedback').value = '';
        $('#image-description').value = '';
        $('#image-description').placeholder = '';
        $('#regen-description-btn').hidden = true;
    }
    // Quadrant strip only applies to MJ grids — uploaded images and
    // already-upscaled images don't have U buttons on Discord. Rendered
    // here so it picks up any freshly-loaded tree state.
    renderQuadrantStrip();

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

// Pulls the fenced code block from the most recent assistant coaching
// message. Coach output always ends with a single ``` block under
// "### Try this" — that's the refined prompt we want to prefill.
// Returns null if there's no assistant message yet or no code block.
function extractCoachSuggestedPrompt() {
    const msgs = state.coachingMessages || [];
    for (let i = msgs.length - 1; i >= 0; i--) {
        if (msgs[i].role !== 'assistant') continue;
        const content = msgs[i].content || '';
        const re = /```[^\n]*\n([\s\S]*?)```/g;
        let match;
        let last = null;
        while ((match = re.exec(content)) !== null) last = match[1];
        return last ? last.trim() : null;
    }
    return null;
}

// Always show the ref selector. If the source node has a live MJ image we
// can auto-fetch its CDN URL on submit; otherwise the user must paste a URL
// into the ref-url input.
function configureIterateRefSelector() {
    const section = $('#iterate-ref-section');
    section.hidden = false;
    const img = state.currentNode?.image;
    const mjKinds = ['grid', 'upscale', 'variation'];
    const canAutoRef = img && mjKinds.includes(img.kind)
        && img.discord_message_id && img.discord_channel_id;
    const label = $('#iterate-ref-label-text');
    label.textContent = canAutoRef
        ? "Use this node's image as"
        : 'Reference image (paste URL below)';
    // Always reset selection + URL input when opening the dialog.
    const none = section.querySelector('input[value=""]');
    if (none) none.checked = true;
    const urlInput = $('#iterate-ref-url');
    urlInput.value = '';
    urlInput.hidden = true;
}

// Strip any existing ref flags (--oref, --cref, --sref) and their
// URL/value arguments from a prompt so they don't stack up.
function stripRefFlags(prompt) {
    return (prompt || '').replace(/--[ocs]ref\s+\S+\s*/gi, '').trim();
}

function stripNijiFromPrompt(prompt) {
    return (prompt || '').replace(/--niji(?:\s+\d+(?:\.\d+)?)?\s*/gi, '').trim();
}

function stripModelFlags(prompt) {
    return (prompt || '')
        .replace(/--niji(?:\s+\d+(?:\.\d+)?)?\s*/gi, '')
        .replace(/--(?:v|version)(?:\s+\d+(?:\.\d+)?)?\s*/gi, '')
        .trim();
}

// Prepares a prompt for the selected ref type: strips conflicting
// flags and adds required model flags. Called live on radio change
// (so the user sees the cleanup in the textarea) and again on submit.
// Returns { prompt, flag } where flag is the MJ param to append.
function preparePromptForRef(prompt, refChoice) {
    if (!refChoice) return { prompt, flag: null };

    // Always strip old ref flags — don't stack --cref + --oref etc.
    let clean = stripRefFlags(prompt);

    if (refChoice === 'niji6-cref') {
        clean = stripModelFlags(clean);
        clean = `${clean} --niji 6`;
        return { prompt: clean, flag: '--cref' };
    }

    if (refChoice === '--oref') {
        // Omni ref needs V7/V8, not niji.
        clean = stripNijiFromPrompt(clean);
        if (!/--(?:v|version)\s/i.test(clean)) {
            clean = `${clean} --v 7`;
        }
        return { prompt: clean, flag: '--oref' };
    }

    if (refChoice === '--cref') {
        // Legacy cref — works on V6 / Niji 6. Strip niji 7+.
        const nijiMatch = /--niji(?:\s+(\d+(?:\.\d+)?))?/i.exec(clean);
        if (nijiMatch) {
            const ver = nijiMatch[1] ? parseFloat(nijiMatch[1]) : 7;
            if (ver >= 7) clean = stripNijiFromPrompt(clean);
        }
        return { prompt: clean, flag: '--cref' };
    }

    if (refChoice === '--sref') {
        // Style ref works everywhere — just clean old refs.
        return { prompt: clean, flag: '--sref' };
    }

    return { prompt, flag: null };
}

// On radio change, auto-clean the prompt textarea so the user sees
// exactly what will be submitted. URL is appended at submit time.
$('#iterate-ref-section').addEventListener('change', (e) => {
    if (e.target.name !== 'iterate-ref') return;
    const checked = $('#iterate-ref-section').querySelector(
        'input[name="iterate-ref"]:checked'
    );
    const urlInput = $('#iterate-ref-url');
    urlInput.hidden = !checked?.value;
    if (!checked?.value) return;
    const el = $('#iterate-prompt-text');
    const { prompt } = preparePromptForRef(el.value, checked.value);
    el.value = prompt;
});

$('#iterate-btn').addEventListener('click', () => {
    if (!state.currentNode) return;
    $('#iterate-dialog-title').textContent = 'Iterate';
    $('#iterate-name').value = '';
    // Prefer the coach's most recent suggested prompt when the user
    // has been chatting with the coach — that's usually what they want
    // to iterate on, not the raw parent prompt.
    const suggested = extractCoachSuggestedPrompt();
    $('#iterate-prompt-text').value = suggested || state.currentNode.prompt_text;
    $('#iterate-notes').value = '';
    resetRootImageUi();
    $('#root-image-section').hidden = false;
    configureIterateRefSelector();
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
    configureIterateRefSelector();
    $('#iterate-dialog').showModal();
    $('#iterate-dialog').dataset.mode = 'fork';
});

$('#iterate-dialog').querySelector('form').addEventListener('submit', async (e) => {
    // Root mode is handled by its own capture-phase listener.
    if ($('#iterate-dialog').dataset.mode === 'root') return;
    if (!state.currentNode) return;

    let promptText = $('#iterate-prompt-text').value;

    // Handle image-reference options. preparePromptForRef strips
    // conflicting flags and adds required model flags. We then
    // fetch the live CDN URL and append the ref param.
    const refChoice = $('#iterate-ref-section').querySelector(
        'input[name="iterate-ref"]:checked'
    )?.value;

    if (refChoice) {
        const { prompt: prepared, flag } = preparePromptForRef(promptText, refChoice);
        promptText = prepared;
        if (flag) {
            const pastedUrl = $('#iterate-ref-url').value.trim();
            let url = pastedUrl;
            if (!url) {
                try {
                    ({ url } = await api.getImageCdnUrl(
                        state.currentProject.id, state.currentNode.id
                    ));
                } catch (err) {
                    toast(`Paste a reference URL or pick a node with an MJ image: ${err.message}`);
                    return;
                }
            }
            promptText = `${promptText.trim()} ${flag} ${url}`;
        }
    }

    const data = {
        parent_id: state.currentNode.id,
        name: $('#iterate-name').value || null,
        prompt_text: promptText,
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
    // No source node, so the user must paste a ref URL if they pick one.
    const section = $('#iterate-ref-section');
    section.hidden = false;
    $('#iterate-ref-label-text').textContent = 'Reference image (paste URL below)';
    section.querySelector('input[value=""]').checked = true;
    const urlInput = $('#iterate-ref-url');
    urlInput.value = '';
    urlInput.hidden = true;
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

    let promptText = $('#iterate-prompt-text').value;
    const refChoice = $('#iterate-ref-section').querySelector(
        'input[name="iterate-ref"]:checked'
    )?.value;
    if (refChoice) {
        const { prompt: prepared, flag } = preparePromptForRef(promptText, refChoice);
        promptText = prepared;
        if (flag) {
            const url = $('#iterate-ref-url').value.trim();
            if (!url) {
                toast('Paste a reference URL or set the ref to None');
                return;
            }
            promptText = `${promptText.trim()} ${flag} ${url}`;
        }
    }

    const data = {
        name: $('#iterate-name').value || null,
        prompt_text: promptText,
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

// Click behavior depends on whether the dropzone has an image yet:
//  - empty: click anywhere → file picker
//  - filled: click → enlarge in the lightbox. Replace via the explicit
//    Replace button, or via drag/drop/paste as before.
dropzone.addEventListener('click', () => {
    const img = $('#image-preview');
    if (!img.hidden && img.src) {
        openLightbox(img.src);
    } else {
        imageInput.click();
    }
});

$('#replace-image-btn').addEventListener('click', (e) => {
    e.stopPropagation();
    imageInput.click();
});
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

$('#regen-description-btn').addEventListener('click', async () => {
    if (!state.currentNode?.image) return;
    const pid = state.currentProject.id;
    const nid = state.currentNode.id;
    const btn = $('#regen-description-btn');
    const ta = $('#image-description');

    // Cancel any in-flight auto-poll so it can't race with our manual call.
    descriptionPollToken++;

    btn.disabled = true;
    btn.textContent = 'Generating...';
    const prevPlaceholder = ta.placeholder;
    ta.placeholder = 'Generating description... (this can take ~30s)';

    try {
        const fresh = await api.describeImage(pid, nid);
        // Only update UI if user is still on this node
        if (state.currentNode?.id === nid) {
            ta.value = fresh.description || '';
            ta.placeholder = '';
            state.currentNode.image = fresh;
            btn.textContent = 'Regenerate';
        }
        toast('Description generated');
    } catch (err) {
        ta.placeholder = prevPlaceholder;
        btn.textContent = state.currentNode?.image?.description ? 'Regenerate' : 'Generate';
        toast(`Failed: ${err.message}`);
    } finally {
        btn.disabled = false;
    }
});

// ===== Lightbox (click an image to enlarge) =====
const lightbox = $('#lightbox');
const lightboxImg = $('#lightbox-img');

function openLightbox(src) {
    lightboxImg.src = src;
    lightbox.hidden = false;
    document.body.style.overflow = 'hidden';
}

function closeLightbox() {
    lightbox.hidden = true;
    lightboxImg.src = '';
    document.body.style.overflow = '';
}

$('#lightbox-close').addEventListener('click', closeLightbox);
lightbox.addEventListener('click', (e) => {
    if (e.target === lightbox) closeLightbox();
});
document.addEventListener('keydown', (e) => {
    if (lightbox.hidden) return;
    if (e.key === 'Escape') closeLightbox();
});

// ===== Midjourney generation =====
// Streams /generate SSE events and updates the status pill + image area
// in place. The status pill is scoped to the node the generation was
// started on — switching to a different node hides it, switching back
// restores it.
let generationInFlight = false;
let generatingNodeId = null;
let lastGenerationStatus = null;

function setGenerationStatus(payload = {}) {
    const { text } = payload;
    lastGenerationStatus = text
        ? { ...payload, nodeId: generatingNodeId }
        : null;
    renderGenerationStatus();
}

function renderGenerationStatus() {
    const el = $('#generation-status');
    const s = lastGenerationStatus;
    const forThisNode = s && state.currentNode && state.currentNode.id === s.nodeId;
    if (!forThisNode) { el.hidden = true; el.innerHTML = ''; return; }
    el.hidden = false;
    el.classList.toggle('error', !!s.error);
    const bar = (s.progress != null && !s.error)
        ? `<div class="gen-bar"><div class="gen-bar-fill" style="width:${s.progress}%"></div></div>`
        : '';
    el.innerHTML = `<span class="gen-dot"></span><span>${s.text}</span>${bar}`;
}

function clearGenerationStatusSoon() {
    setTimeout(() => {
        if (!generationInFlight) setGenerationStatus({});
    }, 2500);
}

async function runGeneration() {
    if (generationInFlight) return;
    if (!state.currentNode) return;
    const pid = state.currentProject.id;
    const nid = state.currentNode.id;
    const promptText = ($('#prompt-text').value || '').trim();
    if (!promptText) {
        toast('Add some prompt text first');
        return;
    }

    const btn = $('#generate-btn');
    generationInFlight = true;
    generatingNodeId = nid;
    btn.disabled = true;
    btn.textContent = 'Generating...';
    setGenerationStatus({ text: 'Submitting to Midjourney...' });

    const controller = new AbortController();
    const timeout = setTimeout(() => controller.abort(), 360_000); // 6min hard cap

    const cleanup = () => {
        generationInFlight = false;
        btn.disabled = false;
        btn.textContent = 'Generate';
        clearTimeout(timeout);
        renderQuadrantStrip();
    };

    try {
        const res = await api.streamGeneration(pid, nid, controller.signal);

        if (res.status === 503) {
            setGenerationStatus({
                text: 'Midjourney is not enabled on the server',
                error: true,
            });
            cleanup();
            return;
        }
        if (!res.ok) {
            const errBody = await res.text();
            throw new Error(`Server error (${res.status}): ${errBody.slice(0, 200)}`);
        }

        const reader = res.body.getReader();
        const decoder = new TextDecoder();
        let buf = '';

        while (true) {
            const { done, value } = await reader.read();
            if (done) break;
            buf += decoder.decode(value, { stream: true });
            const lines = buf.split('\n');
            buf = lines.pop(); // last line may be incomplete

            for (const line of lines) {
                if (!line.startsWith('data: ')) continue;
                const raw = line.slice(6);
                if (raw === '[DONE]') continue;

                let event;
                try { event = JSON.parse(raw); } catch { continue; }

                // Status pill is node-scoped — setGenerationStatus only
                // renders when the user is viewing the generating node.
                // But selectNode() navigation still needs the guard.
                const stillHere = state.currentNode && state.currentNode.id === nid;

                if (event.type === 'queued') {
                    setGenerationStatus({ text: 'Queued — waiting for Midjourney...' });
                } else if (event.type === 'progress') {
                    const pct = event.progress ?? 0;
                    setGenerationStatus({ text: `Generating ${pct}%`, progress: pct });
                } else if (event.type === 'done') {
                    // The image is already saved server-side. Refresh the
                    // node to pick up the new Image row + filename.
                    const fresh = await api.getNode(pid, nid);
                    if (stillHere) {
                        state.currentNode = fresh;
                        selectNode(fresh, true);
                        toast('Image generated');
                    }
                    setGenerationStatus({ text: 'Done', progress: 100 });
                    clearGenerationStatusSoon();
                    await loadTree();
                } else if (event.type === 'error') {
                    setGenerationStatus({
                        text: event.message || 'Generation failed',
                        error: true,
                    });
                }
            }
        }
    } catch (err) {
        const msg = err.name === 'AbortError'
            ? 'Generation timed out'
            : err.message;
        setGenerationStatus({ text: msg, error: true });
    } finally {
        cleanup();
    }
}

$('#generate-btn').addEventListener('click', runGeneration);

// ===== Quadrant strip + upscale =====
// Renders two stacked rows under a grid's image:
//   1. Thumbnails of completed upscale children (U1..U4 in order).
//   2. A compact "Upscale more" action row with buttons only for the
//      quadrants that don't have an upscale yet.
// Either row hides when empty — a brand-new grid shows only row 2
// (labelled "Upscale:"); a fully-upscaled grid shows only row 1.
function renderQuadrantStrip() {
    const wrap = $('#quadrant-actions');
    const thumbsRow = $('#upscale-thumbs');
    const actionsRow = $('#upscale-actions');

    const node = state.currentNode;
    // Both grids and variation results are 2x2 — show quadrant actions for both.
    const isGrid = node?.image && (node.image.kind === 'grid' || node.image.kind === 'variation');
    if (!isGrid) {
        wrap.hidden = true;
        return;
    }
    wrap.hidden = false;

    // Look up children by name (`U1`..`U4`, `V1`..`V4`) in the loaded tree.
    const treeNode = findInTree(state.tree, node.id);
    const byName = {};
    for (const c of (treeNode?.children || [])) {
        if (c.name && /^[UV][1-4]$/.test(c.name)) byName[c.name] = c;
    }

    // --- Thumbnails row: completed upscales + variations ---
    thumbsRow.innerHTML = '';
    let thumbCount = 0;
    for (const prefix of ['U', 'V']) {
        for (let q = 1; q <= 4; q++) {
            const name = `${prefix}${q}`;
            const child = byName[name];
            if (!child || !child.image_filename) continue;
            const cell = document.createElement('button');
            cell.className = 'quadrant-cell filled';
            cell.title = `Open ${name}`;
            cell.style.backgroundImage =
                `url('/images/${node.project_id}/${child.image_filename}')`;
            const badge = document.createElement('span');
            badge.className = 'quadrant-cell-label';
            badge.textContent = name;
            cell.appendChild(badge);
            cell.addEventListener('click', () => selectNodeById(child.id));
            thumbsRow.appendChild(cell);
            thumbCount++;
        }
    }
    thumbsRow.hidden = thumbCount === 0;

    // --- Action rows: upscale + vary for remaining quadrants ---
    actionsRow.innerHTML = '';
    const remainingU = [];
    const remainingV = [];
    for (let q = 1; q <= 4; q++) {
        if (!byName[`U${q}`]) remainingU.push(q);
        if (!byName[`V${q}`]) remainingV.push(q);
    }
    const hasActions = remainingU.length > 0 || remainingV.length > 0;
    actionsRow.hidden = !hasActions;

    if (remainingU.length > 0) {
        const row = document.createElement('div');
        row.className = 'action-btn-row';
        const label = document.createElement('label');
        label.className = 'quadrant-row-label';
        label.textContent = 'Upscale:';
        row.appendChild(label);
        for (const q of remainingU) {
            const btn = document.createElement('button');
            btn.className = 'btn-sm upscale-btn';
            btn.textContent = `U${q}`;
            btn.title = `Upscale U${q}`;
            if (generationInFlight) btn.disabled = true;
            btn.addEventListener('click', () => runUpscale(q));
            row.appendChild(btn);
        }
        actionsRow.appendChild(row);
    }

    if (remainingV.length > 0) {
        const row = document.createElement('div');
        row.className = 'action-btn-row';
        const label = document.createElement('label');
        label.className = 'quadrant-row-label';
        label.textContent = 'Vary:';
        row.appendChild(label);
        for (const q of remainingV) {
            const btn = document.createElement('button');
            btn.className = 'btn-sm upscale-btn';
            btn.textContent = `V${q}`;
            btn.title = `Vary V${q}`;
            if (generationInFlight) btn.disabled = true;
            btn.addEventListener('click', () => runVariation(q));
            row.appendChild(btn);
        }
        actionsRow.appendChild(row);
    }
}

// Presses U1–U4 on the parent grid's Discord message and creates a new
// child node under it when the upscale arrives. Reuses the same
// node-scoped status pill machinery as runGeneration — the pill is
// anchored to the parent grid node while the upscale runs.
async function runUpscale(quadrant) {
    if (generationInFlight) return;
    if (!state.currentNode || !state.currentNode.image) return;
    const kind = state.currentNode.image.kind;
    if (kind !== 'grid' && kind !== 'variation') return;

    const pid = state.currentProject.id;
    const parentNid = state.currentNode.id;

    generationInFlight = true;
    generatingNodeId = parentNid;
    renderQuadrantStrip();  // re-render to disable empty cells
    setGenerationStatus({ text: `Upscaling U${quadrant}...` });

    const controller = new AbortController();
    const timeout = setTimeout(() => controller.abort(), 360_000);

    const cleanup = () => {
        generationInFlight = false;
        clearTimeout(timeout);
        renderQuadrantStrip();  // re-render to re-enable empty cells
    };

    try {
        const res = await api.streamUpscale(pid, parentNid, quadrant, controller.signal);

        if (res.status === 503) {
            setGenerationStatus({ text: 'Midjourney is not enabled on the server', error: true });
            cleanup();
            return;
        }
        if (res.status === 400) {
            const body = await res.text();
            setGenerationStatus({ text: `Can't upscale: ${body.slice(0, 140)}`, error: true });
            cleanup();
            return;
        }
        if (!res.ok) {
            const errBody = await res.text();
            throw new Error(`Server error (${res.status}): ${errBody.slice(0, 200)}`);
        }

        const reader = res.body.getReader();
        const decoder = new TextDecoder();
        let buf = '';

        while (true) {
            const { done, value } = await reader.read();
            if (done) break;
            buf += decoder.decode(value, { stream: true });
            const lines = buf.split('\n');
            buf = lines.pop();

            for (const line of lines) {
                if (!line.startsWith('data: ')) continue;
                const raw = line.slice(6);
                if (raw === '[DONE]') continue;

                let event;
                try { event = JSON.parse(raw); } catch { continue; }

                if (event.type === 'queued') {
                    setGenerationStatus({ text: `Upscaling U${quadrant} — queued...` });
                } else if (event.type === 'progress') {
                    const pct = event.progress ?? 0;
                    setGenerationStatus({ text: `Upscaling U${quadrant} ${pct}%`, progress: pct });
                } else if (event.type === 'done') {
                    setGenerationStatus({ text: `U${quadrant} done`, progress: 100 });
                    clearGenerationStatusSoon();
                    toast(`U${quadrant} added to tree`);
                    // Refresh the tree so the new child appears (both in
                    // the sidebar and in the quadrant strip), but stay
                    // on the parent grid so the user can keep picking
                    // more quadrants without extra navigation.
                    await loadTree();
                    renderQuadrantStrip();
                } else if (event.type === 'error') {
                    setGenerationStatus({
                        text: event.message || `U${quadrant} upscale failed`,
                        error: true,
                    });
                }
            }
        }
    } catch (err) {
        const msg = err.name === 'AbortError' ? 'Upscale timed out' : err.message;
        setGenerationStatus({ text: msg, error: true });
    } finally {
        cleanup();
    }
}

async function runVariation(quadrant) {
    if (generationInFlight) return;
    if (!state.currentNode || !state.currentNode.image) return;
    const kind = state.currentNode.image.kind;
    if (kind !== 'grid' && kind !== 'variation') return;

    const pid = state.currentProject.id;
    const parentNid = state.currentNode.id;

    generationInFlight = true;
    generatingNodeId = parentNid;
    renderQuadrantStrip();
    setGenerationStatus({ text: `Varying V${quadrant}...` });

    const controller = new AbortController();
    const timeout = setTimeout(() => controller.abort(), 360_000);

    const cleanup = () => {
        generationInFlight = false;
        clearTimeout(timeout);
        renderQuadrantStrip();
    };

    try {
        const res = await api.streamVariation(pid, parentNid, quadrant, controller.signal);

        if (res.status === 503) {
            setGenerationStatus({ text: 'Midjourney is not enabled on the server', error: true });
            cleanup();
            return;
        }
        if (res.status === 400) {
            const body = await res.text();
            setGenerationStatus({ text: `Can't vary: ${body.slice(0, 140)}`, error: true });
            cleanup();
            return;
        }
        if (!res.ok) {
            const errBody = await res.text();
            throw new Error(`Server error (${res.status}): ${errBody.slice(0, 200)}`);
        }

        const reader = res.body.getReader();
        const decoder = new TextDecoder();
        let buf = '';

        while (true) {
            const { done, value } = await reader.read();
            if (done) break;
            buf += decoder.decode(value, { stream: true });
            const lines = buf.split('\n');
            buf = lines.pop();

            for (const line of lines) {
                if (!line.startsWith('data: ')) continue;
                const raw = line.slice(6);
                if (raw === '[DONE]') continue;

                let event;
                try { event = JSON.parse(raw); } catch { continue; }

                if (event.type === 'queued') {
                    setGenerationStatus({ text: `Varying V${quadrant} — queued...` });
                } else if (event.type === 'progress') {
                    const pct = event.progress ?? 0;
                    setGenerationStatus({ text: `Varying V${quadrant} ${pct}%`, progress: pct });
                } else if (event.type === 'done') {
                    setGenerationStatus({ text: `V${quadrant} done`, progress: 100 });
                    clearGenerationStatusSoon();
                    toast(`V${quadrant} added to tree`);
                    await loadTree();
                    renderQuadrantStrip();
                } else if (event.type === 'error') {
                    setGenerationStatus({
                        text: event.message || `V${quadrant} variation failed`,
                        error: true,
                    });
                }
            }
        }
    } catch (err) {
        const msg = err.name === 'AbortError' ? 'Variation timed out' : err.message;
        setGenerationStatus({ text: msg, error: true });
    } finally {
        cleanup();
    }
}

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
        let msg;
        if (err.name === 'AbortError') {
            msg = 'Request timed out. The coach took too long to respond.';
        } else if (err instanceof TypeError) {
            // fetch() throws TypeError on connection-level failure
            // (server down, dropped mid-stream, CORS, etc.)
            msg = 'Connection to the server failed. Is the backend still running?';
        } else {
            msg = err.message;
        }
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

// ===== Mobile panel toggles =====
const mobileBackdrop = $('#mobile-backdrop');
const sidebar = $('#sidebar');
const coachingSidebar = $('#coaching-sidebar');

function closeMobilePanels() {
    sidebar.classList.remove('open');
    coachingSidebar.classList.remove('open');
    mobileBackdrop.classList.remove('visible');
    $('#mobile-tree-btn').classList.remove('active');
    $('#mobile-coach-btn').classList.remove('active');
}

$('#mobile-tree-btn').addEventListener('click', () => {
    const opening = !sidebar.classList.contains('open');
    closeMobilePanels();
    if (opening) {
        sidebar.classList.add('open');
        mobileBackdrop.classList.add('visible');
        $('#mobile-tree-btn').classList.add('active');
    }
});

$('#mobile-coach-btn').addEventListener('click', () => {
    const opening = !coachingSidebar.classList.contains('open');
    closeMobilePanels();
    if (opening) {
        coachingSidebar.classList.add('open');
        mobileBackdrop.classList.add('visible');
        $('#mobile-coach-btn').classList.add('active');
    }
});

mobileBackdrop.addEventListener('click', closeMobilePanels);

// Close mobile panels when a tree node is selected (navigates to workspace).
const origSelectNode = selectNode;
// Monkey-patch isn't needed — just close panels on any tree item click.
document.getElementById('prompt-tree').addEventListener('click', (e) => {
    if (e.target.closest('.tree-item')) closeMobilePanels();
});

// ===== Init =====
navigateToRoute();
