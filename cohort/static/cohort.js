/**
 * Cohort - Coding Team Dashboard
 *
 * Socket.IO client connecting to the 8-event dashboard contract
 * plus SMACK-style chat events for roundtable discussions.
 */

// =====================================================================
// State
// =====================================================================

const state = {
    currentPanel: 'team',
    agents: [],
    tasks: [],
    outputs: [],
    filter: 'all',
    selectedVerdict: null,
    socket: null,
    connected: false,

    // Chat state
    agentProfiles: {},       // Agent registry (avatar, color, nickname, role)
    currentChannel: null,    // Currently selected channel
    channels: [],            // List of channels
    messages: {},            // Messages per channel: { channelId: [msg, ...] }

    // Folders: { id, name, channelIds: [], open: bool }
    folders: [],

    // Channel members: { channelId: [agentId, ...] }
    channelMembers: {},
};

// =====================================================================
// DOM references
// =====================================================================

const $ = (sel) => document.querySelector(sel);
const $$ = (sel) => document.querySelectorAll(sel);

let dom = {};

function initDom() {
    dom = {
        connectionStatus: $('#connection-status'),
        navItems: $$('.sidebar-nav__item'),
        panelTeam: $('#panel-team'),
        panelChat: $('#panel-chat'),
        panelQueue: $('#panel-queue'),
        panelOutput: $('#panel-output'),
        panelTitle: $('#panel-title'),
        panelSubtitle: $('#panel-subtitle'),
        panelCount: $('#panel-count'),
        filterContainer: $('#filter-container'),
        filterSelect: $('#filter-select'),
        agentGrid: $('#agent-grid'),
        taskList: $('#task-list'),
        outputList: $('#output-list'),
        teamBadge: $('#team-badge'),
        chatBadge: $('#chat-badge'),
        queueBadge: $('#queue-badge'),
        outputBadge: $('#output-badge'),
        assignTaskBtn: $('#assign-task-btn'),
        assignTaskModal: $('#assign-task-modal'),
        assignTaskClose: $('#assign-task-close'),
        assignTaskCancel: $('#assign-task-cancel'),
        assignTaskForm: $('#assign-task-form'),
        taskAgentSelect: $('#task-agent-select'),
        taskDescriptionInput: $('#task-description-input'),
        taskPrioritySelect: $('#task-priority-select'),
        reviewModal: $('#review-modal'),
        reviewClose: $('#review-close'),
        reviewCancel: $('#review-cancel'),
        reviewForm: $('#review-form'),
        reviewOutputContent: $('#review-output-content'),
        reviewTaskId: $('#review-task-id'),
        reviewNotesInput: $('#review-notes-input'),
        reviewSubmit: $('#review-submit'),
        toastContainer: $('#toast-container'),
        refreshBtn: $('#refresh-btn'),
        mobileMenuBtn: $('#mobile-menu-btn'),
        sidebar: $('#sidebar'),
        sidebarOverlay: $('#sidebar-overlay'),

        // Chat elements
        messagesList: $('#messages-list'),
        messagesContainer: $('#messages-container'),
        messageForm: $('#message-form'),
        messageInput: $('#message-input'),
        channelList: $('#channel-list'),
        folderList: $('#folder-list'),
        participantsList: $('#participants-list'),
        mentionDropdown: $('#mention-dropdown'),

        // Create channel/folder modals
        addChannelBtn: $('#add-channel-btn'),
        addFolderBtn: $('#add-folder-btn'),
        addSessionBtn: $('#add-session-btn'),
        createChannelModal: $('#create-channel-modal'),
        createChannelClose: $('#create-channel-close'),
        createChannelCancel: $('#create-channel-cancel'),
        createChannelForm: $('#create-channel-form'),
        newChannelName: $('#new-channel-name'),
        createFolderModal: $('#create-folder-modal'),
        createFolderClose: $('#create-folder-close'),
        createFolderCancel: $('#create-folder-cancel'),
        createFolderForm: $('#create-folder-form'),
        newFolderName: $('#new-folder-name'),

        // Settings elements
        settingsBtn: $('#settings-btn'),
        settingsModal: $('#settings-modal'),
        settingsClose: $('#settings-close'),
        settingsCancel: $('#settings-cancel'),
        settingsForm: $('#settings-form'),
        settingsApiKey: $('#settings-api-key'),
        settingsClaudeCmd: $('#settings-claude-cmd'),
        settingsBossRoot: $('#settings-boss-root'),
        settingsResponseTimeout: $('#settings-response-timeout'),
        settingsExecBackend: $('#settings-exec-backend'),
        settingsConnectionDot: $('#settings-connection-dot'),
        settingsConnectionText: $('#settings-connection-text'),
        testConnectionBtn: $('#test-connection-btn'),
        toggleApiKeyVis: $('#toggle-api-key-vis'),

        // Permissions elements
        permissionsBtn: $('#permissions-btn'),
        permissionsModal: $('#permissions-modal'),
        permissionsClose: $('#permissions-close'),
        permissionsCancel: $('#permissions-cancel'),
        permissionsSave: $('#permissions-save'),
        permTabs: $('#perm-tabs'),
        permPanelServices: $('#perm-panel-services'),
        permPanelAgents: $('#perm-panel-agents'),
        serviceKeysList: $('#service-keys-list'),
        addServiceKeyBtn: $('#add-service-key-btn'),
        permGridHead: $('#perm-grid-head'),
        permGridBody: $('#perm-grid-body'),

        // Add Service sub-modal
        addServiceModal: $('#add-service-modal'),
        addServiceClose: $('#add-service-close'),
        addServiceCancel: $('#add-service-cancel'),
        addServiceForm: $('#add-service-form'),
        serviceTypeSelect: $('#service-type-select'),
        serviceCustomNameGroup: $('#service-custom-name-group'),
        serviceCustomName: $('#service-custom-name'),
        serviceKeyInput: $('#service-key-input'),
        serviceExtraInput: $('#service-extra-input'),
        toggleServiceKeyVis: $('#toggle-service-key-vis'),

        // Members sidebar
        addMemberBtn: $('#add-member-btn'),
        addMemberDropdown: $('#add-member-dropdown'),
        addMemberSearch: $('#add-member-search'),
        addMemberList: $('#add-member-list'),
    };
}

// =====================================================================
// Panel switching
// =====================================================================

const panelConfig = {
    team: {
        title: 'Team Dashboard',
        subtitle: 'Agent status and skills',
        panel: () => dom.panelTeam,
        filter: false,
    },
    chat: {
        title: 'Team Chat',
        subtitle: 'Agent discussion and collaboration',
        panel: () => dom.panelChat,
        filter: false,
    },
    queue: {
        title: 'Work Queue',
        subtitle: 'Task assignment and progress',
        panel: () => dom.panelQueue,
        filter: true,
    },
    output: {
        title: 'Output Review',
        subtitle: 'Code diffs and test results',
        panel: () => dom.panelOutput,
        filter: true,
    },
};

function switchPanel(panelName) {
    state.currentPanel = panelName;
    const config = panelConfig[panelName];

    // Update nav
    dom.navItems.forEach((item) => {
        const isActive = item.dataset.panel === panelName;
        item.classList.toggle('active', isActive);
        item.setAttribute('aria-current', isActive ? 'true' : 'false');
    });

    // Hide all panels
    [dom.panelTeam, dom.panelChat, dom.panelQueue, dom.panelOutput].forEach((p) => {
        if (p) {
            p.style.display = 'none';
            p.classList.remove('active');
        }
    });

    // Show selected panel
    const panel = config.panel();
    if (panel) {
        panel.style.display = '';
        panel.classList.add('active');
    }

    // Update header
    dom.panelTitle.textContent = config.title;
    dom.panelSubtitle.textContent = config.subtitle;
    dom.filterContainer.style.display = config.filter ? '' : 'none';

    updatePanelCount();
}

function updatePanelCount() {
    let count = '';
    switch (state.currentPanel) {
        case 'team':
            count = `${state.agents.length} agents`;
            break;
        case 'chat': {
            const msgs = state.messages[state.currentChannel] || [];
            count = `${msgs.length} messages`;
            break;
        }
        case 'queue':
            count = `${state.tasks.length} tasks`;
            break;
        case 'output':
            count = `${state.outputs.length} outputs`;
            break;
    }
    dom.panelCount.textContent = count;
}

// =====================================================================
// Agent Profiles (ported from SMACK's getAgentProfile)
// =====================================================================

function getAgentProfile(senderId) {
    if (!senderId) return defaultProfile('unknown');

    const normalized = senderId.toLowerCase().replace(/ /g, '_').replace(/-/g, '_');

    // Exact match
    if (state.agentProfiles[senderId]) return state.agentProfiles[senderId];
    if (state.agentProfiles[normalized]) return state.agentProfiles[normalized];

    // Prefix match
    for (const [key, profile] of Object.entries(state.agentProfiles)) {
        if (key.startsWith(normalized)) return profile;
    }

    return defaultProfile(senderId);
}

function defaultProfile(senderId) {
    const initials = senderId.substring(0, 2).toUpperCase();
    return {
        name: senderId,
        nickname: senderId.substring(0, 10),
        avatar: initials,
        color: '#95A5A6',
        role: 'Agent',
    };
}

async function fetchAgentRegistry() {
    try {
        const response = await fetch('/api/agent-registry');
        state.agentProfiles = await response.json();
        console.log('Agent registry loaded:', Object.keys(state.agentProfiles).length, 'agents');
    } catch (error) {
        console.error('Failed to load agent registry:', error);
    }
}

// =====================================================================
// Message formatting (ported from SMACK's formatMessageContent)
// =====================================================================

function formatMessageContent(content) {
    let formatted = escapeHtml(content);

    // Bold
    formatted = formatted.replace(/\*\*(.*?)\*\*/g, '<strong>$1</strong>');

    // Code blocks with copy button
    let codeBlockIndex = 0;
    formatted = formatted.replace(/```([\s\S]*?)```/g, (match, code) => {
        const blockId = `code-block-${Date.now()}-${codeBlockIndex++}`;
        return `<div class="code-block-wrapper">
            <button class="code-copy-btn" onclick="copyCodeBlock('${blockId}')" title="Copy code">[Copy]</button>
            <pre><code id="${blockId}">${code}</code></pre>
        </div>`;
    });

    // Inline code
    formatted = formatted.replace(/`([^`]+)`/g, '<code>$1</code>');

    // @mentions
    formatted = formatted.replace(/@(\w+)/g, (match, username) => {
        const profile = getAgentProfile(username);
        return `<span class="mention" style="color: ${profile.color}; background-color: ${profile.color}22;" title="${profile.name} - ${profile.role}">@${username}</span>`;
    });

    // Markdown links
    formatted = formatted.replace(/\[([^\]]+)\]\((https?:\/\/[^)]+)\)/g,
        '<a href="$2" target="_blank" rel="noopener">$1</a>');

    // Line breaks
    formatted = formatted.replace(/\n/g, '<br>');

    return formatted;
}

function formatTime(timestamp) {
    if (!timestamp) return '';
    const date = new Date(timestamp);
    return date.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' });
}

// =====================================================================
// Message rendering (ported from SMACK's renderMessages)
// =====================================================================

function renderMessages() {
    const messages = state.messages[state.currentChannel] || [];

    if (messages.length === 0) {
        dom.messagesList.innerHTML = `
            <div class="empty-state" id="chat-empty">
                <p class="empty-state__text">No messages yet</p>
                <p class="empty-state__hint">Select a channel or send a message</p>
            </div>`;
        return;
    }

    dom.messagesList.innerHTML = messages.map(message => {
        const time = formatTime(message.timestamp);
        const profile = getAgentProfile(message.sender);
        const typeClass = `message--${message.message_type || 'chat'}`;

        // Thread indicator
        let threadIndicator = '';
        if (message.thread_id) {
            const parentMsg = messages.find(m => m.id === message.thread_id);
            if (parentMsg) {
                const parentProfile = getAgentProfile(parentMsg.sender);
                threadIndicator = `<div class="message__thread-indicator">-> Reply to ${parentProfile.nickname}</div>`;
            }
        }

        // Detect task confirmation blocks
        let confirmationCard = '';
        const confirmMatch = message.content.match(/---TASK_CONFIRMED---\s*\n([\s\S]*?)\n\s*---END_CONFIRMED---/);
        if (confirmMatch) {
            const fields = {};
            confirmMatch[1].replace(/^(Goal|Approach|Scope|Acceptance)\s*:\s*(.+)/gm, (_, k, v) => {
                fields[k.toLowerCase()] = v.trim();
            });

            // Extract task_id from current channel name (task-XXXX)
            const taskId = state.currentChannel ? state.currentChannel.replace(/^task-/, '') : '';

            // Store brief data on a global so the onclick can access it cleanly
            const briefDataId = 'brief_' + Date.now();
            window._pendingBriefs = window._pendingBriefs || {};
            window._pendingBriefs[briefDataId] = fields;

            confirmationCard = `
                <div class="task-confirmation-card">
                    <div class="task-confirmation-card__header">Task Plan Ready</div>
                    <div class="task-confirmation-card__fields">
                        ${fields.goal ? `<div class="task-confirmation-card__field"><strong>Goal:</strong> ${escapeHtml(fields.goal)}</div>` : ''}
                        ${fields.approach ? `<div class="task-confirmation-card__field"><strong>Approach:</strong> ${escapeHtml(fields.approach)}</div>` : ''}
                        ${fields.scope ? `<div class="task-confirmation-card__field"><strong>Scope:</strong> ${escapeHtml(fields.scope)}</div>` : ''}
                        ${fields.acceptance ? `<div class="task-confirmation-card__field"><strong>Acceptance:</strong> ${escapeHtml(fields.acceptance)}</div>` : ''}
                    </div>
                    <div class="task-confirmation-card__actions">
                        <button class="btn btn--primary btn--small" onclick="confirmTaskExecution('${escapeHtml(taskId)}', '${briefDataId}')">Execute</button>
                    </div>
                </div>`;
        }

        return `
            <div class="message ${typeClass}" style="--agent-color: ${profile.color}" data-message-id="${message.id}">
                <div class="message__avatar" title="${profile.name} - ${profile.role}">${profile.avatar}</div>
                <div class="message__content">
                    <div class="message__header">
                        <span class="message__sender" style="color: ${profile.color}">${escapeHtml(profile.nickname)}</span>
                        <span class="message__role">${profile.role}</span>
                        <span class="message__time">${time}</span>
                    </div>
                    ${threadIndicator}
                    <div class="message__body">${formatMessageContent(message.content)}</div>
                    ${confirmationCard}
                </div>
            </div>
        `;
    }).join('');

    updatePanelCount();
    scrollToBottom();
}

function scrollToBottom() {
    if (dom.messagesContainer) {
        dom.messagesContainer.scrollTop = dom.messagesContainer.scrollHeight;
    }
}

// =====================================================================
// Channel rendering
// =====================================================================

function renderChannels() {
    if (!dom.channelList) return;

    // Only show channels not assigned to any folder
    const unfolderedChannels = getUnfolderedChannels();

    if (unfolderedChannels.length === 0 && state.channels.length === 0) {
        dom.channelList.innerHTML = '<li style="padding: 8px 16px; color: var(--color-text-muted); font-size: 12px;">No channels yet</li>';
        return;
    }

    if (unfolderedChannels.length === 0) {
        dom.channelList.innerHTML = '<li style="padding: 8px 16px; color: var(--color-text-muted); font-size: 12px;">All channels in folders</li>';
        return;
    }

    dom.channelList.innerHTML = unfolderedChannels.map(ch => {
        const isActive = ch.id === state.currentChannel;
        return `
            <li class="channel-item ${isActive ? 'active' : ''}"
                data-channel="${escapeHtml(ch.id)}"
                draggable="true"
                onclick="switchChannel('${escapeHtml(ch.id)}')">
                <span class="channel-item__name"># ${escapeHtml(ch.name || ch.id)}</span>
                <button class="item-edit-btn" onclick="event.stopPropagation(); openEditMenu(this, 'channel', '${escapeHtml(ch.id)}')" title="Edit">[:]</button>
            </li>`;
    }).join('');
}

// =====================================================================
// Folder management (localStorage-persisted)
// =====================================================================

function loadFolders() {
    try {
        const raw = localStorage.getItem('cohort_folders');
        if (raw) state.folders = JSON.parse(raw);
    } catch { /* ignore */ }
}

function saveFolders() {
    localStorage.setItem('cohort_folders', JSON.stringify(state.folders));
}

function createFolder(name) {
    const id = 'folder_' + Date.now();
    state.folders.push({ id, name, channelIds: [], open: true });
    saveFolders();
    renderFolders();
    renderChannels();
}

function toggleFolder(folderId) {
    const folder = state.folders.find(f => f.id === folderId);
    if (folder) {
        folder.open = !folder.open;
        saveFolders();
        renderFolders();
    }
}

function moveChannelToFolder(channelId, folderId) {
    // Remove from any existing folder first
    state.folders.forEach(f => {
        f.channelIds = f.channelIds.filter(id => id !== channelId);
    });
    // Add to target folder
    const folder = state.folders.find(f => f.id === folderId);
    if (folder && !folder.channelIds.includes(channelId)) {
        folder.channelIds.push(channelId);
    }
    saveFolders();
    renderFolders();
    renderChannels();
}

function removeChannelFromFolder(channelId) {
    state.folders.forEach(f => {
        f.channelIds = f.channelIds.filter(id => id !== channelId);
    });
    saveFolders();
    renderFolders();
    renderChannels();
}

function getUnfolderedChannels() {
    const folderedIds = new Set();
    state.folders.forEach(f => f.channelIds.forEach(id => folderedIds.add(id)));
    return state.channels.filter(ch => !folderedIds.has(ch.id));
}

// =====================================================================
// Channel Members
// =====================================================================

function loadChannelMembers() {
    try {
        const raw = localStorage.getItem('cohort_channel_members');
        if (raw) state.channelMembers = JSON.parse(raw);
    } catch { /* ignore */ }
}

function saveChannelMembers() {
    localStorage.setItem('cohort_channel_members', JSON.stringify(state.channelMembers));
}

function getChannelMembers(channelId) {
    if (!channelId) return [];
    return state.channelMembers[channelId] || [];
}

function addChannelMember(channelId, agentId) {
    if (!channelId) return;
    if (!state.channelMembers[channelId]) state.channelMembers[channelId] = [];
    if (!state.channelMembers[channelId].includes(agentId)) {
        state.channelMembers[channelId].push(agentId);
        saveChannelMembers();
        updateParticipants();
    }
}

function removeChannelMember(channelId, agentId) {
    if (!channelId || !state.channelMembers[channelId]) return;
    state.channelMembers[channelId] = state.channelMembers[channelId].filter(id => id !== agentId);
    saveChannelMembers();
    updateParticipants();
}

/** Extract @mentions from message text and auto-add sender + mentioned agents as members. */
function autoAddMembersFromMessage(channelId, message) {
    if (!channelId) return;
    let changed = false;
    if (!state.channelMembers[channelId]) state.channelMembers[channelId] = [];

    // Auto-add sender (skip 'system')
    const sender = message.sender || message.agent_id;
    if (sender && sender !== 'system') {
        const normalizedSender = sender.toLowerCase().replace(/[\s-]/g, '_');
        // Match sender to a known agent profile
        const senderId = resolveAgentId(normalizedSender);
        if (senderId && !state.channelMembers[channelId].includes(senderId)) {
            state.channelMembers[channelId].push(senderId);
            changed = true;
        }
    }

    // Extract @mentions from content
    const content = message.content || '';
    const mentionRe = /@(\w+)/g;
    let match;
    while ((match = mentionRe.exec(content)) !== null) {
        const mentionedId = resolveAgentId(match[1]);
        if (mentionedId && !state.channelMembers[channelId].includes(mentionedId)) {
            state.channelMembers[channelId].push(mentionedId);
            changed = true;
        }
    }

    if (changed) {
        saveChannelMembers();
        if (channelId === state.currentChannel) updateParticipants();
    }
}

/** Resolve a raw name/id to a known agent registry key, or null. */
function resolveAgentId(raw) {
    if (!raw) return null;
    const normalized = raw.toLowerCase().replace(/[\s-]/g, '_');
    // Exact match
    if (state.agentProfiles[normalized]) return normalized;
    // Check all keys
    for (const key of Object.keys(state.agentProfiles)) {
        if (key === normalized) return key;
        if (key.startsWith(normalized)) return key;
        const p = state.agentProfiles[key];
        if (p.nickname && p.nickname.toLowerCase().replace(/[\s-]/g, '_') === normalized) return key;
    }
    return null;
}

function toggleAddMemberDropdown() {
    const dd = dom.addMemberDropdown;
    if (!dd) return;
    const isOpen = dd.style.display !== 'none';
    dd.style.display = isOpen ? 'none' : 'block';
    if (!isOpen) {
        if (dom.addMemberSearch) { dom.addMemberSearch.value = ''; dom.addMemberSearch.focus(); }
        renderAddMemberList('');
    }
}

function renderAddMemberList(filter) {
    if (!dom.addMemberList) return;
    const currentMembers = getChannelMembers(state.currentChannel);
    const profiles = Object.entries(state.agentProfiles);
    const q = (filter || '').toLowerCase();

    const available = profiles.filter(([id, p]) => {
        if (currentMembers.includes(id)) return false;
        if (p.hidden) return false;
        if (q && !p.name.toLowerCase().includes(q) && !p.nickname.toLowerCase().includes(q) && !id.toLowerCase().includes(q)) return false;
        return true;
    });

    if (available.length === 0) {
        dom.addMemberList.innerHTML = '<p class="add-member-dropdown__empty">No agents to add</p>';
        return;
    }

    dom.addMemberList.innerHTML = available.map(([id, p]) => `
        <div class="add-member-option" data-agent-id="${id}">
            <div class="add-member-option__avatar" style="background-color: ${p.color}">${p.avatar}</div>
            <div class="add-member-option__info">
                <span class="add-member-option__name">${escapeHtml(p.nickname)}</span>
                <span class="add-member-option__role">${escapeHtml(p.role || 'Agent')}</span>
            </div>
        </div>
    `).join('');

    // Click handlers
    dom.addMemberList.querySelectorAll('.add-member-option').forEach(opt => {
        opt.addEventListener('click', () => {
            const agentId = opt.dataset.agentId;
            addChannelMember(state.currentChannel, agentId);
            renderAddMemberList(dom.addMemberSearch ? dom.addMemberSearch.value : '');
            showToast(`Added ${state.agentProfiles[agentId]?.nickname || agentId} to channel`, 'success');
        });
    });
}

function renderFolders() {
    if (!dom.folderList) return;

    if (state.folders.length === 0) {
        dom.folderList.innerHTML = '<div style="padding: 8px 16px; color: var(--color-text-muted); font-size: 12px;">No folders yet</div>';
        return;
    }

    dom.folderList.innerHTML = state.folders.map(folder => {
        const channelsHtml = folder.channelIds.map(chId => {
            const ch = state.channels.find(c => c.id === chId);
            if (!ch) return '';
            const isActive = ch.id === state.currentChannel;
            return `<li class="channel-item ${isActive ? 'active' : ''}"
                        data-channel="${escapeHtml(ch.id)}"
                        draggable="true"
                        onclick="switchChannel('${escapeHtml(ch.id)}')">
                        <span class="channel-item__name"># ${escapeHtml(ch.name || ch.id)}</span>
                        <button class="item-edit-btn" onclick="event.stopPropagation(); openEditMenu(this, 'channel', '${escapeHtml(ch.id)}')" title="Edit">[:]</button>
                    </li>`;
        }).join('');

        const emptyHtml = folder.channelIds.length === 0
            ? '<div class="folder-item__empty">Drop channels here</div>'
            : '';

        return `
            <div class="folder-item ${folder.open ? 'open' : ''}" data-folder-id="${escapeHtml(folder.id)}">
                <div class="folder-item__header"
                     ondragover="handleFolderDragOver(event)"
                     ondragleave="handleFolderDragLeave(event)"
                     ondrop="handleFolderDrop(event, '${escapeHtml(folder.id)}')"
                     onclick="toggleFolder('${escapeHtml(folder.id)}')">
                    <span class="folder-item__toggle">></span>
                    <span class="folder-item__name">${escapeHtml(folder.name)}</span>
                    <span class="folder-item__count">${folder.channelIds.length}</span>
                    <button class="item-edit-btn" onclick="event.stopPropagation(); openEditMenu(this, 'folder', '${escapeHtml(folder.id)}')" title="Edit">[:]</button>
                </div>
                <ul class="folder-item__children"
                    ondragover="handleFolderDragOver(event)"
                    ondragleave="handleFolderDragLeave(event)"
                    ondrop="handleFolderDrop(event, '${escapeHtml(folder.id)}')">
                    ${channelsHtml}
                    ${emptyHtml}
                </ul>
            </div>`;
    }).join('');
}

// =====================================================================
// Inline edit menus (rename / delete for channels & folders)
// =====================================================================

let activeEditMenu = null;

function closeEditMenu() {
    if (activeEditMenu) {
        activeEditMenu.remove();
        activeEditMenu = null;
    }
    document.removeEventListener('click', onEditMenuOutsideClick);
}

function onEditMenuOutsideClick(e) {
    if (activeEditMenu && !activeEditMenu.contains(e.target)) {
        closeEditMenu();
    }
}

function openEditMenu(btnEl, itemType, itemId) {
    closeEditMenu();

    const menu = document.createElement('div');
    menu.className = 'item-edit-menu';

    if (itemType === 'folder') {
        menu.innerHTML = `
            <button class="item-edit-menu__option" data-action="rename">Rename</button>
            <button class="item-edit-menu__option item-edit-menu__option--danger" data-action="delete">Delete</button>`;
    } else {
        // channel
        menu.innerHTML = `
            <button class="item-edit-menu__option" data-action="rename">Rename</button>
            <button class="item-edit-menu__option item-edit-menu__option--danger" data-action="delete">Delete</button>`;
    }

    menu.addEventListener('click', (e) => {
        const opt = e.target.closest('.item-edit-menu__option');
        if (!opt) return;
        const action = opt.dataset.action;
        closeEditMenu();
        if (action === 'rename') handleRenameItem(itemType, itemId);
        if (action === 'delete') handleDeleteItem(itemType, itemId);
    });

    // Position relative to button
    const parent = btnEl.closest('.channel-item') || btnEl.closest('.folder-item__header');
    if (parent) {
        parent.style.position = 'relative';
        parent.appendChild(menu);
    }
    activeEditMenu = menu;

    // Close on outside click (deferred so this click doesn't close it)
    setTimeout(() => document.addEventListener('click', onEditMenuOutsideClick), 0);
}

function handleRenameItem(itemType, itemId) {
    if (itemType === 'folder') {
        const folder = state.folders.find(f => f.id === itemId);
        if (!folder) return;
        const newName = prompt('Rename folder:', folder.name);
        if (newName && newName.trim()) {
            folder.name = newName.trim();
            saveFolders();
            renderFolders();
            showToast(`Folder renamed to "${folder.name}"`, 'success');
        }
    } else {
        // Channel rename -- just a toast for now since channel names are server-managed
        showToast('Channel rename coming soon', 'info');
    }
}

function handleDeleteItem(itemType, itemId) {
    if (itemType === 'folder') {
        const folder = state.folders.find(f => f.id === itemId);
        if (!folder) return;
        if (!confirm(`Delete folder "${folder.name}"? Channels inside will be moved back to the main list.`)) return;
        state.folders = state.folders.filter(f => f.id !== itemId);
        saveFolders();
        renderFolders();
        renderChannels();
        showToast(`Folder "${folder.name}" deleted`, 'success');
    } else {
        // Channel delete -- just a toast for now since channels are server-managed
        showToast('Channel delete coming soon', 'info');
    }
}

// =====================================================================
// Drag and drop (channels into folders)
// =====================================================================

let draggedChannelId = null;

function initDragAndDrop() {
    // Delegate dragstart/dragend on channel list and folder children
    document.addEventListener('dragstart', (e) => {
        const item = e.target.closest('.channel-item[draggable="true"]');
        if (!item) return;
        draggedChannelId = item.dataset.channel;
        item.classList.add('dragging');
        e.dataTransfer.effectAllowed = 'move';
        e.dataTransfer.setData('text/plain', draggedChannelId);
    });

    document.addEventListener('dragend', (e) => {
        const item = e.target.closest('.channel-item');
        if (item) item.classList.remove('dragging');
        draggedChannelId = null;
        // Clean up any leftover drag-over highlights
        document.querySelectorAll('.drag-over').forEach(el => el.classList.remove('drag-over'));
    });

    // Allow dropping channels back into the main channel list (removes from folder)
    if (dom.channelList) {
        dom.channelList.addEventListener('dragover', (e) => {
            if (!draggedChannelId) return;
            e.preventDefault();
            e.dataTransfer.dropEffect = 'move';
            dom.channelList.classList.add('drag-over');
        });
        dom.channelList.addEventListener('dragleave', () => {
            dom.channelList.classList.remove('drag-over');
        });
        dom.channelList.addEventListener('drop', (e) => {
            e.preventDefault();
            dom.channelList.classList.remove('drag-over');
            if (!draggedChannelId) return;
            removeChannelFromFolder(draggedChannelId);
            draggedChannelId = null;
        });
    }
}

function handleFolderDragOver(e) {
    if (!draggedChannelId) return;
    e.preventDefault();
    e.dataTransfer.dropEffect = 'move';
    const target = e.currentTarget;
    target.classList.add('drag-over');
}

function handleFolderDragLeave(e) {
    const target = e.currentTarget;
    target.classList.remove('drag-over');
}

function handleFolderDrop(e, folderId) {
    e.preventDefault();
    e.stopPropagation();
    const target = e.currentTarget;
    target.classList.remove('drag-over');
    if (!draggedChannelId) return;
    moveChannelToFolder(draggedChannelId, folderId);
    draggedChannelId = null;
}

function switchChannel(channelId) {
    state.currentChannel = channelId;

    // Switch to chat panel if not already there
    if (state.currentPanel !== 'chat') {
        switchPanel('chat');
    }

    // Join channel via Socket.IO
    if (state.socket && state.connected) {
        state.socket.emit('join_channel', { channel_id: channelId });
    }

    renderChannels();
    renderFolders();
    renderMessages();
    updateParticipants();
    // Close add-member dropdown when switching channels
    if (dom.addMemberDropdown) dom.addMemberDropdown.style.display = 'none';
}

function updateParticipants() {
    if (!dom.participantsList) return;

    const memberIds = getChannelMembers(state.currentChannel);

    if (memberIds.length === 0) {
        dom.participantsList.innerHTML = '<p class="participants-list__empty">No members yet. Click + to add.</p>';
        return;
    }

    dom.participantsList.innerHTML = memberIds.map(agentId => {
        const profile = state.agentProfiles[agentId] || getAgentProfile(agentId);
        const agentState = state.agents.find(a => a.agent_id === agentId);
        const status = agentState ? (agentState.status || 'Online') : 'Offline';
        const isOnline = agentState != null;
        return `
            <div class="member-card" data-agent-id="${agentId}">
                <div class="member-card__avatar" style="background-color: ${profile.color}">
                    ${profile.avatar}
                    <span class="member-card__status-dot ${isOnline ? 'member-card__status-dot--online' : ''}"></span>
                </div>
                <div class="member-card__info">
                    <p class="member-card__name">${escapeHtml(profile.nickname)}</p>
                    <p class="member-card__role">${escapeHtml(profile.role || 'Agent')}</p>
                </div>
                <button class="member-card__remove" title="Remove member" data-agent-id="${agentId}">&times;</button>
            </div>`;
    }).join('');

    // Remove member handlers
    dom.participantsList.querySelectorAll('.member-card__remove').forEach(btn => {
        btn.addEventListener('click', (e) => {
            e.stopPropagation();
            const agentId = btn.dataset.agentId;
            removeChannelMember(state.currentChannel, agentId);
        });
    });

    // Click card to open chat with agent
    dom.participantsList.querySelectorAll('.member-card').forEach(card => {
        card.addEventListener('click', () => {
            const agentId = card.dataset.agentId;
            if (agentId && dom.messageInput) {
                dom.messageInput.focus();
            }
        });
    });
}

// =====================================================================
// @mention autocomplete (contenteditable)
// =====================================================================

const mention = {
    active: false,
    query: '',
    selectedIndex: 0,
    matches: [],
    // Range from '@' to cursor, used for replacement
    atNode: null,
    atOffset: -1,
};

function getMentionableAgents() {
    return Object.entries(state.agentProfiles)
        .filter(([, p]) => !p.hidden)
        .map(([id, p]) => ({
            id,
            name: p.name || id,
            nickname: (p.nickname || id).toLowerCase(),
            avatar: p.avatar || id.substring(0, 2).toUpperCase(),
            color: p.color || '#95A5A6',
            role: p.role || 'Agent',
        }));
}

function filterAgents(query) {
    const q = query.toLowerCase();
    if (!q) return getMentionableAgents();
    return getMentionableAgents().filter(a =>
        a.id.toLowerCase().includes(q) ||
        a.nickname.includes(q) ||
        a.name.toLowerCase().includes(q) ||
        a.role.toLowerCase().includes(q)
    );
}

function renderMentionDropdown() {
    if (!dom.mentionDropdown || !mention.active || mention.matches.length === 0) {
        closeMentionDropdown();
        return;
    }

    dom.mentionDropdown.style.display = '';
    dom.mentionDropdown.innerHTML = mention.matches.map((agent, i) => `
        <div class="mention-item ${i === mention.selectedIndex ? 'active' : ''}"
             data-index="${i}" data-agent-id="${escapeHtml(agent.id)}">
            <div class="mention-item__avatar" style="background-color: ${agent.color}">${agent.avatar}</div>
            <div class="mention-item__info">
                <span class="mention-item__name">${escapeHtml(agent.name)}</span>
                <span class="mention-item__role">${agent.role}</span>
            </div>
        </div>
    `).join('');

    const activeEl = dom.mentionDropdown.querySelector('.mention-item.active');
    if (activeEl) activeEl.scrollIntoView({ block: 'nearest' });
}

function closeMentionDropdown() {
    mention.active = false;
    mention.query = '';
    mention.selectedIndex = 0;
    mention.matches = [];
    mention.atNode = null;
    mention.atOffset = -1;
    if (dom.mentionDropdown) dom.mentionDropdown.style.display = 'none';
}

function getInputPlainText() {
    // Extract plain text from contenteditable, converting mention tags back to @id
    if (!dom.messageInput) return '';
    let text = '';
    dom.messageInput.childNodes.forEach(node => {
        if (node.nodeType === Node.TEXT_NODE) {
            text += node.textContent;
        } else if (node.nodeType === Node.ELEMENT_NODE) {
            if (node.classList && node.classList.contains('mention-tag')) {
                text += node.textContent;
            } else {
                text += node.textContent;
            }
        }
    });
    // Convert non-breaking spaces to regular spaces
    return text.replace(/\u00A0/g, ' ');
}

function clearInput() {
    if (dom.messageInput) {
        dom.messageInput.innerHTML = '';
    }
}

function completeMention(agent) {
    // Replace the @query text with a colored mention tag
    const sel = window.getSelection();
    if (!sel.rangeCount || !mention.atNode) {
        closeMentionDropdown();
        return;
    }

    const profile = getAgentProfile(agent.id);
    const color = profile.color || agent.color;

    // Build the range from @ to current cursor
    const range = document.createRange();
    range.setStart(mention.atNode, mention.atOffset);
    range.setEnd(sel.focusNode, sel.focusOffset);
    range.deleteContents();

    // Create the mention tag span
    const tag = document.createElement('span');
    tag.className = 'mention-tag';
    tag.contentEditable = 'false';
    tag.style.setProperty('--mention-color', color);
    tag.style.setProperty('--mention-bg', color + '22');
    tag.setAttribute('data-agent-id', agent.id);
    tag.textContent = '@' + agent.id;

    // Insert tag + trailing space
    range.insertNode(tag);

    const space = document.createTextNode('\u00A0');
    tag.after(space);

    // Move cursor after the space
    const newRange = document.createRange();
    newRange.setStartAfter(space);
    newRange.collapse(true);
    sel.removeAllRanges();
    sel.addRange(newRange);

    dom.messageInput.focus();
    closeMentionDropdown();
}

function handleMentionInput() {
    const sel = window.getSelection();
    if (!sel.rangeCount) {
        closeMentionDropdown();
        return;
    }

    const focusNode = sel.focusNode;
    // Only handle text nodes inside our input
    if (!focusNode || focusNode.nodeType !== Node.TEXT_NODE ||
        !dom.messageInput.contains(focusNode)) {
        closeMentionDropdown();
        return;
    }

    const text = focusNode.textContent.substring(0, sel.focusOffset);

    // Find the last '@' not preceded by a word char
    const atMatch = text.match(/(^|[^@\w])@([\w.]*)$/);
    if (!atMatch) {
        closeMentionDropdown();
        return;
    }

    const query = atMatch[2];
    // Calculate the offset of '@' within this text node
    mention.atNode = focusNode;
    mention.atOffset = sel.focusOffset - query.length - 1;
    mention.query = query;
    mention.active = true;
    mention.matches = filterAgents(query).slice(0, 8);
    mention.selectedIndex = Math.min(mention.selectedIndex, Math.max(0, mention.matches.length - 1));
    renderMentionDropdown();
}

function handleMentionKeydown(e) {
    // Enter without dropdown = submit
    if (e.key === 'Enter' && !e.shiftKey && (!mention.active || mention.matches.length === 0)) {
        e.preventDefault();
        dom.messageForm.dispatchEvent(new Event('submit', { cancelable: true }));
        return;
    }

    if (!mention.active || mention.matches.length === 0) return;

    if (e.key === 'ArrowDown') {
        e.preventDefault();
        mention.selectedIndex = (mention.selectedIndex + 1) % mention.matches.length;
        renderMentionDropdown();
    } else if (e.key === 'ArrowUp') {
        e.preventDefault();
        mention.selectedIndex = (mention.selectedIndex - 1 + mention.matches.length) % mention.matches.length;
        renderMentionDropdown();
    } else if (e.key === 'Tab' || e.key === 'Enter') {
        e.preventDefault();
        completeMention(mention.matches[mention.selectedIndex]);
    } else if (e.key === 'Escape') {
        closeMentionDropdown();
    }
}

function initMentionAutocomplete() {
    if (!dom.messageInput || !dom.mentionDropdown) return;

    dom.messageInput.addEventListener('input', handleMentionInput);
    dom.messageInput.addEventListener('keydown', handleMentionKeydown);

    // Prevent contenteditable from pasting rich HTML
    dom.messageInput.addEventListener('paste', (e) => {
        e.preventDefault();
        const text = (e.clipboardData || window.clipboardData).getData('text/plain');
        document.execCommand('insertText', false, text);
    });

    // Click to select from dropdown
    dom.mentionDropdown.addEventListener('mousedown', (e) => {
        const item = e.target.closest('.mention-item');
        if (!item) return;
        e.preventDefault();
        const idx = parseInt(item.dataset.index, 10);
        if (!isNaN(idx) && mention.matches[idx]) {
            completeMention(mention.matches[idx]);
        }
    });

    // Close on blur (with delay so click events register)
    dom.messageInput.addEventListener('blur', () => {
        setTimeout(closeMentionDropdown, 150);
    });
}

// =====================================================================
// Card renderers (Team, Queue, Output -- unchanged)
// =====================================================================

function renderAgentCard(agent) {
    const skills = (agent.skills || []).slice(0, 5)
        .map((s) => `<span class="agent-card__skill">${escapeHtml(s)}</span>`)
        .join('');

    const taskHtml = agent.current_task
        ? `<div class="agent-card__task">
            <div class="agent-card__task-label">Current Task</div>
            ${escapeHtml(agent.current_task.description || 'Working...')}
           </div>`
        : '';

    return `
    <div class="agent-card" data-agent-id="${escapeHtml(agent.agent_id)}">
        <div class="agent-card__header">
            <h3 class="agent-card__name">${escapeHtml(agent.name || agent.agent_id)}</h3>
            <div class="agent-card__status">
                <span class="agent-card__status-dot agent-card__status-dot--${agent.status}"></span>
                ${agent.status}
            </div>
        </div>
        <div class="agent-card__skills">${skills}</div>
        ${taskHtml}
        <div class="agent-card__footer">
            <span class="agent-card__stat">${agent.tasks_completed || 0} tasks completed</span>
            <div class="agent-card__actions">
                <button class="btn btn--small btn--secondary" onclick="openChatForAgent('${escapeHtml(agent.agent_id)}')">Chat</button>
                <button class="btn btn--small btn--secondary" onclick="openAssignForAgent('${escapeHtml(agent.agent_id)}')">Assign</button>
            </div>
        </div>
    </div>`;
}

function renderTaskCard(task) {
    const priority = task.priority || 'medium';
    const timeAgo = formatTimeAgo(task.created_at);
    const isBriefing = task.status === 'briefing';
    const clickAction = isBriefing
        ? `onclick="switchChannel('task-${escapeHtml(task.task_id)}')" style="cursor: pointer;"`
        : '';
    const statusLabel = isBriefing ? 'briefing - click to chat' : escapeHtml(task.status);

    return `
    <div class="task-card task-card--${priority} ${isBriefing ? 'task-card--briefing' : ''}" data-task-id="${escapeHtml(task.task_id)}" ${clickAction}>
        <div class="task-card__header">
            <span class="task-card__agent">${escapeHtml(task.agent_id)}</span>
            <span class="task-card__priority task-card__priority--${priority}">${priority}</span>
        </div>
        <p class="task-card__description">${escapeHtml(task.description)}</p>
        <div class="task-card__footer">
            <span class="task-card__status">${statusLabel}</span>
            <span class="task-card__time">${timeAgo}</span>
        </div>
    </div>`;
}

function renderOutputCard(task) {
    const output = task.output || {};
    const review = task.review;
    const content = output.diff || output.content || output.summary || JSON.stringify(output, null, 2);

    let reviewHtml = '';
    if (review) {
        reviewHtml = `<span class="output-card__review-status output-card__review-status--${review.verdict}">${review.verdict}</span>`;
    } else {
        reviewHtml = `<button class="btn btn--primary btn--small" onclick="openReview('${escapeHtml(task.task_id)}')">Review</button>`;
    }

    return `
    <div class="output-card" data-task-id="${escapeHtml(task.task_id)}">
        <div class="output-card__header">
            <h4 class="output-card__title">${escapeHtml(task.description || task.task_id)}</h4>
            <span class="output-card__agent">${escapeHtml(task.agent_id)}</span>
        </div>
        <div class="output-card__body">
            <div class="output-card__diff">${escapeHtml(content)}</div>
        </div>
        <div class="output-card__footer">
            ${reviewHtml}
        </div>
    </div>`;
}

// =====================================================================
// Agent group collapse state (localStorage-persisted)
// =====================================================================

const GROUP_ORDER = ['Leadership', 'Core Developers', 'Specialists', 'Support', 'Operators'];

function loadGroupState() {
    try {
        const raw = localStorage.getItem('cohort_group_state');
        if (raw) return JSON.parse(raw);
    } catch { /* ignore */ }
    // Default: all open
    return {};
}

function saveGroupState(groupState) {
    localStorage.setItem('cohort_group_state', JSON.stringify(groupState));
}

function toggleGroup(groupName) {
    const gs = loadGroupState();
    gs[groupName] = !(gs[groupName] !== false); // toggle, default open
    saveGroupState(gs);
    renderTeam();
}

function getAgentGroup(agent) {
    // Look up group from profile registry, fallback to 'Other'
    const profile = state.agentProfiles[agent.agent_id];
    return (profile && profile.group) || 'Other';
}

// =====================================================================
// Render functions
// =====================================================================

function renderTeam() {
    const empty = $('#team-empty');
    if (state.agents.length === 0) {
        dom.agentGrid.innerHTML = '';
        dom.agentGrid.appendChild(empty || createEmpty('team-empty', 'No agents connected', 'Agents will appear here when they join a session'));
        return;
    }

    // Group agents
    const groups = {};
    state.agents.forEach(agent => {
        const group = getAgentGroup(agent);
        if (!groups[group]) groups[group] = [];
        groups[group].push(agent);
    });

    // Sort groups by defined order
    const sortedGroupNames = Object.keys(groups).sort((a, b) => {
        const ia = GROUP_ORDER.indexOf(a);
        const ib = GROUP_ORDER.indexOf(b);
        return (ia === -1 ? 999 : ia) - (ib === -1 ? 999 : ib);
    });

    const groupState = loadGroupState();

    dom.agentGrid.innerHTML = `<div class="agent-groups">${sortedGroupNames.map(groupName => {
        const agents = groups[groupName];
        const isOpen = groupState[groupName] !== false; // default open
        return `
            <div class="agent-group ${isOpen ? 'open' : ''}" data-group="${escapeHtml(groupName)}">
                <div class="agent-group__header" onclick="toggleGroup('${escapeHtml(groupName)}')">
                    <span class="agent-group__toggle">></span>
                    <h3 class="agent-group__title">${escapeHtml(groupName)}</h3>
                    <span class="agent-group__count">${agents.length}</span>
                </div>
                <div class="agent-group__body">
                    <div class="agent-grid">
                        ${agents.map(renderAgentCard).join('')}
                    </div>
                </div>
            </div>`;
    }).join('')}</div>`;

    dom.teamBadge.textContent = state.agents.length;
    updatePanelCount();
}

function renderQueue() {
    let tasks = state.tasks;
    if (state.filter !== 'all') {
        tasks = tasks.filter((t) => t.status === state.filter);
    }

    const empty = $('#queue-empty');
    if (tasks.length === 0) {
        dom.taskList.innerHTML = '';
        const emptyEl = empty || createEmpty('queue-empty', 'No tasks in queue', 'Assign tasks from the Team panel');
        dom.taskList.appendChild(emptyEl);
        return;
    }

    dom.taskList.innerHTML = tasks.map(renderTaskCard).join('');
    dom.queueBadge.textContent = state.tasks.filter((t) => t.status !== 'complete').length;
    updatePanelCount();
}

function renderOutputs() {
    const outputs = state.tasks.filter((t) => t.status === 'complete');
    state.outputs = outputs;

    let filtered = outputs;
    if (state.filter === 'needs_review') {
        filtered = outputs.filter((t) => !t.review);
    } else if (state.filter === 'approved') {
        filtered = outputs.filter((t) => t.review && t.review.verdict === 'approved');
    } else if (state.filter === 'rejected') {
        filtered = outputs.filter((t) => t.review && t.review.verdict === 'rejected');
    }

    if (filtered.length === 0) {
        dom.outputList.innerHTML = '';
        const emptyEl = createEmpty('output-empty', 'No outputs to review', 'Completed task outputs will appear here');
        dom.outputList.appendChild(emptyEl);
        return;
    }

    dom.outputList.innerHTML = filtered.map(renderOutputCard).join('');
    dom.outputBadge.textContent = outputs.filter((t) => !t.review).length;
    updatePanelCount();
}

function createEmpty(id, text, hint) {
    const div = document.createElement('div');
    div.className = 'empty-state';
    div.id = id;
    div.innerHTML = `<p class="empty-state__text">${text}</p><p class="empty-state__hint">${hint}</p>`;
    return div;
}

// =====================================================================
// Socket.IO connection
// =====================================================================

function connectSocket() {
    state.socket = io({
        transports: ['websocket', 'polling'],
        reconnection: true,
        reconnectionDelay: 1000,
        reconnectionAttempts: 10,
    });

    const sock = state.socket;

    sock.on('connect', () => {
        state.connected = true;
        dom.connectionStatus.textContent = 'Connected';
        dom.connectionStatus.className = 'sidebar__status connected';
        sock.emit('join', {});
        sock.emit('get_channels', {});
        showToast('Connected to Cohort', 'success');
    });

    sock.on('disconnect', () => {
        state.connected = false;
        dom.connectionStatus.textContent = 'Disconnected';
        dom.connectionStatus.className = 'sidebar__status disconnected';
    });

    sock.on('connect_error', () => {
        dom.connectionStatus.textContent = 'Connection error';
        dom.connectionStatus.className = 'sidebar__status disconnected';
    });

    // -- Dashboard events --

    sock.on('cohort:team_update', (data) => {
        state.agents = data.agents || [];
        renderTeam();
    });

    sock.on('cohort:task_assigned', (task) => {
        const idx = state.tasks.findIndex((t) => t.task_id === task.task_id);
        if (idx >= 0) {
            state.tasks[idx] = task;
        } else {
            state.tasks.unshift(task);
        }
        renderQueue();
        showToast(`Task assigned to ${task.agent_id}`, 'info');
    });

    sock.on('cohort:task_progress', (data) => {
        const task = state.tasks.find((t) => t.task_id === data.task_id);
        if (task) {
            Object.assign(task, data);
            renderQueue();
        }
    });

    sock.on('cohort:task_complete', (data) => {
        const task = state.tasks.find((t) => t.task_id === data.task_id);
        if (task) {
            Object.assign(task, data);
            renderQueue();
            renderOutputs();
            showToast(`Task completed: ${task.description || task.task_id}`, 'success');
        }
    });

    sock.on('cohort:output_ready', (data) => {
        const task = state.tasks.find((t) => t.task_id === data.task_id);
        if (task) {
            task.output = data.output || data;
            renderOutputs();
            showToast('Output ready for review', 'info');
        }
    });

    sock.on('cohort:review_submitted', (data) => {
        const task = state.tasks.find((t) => t.task_id === data.task_id);
        if (task) {
            task.review = data;
            renderOutputs();
        }
    });

    sock.on('cohort:status_change', (data) => {
        sock.emit('request_team_update', {});
        showToast(`Status: ${data.event_type || 'change'}`, 'info');
    });

    sock.on('cohort:error', (data) => {
        showToast(data.message || 'An error occurred', 'error');
    });

    // -- Chat events (mirroring SMACK exactly) --

    sock.on('channels_list', (data) => {
        state.channels = data.channels || [];
        renderChannels();
        renderFolders();
    });

    sock.on('channel_messages', (data) => {
        state.messages[data.channel_id] = data.messages || [];

        // Backfill members from message history (sender + @mentions)
        (data.messages || []).forEach(msg => autoAddMembersFromMessage(data.channel_id, msg));

        if (data.channel_id === state.currentChannel) {
            renderMessages();
        }
        // Update chat badge with total message count
        let total = 0;
        for (const ch in state.messages) {
            total += state.messages[ch].length;
        }
        if (dom.chatBadge) dom.chatBadge.textContent = total;
    });

    sock.on('new_message', (message) => {
        // Add to state with dedup (SMACK pattern)
        if (!state.messages[message.channel_id]) {
            state.messages[message.channel_id] = [];
        }
        if (state.messages[message.channel_id].some(m => m.id === message.id)) return;
        state.messages[message.channel_id].push(message);

        // Auto-add sender and @mentioned agents as channel members
        autoAddMembersFromMessage(message.channel_id, message);

        // Re-render if current channel
        if (message.channel_id === state.currentChannel) {
            renderMessages();
            scrollToBottom();
        }
    });

    // -- Typing indicator for agent responses --
    sock.on('user_typing', (data) => {
        if (data.channel_id !== state.currentChannel) return;
        const indicator = document.getElementById('typing-indicator');
        if (!indicator) return;

        if (data.typing) {
            const profile = getAgentProfile(data.sender);
            indicator.innerHTML = `
                <div class="typing-indicator">
                    <div class="typing-indicator__avatar" style="background-color: ${profile.color}">${profile.avatar}</div>
                    <span class="typing-indicator__text">${escapeHtml(profile.nickname)} is thinking...</span>
                    <span class="typing-indicator__dots"><span>.</span><span>.</span><span>.</span></span>
                </div>`;
            indicator.style.display = 'block';
            scrollToBottom();
        } else {
            indicator.style.display = 'none';
            indicator.innerHTML = '';
        }
    });

}

// =====================================================================
// Modals
// =====================================================================

function insertMentionTag(agentId) {
    if (!dom.messageInput) return;
    const profile = getAgentProfile(agentId);
    const color = profile.color || '#95A5A6';
    const tag = document.createElement('span');
    tag.className = 'mention-tag';
    tag.contentEditable = 'false';
    tag.style.setProperty('--mention-color', color);
    tag.style.setProperty('--mention-bg', color + '22');
    tag.setAttribute('data-agent-id', agentId);
    tag.textContent = '@' + agentId;

    clearInput();
    dom.messageInput.appendChild(tag);
    const space = document.createTextNode('\u00A0');
    dom.messageInput.appendChild(space);

    // Place cursor after space
    const sel = window.getSelection();
    const range = document.createRange();
    range.setStartAfter(space);
    range.collapse(true);
    sel.removeAllRanges();
    sel.addRange(range);
    dom.messageInput.focus();
}

function openChatForAgent(agentId) {
    // Ensure a channel is selected -- pick current, first available, or create 'general'
    if (!state.currentChannel) {
        if (state.channels.length > 0) {
            switchChannel(state.channels[0].id);
        } else {
            // Bootstrap a general channel
            const defaultChannel = 'general';
            state.currentChannel = defaultChannel;
            if (state.socket && state.connected) {
                state.socket.emit('join_channel', { channel_id: defaultChannel });
                state.socket.emit('get_channels', {});
            }
            switchPanel('chat');
        }
    } else {
        switchPanel('chat');
    }

    insertMentionTag(agentId);
}

function openAssignForAgent(agentId) {
    populateAgentSelect();
    if (agentId) {
        dom.taskAgentSelect.value = agentId;
    }
    dom.assignTaskModal.hidden = false;
}

function closeAssignModal() {
    dom.assignTaskModal.hidden = true;
    dom.assignTaskForm.reset();
}

window.confirmTaskExecution = function(taskId, briefDataId) {
    if (!state.socket || !state.connected) {
        showToast('Not connected', 'error');
        return;
    }

    const brief = (window._pendingBriefs && window._pendingBriefs[briefDataId]) || {};

    state.socket.emit('confirm_task', {
        task_id: taskId,
        brief: brief,
    }, (response) => {
        if (response && response.error) {
            showToast(response.error, 'error');
        } else {
            showToast('Task execution started', 'success');
            // Disable the execute button to prevent double-clicks
            document.querySelectorAll('.task-confirmation-card__actions .btn').forEach(btn => {
                btn.disabled = true;
                btn.textContent = 'Executing...';
            });
        }
    });
};

function populateAgentSelect() {
    dom.taskAgentSelect.innerHTML = state.agents
        .map((a) => `<option value="${escapeHtml(a.agent_id)}">${escapeHtml(a.name || a.agent_id)}</option>`)
        .join('');
}

function openReview(taskId) {
    const task = state.tasks.find((t) => t.task_id === taskId);
    if (!task) return;

    const output = task.output || {};
    const content = output.diff || output.content || output.summary || JSON.stringify(output, null, 2);
    dom.reviewOutputContent.textContent = content;
    dom.reviewTaskId.value = taskId;
    state.selectedVerdict = null;
    dom.reviewSubmit.disabled = true;

    $$('.verdict-btn').forEach((btn) => btn.classList.remove('selected'));

    dom.reviewModal.hidden = false;
}

function closeReviewModal() {
    dom.reviewModal.hidden = true;
    dom.reviewForm.reset();
    state.selectedVerdict = null;
}

// =====================================================================
// Toast notifications
// =====================================================================

function showToast(message, type = 'info') {
    const toast = document.createElement('div');
    toast.className = `toast toast--${type}`;
    toast.textContent = message;
    dom.toastContainer.appendChild(toast);

    setTimeout(() => {
        toast.style.opacity = '0';
        toast.style.transform = 'translateY(20px)';
        toast.style.transition = 'all 300ms ease';
        setTimeout(() => toast.remove(), 300);
    }, 4000);
}

// =====================================================================
// Code block copy
// =====================================================================

function copyCodeBlock(blockId) {
    const block = document.getElementById(blockId);
    if (!block) return;
    navigator.clipboard.writeText(block.textContent).then(() => {
        showToast('Copied to clipboard', 'success');
    });
}

// =====================================================================
// Utilities
// =====================================================================

function escapeHtml(str) {
    if (!str) return '';
    const s = String(str);
    const div = document.createElement('div');
    div.textContent = s;
    return div.innerHTML;
}

function formatTimeAgo(isoStr) {
    if (!isoStr) return '';
    const diff = Date.now() - new Date(isoStr).getTime();
    const mins = Math.floor(diff / 60000);
    if (mins < 1) return 'just now';
    if (mins < 60) return `${mins}m ago`;
    const hours = Math.floor(mins / 60);
    if (hours < 24) return `${hours}h ago`;
    return `${Math.floor(hours / 24)}d ago`;
}

// =====================================================================
// Settings
// =====================================================================

function openSettings() {
    // Load current settings from server
    fetch('/api/settings')
        .then(r => r.json())
        .then(data => {
            if (dom.settingsApiKey) dom.settingsApiKey.value = data.api_key_masked || '';
            if (dom.settingsClaudeCmd) dom.settingsClaudeCmd.value = data.claude_cmd || '';
            if (dom.settingsBossRoot) dom.settingsBossRoot.value = data.boss_root || '';
            if (dom.settingsResponseTimeout) dom.settingsResponseTimeout.value = data.response_timeout || 300;
            if (dom.settingsExecBackend) dom.settingsExecBackend.value = data.execution_backend || 'cli';

            // Show connection status
            updateSettingsConnectionStatus(data.claude_code_connected ? 'ok' : 'unknown',
                data.claude_code_connected ? 'Claude CLI found' : 'Not tested');
        })
        .catch(() => {
            // Fields stay empty if server can't be reached
        });

    if (dom.settingsModal) dom.settingsModal.hidden = false;
}

function closeSettings() {
    if (dom.settingsModal) dom.settingsModal.hidden = true;
}

function saveSettings(e) {
    e.preventDefault();

    const payload = {
        claude_cmd: dom.settingsClaudeCmd ? dom.settingsClaudeCmd.value.trim() : '',
        boss_root: dom.settingsBossRoot ? dom.settingsBossRoot.value.trim() : '',
        response_timeout: dom.settingsResponseTimeout ? parseInt(dom.settingsResponseTimeout.value, 10) : 300,
        execution_backend: dom.settingsExecBackend ? dom.settingsExecBackend.value : 'cli',
    };

    // Only include API key if user typed a real value (not the masked placeholder)
    const apiKeyVal = dom.settingsApiKey ? dom.settingsApiKey.value.trim() : '';
    if (apiKeyVal && !apiKeyVal.startsWith('sk-...')) {
        payload.api_key = apiKeyVal;
    }

    fetch('/api/settings', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(payload),
    })
        .then(r => r.json())
        .then(data => {
            if (data.success) {
                showToast('Settings saved', 'success');
                closeSettings();
            } else {
                showToast(data.error || 'Failed to save settings', 'error');
            }
        })
        .catch(err => {
            showToast('Failed to save settings: ' + err.message, 'error');
        });
}

function testClaudeConnection() {
    updateSettingsConnectionStatus('testing', 'Testing...');

    fetch('/api/settings/test-connection', { method: 'POST' })
        .then(r => r.json())
        .then(data => {
            if (data.success) {
                updateSettingsConnectionStatus('ok', data.message || 'Connected');
            } else {
                updateSettingsConnectionStatus('error', data.error || 'Connection failed');
            }
        })
        .catch(err => {
            updateSettingsConnectionStatus('error', 'Request failed: ' + err.message);
        });
}

function updateSettingsConnectionStatus(status, text) {
    if (dom.settingsConnectionDot) {
        dom.settingsConnectionDot.className = 'settings-connection-dot ' + (status || '');
    }
    if (dom.settingsConnectionText) {
        dom.settingsConnectionText.textContent = text || '';
    }
}

function toggleApiKeyVisibility() {
    if (!dom.settingsApiKey) return;
    const isPassword = dom.settingsApiKey.type === 'password';
    dom.settingsApiKey.type = isPassword ? 'text' : 'password';
    if (dom.toggleApiKeyVis) {
        dom.toggleApiKeyVis.textContent = isPassword ? '[.]' : '[*]';
    }
}

// =====================================================================
// Permissions
// =====================================================================

// Service type metadata (display names + colors)
const SERVICE_TYPES = {
    youtube:    { name: 'YouTube Data API',  color: '#FF0000', icon: 'YT' },
    rss:        { name: 'RSS Feed Reader',   color: '#F99830', icon: 'RS' },
    email_smtp: { name: 'Email (SMTP)',      color: '#4CAF50', icon: 'SM' },
    email_imap: { name: 'Email (IMAP)',      color: '#2196F3', icon: 'IM' },
    github:     { name: 'GitHub API',        color: '#333',    icon: 'GH' },
    slack:      { name: 'Slack Webhook',     color: '#4A154B', icon: 'SL' },
    discord:    { name: 'Discord Webhook',   color: '#5865F2', icon: 'DC' },
    openai:     { name: 'OpenAI API',        color: '#10A37F', icon: 'OA' },
    google:     { name: 'Google Cloud API',  color: '#4285F4', icon: 'GC' },
    aws:        { name: 'AWS Credentials',   color: '#FF9900', icon: 'AW' },
    webhook:    { name: 'Custom Webhook',    color: '#9C27B0', icon: 'WH' },
    custom:     { name: 'Custom Service',    color: '#607D8B', icon: 'CS' },
};

// Local state for permissions editor
let permState = {
    services: [],       // [{ id, type, name, key_masked, has_key, extra }, ...]
    permissions: {},    // { agent_id: { service_id: true/false } }
};

function openPermissions() {
    fetch('/api/permissions')
        .then(r => r.json())
        .then(data => {
            permState.services = data.services || [];
            permState.permissions = data.permissions || {};
            renderServiceKeys();
            renderPermGrid();
        })
        .catch(() => {
            permState.services = [];
            permState.permissions = {};
            renderServiceKeys();
            renderPermGrid();
        });
    if (dom.permissionsModal) dom.permissionsModal.hidden = false;
}

function closePermissions() {
    if (dom.permissionsModal) dom.permissionsModal.hidden = true;
}

function savePermissions() {
    // Collect current checkbox state from DOM
    const checkboxes = document.querySelectorAll('.perm-grid__check');
    checkboxes.forEach(cb => {
        const agentId = cb.dataset.agent;
        const serviceId = cb.dataset.service;
        if (!permState.permissions[agentId]) permState.permissions[agentId] = {};
        permState.permissions[agentId][serviceId] = cb.checked;
    });

    fetch('/api/permissions', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
            services: permState.services,
            permissions: permState.permissions,
        }),
    })
        .then(r => r.json())
        .then(data => {
            if (data.success) {
                showToast('Permissions saved', 'success');
                closePermissions();
            } else {
                showToast(data.error || 'Failed to save', 'error');
            }
        })
        .catch(err => showToast('Save failed: ' + err.message, 'error'));
}

function switchPermTab(tabName) {
    document.querySelectorAll('.perm-tab').forEach(t => {
        t.classList.toggle('active', t.dataset.permTab === tabName);
    });
    if (dom.permPanelServices) dom.permPanelServices.style.display = tabName === 'services' ? '' : 'none';
    if (dom.permPanelAgents) dom.permPanelAgents.style.display = tabName === 'agents' ? '' : 'none';
}

function renderServiceKeys() {
    if (!dom.serviceKeysList) return;

    if (permState.services.length === 0) {
        dom.serviceKeysList.innerHTML = '<div class="perm-empty">No services configured yet.<br>Add a service to get started.</div>';
        return;
    }

    dom.serviceKeysList.innerHTML = permState.services.map((svc, idx) => {
        const meta = SERVICE_TYPES[svc.type] || SERVICE_TYPES.custom;
        const displayName = svc.name || meta.name;
        const statusClass = svc.has_key ? 'active' : 'missing';
        const statusText = svc.has_key ? 'Configured' : 'No key';

        return `
            <div class="service-key-card" data-service-idx="${idx}">
                <div class="service-key-card__icon" style="background-color: ${meta.color}">${meta.icon}</div>
                <div class="service-key-card__info">
                    <div class="service-key-card__name">${escapeHtml(displayName)}</div>
                    <div class="service-key-card__key">${escapeHtml(svc.key_masked || '(not set)')}</div>
                </div>
                <span class="service-key-card__status service-key-card__status--${statusClass}">${statusText}</span>
                <div class="service-key-card__actions">
                    <button class="btn btn--small btn--secondary" onclick="editServiceKey(${idx})" title="Edit key">Edit</button>
                    <button class="btn btn--small btn--danger" onclick="removeServiceKey(${idx})" title="Remove">&times;</button>
                </div>
            </div>`;
    }).join('');
}

function renderPermGrid() {
    if (!dom.permGridHead || !dom.permGridBody) return;

    if (permState.services.length === 0) {
        dom.permGridHead.innerHTML = '';
        dom.permGridBody.innerHTML = '<tr><td colspan="99" class="perm-empty">Add services first, then configure agent access here.</td></tr>';
        return;
    }

    // Header: Agent | Service1 | Service2 | ...
    dom.permGridHead.innerHTML = `<tr>
        <th>Agent</th>
        ${permState.services.map(svc => {
            const meta = SERVICE_TYPES[svc.type] || SERVICE_TYPES.custom;
            return `<th title="${escapeHtml(svc.name || meta.name)}">${meta.icon}</th>`;
        }).join('')}
    </tr>`;

    // Get visible agents (exclude hidden, operators)
    const agents = Object.entries(state.agentProfiles)
        .filter(([, p]) => !p.hidden && p.group !== 'Operators')
        .sort((a, b) => (a[1].name || a[0]).localeCompare(b[1].name || b[0]));

    dom.permGridBody.innerHTML = agents.map(([agentId, profile]) => {
        const agentPerms = permState.permissions[agentId] || {};
        const cells = permState.services.map(svc => {
            const checked = agentPerms[svc.id] ? 'checked' : '';
            return `<td><input type="checkbox" class="perm-grid__check" data-agent="${escapeHtml(agentId)}" data-service="${escapeHtml(svc.id)}" ${checked}></td>`;
        }).join('');

        return `<tr>
            <td><div class="perm-grid__agent-cell">
                <div class="perm-grid__agent-avatar" style="background-color: ${profile.color || '#95A5A6'}">${escapeHtml(profile.avatar || agentId.substring(0, 2).toUpperCase())}</div>
                <span class="perm-grid__agent-name">${escapeHtml(profile.nickname || agentId)}</span>
            </div></td>
            ${cells}
        </tr>`;
    }).join('');
}

function openAddService() {
    if (dom.serviceTypeSelect) dom.serviceTypeSelect.value = 'youtube';
    if (dom.serviceCustomNameGroup) dom.serviceCustomNameGroup.style.display = 'none';
    if (dom.serviceCustomName) dom.serviceCustomName.value = '';
    if (dom.serviceKeyInput) { dom.serviceKeyInput.value = ''; dom.serviceKeyInput.type = 'password'; }
    if (dom.serviceExtraInput) dom.serviceExtraInput.value = '';
    if (dom.addServiceModal) dom.addServiceModal.hidden = false;

    // Reset editing state
    dom.addServiceForm.dataset.editIdx = '';
}

function closeAddService() {
    if (dom.addServiceModal) dom.addServiceModal.hidden = true;
}

function submitAddService(e) {
    e.preventDefault();
    const type = dom.serviceTypeSelect ? dom.serviceTypeSelect.value : 'custom';
    const meta = SERVICE_TYPES[type] || SERVICE_TYPES.custom;
    const customName = (dom.serviceCustomName && dom.serviceCustomName.value.trim()) || '';
    const name = (type === 'custom' || type === 'webhook') && customName ? customName : meta.name;
    const key = dom.serviceKeyInput ? dom.serviceKeyInput.value.trim() : '';
    const extra = dom.serviceExtraInput ? dom.serviceExtraInput.value.trim() : '';

    const editIdx = dom.addServiceForm.dataset.editIdx;

    if (editIdx !== undefined && editIdx !== '') {
        // Editing existing service
        const idx = parseInt(editIdx, 10);
        const svc = permState.services[idx];
        if (svc) {
            svc.type = type;
            svc.name = name;
            if (key) {
                svc.new_key = key;
                svc.has_key = true;
                svc.key_masked = key.length > 8 ? '...' + key.slice(-4) : '...(set)';
            }
            svc.extra = extra;
        }
    } else {
        // New service
        const id = type + '_' + Date.now();
        permState.services.push({
            id,
            type,
            name,
            new_key: key,
            has_key: !!key,
            key_masked: key ? (key.length > 8 ? '...' + key.slice(-4) : '...(set)') : '',
            extra,
        });
    }

    renderServiceKeys();
    renderPermGrid();
    closeAddService();
    showToast('Service added -- save to persist', 'info');
}

function editServiceKey(idx) {
    const svc = permState.services[idx];
    if (!svc) return;
    if (dom.serviceTypeSelect) dom.serviceTypeSelect.value = svc.type;
    if (dom.serviceCustomNameGroup) {
        dom.serviceCustomNameGroup.style.display = (svc.type === 'custom' || svc.type === 'webhook') ? '' : 'none';
    }
    if (dom.serviceCustomName) dom.serviceCustomName.value = svc.name || '';
    if (dom.serviceKeyInput) { dom.serviceKeyInput.value = ''; dom.serviceKeyInput.type = 'password'; dom.serviceKeyInput.placeholder = svc.has_key ? '(leave blank to keep current)' : 'Paste API key...'; }
    if (dom.serviceExtraInput) dom.serviceExtraInput.value = svc.extra || '';
    dom.addServiceForm.dataset.editIdx = idx;
    if (dom.addServiceModal) dom.addServiceModal.hidden = false;
}

function removeServiceKey(idx) {
    const svc = permState.services[idx];
    if (!svc) return;
    if (!confirm('Remove ' + (svc.name || svc.type) + '?')) return;
    const removedId = svc.id;
    permState.services.splice(idx, 1);
    // Clean up permissions referencing this service
    for (const agentId in permState.permissions) {
        delete permState.permissions[agentId][removedId];
    }
    renderServiceKeys();
    renderPermGrid();
    showToast('Service removed -- save to persist', 'info');
}

function toggleServiceKeyVisibility() {
    if (!dom.serviceKeyInput) return;
    const isPassword = dom.serviceKeyInput.type === 'password';
    dom.serviceKeyInput.type = isPassword ? 'text' : 'password';
    if (dom.toggleServiceKeyVis) {
        dom.toggleServiceKeyVis.textContent = isPassword ? '[.]' : '[*]';
    }
}

// =====================================================================
// Event listeners
// =====================================================================

function init() {
    initDom();

    // Nav panel switching
    dom.navItems.forEach((item) => {
        item.addEventListener('click', () => {
            switchPanel(item.dataset.panel);
        });
    });

    // Filter
    if (dom.filterSelect) {
        dom.filterSelect.addEventListener('change', () => {
            state.filter = dom.filterSelect.value;
            if (state.currentPanel === 'queue') renderQueue();
            if (state.currentPanel === 'output') renderOutputs();
        });
    }

    // Refresh
    if (dom.refreshBtn) {
        dom.refreshBtn.addEventListener('click', () => {
            if (state.socket && state.connected) {
                state.socket.emit('request_team_update', {});
                state.socket.emit('get_channels', {});
                if (state.currentChannel) {
                    state.socket.emit('join_channel', { channel_id: state.currentChannel });
                }
                showToast('Refreshing...', 'info');
            }
        });
    }

    // Assign task modal
    if (dom.assignTaskBtn) dom.assignTaskBtn.addEventListener('click', () => openAssignForAgent(null));
    if (dom.assignTaskClose) dom.assignTaskClose.addEventListener('click', closeAssignModal);
    if (dom.assignTaskCancel) dom.assignTaskCancel.addEventListener('click', closeAssignModal);

    if (dom.assignTaskForm) {
        dom.assignTaskForm.addEventListener('submit', (e) => {
            e.preventDefault();
            if (!state.socket || !state.connected) return;

            state.socket.emit('assign_task', {
                agent_id: dom.taskAgentSelect.value,
                description: dom.taskDescriptionInput.value,
                priority: dom.taskPrioritySelect.value,
            }, (response) => {
                closeAssignModal();
                // Switch to chat panel and open the task channel
                if (response && response.task_id) {
                    const taskChannel = 'task-' + response.task_id;
                    // Refresh channels to pick up the new task channel
                    state.socket.emit('get_channels', {});
                    // Give the server a moment to create the channel, then switch
                    setTimeout(() => {
                        switchChannel(taskChannel);
                    }, 500);
                }
            });
        });
    }

    // Message form
    if (dom.messageForm) {
        dom.messageForm.addEventListener('submit', (e) => {
            e.preventDefault();
            const content = getInputPlainText().trim();
            if (!content || !state.socket || !state.connected) return;

            if (!state.currentChannel) {
                showToast('Select a channel first', 'warning');
                return;
            }

            const outgoing = {
                channel_id: state.currentChannel,
                sender: 'user',
                content: content,
            };
            state.socket.emit('send_message', outgoing);

            // Immediately add sender + mentioned agents as members
            autoAddMembersFromMessage(state.currentChannel, outgoing);

            clearInput();
            closeMentionDropdown();
        });
    }

    // @mention autocomplete
    initMentionAutocomplete();

    // Drag and drop
    initDragAndDrop();

    // Load folders from localStorage
    loadFolders();
    renderFolders();

    // [+] Create channel button & modal
    if (dom.addChannelBtn) {
        dom.addChannelBtn.addEventListener('click', () => {
            if (dom.createChannelModal) dom.createChannelModal.hidden = false;
            if (dom.newChannelName) { dom.newChannelName.value = ''; dom.newChannelName.focus(); }
        });
    }
    if (dom.createChannelClose) dom.createChannelClose.addEventListener('click', () => { dom.createChannelModal.hidden = true; });
    if (dom.createChannelCancel) dom.createChannelCancel.addEventListener('click', () => { dom.createChannelModal.hidden = true; });
    if (dom.createChannelForm) {
        dom.createChannelForm.addEventListener('submit', (e) => {
            e.preventDefault();
            const name = dom.newChannelName.value.trim().toLowerCase().replace(/\s+/g, '-');
            if (!name) return;
            // Join the channel (auto-creates on server)
            if (state.socket && state.connected) {
                state.socket.emit('join_channel', { channel_id: name });
                state.socket.emit('get_channels', {});
            }
            state.currentChannel = name;
            dom.createChannelModal.hidden = true;
            switchPanel('chat');
            showToast(`Channel #${name} created`, 'success');
        });
    }

    // [+] Create folder button & modal
    if (dom.addFolderBtn) {
        dom.addFolderBtn.addEventListener('click', () => {
            if (dom.createFolderModal) dom.createFolderModal.hidden = false;
            if (dom.newFolderName) { dom.newFolderName.value = ''; dom.newFolderName.focus(); }
        });
    }
    if (dom.createFolderClose) dom.createFolderClose.addEventListener('click', () => { dom.createFolderModal.hidden = true; });
    if (dom.createFolderCancel) dom.createFolderCancel.addEventListener('click', () => { dom.createFolderModal.hidden = true; });
    if (dom.createFolderForm) {
        dom.createFolderForm.addEventListener('submit', (e) => {
            e.preventDefault();
            const name = dom.newFolderName.value.trim();
            if (!name) return;
            createFolder(name);
            dom.createFolderModal.hidden = true;
            showToast(`Folder "${name}" created`, 'success');
        });
    }

    // [+] Session button (placeholder -- show toast for now)
    if (dom.addSessionBtn) {
        dom.addSessionBtn.addEventListener('click', () => {
            showToast('Session creation coming soon', 'info');
        });
    }

    // [+] Add Member button & dropdown
    loadChannelMembers();
    if (dom.addMemberBtn) {
        dom.addMemberBtn.addEventListener('click', (e) => {
            e.stopPropagation();
            if (!state.currentChannel) {
                showToast('Select a channel first', 'warning');
                return;
            }
            toggleAddMemberDropdown();
        });
    }
    if (dom.addMemberSearch) {
        dom.addMemberSearch.addEventListener('input', (e) => {
            renderAddMemberList(e.target.value);
        });
    }
    // Close dropdown on outside click
    document.addEventListener('click', (e) => {
        if (dom.addMemberDropdown && dom.addMemberDropdown.style.display !== 'none') {
            if (!dom.addMemberDropdown.contains(e.target) && e.target !== dom.addMemberBtn) {
                dom.addMemberDropdown.style.display = 'none';
            }
        }
    });

    // Review modal
    if (dom.reviewClose) dom.reviewClose.addEventListener('click', closeReviewModal);
    if (dom.reviewCancel) dom.reviewCancel.addEventListener('click', closeReviewModal);

    $$('.verdict-btn').forEach((btn) => {
        btn.addEventListener('click', () => {
            $$('.verdict-btn').forEach((b) => b.classList.remove('selected'));
            btn.classList.add('selected');
            state.selectedVerdict = btn.dataset.verdict;
            dom.reviewSubmit.disabled = false;
        });
    });

    if (dom.reviewForm) {
        dom.reviewForm.addEventListener('submit', (e) => {
            e.preventDefault();
            if (!state.socket || !state.connected || !state.selectedVerdict) return;

            state.socket.emit('submit_review', {
                task_id: dom.reviewTaskId.value,
                verdict: state.selectedVerdict,
                notes: dom.reviewNotesInput.value,
            });
            closeReviewModal();
            showToast('Review submitted', 'success');
        });
    }

    // Mobile menu
    if (dom.mobileMenuBtn) {
        dom.mobileMenuBtn.addEventListener('click', () => {
            dom.sidebar.classList.toggle('open');
            dom.sidebarOverlay.classList.toggle('active');
        });
    }

    if (dom.sidebarOverlay) {
        dom.sidebarOverlay.addEventListener('click', () => {
            dom.sidebar.classList.remove('open');
            dom.sidebarOverlay.classList.remove('active');
        });
    }

    // Settings modal
    if (dom.settingsBtn) dom.settingsBtn.addEventListener('click', openSettings);
    if (dom.settingsClose) dom.settingsClose.addEventListener('click', closeSettings);
    if (dom.settingsCancel) dom.settingsCancel.addEventListener('click', closeSettings);
    if (dom.settingsForm) dom.settingsForm.addEventListener('submit', saveSettings);
    if (dom.testConnectionBtn) dom.testConnectionBtn.addEventListener('click', testClaudeConnection);
    if (dom.toggleApiKeyVis) dom.toggleApiKeyVis.addEventListener('click', toggleApiKeyVisibility);

    // Permissions modal
    if (dom.permissionsBtn) dom.permissionsBtn.addEventListener('click', openPermissions);
    if (dom.permissionsClose) dom.permissionsClose.addEventListener('click', closePermissions);
    if (dom.permissionsCancel) dom.permissionsCancel.addEventListener('click', closePermissions);
    if (dom.permissionsSave) dom.permissionsSave.addEventListener('click', savePermissions);
    if (dom.addServiceKeyBtn) dom.addServiceKeyBtn.addEventListener('click', openAddService);
    if (dom.addServiceClose) dom.addServiceClose.addEventListener('click', closeAddService);
    if (dom.addServiceCancel) dom.addServiceCancel.addEventListener('click', closeAddService);
    if (dom.addServiceForm) dom.addServiceForm.addEventListener('submit', submitAddService);
    if (dom.toggleServiceKeyVis) dom.toggleServiceKeyVis.addEventListener('click', toggleServiceKeyVisibility);

    // Permissions tab switching
    if (dom.permTabs) {
        dom.permTabs.addEventListener('click', (e) => {
            const tab = e.target.closest('.perm-tab');
            if (tab && tab.dataset.permTab) switchPermTab(tab.dataset.permTab);
        });
    }

    // Show/hide custom name field based on service type
    if (dom.serviceTypeSelect) {
        dom.serviceTypeSelect.addEventListener('change', () => {
            const isCustom = dom.serviceTypeSelect.value === 'custom' || dom.serviceTypeSelect.value === 'webhook';
            if (dom.serviceCustomNameGroup) dom.serviceCustomNameGroup.style.display = isCustom ? '' : 'none';
        });
    }

    // Close modals on backdrop click
    [dom.assignTaskModal, dom.reviewModal, dom.settingsModal, dom.permissionsModal, dom.addServiceModal, dom.createChannelModal, dom.createFolderModal].forEach((modal) => {
        if (modal) {
            modal.addEventListener('click', (e) => {
                if (e.target === modal) {
                    modal.hidden = true;
                }
            });
        }
    });

    // Load agent registry, then connect
    fetchAgentRegistry().then(() => {
        connectSocket();
    });

    // Initial render
    switchPanel('team');
}

// Boot
document.addEventListener('DOMContentLoaded', init);
