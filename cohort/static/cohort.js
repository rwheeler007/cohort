/**
 * Cohort - Coding Team Dashboard
 *
 * Socket.IO client connecting to the 8-event dashboard contract
 * plus chat events for roundtable discussions.
 */

// =====================================================================
// State
// =====================================================================

const state = {
    currentPanel: 'team',
    agents: [],
    tasks: [],
    outputs: [],
    workQueue: [],
    filter: 'all',
    selectedVerdict: null,
    socket: null,
    connected: false,

    // Chat state
    agentProfiles: {},       // Agent registry (avatar, color, nickname, role)
    currentChannel: null,    // Currently selected channel
    channels: [],            // List of active (non-archived) channels
    archivedChannels: [],    // List of archived channels
    messages: {},            // Messages per channel: { channelId: [msg, ...] }
    replyingTo: null,        // Message ID being replied to

    // Response mode: per-channel toggle (Smarter is default)
    responseModeChannels: {},  // channel_id -> "smart" | "smarter" | "smartest"
    smartestAvailable: false,  // Set to true when Claude CLI is detected

    // Folders: { id, name, channelIds: [], open: bool }
    folders: [],

    // Channel members: { channelId: [agentId, ...] }
    channelMembers: {},

    // Tools from boss_config.yaml
    tools: [],
    adminMode: false,

    // Roundtable sessions
    sessions: [],

    // Social media pending posts (for Pending Review panel)
    pendingSocialPosts: [],
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
        panelTasks: $('#panel-tasks'),
        panelOutput: $('#panel-output'),
        panelTool: $('#panel-tool'),
        toolPanelContent: $('#tool-panel-content'),
        panelTitle: $('#panel-title'),
        panelSubtitle: $('#panel-subtitle'),
        panelCount: $('#panel-count'),
        filterContainer: $('#filter-container'),
        filterSelect: $('#filter-select'),
        agentGrid: $('#agent-grid'),
        taskList: $('#task-list'),
        taskBadge: $('#task-badge'),
        workQueueList: $('#work-queue-list'),
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
        agentChatList: $('#agent-chat-list'),
        archivedChatList: $('#archived-chat-list'),
        folderList: $('#folder-list'),
        sidebarSessionList: $('#session-list'),
        sidebarToolList: $('#tool-list'),
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
        settingsAgentsRoot: $('#settings-agents-root'),
        settingsResponseTimeout: $('#settings-response-timeout'),
        settingsExecBackend: $('#settings-exec-backend'),
        settingsConnectionDot: $('#settings-connection-dot'),
        settingsConnectionText: $('#settings-connection-text'),
        settingsUserName: $('#settings-user-name'),
        settingsUserRole: $('#settings-user-role'),
        settingsUserAvatar: $('#settings-user-avatar'),
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
        permPanelToolDefaults: $('#perm-panel-tool-defaults'),
        permPanelFilePerms: $('#perm-panel-file-perms'),
        toolDefaultsGrid: $('#tool-defaults-grid'),
        toolDefaultsDenied: $('#tool-defaults-denied'),
        filePermsGrid: $('#file-perms-grid'),

        // Tool Permissions modal (per-agent)
        toolPermsModal: $('#tool-perms-modal'),
        toolPermsClose: $('#tool-perms-close'),
        toolPermsCancel: $('#tool-perms-cancel'),
        toolPermsSave: $('#tool-perms-save'),
        toolPermsReset: $('#tool-perms-reset'),
        toolPermsTitle: $('#tool-perms-title'),
        toolPermsProfile: $('#tool-perms-profile'),
        toolPermsGrid: $('#tool-perms-grid'),
        toolPermsOverrideNote: $('#tool-perms-override-note'),

        // Add Service sub-modal
        addServiceModal: $('#add-service-modal'),
        addServiceClose: $('#add-service-close'),
        addServiceCancel: $('#add-service-cancel'),
        addServiceForm: $('#add-service-form'),
        serviceTypeSelect: $('#service-type-select'),
        serviceCustomNameGroup: $('#service-custom-name-group'),
        serviceCustomName: $('#service-custom-name'),
        serviceDynamicFields: $('#service-dynamic-fields'),

        // Members sidebar
        addMemberBtn: $('#add-member-btn'),
        addMemberDropdown: $('#add-member-dropdown'),
        addMemberSearch: $('#add-member-search'),
        addMemberList: $('#add-member-list'),

        // Setup wizard
        setupWizard: $('#setup-wizard'),
        setupWizardClose: $('#setup-wizard-close'),
        setupBackBtn: $('#setup-back-btn'),
        setupNextBtn: $('#setup-next-btn'),
        setupSkipBtn: $('#setup-skip-btn'),
        setupFinishBtn: $('#setup-finish-btn'),
        setupRerunBtn: $('#settings-rerun-setup'),
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
        subtitle: 'Sequential execution queue',
        panel: () => dom.panelQueue,
        filter: false,
    },
    tasks: {
        title: 'Tasks',
        subtitle: 'Agent task assignment and progress',
        panel: () => dom.panelTasks,
        filter: true,
    },
    output: {
        title: 'Pending Review',
        subtitle: 'Task outputs and social posts awaiting approval',
        panel: () => dom.panelOutput,
        filter: true,
    },
    tool: {
        title: 'Tool',
        subtitle: '',
        panel: () => dom.panelTool,
        filter: false,
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
    [dom.panelTeam, dom.panelChat, dom.panelQueue, dom.panelTasks, dom.panelOutput, dom.panelTool].forEach((p) => {
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
        case 'queue': {
            const wqItems = state.workQueue.filter((i) => i.status !== 'completed' && i.status !== 'failed' && i.status !== 'cancelled');
            count = `${wqItems.length} items`;
            break;
        }
        case 'tasks':
            count = `${state.tasks.length} tasks`;
            break;
        case 'output': {
            const socialCount = (state.pendingSocialPosts || []).length;
            const totalItems = state.outputs.length + socialCount;
            count = `${totalItems} items`;
            break;
        }
    }
    dom.panelCount.textContent = count;
}

// =====================================================================
// Agent Profiles
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

/** Apply the user's display name, role, and initials to the 'user' profile.
 *  Also persists to localStorage so identity survives reloads instantly. */
function applyUserIdentity(displayName, displayRole, displayAvatar) {
    if (!displayName && !displayRole && !displayAvatar) return;
    const avatarText = displayAvatar || (displayName ? displayName.substring(0, 2).toUpperCase() : 'U');
    if (!state.agentProfiles['user']) {
        state.agentProfiles['user'] = {
            name: displayName || 'User',
            nickname: displayName || 'User',
            avatar: avatarText,
            color: '#95E1D3',
            role: displayRole || 'Operator',
            group: 'Operators',
        };
    } else {
        if (displayName) {
            state.agentProfiles['user'].name = displayName;
            state.agentProfiles['user'].nickname = displayName;
        }
        state.agentProfiles['user'].avatar = avatarText;
        if (displayRole) {
            state.agentProfiles['user'].role = displayRole;
        }
    }
    // Persist to localStorage for instant restore on reload
    try {
        localStorage.setItem('cohort_user_identity', JSON.stringify({
            name: displayName, role: displayRole, avatar: displayAvatar,
        }));
    } catch { /* ignore */ }
    // Re-render if currently viewing a channel (so existing messages update)
    if (state.currentChannel) {
        renderMessages();
        updateParticipants();
    }
}

/** Restore user identity from localStorage (runs before server fetch). */
function restoreUserIdentity() {
    try {
        const raw = localStorage.getItem('cohort_user_identity');
        if (raw) {
            const { name, role, avatar } = JSON.parse(raw);
            if (name || role || avatar) applyUserIdentity(name, role, avatar);
        }
    } catch { /* ignore */ }
}

// =====================================================================
// Message formatting
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
    const now = new Date();
    const time = date.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' });

    const isToday = date.getFullYear() === now.getFullYear()
        && date.getMonth() === now.getMonth()
        && date.getDate() === now.getDate();

    if (isToday) return `Today ${time}`;

    const isYesterday = (() => {
        const y = new Date(now);
        y.setDate(y.getDate() - 1);
        return date.getFullYear() === y.getFullYear()
            && date.getMonth() === y.getMonth()
            && date.getDate() === y.getDate();
    })();

    if (isYesterday) return `Yesterday ${time}`;

    return date.toLocaleDateString([], { month: 'short', day: 'numeric' }) + ' ' + time;
}

// =====================================================================
// Message rendering
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

        // Detect roundtable setup cards
        let roundtableCard = '';
        const rtMatch = message.content.match(/---ROUNDTABLE_READY---\s*\n([\s\S]*?)\n\s*---END_ROUNDTABLE_READY---/);
        if (rtMatch) {
            const rtFields = {};
            rtMatch[1].replace(/^(Topic|Channel|Agents|Turns)\s*:\s*(.+)/gm, (_, k, v) => {
                rtFields[k.toLowerCase()] = v.trim();
            });

            const rtDataId = 'rt_' + Date.now() + '_' + Math.random().toString(36).slice(2, 6);
            window._pendingRoundtables = window._pendingRoundtables || {};
            window._pendingRoundtables[rtDataId] = {
                topic: rtFields.topic || '',
                channel_name: rtFields.channel || '',
                suggested_agents: (rtFields.agents || '').split(',').map(a => a.trim()).filter(Boolean),
                max_turns: parseInt(rtFields.turns, 10) || 20,
            };

            const agentChips = (rtFields.agents || '').split(',').map(a => {
                const agent = a.trim();
                const profile = getAgentProfile(agent);
                return `<span class="roundtable-agent-chip" style="--agent-color: ${profile.color}" title="${profile.name}">${profile.avatar} ${escapeHtml(agent)}</span>`;
            }).join(' ');

            roundtableCard = `
                <div class="roundtable-setup-card">
                    <div class="roundtable-setup-card__header">Session Ready</div>
                    <div class="roundtable-setup-card__fields">
                        <div class="roundtable-setup-card__field"><strong>Topic:</strong> ${escapeHtml(rtFields.topic || '')}</div>
                        <div class="roundtable-setup-card__field"><strong>Channel:</strong> <code>${escapeHtml(rtFields.channel || '')}</code></div>
                        <div class="roundtable-setup-card__field"><strong>Agents:</strong> ${agentChips}</div>
                        <div class="roundtable-setup-card__field"><strong>Max Turns:</strong> ${escapeHtml(rtFields.turns || '20')}</div>
                    </div>
                    <div class="roundtable-setup-card__actions">
                        <button class="btn btn--primary btn--small" onclick="confirmRoundtableSetup(window._pendingRoundtables['${rtDataId}'])">Start Session</button>
                    </div>
                </div>`;
        }

        // Model info badge
        let modelBadge = '';
        if (message.metadata) {
            const model = message.metadata.model;
            const tier = message.metadata.tier;
            const pipeline = message.metadata.pipeline;
            if (model) {
                const tierLabel = tier != null ? `T${tier}` : '';
                const modelShort = model.length > 20 ? model.substring(0, 20) : model;
                const elapsed = message.metadata.elapsed_seconds;
                const elapsedStr = elapsed != null ? ` ${elapsed}s` : '';
                const pipelineLabel = pipeline === 'smartest' ? ' [S++]'
                    : pipeline === 'smartest-degraded' ? ' [S++ degraded]'
                    : '';
                modelBadge = `<span class="message__model-badge${pipeline === 'smartest' ? ' smartest-pipeline' : pipeline === 'smartest-degraded' ? ' degraded-pipeline' : ''}" title="Tier ${tier || '?'} - ${model}${elapsedStr}${pipeline ? ' | pipeline: ' + pipeline : ''}">${tierLabel} ${modelShort}${elapsedStr}${pipelineLabel}</span>`;
            }
        }

        // Token count badge
        let tokenBadge = '';
        if (message.metadata) {
            const tokIn = message.metadata.tokens_in;
            const tokOut = message.metadata.tokens_out;
            if (tokIn != null || tokOut != null) {
                const inStr = tokIn != null ? tokIn.toLocaleString() : '?';
                const outStr = tokOut != null ? tokOut.toLocaleString() : '?';
                tokenBadge = `<span class="message__token-badge" title="Tokens: ${inStr} in / ${outStr} out">${inStr}/${outStr} tok</span>`;
            }
        }

        // Action buttons (hidden by default, shown on hover)
        const isSystem = (message.message_type || 'chat') === 'system';
        const actionsHtml = isSystem ? '' : `
            <div class="message__actions">
                <button class="message__action-btn" onclick="replyToMessage('${message.id}', '${escapeHtml(profile.nickname)}')" title="Reply">Reply</button>
                <button class="message__action-btn" onclick="copyMessage('${message.id}')" title="Copy">Copy</button>
                <button class="message__action-btn" onclick="resendMessage('${message.id}')" title="Resend">Resend</button>
                <button class="message__action-btn message__action-btn--danger" onclick="deleteMessage('${message.id}')" title="Delete">Delete</button>
            </div>`;

        return `
            <div class="message ${typeClass}" style="--agent-color: ${profile.color}" data-message-id="${message.id}">
                <div class="message__avatar" title="${profile.name} - ${profile.role}">${profile.avatar}</div>
                <div class="message__content">
                    <div class="message__header">
                        <span class="message__sender" style="color: ${profile.color}">${escapeHtml(profile.nickname)}</span>
                        <span class="message__role">${profile.role}</span>
                        ${modelBadge}
                        ${tokenBadge}
                        <span class="message__time">${time}</span>
                    </div>
                    ${threadIndicator}
                    <div class="message__body">${formatMessageContent(message.content)}</div>
                    ${confirmationCard}
                    ${roundtableCard}
                    ${actionsHtml}
                </div>
            </div>
        `;
    }).join('');

    updatePanelCount();
    scrollToBottom();
}

function scrollToBottom() {
    if (!dom.messagesContainer) return;
    // Defer scroll until after the browser has laid out new content.
    // Double-rAF ensures innerHTML reflow is complete before reading scrollHeight.
    requestAnimationFrame(() => {
        requestAnimationFrame(() => {
            dom.messagesContainer.scrollTop = dom.messagesContainer.scrollHeight;
        });
    });
}


// =====================================================================
// Sidebar session list rendering
// =====================================================================

async function fetchSessions() {
    try {
        const resp = await fetch('/api/sessions/sessions');
        if (!resp.ok) return;
        const data = await resp.json();
        state.sessions = data.sessions || [];
        renderSidebarSessions();
    } catch (err) {
        console.warn('Failed to fetch sessions:', err);
    }
}

function renderSidebarSessions() {
    if (!dom.sidebarSessionList) return;

    const sessions = state.sessions || [];
    // Show non-completed sessions
    const activeSessions = sessions.filter(s => s.state !== 'completed');

    if (activeSessions.length === 0) {
        dom.sidebarSessionList.innerHTML = '<li style="padding: 4px 16px; color: var(--color-text-muted); font-size: 11px;">No active sessions</li>';
        return;
    }

    dom.sidebarSessionList.innerHTML = activeSessions.map(session => {
        const channelId = session.channel_id;
        const isActive = channelId === state.currentChannel;
        const statusDot = session.state === 'active' ? 'active'
            : session.state === 'paused' ? 'paused'
            : session.state === 'initializing' ? 'initializing'
            : 'concluding';
        const label = session.topic.length > 30 ? session.topic.slice(0, 30) + '...' : session.topic;
        const turnInfo = `${session.current_turn}/${session.max_turns}`;

        return `
            <li class="channel-item ${isActive ? 'active' : ''}"
                data-channel="${escapeHtml(channelId)}"
                onclick="switchChannel('${escapeHtml(channelId)}')"
                title="${escapeHtml(session.topic)} (${session.state})">
                <span class="sidebar-session-dot sidebar-session-dot--${statusDot}"></span>
                <span class="channel-item__name">${escapeHtml(label)}</span>
                <span class="sidebar-session-turn">${turnInfo}</span>
            </li>`;
    }).join('');
}

// =====================================================================
// Roundtable guided setup
// =====================================================================

const SESSION_SETUP_CHANNEL = 'session-setup';

// Current pending roundtable config (updated as user refines)
let pendingRoundtableConfig = null;

function startRoundtableSetup() {
    // Switch to chat panel and open the setup channel
    switchPanel('chat');

    // Post greeting as system message (auto-creates channel)
    if (state.socket && state.connected) {
        state.socket.emit('send_message', {
            channel_id: SESSION_SETUP_CHANNEL,
            sender: 'system',
            content: (
                '**Session Setup**\n\n' +
                'What would you like to discuss? Describe the topic naturally ' +
                'and I\'ll suggest the right agents and set everything up.\n\n' +
                '*Example: "Review our API authentication design with the security and python teams"*'
            ),
        });

        // Give server a moment to create channel, then switch
        state.socket.emit('get_channels', {});
        setTimeout(() => {
            switchChannel(SESSION_SETUP_CHANNEL);
        }, 400);
    }

    pendingRoundtableConfig = null;
}

async function handleRoundtableSetupMessage(content) {
    // Parse user's message into roundtable config
    try {
        const resp = await fetch('/api/sessions/setup-parse', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
                message: content,
                context: pendingRoundtableConfig,
            }),
        });
        const data = await resp.json();
        if (!data.success || !data.config) {
            return;
        }

        pendingRoundtableConfig = data.config;
        const cfg = data.config;

        // Build agent list display
        const agentList = cfg.suggested_agents.map(a => `\`${a}\``).join(', ') || '_none identified_';

        // Post the config as a system message with embedded card
        const cardContent = (
            `Here's what I've set up:\n\n` +
            `---ROUNDTABLE_READY---\n` +
            `Topic: ${cfg.topic}\n` +
            `Channel: ${cfg.channel_name}\n` +
            `Agents: ${cfg.suggested_agents.join(', ')}\n` +
            `Turns: ${cfg.max_turns}\n` +
            `---END_ROUNDTABLE_READY---\n\n` +
            `**Topic:** ${cfg.topic}\n` +
            `**Channel:** ${cfg.channel_name}\n` +
            `**Agents:** ${agentList}\n` +
            `**Max Turns:** ${cfg.max_turns}\n\n` +
            `_You can refine this — try "add web_developer" or "make it 30 turns". ` +
            `Or click **Start Session** below when ready._`
        );

        state.socket.emit('send_message', {
            channel_id: SESSION_SETUP_CHANNEL,
            sender: 'system',
            content: cardContent,
        });
    } catch (err) {
        console.warn('Session setup parse failed:', err);
    }
}

async function confirmRoundtableSetup(configOverride) {
    const cfg = configOverride || pendingRoundtableConfig;
    if (!cfg) {
        showToast('No session configuration ready', 'warning');
        return;
    }

    try {
        const resp = await fetch('/api/sessions/start', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
                channel_id: cfg.channel_name,
                topic: cfg.topic,
                initial_agents: cfg.suggested_agents,
                max_turns: cfg.max_turns,
            }),
        });
        const data = await resp.json();
        if (data.success) {
            // Clean up setup channel
            deleteSetupChannel();
            showToast('Session started!', 'success');
            fetchSessions();
            // Switch to the new session channel
            state.socket.emit('get_channels', {});
            setTimeout(() => switchChannel(cfg.channel_name), 500);
        } else {
            showToast(data.error || 'Failed to start session', 'error');
        }
    } catch (err) {
        showToast('Error starting session:' + err.message, 'error');
    }

    pendingRoundtableConfig = null;
}

function deleteSetupChannel() {
    // Remove from local state (server auto-created it, we just stop showing it)
    state.channels = state.channels.filter(ch => (ch.id || ch.name) !== SESSION_SETUP_CHANNEL);
    delete state.messages[SESSION_SETUP_CHANNEL];
    renderChannels();
    renderFolders();
}

// Expose to onclick handlers in rendered HTML
window.confirmRoundtableSetup = confirmRoundtableSetup;

// =====================================================================
// Message action handlers (reply, copy, resend, delete)
// =====================================================================

function replyToMessage(messageId, senderNickname) {
    state.replyingTo = messageId;
    showReplyIndicator(messageId, senderNickname);

    // Auto-tag the sender
    const input = dom.messageInput;
    if (!input) return;
    const current = input.textContent || '';
    const mention = `@${senderNickname}`;
    if (!current.includes(mention)) {
        input.textContent = current ? `${mention} ${current}` : `${mention} `;
    }
    input.focus();
    // Move cursor to end
    const range = document.createRange();
    const sel = window.getSelection();
    range.selectNodeContents(input);
    range.collapse(false);
    sel.removeAllRanges();
    sel.addRange(range);
}

function showReplyIndicator(messageId, senderNickname) {
    const existing = document.querySelector('.reply-indicator');
    if (existing) existing.remove();

    const indicator = document.createElement('div');
    indicator.className = 'reply-indicator';
    indicator.innerHTML = `
        <span>Replying to <strong>${escapeHtml(senderNickname)}</strong></span>
        <button onclick="cancelReply()" title="Cancel reply">[x]</button>
    `;

    const inputArea = document.querySelector('.message-input-area');
    if (inputArea) inputArea.insertBefore(indicator, inputArea.firstChild);
}

function cancelReply() {
    state.replyingTo = null;
    const indicator = document.querySelector('.reply-indicator');
    if (indicator) indicator.remove();
}

function copyMessage(messageId) {
    const messages = state.messages[state.currentChannel] || [];
    const message = messages.find(m => m.id === messageId);
    if (!message) return;

    navigator.clipboard.writeText(message.content).then(() => {
        showToast('Copied to clipboard', 'success');
    }).catch(() => {
        showToast('Failed to copy', 'error');
    });
}

function resendMessage(messageId) {
    const messages = state.messages[state.currentChannel] || [];
    const message = messages.find(m => m.id === messageId);
    if (!message || !dom.messageInput) return;

    dom.messageInput.textContent = message.content;
    dom.messageInput.focus();
    const range = document.createRange();
    const sel = window.getSelection();
    range.selectNodeContents(dom.messageInput);
    range.collapse(false);
    sel.removeAllRanges();
    sel.addRange(range);
}

async function deleteMessage(messageId) {
    if (!confirm('Delete this message?')) return;

    if (!state.socket || !state.connected) {
        showToast('Not connected', 'error');
        return;
    }

    state.socket.emit('delete_message', { message_id: messageId, channel_id: state.currentChannel }, (resp) => {
        if (resp && resp.success) {
            const msgs = state.messages[state.currentChannel];
            if (msgs) {
                state.messages[state.currentChannel] = msgs.filter(m => m.id !== messageId);
                renderMessages();
            }
        } else {
            showToast(resp?.error || 'Failed to delete message', 'error');
        }
    });
}

// Expose message action handlers to onclick in rendered HTML
window.replyToMessage = replyToMessage;
window.copyMessage = copyMessage;
window.resendMessage = resendMessage;
window.deleteMessage = deleteMessage;
window.cancelReply = cancelReply;

// =====================================================================
// Sidebar tools list rendering
// =====================================================================

async function fetchTools() {
    try {
        const resp = await fetch('/api/tools');
        if (!resp.ok) return;
        const data = await resp.json();
        state.tools = data.tools || [];
        renderSidebarTools();
    } catch (err) {
        console.warn('Failed to fetch tools:', err);
    }
}

function renderSidebarTools() {
    if (!dom.sidebarToolList) return;

    const tools = state.tools || [];
    if (tools.length === 0) {
        dom.sidebarToolList.innerHTML = '<li class="sidebar-tool-empty">No tools configured</li>';
        return;
    }

    dom.sidebarToolList.innerHTML = tools.map(tool => {
        const isActive = state.currentTool === tool.id && state.currentPanel === 'tool';
        return `
            <li class="sidebar-tool-item ${isActive ? 'active' : ''}" title="${escapeHtml(tool.description)}"
                onclick="openToolDetail('${escapeHtml(tool.id)}')">
                <span class="sidebar-tool-icon">[*]</span>
                <span class="channel-item__name">${escapeHtml(tool.name)}</span>
            </li>`;
    }).join('');
}

function openToolDetail(toolId) {
    const tool = (state.tools || []).find(t => t.id === toolId);
    if (!tool) return;

    state.currentTool = toolId;
    state.currentChannel = null;

    // Update panel header with tool name
    panelConfig.tool.title = tool.name;
    panelConfig.tool.subtitle = tool.description || '';
    switchPanel('tool');
    renderSidebarTools();
    renderChannels();
    renderToolPanel(tool);
}

// =====================================================================
// Tool panel renderers
// =====================================================================

async function renderToolPanel(tool) {
    if (!dom.toolPanelContent) return;

    // Hide help chat by default; renderers that use it will call showToolHelpChat()
    const helpEl = document.getElementById('tool-help-chat');
    if (helpEl) helpEl.style.display = 'none';

    const renderer = toolRenderers[tool.id];
    if (renderer) {
        dom.toolPanelContent.innerHTML = '<div class="tool-dashboard"><p style="color:var(--color-text-secondary)">Loading...</p></div>';
        await renderer(tool);
    } else {
        dom.toolPanelContent.innerHTML = renderGenericToolPanel(tool);
    }
}

const toolRenderers = {
    comms_service: renderCommsPanel,
    web_search: renderWebSearchPanel,
    youtube_service: renderYouTubePanel,
    intel_scheduler: renderRSSPanel,
    content_monitor_scheduler: renderContentMonitorPanel,
    document_processor: renderDocProcessorPanel,
    health_monitor: renderHealthMonitorPanel,
    llm_manager: renderLLMManagerPanel,
};

function toolHeader(tool, statusHtml) {
    return `
        <div class="tool-dashboard__header">
            <div class="tool-dashboard__icon">[*]</div>
            <div>
                <h2 class="tool-dashboard__title">${escapeHtml(tool.name)}</h2>
                <p class="tool-dashboard__desc">${escapeHtml(tool.description || '')}</p>
            </div>
            <div class="tool-dashboard__status" id="tool-status">
                ${statusHtml}
            </div>
        </div>`;
}

function configCard(label, value) {
    const muteClass = value ? '' : ' tool-config-card__value--muted';
    const display = value || 'Not configured';
    return `
        <div class="tool-config-card">
            <p class="tool-config-card__label">${escapeHtml(label)}</p>
            <p class="tool-config-card__value${muteClass}">${escapeHtml(display)}</p>
        </div>`;
}

function _editableCardWrap(label, value, toolId, key, editorHtml) {
    const muteClass = value ? '' : ' tool-config-card__value--muted';
    const display = value || 'Not configured';
    return `
        <div class="tool-config-card tool-config-card--editable" data-config-key="${escapeHtml(key)}" data-tool-id="${escapeHtml(toolId)}">
            <div class="tool-config-card__header">
                <p class="tool-config-card__label">${escapeHtml(label)}</p>
                <button class="btn btn--icon btn--edit-config" title="Edit" onclick="editConfigCard(this)">
                    [e]
                </button>
            </div>
            <div class="tool-config-card__display">
                <p class="tool-config-card__value${muteClass}">${escapeHtml(display)}</p>
            </div>
            <div class="tool-config-card__editor" style="display:none">
                ${editorHtml}
                <div class="tool-config-card__editor-actions">
                    <button class="btn btn--primary btn--sm" onclick="saveConfigCard(this)">[ok]</button>
                    <button class="btn btn--secondary btn--sm" onclick="cancelConfigCard(this)">[x]</button>
                </div>
            </div>
        </div>`;
}

function configSelect(label, value, options, toolId, key) {
    const opts = options.map(o => `<option value="${escapeHtml(o)}"${o === value ? ' selected' : ''}>${escapeHtml(o)}</option>`).join('');
    return _editableCardWrap(label, value, toolId, key,
        `<select class="tool-config-card__input">${opts}</select>`);
}

function configNumber(label, value, min, max, toolId, key, suffix) {
    const sfx = suffix ? ` <span class="tool-config-card__suffix">${escapeHtml(suffix)}</span>` : '';
    const displayVal = suffix ? `${value} ${suffix}` : String(value);
    return _editableCardWrap(label, displayVal, toolId, key,
        `<div class="tool-config-card__number-row"><input type="number" class="tool-config-card__input" value="${escapeHtml(String(value))}" min="${min}" max="${max}" />${sfx}</div>`);
}

function configText(label, value, toolId, key) {
    return _editableCardWrap(label, value, toolId, key,
        `<input type="text" class="tool-config-card__input" value="${escapeHtml(value || '')}" />`);
}

function configLocked(label, value) {
    const muteClass = value ? '' : ' tool-config-card__value--muted';
    const display = value || 'Not configured';
    return `
        <div class="tool-config-card tool-config-card--locked">
            <div class="tool-config-card__header">
                <p class="tool-config-card__label">${escapeHtml(label)}</p>
                <button class="btn btn--icon btn--lock-config" title="Admin Mode required" onclick="onLockedFieldClick()">
                    [lock]
                </button>
            </div>
            <p class="tool-config-card__value tool-config-card__value--locked${muteClass}">${escapeHtml(display)}</p>
        </div>`;
}

function onLockedFieldClick() {
    showToast('Enable Admin Mode in Settings to edit this field', 'warning');
    openSettings();
}

function configAdminSelect(label, value, options, toolId, key) {
    return state.adminMode ? configSelect(label, value, options, toolId, key) : configLocked(label, value);
}

function configAdminNumber(label, value, min, max, toolId, key, suffix) {
    const displayVal = suffix ? `${value} ${suffix}` : String(value);
    return state.adminMode ? configNumber(label, value, min, max, toolId, key, suffix) : configLocked(label, displayVal);
}

function configAdminText(label, value, toolId, key) {
    return state.adminMode ? configText(label, value, toolId, key) : configLocked(label, value);
}

function configSection(title, cardsHtml) {
    return `
        <div class="tool-config-section">
            <h3 class="tool-config-section__title">${escapeHtml(title)}</h3>
            <div class="tool-config-grid">${cardsHtml}</div>
        </div>`;
}

function configSectionFull(title, contentHtml) {
    return `
        <div class="tool-config-section">
            <h3 class="tool-config-section__title">${escapeHtml(title)}</h3>
            <div class="tool-config-grid--full">${contentHtml}</div>
        </div>`;
}

/* ── Interactive tool components ── */

function statCard(label, value, opts = {}) {
    const cls = opts.color ? ` tool-stat-card__value--${opts.color}` : '';
    const sub = opts.subtitle ? `<p class="tool-stat-card__sub">${escapeHtml(opts.subtitle)}</p>` : '';
    return `<div class="tool-stat-card">
        <p class="tool-stat-card__value${cls}">${escapeHtml(String(value))}</p>
        <p class="tool-stat-card__label">${escapeHtml(label)}</p>
        ${sub}
    </div>`;
}

function statRow(cards) {
    return `<div class="tool-stat-row">${cards}</div>`;
}

function statusGrid(items) {
    if (!items || items.length === 0) return '<p style="color:var(--color-text-muted);font-size:var(--font-size-sm);font-style:italic">No data available</p>';
    return `<div class="tool-status-grid">${items.map(i => {
        const dotCls = i.status || 'unknown';
        const detail = i.detail ? `<span class="tool-status-item__detail">${escapeHtml(i.detail)}</span>` : '';
        return `<div class="tool-status-item">
            <span class="tool-status-item__dot tool-status-item__dot--${dotCls}"></span>
            <span class="tool-status-item__name">${escapeHtml(i.name)}</span>
            ${detail}
        </div>`;
    }).join('')}</div>`;
}

function activityLog(items, opts = {}) {
    if (!items || items.length === 0) {
        return `<div class="tool-activity-log"><div class="tool-activity-log__empty">${escapeHtml(opts.emptyMsg || 'No recent activity')}</div></div>`;
    }
    return `<div class="tool-activity-log">${items.map(i => {
        const dotCls = i.status || 'info';
        const clickable = i.detail ? ' tool-activity-log__item--clickable' : '';
        const onclick = i.detail ? ' onclick="this.classList.toggle(\'tool-activity-log__item--expanded\')"' : '';
        const detail = i.detail ? `<div class="tool-activity-log__detail">${escapeHtml(i.detail)}</div>` : '';
        return `<div class="tool-activity-log__item${clickable}"${onclick}>
            <span class="tool-activity-log__time">${escapeHtml(i.time || '')}</span>
            <span class="tool-activity-log__dot tool-activity-log__dot--${dotCls}"></span>
            <span class="tool-activity-log__msg">${escapeHtml(i.message || '')}${detail}</span>
        </div>`;
    }).join('')}</div>`;
}

function progressBar(current, max, opts = {}) {
    const pct = max > 0 ? Math.min(100, Math.round((current / max) * 100)) : 0;
    const colorCls = pct > 90 ? ' tool-progress__fill--error' : pct > 70 ? ' tool-progress__fill--warning' : ' tool-progress__fill--success';
    const label = opts.label || '';
    return `<div class="tool-progress">
        <div class="tool-progress__label">
            <span>${escapeHtml(label)}</span>
            <span>${current} / ${max}${opts.suffix ? ' ' + escapeHtml(opts.suffix) : ''}</span>
        </div>
        <div class="tool-progress__bar">
            <div class="tool-progress__fill${colorCls}" style="width:${pct}%"></div>
        </div>
    </div>`;
}

function actionButton(label, onclick, opts = {}) {
    const disabled = opts.disabled ? ' disabled' : '';
    const id = opts.id ? ` id="${escapeHtml(opts.id)}"` : '';
    return `<button class="btn--tool-action"${id}${disabled} onclick="${escapeHtml(onclick)}">${escapeHtml(label)}</button>`;
}

function expandableRow(summary, detailHtml) {
    return `<div class="tool-expandable">
        <div class="tool-expandable__header" onclick="this.parentElement.classList.toggle('tool-expandable--open')">
            <span class="tool-expandable__chevron">[>]</span>
            <span>${escapeHtml(summary)}</span>
        </div>
        <div class="tool-expandable__body">${detailHtml}</div>
    </div>`;
}

function alertBadge(level, text) {
    const cls = level === 'critical' || level === 'error' ? 'critical' : level === 'warning' ? 'warning' : 'ok';
    return `<span class="tool-alert-badge tool-alert-badge--${cls}">${escapeHtml(text || level)}</span>`;
}

function tryItBox(placeholder, toolId, searchFn) {
    return `<div class="tool-try-it">
        <div class="tool-try-it__form">
            <input type="text" class="tool-try-it__input" id="${toolId}-try-input" placeholder="${escapeHtml(placeholder)}"
                onkeydown="if(event.key==='Enter'){${searchFn}()}">
            <button class="btn btn--primary btn--sm" onclick="${searchFn}()">Search</button>
        </div>
        <div class="tool-try-it__results" id="${toolId}-try-results"></div>
    </div>`;
}

function timeAgo(isoStr) {
    if (!isoStr) return '--';
    const d = new Date(isoStr);
    const now = new Date();
    const secs = Math.floor((now - d) / 1000);
    if (secs < 60) return 'just now';
    if (secs < 3600) return Math.floor(secs / 60) + 'm ago';
    if (secs < 86400) return Math.floor(secs / 3600) + 'h ago';
    return Math.floor(secs / 86400) + 'd ago';
}

function fmtTime(isoStr) {
    if (!isoStr) return '--';
    try {
        const d = new Date(isoStr);
        return d.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' });
    } catch { return '--'; }
}

/* ── Inline config card editing ── */

async function applySavedConfigValues(toolId) {
    try {
        const resp = await fetch(`/api/tool-config/${encodeURIComponent(toolId)}/values`);
        if (!resp.ok) return;
        const saved = await resp.json();
        document.querySelectorAll(`.tool-config-card--editable[data-tool-id="${toolId}"]`).forEach(card => {
            const key = card.dataset.configKey;
            if (saved[key] != null && saved[key] !== '') {
                const valueEl = card.querySelector('.tool-config-card__value');
                const ctrl = card.querySelector('.tool-config-card__input');
                // For number cards with suffix, rebuild display
                const suffix = card.querySelector('.tool-config-card__suffix');
                valueEl.textContent = suffix ? `${saved[key]} ${suffix.textContent}` : saved[key];
                valueEl.classList.remove('tool-config-card__value--muted');
                if (ctrl) ctrl.value = saved[key];
            }
        });
    } catch (e) {
        console.warn('Failed to load saved config:', e);
    }
}

function _getEditorControl(card) {
    return card.querySelector('.tool-config-card__input');
}

function _getEditorValue(ctrl) {
    if (!ctrl) return '';
    if (ctrl.tagName === 'SELECT') return ctrl.value;
    return ctrl.value.trim();
}

function editConfigCard(btn) {
    const card = btn.closest('.tool-config-card');
    card.querySelector('.tool-config-card__display').style.display = 'none';
    btn.style.display = 'none';
    const editor = card.querySelector('.tool-config-card__editor');
    editor.style.display = '';
    const ctrl = _getEditorControl(card);
    ctrl.focus();
    if (ctrl.select) ctrl.select();
    ctrl.onkeydown = (ev) => {
        if (ev.key === 'Enter') saveConfigCard(editor.querySelector('.btn--primary'));
        if (ev.key === 'Escape') cancelConfigCard(editor.querySelector('.btn--secondary'));
    };
}

function cancelConfigCard(btn) {
    const card = btn.closest('.tool-config-card');
    const editor = card.querySelector('.tool-config-card__editor');
    const display = card.querySelector('.tool-config-card__display');
    const editBtn = card.querySelector('.btn--edit-config');
    // Reset control to current display value
    const ctrl = _getEditorControl(card);
    const valueText = display.querySelector('.tool-config-card__value').textContent;
    // For number+suffix, strip the suffix to get raw value
    const suffix = card.querySelector('.tool-config-card__suffix');
    ctrl.value = suffix ? valueText.replace(suffix.textContent, '').trim() : valueText;
    editor.style.display = 'none';
    display.style.display = '';
    editBtn.style.display = '';
}

async function saveConfigCard(btn) {
    const card = btn.closest('.tool-config-card');
    const ctrl = _getEditorControl(card);
    const rawValue = _getEditorValue(ctrl);
    if (rawValue === '') return;

    // Validate number inputs
    if (ctrl.type === 'number') {
        const n = Number(rawValue);
        const min = Number(ctrl.min);
        const max = Number(ctrl.max);
        if (isNaN(n) || n < min || n > max) {
            ctrl.classList.add('tool-config-card__input--error');
            setTimeout(() => ctrl.classList.remove('tool-config-card__input--error'), 1500);
            return;
        }
    }

    const toolId = card.dataset.toolId;
    const configKey = card.dataset.configKey;

    // Update display — for number+suffix rebuild, for select use the option text
    const valueEl = card.querySelector('.tool-config-card__value');
    const suffix = card.querySelector('.tool-config-card__suffix');
    if (suffix) {
        valueEl.textContent = `${rawValue} ${suffix.textContent}`;
    } else if (ctrl.tagName === 'SELECT') {
        valueEl.textContent = ctrl.options[ctrl.selectedIndex].text;
    } else {
        valueEl.textContent = rawValue;
    }
    valueEl.classList.remove('tool-config-card__value--muted');

    // Close editor
    card.querySelector('.tool-config-card__editor').style.display = 'none';
    card.querySelector('.tool-config-card__display').style.display = '';
    card.querySelector('.btn--edit-config').style.display = '';

    // Persist to server
    try {
        await fetch(`/api/tool-config/${encodeURIComponent(toolId)}/values`, {
            method: 'PUT',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ key: configKey, value: rawValue }),
        });
    } catch (e) {
        console.warn('Failed to save config:', e);
    }
}

/* ── Tool help chat ── */

let _toolHelpChannel = null;
let _toolHelpJoined = false;
let _toolHelpContextSent = false;

function showToolHelpChat(toolId) {
    const el = document.getElementById('tool-help-chat');
    if (el) {
        el.style.display = '';
        _toolHelpChannel = `tool-help-${toolId}`;
        _toolHelpJoined = false;
        _toolHelpContextSent = false;
        // Collapse the body on tool switch
        const body = document.getElementById('tool-help-body');
        if (body) body.style.display = 'none';
        const arrow = document.getElementById('tool-help-arrow');
        if (arrow) arrow.textContent = '[^]';
        el.classList.remove('tool-help-chat--expanded');
    }
}

function _joinToolHelpChannel() {
    // Join channel and bind input on first activation
    if (_toolHelpJoined || !_toolHelpChannel) return;
    _toolHelpJoined = true;
    if (state.socket && state.connected) {
        state.socket.emit('join_channel', { channel_id: _toolHelpChannel });
    }
    // Bind Enter key on input
    const helpInput = document.getElementById('tool-help-input');
    if (helpInput && !helpInput._toolHelpBound) {
        helpInput._toolHelpBound = true;
        helpInput.addEventListener('keydown', (e) => {
            if (e.key === 'Enter' && !e.shiftKey) {
                e.preventDefault();
                sendToolHelpMessage();
            }
        });
    }
    renderToolHelpMessages();
}

// --- Tool help chat: drag-to-resize + click-to-toggle ---
let _toolHelpDragState = null;     // { startY, startHeight, didDrag } during drag
let _toolHelpCustomHeight = null;  // persisted height across open/close
const _TOOL_HELP_MIN_HEIGHT = 200; // minimum expanded height (px)
const _TOOL_HELP_MAX_HEIGHT = 800; // maximum drag height (px)

function toggleToolHelpChat() {
    const body = document.getElementById('tool-help-body');
    const arrow = document.getElementById('tool-help-arrow');
    const chat = document.getElementById('tool-help-chat');
    if (!body) return;
    const opening = body.style.display === 'none';
    body.style.display = opening ? '' : 'none';
    if (arrow) arrow.textContent = opening ? '[v]' : '[^]';
    if (chat) chat.classList.toggle('tool-help-chat--expanded', opening);

    if (opening) {
        if (_toolHelpCustomHeight) {
            const msgs = document.getElementById('tool-help-messages');
            if (msgs) msgs.style.setProperty('--tool-help-height', _toolHelpCustomHeight + 'px');
        }
        _joinToolHelpChannel();
    }
}

(function initToolHelpDrag() {
    function bind() {
        const toggle = document.getElementById('tool-help-toggle');
        if (!toggle) return;

        toggle.addEventListener('mousedown', function(e) {
            const body = document.getElementById('tool-help-body');
            const isExpanded = body && body.style.display !== 'none';
            const msgs = document.getElementById('tool-help-messages');
            _toolHelpDragState = {
                startY: e.clientY,
                startHeight: (isExpanded && msgs) ? msgs.offsetHeight : 0,
                didDrag: false,
                wasExpanded: isExpanded
            };
            e.preventDefault();
        });

        document.addEventListener('mousemove', function(e) {
            if (!_toolHelpDragState) return;
            if (!_toolHelpDragState.wasExpanded) return; // can't resize when collapsed
            const dy = _toolHelpDragState.startY - e.clientY;
            if (!_toolHelpDragState.didDrag && Math.abs(dy) < 4) return;
            _toolHelpDragState.didDrag = true;

            let newHeight = _toolHelpDragState.startHeight + dy;
            newHeight = Math.max(_TOOL_HELP_MIN_HEIGHT, Math.min(_TOOL_HELP_MAX_HEIGHT, newHeight));

            const msgs = document.getElementById('tool-help-messages');
            if (msgs) {
                msgs.style.setProperty('--tool-help-height', newHeight + 'px');
                msgs.style.transition = 'none';
            }
        });

        document.addEventListener('mouseup', function() {
            if (!_toolHelpDragState) return;
            const wasDrag = _toolHelpDragState.didDrag;
            if (wasDrag) {
                const msgs = document.getElementById('tool-help-messages');
                if (msgs) {
                    _toolHelpCustomHeight = msgs.offsetHeight;
                    msgs.style.transition = '';
                }
            } else {
                // Click (no drag) — toggle the panel
                toggleToolHelpChat();
            }
            _toolHelpDragState = null;
        });
    }

    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', bind);
    } else {
        bind();
    }
})();

function sendToolHelpMessage() {
    const input = document.getElementById('tool-help-input');
    if (!input) return;
    const content = input.value.trim();
    if (!content || !_toolHelpChannel) return;
    if (!state.socket || !state.connected) {
        showToast('Not connected to server', 'warning');
        return;
    }

    // Ensure channel is joined
    _joinToolHelpChannel();

    // On first message in this tool session, inject tool context so the agent
    // knows which tool the user is configuring
    if (!_toolHelpContextSent) {
        _toolHelpContextSent = true;
        const tool = (state.tools || []).find(t => t.id === state.currentTool);
        if (tool) {
            const configSummary = _buildToolConfigSummary(tool);
            state.socket.emit('send_message', {
                channel_id: _toolHelpChannel,
                sender: 'system',
                content: `@setup_guide The user is viewing the **${tool.name}** configuration panel. ${configSummary}Help them configure this tool.`,
            });
        }
    }

    // Auto-mention setup_guide so the agent router picks it up
    const fullContent = content.includes('@setup_guide') ? content : `@setup_guide ${content}`;

    state.socket.emit('send_message', {
        channel_id: _toolHelpChannel,
        sender: 'user',
        content: fullContent,
    });

    input.value = '';
}

function renderToolHelpMessages() {
    const container = document.getElementById('tool-help-messages');
    if (!container || !_toolHelpChannel) return;

    const allMsgs = state.messages[_toolHelpChannel] || [];
    // Hide system context injection messages from the user
    const msgs = allMsgs.filter(m => m.sender !== 'system');
    if (msgs.length === 0) {
        container.innerHTML = '<p class="tool-help-chat__empty">Ask Cohort a question about configuring this tool.</p>';
        return;
    }

    container.innerHTML = msgs.map(m => {
        const profile = typeof getAgentProfile === 'function' ? getAgentProfile(m.sender) : { avatar: '?', name: m.sender, color: '#95A5A6' };
        // Strip @setup_guide mention from display — it's auto-added internally
        const cleanContent = (m.content || '').replace(/^@setup_guide\s*/i, '');
        const body = typeof formatMessageContent === 'function' ? formatMessageContent(cleanContent) : escapeHtml(cleanContent);
        // Friendly display names for the help chat
        const displayName = m.sender === 'setup_guide' ? 'Cohort' : m.sender === 'user' ? 'You' : profile.name;
        return `<div class="tool-help-msg">
            <div class="tool-help-msg__avatar" style="background-color: ${profile.color}">${profile.avatar}</div>
            <div class="tool-help-msg__body">
                <div class="tool-help-msg__sender" style="color: ${profile.color}">${escapeHtml(displayName)}</div>
                <div class="tool-help-msg__text">${body}</div>
            </div>
        </div>`;
    }).join('');

    container.scrollTop = container.scrollHeight;
}

function _buildToolConfigSummary(tool) {
    // Extract visible config values from the currently rendered cards
    const cards = document.querySelectorAll('.tool-config-card');
    if (!cards.length) return '';
    const parts = [];
    cards.forEach(card => {
        const label = card.querySelector('.tool-config-card__label');
        const value = card.querySelector('.tool-config-card__value');
        if (label && value) {
            const l = label.textContent.trim();
            const v = value.textContent.trim();
            if (l && v) parts.push(`${l}: ${v}`);
        }
    });
    if (!parts.length) return '';
    return `Current settings: ${parts.join(', ')}. `;
}

async function fetchServiceStatus(serviceId) {
    try {
        const resp = await fetch('/api/service-status/' + encodeURIComponent(serviceId));
        if (!resp.ok) return { status: 'unknown' };
        return await resp.json();
    } catch { return { status: 'unknown' }; }
}

function statusDotHtml(status) {
    const cls = status === 'up' ? 'up' : status === 'down' ? 'down' : 'unknown';
    const label = status === 'up' ? 'Online' : status === 'down' ? 'Offline' : 'Unknown';
    return `<span class="tool-status-dot tool-status-dot--${cls}"></span> ${label}`;
}

function renderGenericToolPanel(tool) {
    return `<div class="tool-dashboard">
        ${toolHeader(tool, '<span class="tool-status-dot tool-status-dot--unknown"></span> --')}
        ${configSection('About', configCard('Type', 'Agent capability'))}
    </div>`;
}

const DAYS_OF_WEEK = ['Sunday', 'Monday', 'Tuesday', 'Wednesday', 'Thursday', 'Friday', 'Saturday'];
const DAY_PRESETS = ['Weekdays', 'Daily', 'Weekends'];

/* ── Email & Calendar ── */

async function renderCommsPanel(tool) {
    const tid = 'comms_service';

    // Fetch status, activity, and pending approvals in parallel
    const [status, activityResp, pendingResp] = await Promise.all([
        fetchServiceStatus('comms_service'),
        fetch('/api/comms/recent-activity?limit=12').catch(() => null),
        fetch('/api/comms/pending-approvals').catch(() => null),
    ]);

    let activityData = { activity: [] };
    let pendingData = { count: 0, pending: [] };
    try { activityData = activityResp ? await activityResp.json() : { activity: [] }; } catch {}
    try { pendingData = pendingResp ? await pendingResp.json() : { count: 0, pending: [] }; } catch {}

    const activity = activityData.activity || [];
    const pendingCount = pendingData.count || 0;
    const pendingPosts = pendingData.pending || [];

    // Activity log items
    const items = activity.slice().reverse().map(a => {
        const failed = (a.channels_failed || []).length > 0;
        return {
            time: fmtTime(a.timestamp),
            status: failed ? 'error' : 'success',
            message: (a.title || a.message || '').substring(0, 80),
            detail: `Agent: ${a.agent_id || 'unknown'}\nPriority: ${a.priority || '--'}\nChannels: ${(a.channels_sent || []).join(', ') || 'none'}\n\n${a.message || ''}`,
        };
    });

    // Stats
    const sent = activity.filter(a => (a.channels_sent || []).length > 0).length;
    const failed = activity.filter(a => (a.channels_failed || []).length > 0).length;
    const errors = activity.filter(a => a.priority === 'error').length;
    const warnings = activity.filter(a => a.priority === 'warning').length;

    // Pending approval cards
    let pendingHtml = '';
    if (pendingCount > 0) {
        const cards = pendingPosts.map(p => `<div class="comms-pending-card">
            <span class="comms-pending-card__platform">${escapeHtml(p.platform || 'post')}</span>
            <span class="comms-pending-card__text">${escapeHtml(p.text || '')}</span>
            <span class="comms-pending-card__time">${p.created_at ? timeAgo(p.created_at) : '--'}</span>
        </div>`).join('');
        pendingHtml = configSectionFull(
            `Pending Approval (${pendingCount})`,
            `<div class="comms-pending-list">${cards}</div>
            <p style="font-size:var(--font-size-xs);color:var(--color-text-muted);margin-top:var(--space-2)">Approve posts on the Social Media & Marketing page</p>`
        );
    }

    // Message type breakdown
    const byAgent = {};
    activity.forEach(a => {
        const ag = a.agent_id || 'unknown';
        byAgent[ag] = (byAgent[ag] || 0) + 1;
    });
    const agentItems = Object.entries(byAgent).sort((a, b) => b[1] - a[1]).map(([name, count]) => ({
        name: name.replace(/_/g, ' '),
        status: 'up',
        detail: `${count} message${count !== 1 ? 's' : ''}`,
    }));

    dom.toolPanelContent.innerHTML = `<div class="tool-dashboard">
        ${toolHeader(tool, statusDotHtml(status.status))}
        ${statRow(
            statCard('Sent Today', sent, { color: 'success' }) +
            statCard('Failed', failed, { color: failed > 0 ? 'error' : 'success' }) +
            statCard('Errors', errors, { color: errors > 0 ? 'error' : 'success' }) +
            statCard('Pending', pendingCount, { color: pendingCount > 0 ? 'warning' : 'success', subtitle: 'awaiting approval' })
        )}
        ${pendingHtml}
        ${configSectionFull('Today\'s Activity', activityLog(items, { emptyMsg: 'No outbound messages today' }))}
        ${agentItems.length > 0 ? configSectionFull('Messages by Source', statusGrid(agentItems)) : ''}
        ${configSection('Services',
            configAdminSelect('Email Provider', 'Resend', ['Resend'], tid, 'email_provider') +
            configAdminSelect('Calendar', 'Google Calendar', ['Google Calendar'], tid, 'calendar_provider') +
            configCard('Social Platforms', 'Twitter, LinkedIn, Facebook, Threads')
        )}
        ${configSection('Safety',
            configCard('Approval Gate', 'All outbound messages require human approval') +
            configCard('Audit Log', 'All actions logged with full audit trail')
        )}
    </div>`;
    applySavedConfigValues(tid);
    showToolHelpChat(tid);
}

/* ── Web Search ── */

async function renderWebSearchPanel(tool) {
    const status = await fetchServiceStatus('web_search');
    const tid = 'web_search';
    const isUp = status.status === 'up';

    // Check local ddgs availability
    let localAvailable = false;
    try {
        const localResp = await fetch('/api/internal-web-search/status');
        const localData = await localResp.json();
        localAvailable = localData.available;
    } catch {}

    // Load saved toggle states (default: both on)
    let apiEnabled = true, localEnabled = true;
    try {
        const cfgResp = await fetch(`/api/tool-config/${tid}/values`);
        if (cfgResp.ok) {
            const cfg = await cfgResp.json();
            if (cfg.api_search_enabled === 'false') apiEnabled = false;
            if (cfg.local_search_enabled === 'false') localEnabled = false;
        }
    } catch {}

    const localDot = localAvailable
        ? '<span class="tool-status-dot tool-status-dot--up"></span> Available'
        : '<span class="tool-status-dot tool-status-dot--down"></span> Not installed';

    // Combined header status: green if any enabled provider is available
    const anyActive = (isUp && apiEnabled) || (localAvailable && localEnabled);
    const headerStatus = anyActive
        ? '<span class="tool-status-dot tool-status-dot--up"></span> Online'
        : statusDotHtml('down');

    const apiCardOpacity = apiEnabled ? '1' : '0.45';
    const localCardOpacity = localEnabled ? '1' : '0.45';

    dom.toolPanelContent.innerHTML = `<div class="tool-dashboard">
        ${toolHeader(tool, headerStatus)}
        ${configSectionFull('Providers', `
            <div style="display:grid;grid-template-columns:1fr 1fr;gap:var(--space-3)">
                <div class="tool-config-card" style="padding:var(--space-3);opacity:${apiCardOpacity};transition:opacity .2s" id="ws-provider-api">
                    <div style="display:flex;align-items:center;gap:var(--space-2);margin-bottom:var(--space-2)">
                        <strong>API Search</strong>
                        <span style="margin-left:auto;font-size:var(--font-size-xs)">${statusDotHtml(status.status)}</span>
                    </div>
                    <p style="font-size:var(--font-size-xs);color:var(--color-text-secondary);margin:0 0 var(--space-2) 0">SerpAPI / Serper / Google -- requires API key, billed per request</p>
                    <label class="toggle-label" style="margin-top:auto">
                        <input type="checkbox" class="toggle-input" id="ws-toggle-api" ${apiEnabled ? 'checked' : ''} onchange="toggleWebSearchProvider('api_search_enabled', this.checked)">
                        <span class="toggle-switch"></span>
                        <span style="font-size:var(--font-size-xs)">${apiEnabled ? 'Enabled' : 'Disabled'}</span>
                    </label>
                </div>
                <div class="tool-config-card" style="padding:var(--space-3);opacity:${localCardOpacity};transition:opacity .2s" id="ws-provider-local">
                    <div style="display:flex;align-items:center;gap:var(--space-2);margin-bottom:var(--space-2)">
                        <strong>Local Search</strong>
                        <span style="margin-left:auto;font-size:var(--font-size-xs)">${localDot}</span>
                    </div>
                    <p style="font-size:var(--font-size-xs);color:var(--color-text-secondary);margin:0 0 var(--space-2) 0">DuckDuckGo via ddgs -- free, no API key, runs locally${!localAvailable ? '<br><code style="font-size:var(--font-size-xs)">pip install ddgs</code>' : ''}</p>
                    <label class="toggle-label" style="margin-top:auto">
                        <input type="checkbox" class="toggle-input" id="ws-toggle-local" ${localEnabled ? 'checked' : ''} onchange="toggleWebSearchProvider('local_search_enabled', this.checked)">
                        <span class="toggle-switch"></span>
                        <span style="font-size:var(--font-size-xs)">${localEnabled ? 'Enabled' : 'Disabled'}</span>
                    </label>
                </div>
            </div>
        `)}
        ${isUp && apiEnabled ? configSectionFull('Try It -- API Search', `
            <div class="ws-try-it">
                <div class="ws-try-it__search">
                    <input type="text" id="web_search-try-input" class="ws-try-it__input"
                        placeholder="Search via API provider..." onkeydown="if(event.key==='Enter')tryWebSearch()">
                    <button class="btn btn--primary btn--sm" onclick="tryWebSearch()">Search</button>
                </div>
                <div id="web_search-try-results" class="ws-try-it__results"></div>
            </div>
        `) : ''}
        ${localAvailable && localEnabled ? configSectionFull('Try It -- Local Search (free)', `
            <div class="ws-try-it">
                <div class="ws-try-it__search">
                    <input type="text" id="web_search_local-try-input" class="ws-try-it__input"
                        placeholder="Search via DuckDuckGo (free)..." onkeydown="if(event.key==='Enter')tryWebSearchLocal()">
                    <button class="btn btn--primary btn--sm" style="background:var(--color-success)" onclick="tryWebSearchLocal()">Search</button>
                </div>
                <div id="web_search_local-try-results" class="ws-try-it__results"></div>
            </div>
        `) : ''}
        ${!anyActive ? configSectionFull('Web Search', `<div style="padding:var(--space-3);background:rgba(239,68,68,0.1);border-radius:var(--radius-md);font-size:var(--font-size-sm);color:var(--color-error)">No search providers active. Enable a provider above, or install ddgs: <code>pip install ddgs</code></div>`) : ''}
        ${configSection('API Configuration',
            configAdminSelect('Provider', 'SerpAPI', ['SerpAPI', 'Serper', 'Google'], tid, 'provider')
        )}
        ${configSection('Rate Limits',
            configNumber('Per Minute', 30, 1, 120, tid, 'rate_limit_per_min', 'req/min') +
            configNumber('Per Day', 250, 1, 10000, tid, 'rate_limit_per_day', 'req/day')
        )}
    </div>`;
    applySavedConfigValues(tid);
    showToolHelpChat(tid);
}

async function toggleWebSearchProvider(key, enabled) {
    const tid = 'web_search';
    // Update label text next to toggle
    const toggle = key === 'api_search_enabled'
        ? document.getElementById('ws-toggle-api')
        : document.getElementById('ws-toggle-local');
    if (toggle) {
        const label = toggle.closest('.toggle-label').querySelector('span:last-child');
        if (label) label.textContent = enabled ? 'Enabled' : 'Disabled';
    }
    // Update card opacity
    const card = key === 'api_search_enabled'
        ? document.getElementById('ws-provider-api')
        : document.getElementById('ws-provider-local');
    if (card) card.style.opacity = enabled ? '1' : '0.45';

    // Persist toggle state
    try {
        await fetch(`/api/tool-config/${tid}/values`, {
            method: 'PUT',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ key, value: String(enabled) }),
        });
    } catch (e) {
        console.warn('Failed to save provider toggle:', e);
    }

    // Re-render panel to update Try It sections and header status
    const tool = { id: tid, name: 'Web Search', description: 'Search the internet using API providers (SerpAPI, Serper) or locally via DuckDuckGo (free)' };
    renderWebSearchPanel(tool);
}

async function tryWebSearch() {
    const input = document.getElementById('web_search-try-input');
    const results = document.getElementById('web_search-try-results');
    if (!input || !input.value.trim()) return;
    results.innerHTML = '<p style="color:var(--color-text-muted);font-size:var(--font-size-sm)">Searching...</p>';
    try {
        const resp = await fetch('/api/web-search/test', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ query: input.value.trim(), limit: 5 }),
        });
        const data = await resp.json();
        if (data.error) {
            results.innerHTML = `<p style="color:var(--color-error);font-size:var(--font-size-sm)">${escapeHtml(data.error)}</p>`;
        } else if ((data.results || []).length === 0) {
            results.innerHTML = '<p style="color:var(--color-text-muted);font-size:var(--font-size-sm)">No results found</p>';
        } else {
            results.innerHTML = data.results.map(r => {
                const domain = r.url ? new URL(r.url).hostname.replace('www.', '') : '';
                return `<div class="ws-result-card">
                    <a class="ws-result-card__title" href="${escapeHtml(r.url || '#')}" target="_blank">${escapeHtml(r.title || 'Untitled')}</a>
                    <span class="ws-result-card__url">${escapeHtml(domain)}</span>
                    <p class="ws-result-card__snippet">${escapeHtml(r.snippet || '')}</p>
                </div>`;
            }).join('');
        }
    } catch (err) {
        results.innerHTML = `<p style="color:var(--color-error);font-size:var(--font-size-sm)">Search failed: ${escapeHtml(err.message)}</p>`;
    }
}

async function tryWebSearchLocal() {
    const input = document.getElementById('web_search_local-try-input');
    const results = document.getElementById('web_search_local-try-results');
    if (!input || !input.value.trim()) return;
    results.innerHTML = '<p style="color:var(--color-text-muted);font-size:var(--font-size-sm)">Searching locally...</p>';
    try {
        const resp = await fetch('/api/web-search/test-local', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ query: input.value.trim(), limit: 5 }),
        });
        const data = await resp.json();
        if (data.error) {
            results.innerHTML = `<p style="color:var(--color-error);font-size:var(--font-size-sm)">${escapeHtml(data.error)}</p>`;
        } else if ((data.results || []).length === 0) {
            results.innerHTML = '<p style="color:var(--color-text-muted);font-size:var(--font-size-sm)">No results found</p>';
        } else {
            results.innerHTML = data.results.map(r => {
                const domain = r.url ? new URL(r.url).hostname.replace('www.', '') : '';
                return `<div class="ws-result-card">
                    <a class="ws-result-card__title" href="${escapeHtml(r.url || '#')}" target="_blank">${escapeHtml(r.title || 'Untitled')}</a>
                    <span class="ws-result-card__url">${escapeHtml(domain)}</span>
                    <p class="ws-result-card__snippet">${escapeHtml(r.snippet || '')}</p>
                </div>`;
            }).join('');
        }
    } catch (err) {
        results.innerHTML = `<p style="color:var(--color-error);font-size:var(--font-size-sm)">Search failed: ${escapeHtml(err.message)}</p>`;
    }
}

/* ── YouTube ── */

async function renderYouTubePanel(tool) {
    const status = await fetchServiceStatus('youtube_service');
    const tid = 'youtube_service';
    const isUp = status.status === 'up';

    dom.toolPanelContent.innerHTML = `<div class="tool-dashboard">
        ${toolHeader(tool, statusDotHtml(status.status))}
        ${isUp ? configSectionFull('Try It -- Video Search', `
            <div class="yt-try-it">
                <div class="yt-try-it__search">
                    <input type="text" id="youtube-try-input" class="yt-try-it__input"
                        placeholder="Search YouTube videos..." onkeydown="if(event.key==='Enter')tryYouTubeSearch()">
                    <button class="btn btn--primary btn--sm" onclick="tryYouTubeSearch()">Search</button>
                </div>
                <div id="youtube-try-results" class="yt-try-it__results"></div>
            </div>
        `) : configSectionFull('Video Search', `<div style="padding:var(--space-3);background:rgba(239,68,68,0.1);border-radius:var(--radius-md);font-size:var(--font-size-sm);color:var(--color-error)">YouTube service is offline. Check that it is running on port 8002.</div>`)}
        ${configSection('Rate Limits',
            configNumber('Per Minute', 30, 1, 120, tid, 'rate_limit_per_min', 'req/min') +
            configNumber('Per Day', 1000, 1, 10000, tid, 'rate_limit_per_day', 'req/day') +
            configNumber('Default Results', 10, 1, 50, tid, 'default_results')
        )}
        ${configSection('Capabilities',
            configCard('Video Search', 'Search by keyword with filters') +
            configCard('Metadata', 'Video details, channel info, view counts') +
            configCard('Chapters', 'Auto-extract timestamps from descriptions')
        )}
    </div>`;
    applySavedConfigValues(tid);
    showToolHelpChat(tid);
}

async function tryYouTubeSearch() {
    const input = document.getElementById('youtube-try-input');
    const results = document.getElementById('youtube-try-results');
    if (!input || !input.value.trim()) return;
    results.innerHTML = '<p style="color:var(--color-text-muted);font-size:var(--font-size-sm)">Searching...</p>';
    try {
        const resp = await fetch('/api/youtube/test', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ query: input.value.trim(), limit: 5 }),
        });
        const data = await resp.json();
        if (data.error) {
            results.innerHTML = `<p style="color:var(--color-error);font-size:var(--font-size-sm)">${escapeHtml(data.error)}</p>`;
        } else if ((data.results || []).length === 0) {
            results.innerHTML = '<p style="color:var(--color-text-muted);font-size:var(--font-size-sm)">No results found</p>';
        } else {
            results.innerHTML = `<div class="yt-results-grid">${data.results.map(r => {
                const thumb = r.thumbnail || '';
                const thumbHtml = thumb ? `<div class="yt-card__thumb"><img src="${escapeHtml(thumb)}" alt="" loading="lazy"><span class="yt-card__duration">${escapeHtml(r.duration || '')}</span></div>` : '';
                return `<div class="yt-card">
                    ${thumbHtml}
                    <div class="yt-card__info">
                        <a class="yt-card__title" href="${escapeHtml(r.url || '#')}" target="_blank">${escapeHtml(r.title || 'Untitled')}</a>
                        <span class="yt-card__channel">${escapeHtml(r.channel || '')}</span>
                        ${r.views ? `<span class="yt-card__views">${escapeHtml(r.views)}</span>` : ''}
                    </div>
                </div>`;
            }).join('')}</div>`;
        }
    } catch (err) {
        results.innerHTML = `<p style="color:var(--color-error);font-size:var(--font-size-sm)">Search failed: ${escapeHtml(err.message)}</p>`;
    }
}

/* ── RSS & News Monitoring ── */

async function renderRSSPanel(tool) {
    const tid = 'intel_scheduler';

    // Fetch live data in parallel
    let runsData = { runs: [] };
    let articlesData = { articles: [] };
    try {
        const [runsResp, articlesResp] = await Promise.all([
            fetch('/api/scheduler/recent-runs?task=rss_fetch&limit=7&source=scheduler'),
            fetch('/api/intel/recent-articles?limit=5'),
        ]);
        runsData = await runsResp.json();
        articlesData = await articlesResp.json();
    } catch {}

    const runs = runsData.runs || [];
    const lastRun = runs.length > 0 ? runs[runs.length - 1] : null;
    const articles = articlesData.articles || [];

    // Stats from last run
    const lastRunResult = lastRun ? (lastRun.result || {}) : {};
    const totalRecentArticles = runs.reduce((sum, r) => sum + ((r.result || {}).new_articles || 0), 0);
    const statsHtml = statRow(
        statCard('Last Run', lastRun ? timeAgo(lastRun.timestamp) : '--', { subtitle: lastRun ? (lastRunResult.status || '') : 'No runs found' }) +
        statCard('Last Fetch', lastRunResult.new_articles != null ? lastRunResult.new_articles : '--', { subtitle: 'articles' }) +
        statCard('Recent Total', totalRecentArticles, { subtitle: `across ${runs.length} runs` }) +
        statCard('Feeds', lastRunResult.feeds_processed != null ? lastRunResult.feeds_processed : '--', { subtitle: 'sources' })
    );

    // Run history as activity log
    const runItems = runs.reverse().map(r => {
        const res = r.result || {};
        return {
            time: fmtTime(r.timestamp),
            status: res.status === 'completed' ? 'success' : res.status === 'error' ? 'error' : 'info',
            message: `${res.feeds_processed || 0} feeds, ${res.new_articles || 0} new articles`,
            detail: (res.errors || []).length > 0 ? 'Errors:\n' + res.errors.join('\n') : null,
        };
    });

    // Recent articles -- rich cards instead of basic log
    const articleItems = articles.map(a => ({
        time: fmtTime(a.fetched_at || a.published || ''),
        status: a.relevance_score >= 7 ? 'success' : a.relevance_score >= 4 ? 'warning' : 'info',
        message: a.title || a.url || 'Untitled',
        detail: [
            a.source_feed ? `Source: ${a.source_feed}` : null,
            a.relevance_score != null ? `Relevance: ${a.relevance_score}/10` : null,
            a.summary ? `Summary: ${a.summary}` : null,
            a.url ? `URL: ${a.url}` : null,
        ].filter(Boolean).join('\n'),
    }));

    dom.toolPanelContent.innerHTML = `<div class="tool-dashboard">
        ${toolHeader(tool, '<span class="tool-status-dot tool-status-dot--up"></span> Scheduled')}
        ${statsHtml}
        ${configSectionFull('Run History', activityLog(runItems, { emptyMsg: 'No recent runs' }))}
        ${configSectionFull('Recent Articles', activityLog(articleItems, { emptyMsg: 'No recent articles' }))}
        ${configSection('RSS Fetch',
            configNumber('Interval', 4, 1, 24, tid, 'fetch_interval_hrs', 'hours') +
            configNumber('Window Start', 8, 0, 23, tid, 'fetch_window_start', ':00') +
            configNumber('Window End', 18, 0, 23, tid, 'fetch_window_end', ':00')
        )}
        ${configSection('Analysis',
            configSelect('Days', 'Weekdays', DAY_PRESETS, tid, 'analysis_days') +
            configText('Time', '07:00', tid, 'analysis_time')
        )}
        ${configSection('Briefing',
            configSelect('Day', 'Sunday', DAYS_OF_WEEK, tid, 'briefing_day') +
            configText('Time', '18:00', tid, 'briefing_time')
        )}
        ${configSection('Limits',
            configNumber('Max Fetches/Day', 6, 1, 50, tid, 'max_fetches_day') +
            configNumber('Max Articles/Day', 200, 1, 1000, tid, 'max_articles_day')
        )}
        ${state.adminMode ? configSection('Advanced',
            configAdminNumber('Min Relevance', 5, 0, 10, tid, 'min_relevance') +
            configAdminText('Feed Sources', 'Default feeds', tid, 'feed_sources')
        ) : ''}
    </div>`;
    applySavedConfigValues(tid);
    showToolHelpChat(tid);
}

/* ── Social Media & Marketing ── */

async function renderContentMonitorPanel(tool) {
    const tid = 'content_monitor_scheduler';

    // Fetch all data in parallel
    const [pipelineResp, postsResp, configResp] = await Promise.all([
        fetch('/api/content-monitor/pipeline-status').catch(() => null),
        fetch('/api/content-monitor/posts?limit=20').catch(() => null),
        fetch('/api/content-monitor/config').catch(() => null),
    ]);

    let pipelineData = { stages: {} };
    let postsData = { posts: [] };
    let configData = {};
    try { pipelineData = pipelineResp ? await pipelineResp.json() : { stages: {} }; } catch {}
    try { postsData = postsResp ? await postsResp.json() : { posts: [] }; } catch {}
    try { configData = configResp ? await configResp.json() : {}; } catch {}

    const stages = pipelineData.stages || {};
    const posts = postsData.posts || [];
    const feedNames = configData.feed_names || [];
    const safetyLimits = configData.safety_limits || {};

    // ── Pipeline flow visualization ──
    const fetchStage = stages.rss_fetch || {};
    const analysisStage = stages.analysis || {};
    const draftStage = stages.post_drafting || {};
    const digestStage = stages.weekly_digest || {};

    const pendingPosts = posts.filter(p => p.status === 'pending');
    const approvedPosts = posts.filter(p => p.status === 'approved');
    const rejectedPosts = posts.filter(p => p.status === 'rejected');

    const pipelineFlowHtml = `<div class="social-pipeline">
        <div class="social-pipeline__stage">
            <span class="social-pipeline__stage-count">${fetchStage.today_count || 0}</span>
            <span class="social-pipeline__stage-label">Fetched</span>
            <span class="social-pipeline__stage-time">${fetchStage.last_run ? timeAgo(fetchStage.last_run) : '--'}</span>
        </div>
        <span class="social-pipeline__arrow">[>>]</span>
        <div class="social-pipeline__stage">
            <span class="social-pipeline__stage-count">${analysisStage.today_count || 0}</span>
            <span class="social-pipeline__stage-label">Analyzed</span>
            <span class="social-pipeline__stage-time">${analysisStage.last_run ? timeAgo(analysisStage.last_run) : '--'}</span>
        </div>
        <span class="social-pipeline__arrow">[>>]</span>
        <div class="social-pipeline__stage">
            <span class="social-pipeline__stage-count">${draftStage.today_count || 0}</span>
            <span class="social-pipeline__stage-label">Drafted</span>
            <span class="social-pipeline__stage-time">${draftStage.last_run ? timeAgo(draftStage.last_run) : '--'}</span>
        </div>
        <span class="social-pipeline__arrow">[>>]</span>
        <div class="social-pipeline__stage">
            <span class="social-pipeline__stage-count" style="color:var(--color-warning)">${pendingPosts.length}</span>
            <span class="social-pipeline__stage-label">Pending</span>
            <span class="social-pipeline__stage-time">awaiting review</span>
        </div>
        <span class="social-pipeline__arrow">[>>]</span>
        <div class="social-pipeline__stage">
            <span class="social-pipeline__stage-count" style="color:var(--color-success)">${approvedPosts.length}</span>
            <span class="social-pipeline__stage-label">Approved</span>
            <span class="social-pipeline__stage-time">ready to post</span>
        </div>
    </div>`;

    // ── Daily limits progress bars ──
    const maxFetches = safetyLimits.max_rss_fetches_per_day || 12;
    const maxArticles = safetyLimits.max_articles_analyzed_per_day || 50;
    const maxDrafts = safetyLimits.max_drafts_per_day || 5;
    const totalFetched = fetchStage.today_count || 0;
    const totalAnalyzed = analysisStage.today_count || 0;
    const totalDrafted = draftStage.today_count || 0;

    const limitsHtml = `<div style="display:flex;gap:var(--space-3);flex-wrap:wrap">
        <div style="flex:1;min-width:150px">${progressBar(totalFetched, maxFetches, { label: 'Fetches', suffix: 'today' })}</div>
        <div style="flex:1;min-width:150px">${progressBar(totalAnalyzed, maxArticles, { label: 'Analyzed', suffix: 'today' })}</div>
        <div style="flex:1;min-width:150px">${progressBar(totalDrafted, maxDrafts, { label: 'Drafts', suffix: 'today' })}</div>
    </div>`;

    // ── Post cards with tab filtering ──
    const postsHtml = _renderSocialPostsSection(posts, pendingPosts, approvedPosts, rejectedPosts);

    // ── Feed sources ──
    const feedsHtml = feedNames.length > 0
        ? `<div class="social-feeds">${feedNames.map(name => `<div class="social-feed-item"><span class="social-feed-item__dot"></span><span>${escapeHtml(name)}</span></div>`).join('')}</div>`
        : '<p style="color:var(--color-text-muted);font-size:var(--font-size-sm);font-style:italic">No feeds configured</p>';

    // ── Stage details expandables ──
    let stageDetails = '';
    for (const [name, stageData] of Object.entries(stages)) {
        const res = stageData.last_result || {};
        const detailLines = Object.entries(res).map(([k, v]) => {
            const label = k.replace(/_/g, ' ');
            return `${label}: ${Array.isArray(v) ? (v.length > 0 ? v.join(', ') : 'none') : v}`;
        }).join('\n');
        stageDetails += expandableRow(
            `${name.replace(/_/g, ' ')} -- last run ${timeAgo(stageData.last_run)} (${stageData.today_count || 0} runs today)`,
            `<pre style="margin:0;font-size:var(--font-size-xs);white-space:pre-wrap">${escapeHtml(detailLines || 'No details')}</pre>`
        );
    }

    dom.toolPanelContent.innerHTML = `<div class="tool-dashboard">
        ${toolHeader(tool, '<span class="tool-status-dot tool-status-dot--up"></span> Scheduled')}
        ${configSectionFull('Content Pipeline', pipelineFlowHtml)}
        ${configSectionFull('Daily Limits', limitsHtml)}
        ${postsHtml}
        ${configSectionFull('Monitored Feeds (' + feedNames.length + ')', feedsHtml)}
        ${stageDetails ? configSectionFull('Pipeline Stages', stageDetails) : ''}
        ${configSection('RSS Fetch',
            configNumber('Interval', 2, 1, 24, tid, 'fetch_interval_hrs', 'hours') +
            configNumber('Window Start', 9, 0, 23, tid, 'fetch_window_start', ':00') +
            configNumber('Window End', 21, 0, 23, tid, 'fetch_window_end', ':00')
        )}
        ${configSection('Analysis',
            configNumber('Interval', 4, 1, 24, tid, 'analysis_interval_hrs', 'hours')
        )}
        ${configSection('Post Drafting',
            configSelect('Days', 'Weekdays', DAY_PRESETS, tid, 'drafting_days') +
            configText('Time', '08:00', tid, 'drafting_time')
        )}
        ${configSection('Weekly Digest',
            configSelect('Day', 'Sunday', DAYS_OF_WEEK, tid, 'digest_day') +
            configText('Time', '18:00', tid, 'digest_time')
        )}
        ${state.adminMode ? configSection('Advanced',
            configAdminNumber('Min Relevance', 5, 0, 10, tid, 'min_relevance_for_draft') +
            configAdminText('Feed Sources', 'Default feeds', tid, 'feed_sources')
        ) : ''}
    </div>`;
    applySavedConfigValues(tid);
    showToolHelpChat(tid);
}

function _renderSocialPostsSection(allPosts, pending, approved, rejected) {
    if (allPosts.length === 0) {
        return configSectionFull('Posts', '<p style="color:var(--color-text-muted);font-size:var(--font-size-sm);font-style:italic">No posts yet. Posts are auto-drafted from high-relevance articles.</p>');
    }

    const tabsHtml = `<div class="social-tabs">
        <button class="social-tab social-tab--active" onclick="_filterSocialPosts('all', this)">All (${allPosts.length})</button>
        <button class="social-tab" onclick="_filterSocialPosts('pending', this)">Pending (${pending.length})</button>
        <button class="social-tab" onclick="_filterSocialPosts('approved', this)">Approved (${approved.length})</button>
        <button class="social-tab" onclick="_filterSocialPosts('rejected', this)">Rejected (${rejected.length})</button>
    </div>`;

    const cardsHtml = allPosts.map(p => _renderSocialPostCard(p)).join('');

    return configSectionFull(`Posts (${allPosts.length})`,
        tabsHtml + `<div id="social-posts-list">${cardsHtml}</div>`);
}

function _renderSocialPostCard(post) {
    const meta = post.metadata || {};
    const platform = post.platform || 'unknown';
    const score = meta.relevance_score != null ? meta.relevance_score : null;
    const painPoints = meta.pain_points || [];
    const audience = meta.audience || '';
    const source = meta.source || '';
    const template = meta.template || '';
    const postText = post.text || '';
    const statusCls = post.status || 'pending';
    const cardId = 'post-' + (post.post_id || '').substring(0, 8);

    // Score badge coloring
    let scoreCls = 'score-low';
    if (score >= 7) scoreCls = 'score-high';
    else if (score >= 4) scoreCls = 'score-med';

    // Tags
    let tagsHtml = '';
    if (score != null) tagsHtml += `<span class="social-post-card__tag social-post-card__tag--score social-post-card__tag--${scoreCls}">Score: ${score}/10</span>`;
    if (source) tagsHtml += `<span class="social-post-card__tag social-post-card__tag--source">${escapeHtml(source)}</span>`;
    if (audience && audience !== 'unknown') tagsHtml += `<span class="social-post-card__tag social-post-card__tag--audience">${escapeHtml(audience)}</span>`;
    if (template) tagsHtml += `<span class="social-post-card__tag">${escapeHtml(template.replace(/_/g, ' '))}</span>`;
    painPoints.slice(0, 3).forEach(pp => {
        tagsHtml += `<span class="social-post-card__tag social-post-card__tag--pain-point">${escapeHtml(pp.replace(/_/g, ' '))}</span>`;
    });
    if (painPoints.length > 3) {
        tagsHtml += `<span class="social-post-card__tag">+${painPoints.length - 3} more</span>`;
    }

    // Actions
    let actionsHtml = '';
    if (post.status === 'pending') {
        actionsHtml = `<div class="social-post-card__actions">
            <button class="btn--approve" onclick="_approveSocialPost('${escapeHtml(post.post_id)}')">Approve</button>
            <button class="btn--reject" onclick="_rejectSocialPost('${escapeHtml(post.post_id)}')">Reject</button>
            ${meta.source_article ? `<a class="btn--view-source" href="${escapeHtml(meta.source_article)}" target="_blank">View Source</a>` : ''}
        </div>`;
    } else if (post.status === 'approved') {
        actionsHtml = `<div class="social-post-card__actions">
            <span class="social-status-badge social-status-badge--approved">Approved ${post.approved_at ? timeAgo(post.approved_at) : ''}</span>
            ${meta.source_article ? `<a class="btn--view-source" href="${escapeHtml(meta.source_article)}" target="_blank" style="margin-left:auto">View Source</a>` : ''}
        </div>`;
    } else if (post.status === 'rejected') {
        actionsHtml = `<div class="social-post-card__actions">
            <span class="social-status-badge social-status-badge--rejected">Rejected${post.reject_reason ? ': ' + escapeHtml(post.reject_reason) : ''}</span>
        </div>`;
    }

    return `<div class="social-post-card social-post-card--${statusCls}" id="${cardId}" data-status="${statusCls}">
        <div class="social-post-card__header">
            <span class="social-post-card__platform social-post-card__platform--${platform}">${escapeHtml(platform)}</span>
            <div class="social-post-card__meta">
                <span>${post.created_at ? timeAgo(post.created_at) : '--'}</span>
            </div>
        </div>
        <div class="social-post-card__body" id="${cardId}-body">${escapeHtml(postText)}</div>
        <span class="social-post-card__expand" onclick="_togglePostExpand('${cardId}')">Show more</span>
        ${tagsHtml ? `<div class="social-post-card__tags">${tagsHtml}</div>` : ''}
        ${actionsHtml}
    </div>`;
}

function _togglePostExpand(cardId) {
    const body = document.getElementById(cardId + '-body');
    if (!body) return;
    body.classList.toggle('social-post-card__body--expanded');
    const btn = body.nextElementSibling;
    if (btn) btn.textContent = body.classList.contains('social-post-card__body--expanded') ? 'Show less' : 'Show more';
}

function _filterSocialPosts(status, tabEl) {
    // Update tab active state
    const tabs = tabEl.parentElement.querySelectorAll('.social-tab');
    tabs.forEach(t => t.classList.remove('social-tab--active'));
    tabEl.classList.add('social-tab--active');

    // Filter post cards
    const list = document.getElementById('social-posts-list');
    if (!list) return;
    const cards = list.querySelectorAll('.social-post-card');
    cards.forEach(card => {
        if (status === 'all' || card.dataset.status === status) {
            card.style.display = '';
        } else {
            card.style.display = 'none';
        }
    });
}

async function _approveSocialPost(postId) {
    if (!confirm('Approve this post for publishing?')) return;
    try {
        const resp = await fetch(`/api/content-monitor/posts/${encodeURIComponent(postId)}`, {
            method: 'PATCH',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ action: 'approve' }),
        });
        const data = await resp.json();
        if (data.ok) {
            showToast('Post approved', 'success');
            // Re-render both the tool panel and the pending review panel
            const tool = (state.tools || []).find(t => t.id === 'content_monitor_scheduler');
            if (tool) renderContentMonitorPanel(tool);
            fetchPendingSocialPosts();
        } else {
            showToast('Failed: ' + (data.error || 'Unknown error'), 'error');
        }
    } catch (err) {
        showToast('Failed: ' + err.message, 'error');
    }
}

async function _rejectSocialPost(postId) {
    const reason = prompt('Rejection reason (optional):');
    if (reason === null) return; // cancelled
    try {
        const resp = await fetch(`/api/content-monitor/posts/${encodeURIComponent(postId)}`, {
            method: 'PATCH',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ action: 'reject', reason: reason || 'Rejected via dashboard' }),
        });
        const data = await resp.json();
        if (data.ok) {
            showToast('Post rejected', 'success');
            const tool = (state.tools || []).find(t => t.id === 'content_monitor_scheduler');
            if (tool) renderContentMonitorPanel(tool);
            fetchPendingSocialPosts();
        } else {
            showToast('Failed: ' + (data.error || 'Unknown error'), 'error');
        }
    } catch (err) {
        showToast('Failed: ' + err.message, 'error');
    }
}

/* ── Pending Social Posts (for Pending Review panel) ── */

async function fetchPendingSocialPosts() {
    try {
        const resp = await fetch('/api/comms/pending-approvals');
        const data = await resp.json();
        state.pendingSocialPosts = (data.pending || []).filter(p => p.status === 'pending');
    } catch {
        state.pendingSocialPosts = [];
    }
    // Update badge even if we're not on the output panel
    const taskReviewCount = state.tasks.filter(t => t.status === 'complete' && !t.review).length;
    const totalPending = taskReviewCount + state.pendingSocialPosts.length;
    if (dom.outputBadge) dom.outputBadge.textContent = totalPending;
    if (state.currentPanel === 'output') renderOutputs();
}

function renderSocialPostOutputCard(post) {
    const postText = post.text || '';
    const platform = post.platform || 'unknown';
    const meta = post.metadata || {};
    const score = meta.relevance_score != null ? meta.relevance_score : null;
    const painPoints = meta.pain_points || [];

    let tagsHtml = '';
    if (score != null) {
        let scoreCls = 'score-low';
        if (score >= 7) scoreCls = 'score-high';
        else if (score >= 4) scoreCls = 'score-med';
        tagsHtml += `<span class="social-post-card__tag social-post-card__tag--score social-post-card__tag--${scoreCls}">Score: ${score}/10</span>`;
    }
    if (meta.source) tagsHtml += `<span class="social-post-card__tag social-post-card__tag--source">${escapeHtml(meta.source)}</span>`;
    painPoints.slice(0, 3).forEach(pp => {
        tagsHtml += `<span class="social-post-card__tag social-post-card__tag--pain-point">${escapeHtml(pp.replace(/_/g, ' '))}</span>`;
    });

    return `
    <div class="output-card output-card--social" data-post-id="${escapeHtml(post.post_id)}">
        <div class="output-card__header">
            <h4 class="output-card__title">
                <span class="social-post-card__platform social-post-card__platform--${platform}">${escapeHtml(platform)}</span>
                Social Media Post
            </h4>
            <span class="output-card__agent">content_monitor</span>
        </div>
        <div class="output-card__body">
            <div class="output-card__diff">${escapeHtml(postText)}</div>
            ${tagsHtml ? `<div class="social-post-card__tags" style="margin-top:var(--space-2)">${tagsHtml}</div>` : ''}
        </div>
        <div class="output-card__footer">
            <button class="btn btn--primary btn--small" onclick="_approveSocialPost('${escapeHtml(post.post_id)}')">Approve</button>
            <button class="btn btn--danger btn--small" onclick="_rejectSocialPost('${escapeHtml(post.post_id)}')">Reject</button>
            ${meta.source_article ? `<a class="btn--view-source" href="${escapeHtml(meta.source_article)}" target="_blank" style="margin-left:auto">View Source</a>` : ''}
        </div>
    </div>`;
}

/* ── Document Processing ── */

let _docHistory = [];

async function _loadDocHistory() {
    try {
        const resp = await fetch('/api/doc-processor/history');
        const data = await resp.json();
        if (data.ok) _docHistory = data.history || [];
    } catch { _docHistory = []; }
}

async function _addDocHistoryEntry(data, source, isAllMode) {
    const entry = {
        id: Date.now(),
        timestamp: new Date().toISOString(),
        source: source || 'Unknown',
        mode: isAllMode ? 'all' : (data.mode || 'summary'),
        status: 'success',
        model: data.model || '',
        file_type: data.file_type || '',
    };
    if (isAllMode && Array.isArray(data)) {
        entry.stats = data[0] ? data[0].stats : {};
        entry.fullData = data.map(d => ({
            ok: true, summary: (d.summary || '').substring(0, 2000),
            mode: d.mode, model: d.model, file_type: d.file_type,
            filename: d.filename, stats: d.stats,
        }));
        entry.model = data[0] ? data[0].model : '';
        entry.file_type = data[0] ? data[0].file_type : '';
    } else {
        entry.stats = data.stats || {};
        entry.fullData = {
            ok: true, summary: (data.summary || '').substring(0, 2000),
            mode: data.mode, model: data.model, file_type: data.file_type,
            filename: data.filename, stats: data.stats,
        };
    }
    _docHistory.unshift(entry);
    _refreshDocHistory();
    try {
        await fetch('/api/doc-processor/history', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ entry }),
        });
    } catch { /* server save failed, still in memory for this session */ }
}

function _restoreDocResult(entryId) {
    const entry = _docHistory.find(h => h.id === entryId);
    if (!entry) return;
    if (entry.mode === 'all' && Array.isArray(entry.fullData)) {
        _renderDocResultAll(entry.fullData);
    } else if (entry.fullData) {
        _renderDocResult(entry.fullData);
    }
    const resultArea = document.getElementById('doc-result-area');
    if (resultArea) resultArea.scrollIntoView({ behavior: 'smooth', block: 'start' });
}

async function _clearDocHistory() {
    _docHistory = [];
    _refreshDocHistory();
    try {
        await fetch('/api/doc-processor/history', { method: 'DELETE' });
    } catch { /* ignore */ }
}

let _docHistoryShowArchive = false;
const _DOC_HISTORY_VISIBLE = 10;

function _toggleDocArchive() {
    _docHistoryShowArchive = !_docHistoryShowArchive;
    _refreshDocHistory();
}

function _buildDocHistoryRow(h) {
    const modeLabels = { summary: 'Summary', outline: 'Outline', extract_key_points: 'Key Points', all: 'All Modes' };
    const stats = h.stats || {};
    let detail = '';
    if (stats.input_words) detail += `Input: ${stats.input_words.toLocaleString()} words`;
    if (stats.output_words) detail += `${detail ? ' | ' : ''}Output: ${stats.output_words.toLocaleString()} words`;
    if (stats.elapsed_seconds) detail += `${detail ? ' | ' : ''}Time: ${stats.elapsed_seconds}s`;
    if (h.model) detail += `${detail ? ' | ' : ''}Model: ${h.model}`;

    return `<div class="tool-activity-log__item tool-activity-log__item--clickable" onclick="_restoreDocResult(${h.id})">
        <span class="tool-activity-log__time">${escapeHtml(fmtTime(h.timestamp))}</span>
        <span class="tool-activity-log__dot tool-activity-log__dot--${h.status === 'success' ? 'success' : 'error'}"></span>
        <span class="tool-activity-log__msg">${escapeHtml(h.source)} -- ${escapeHtml(modeLabels[h.mode] || h.mode)}${detail ? `<div class="tool-activity-log__detail" style="display:block;margin-top:var(--space-1)">${escapeHtml(detail)}</div>` : ''}</span>
    </div>`;
}

function _refreshDocHistory() {
    const el = document.getElementById('doc-history-section');
    if (!el) return;
    if (_docHistory.length === 0) { el.innerHTML = ''; return; }

    const recent = _docHistory.slice(0, _DOC_HISTORY_VISIBLE);
    const archived = _docHistory.slice(_DOC_HISTORY_VISIBLE);
    const recentRows = recent.map(_buildDocHistoryRow).join('');

    let archiveHtml = '';
    if (archived.length > 0) {
        if (_docHistoryShowArchive) {
            const archiveRows = archived.map(_buildDocHistoryRow).join('');
            archiveHtml = `<div class="tool-config-section" style="margin-top:var(--space-3)">
                <div style="display:flex;align-items:center;justify-content:space-between">
                    <h3 class="tool-config-section__title">Archive (${archived.length})</h3>
                    <button class="btn btn--sm btn--ghost" onclick="_toggleDocArchive()" style="font-size:var(--font-size-xs)">Hide Archive</button>
                </div>
                <div class="tool-config-grid--full">
                    <div class="tool-activity-log">${archiveRows}</div>
                </div>
            </div>`;
        } else {
            archiveHtml = `<div style="text-align:center;margin-top:var(--space-2)">
                <button class="btn btn--sm btn--ghost" onclick="_toggleDocArchive()">View Archive (${archived.length} older)</button>
            </div>`;
        }
    }

    el.innerHTML = `<div class="tool-config-section">
        <div style="display:flex;align-items:center;justify-content:space-between">
            <h3 class="tool-config-section__title">Processing History (${recent.length})</h3>
            <button class="btn btn--sm btn--ghost" onclick="_clearDocHistory()" style="font-size:var(--font-size-xs)">Clear</button>
        </div>
        <div class="tool-config-grid--full">
            <div class="tool-activity-log">${recentRows}</div>
        </div>
    </div>${archiveHtml}`;
}

async function renderDocProcessorPanel(tool) {
    const tid = tool.id;
    const [ollamaStatus, runningData] = await Promise.all([
        fetchServiceStatus('llm_manager'),
        fetch('/api/llm/running').then(r => r.json()).catch(() => ({ status: 'down', models: [] })),
    ]);
    const isUp = ollamaStatus.status === 'up';

    // Get model info + settings
    let allModels = [];
    let configuredModel = 'qwen3.5:9b';
    let modelCount = 0;
    let vramHtml = '';
    if (isUp) {
        try {
            const [modelsResp, settingsResp] = await Promise.all([
                fetch('/api/llm/models').then(r => r.json()),
                fetch('/api/settings').then(r => r.json()).catch(() => ({})),
            ]);
            allModels = (modelsResp.models || []).map(m => m.name).sort();
            modelCount = allModels.length;
            configuredModel = settingsResp.model_name || 'qwen3.5:9b';
        } catch { /* keep defaults */ }

        const runningModels = runningData.models || [];
        if (runningModels.length > 0) {
            const totalVram = runningModels.reduce((sum, m) => sum + (m.vram_bytes || 0), 0);
            const totalVramGB = (totalVram / 1073741824).toFixed(1);
            vramHtml = `<div class="doc-vram-bar">${progressBar(parseFloat(totalVramGB), 12.0, { label: 'GPU Memory', suffix: 'GB' })}</div>`;
        }
    }

    // File format badges
    const formats = [
        { ext: 'PDF', icon: '[PDF]', color: '#ef4444' },
        { ext: 'Word', icon: '[DOC]', color: '#3b82f6' },
        { ext: 'Excel', icon: '[XLS]', color: '#22c55e' },
        { ext: 'Images', icon: '[IMG]', color: '#a855f7' },
        { ext: 'Video', icon: '[VID]', color: '#f59e0b' },
        { ext: 'HTML', icon: '[HTM]', color: '#06b6d4' },
        { ext: 'CSV', icon: '[CSV]', color: '#64748b' },
        { ext: 'Code', icon: '[</>]', color: '#ec4899' },
        { ext: 'JSON', icon: '[{ }]', color: '#8b5cf6' },
        { ext: 'Text', icon: '[TXT]', color: '#94a3b8' },
    ];
    const formatBadgesHtml = formats.map(f =>
        `<span class="doc-format-badge" style="--badge-color:${f.color}"><span class="doc-format-badge__icon">${f.icon}</span>${f.ext}</span>`
    ).join('');

    // Offline banner
    const offlineHtml = !isUp ? `<div class="doc-offline-banner">
        <strong>[!] Ollama is offline</strong>
        <p>Start Ollama to enable document processing. File extraction still works, but AI summarization requires a running model.</p>
    </div>` : '';

    dom.toolPanelContent.innerHTML = `<div class="tool-dashboard">
        ${toolHeader(tool, statusDotHtml(ollamaStatus.status))}
        ${offlineHtml}
        <div class="doc-engine-bar">
            <div class="doc-engine-toggle">
                <label class="doc-engine-radio" title="Use local Ollama model">
                    <input type="radio" name="doc-engine" value="ollama" checked onchange="_onDocEngineChange()"> Ollama
                </label>
                <label class="doc-engine-radio doc-engine-radio--smart" title="Use Claude Code CLI (cloud)">
                    <input type="radio" name="doc-engine" value="smart" onchange="_onDocEngineChange()"> Smart
                </label>
            </div>
            <div class="doc-engine-bar__model" id="doc-engine-model-group">
                <label class="doc-engine-bar__model-label" for="doc-model-select">Model</label>
                <select id="doc-model-select" class="doc-model-select" ${!isUp ? 'disabled' : ''}>
                    ${allModels.length ? allModels.map(m =>
                        `<option value="${escapeHtml(m)}" ${m === configuredModel ? 'selected' : ''}>${escapeHtml(m)}</option>`
                    ).join('') : '<option value="">No models</option>'}
                </select>
                <span class="doc-engine-bar__cap">${modelCount} available</span>
            </div>
            ${vramHtml ? `<div class="doc-engine-bar__vram">${vramHtml}</div>` : ''}
        </div>

        <div class="doc-processor-main">
            <!-- Drop zone -->
            <div class="doc-dropzone" id="doc-dropzone"
                ondragover="event.preventDefault(); this.classList.add('doc-dropzone--hover')"
                ondragleave="this.classList.remove('doc-dropzone--hover')"
                ondrop="_handleDocDrop(event)">
                <div class="doc-dropzone__content">
                    <div class="doc-dropzone__icon">[^]</div>
                    <div class="doc-dropzone__text">
                        <strong>Drop any file here</strong>
                        <p>or <label class="doc-dropzone__browse" for="doc-file-input">browse files</label></p>
                    </div>
                    <div class="doc-dropzone__formats">${formatBadgesHtml}</div>
                </div>
                <input type="file" id="doc-file-input" style="display:none"
                    accept=".pdf,.docx,.xlsx,.xls,.html,.htm,.csv,.json,.md,.txt,.log,.py,.js,.ts,.css,.yaml,.yml,.xml,.sql,.png,.jpg,.jpeg,.gif,.bmp,.webp,.tiff,.mp4,.avi,.mov,.mkv,.webm"
                    onchange="_handleDocFileSelect(this)">
            </div>

            <!-- Or fetch URL -->
            <div class="doc-url-input">
                <div class="doc-url-input__header">
                    <span>Or enter a web address</span>
                </div>
                <div class="doc-url-input__row">
                    <input type="url" id="doc-url-input" class="doc-url-input__field"
                        placeholder="https://example.com/article..."
                        onkeydown="if(event.key==='Enter'){event.preventDefault();_processDocInput()}">
                </div>
            </div>

            <!-- Or paste text -->
            <div class="doc-text-input">
                <div class="doc-text-input__header">
                    <span>Or paste text directly</span>
                </div>
                <textarea id="doc-summarize-input" class="doc-try-it__textarea" rows="4"
                    placeholder="Paste articles, meeting notes, code, documentation..."></textarea>
            </div>

            <!-- Mode selector -->
            <div class="doc-controls">
                <div class="doc-mode-selector">
                    <label class="doc-mode-option" title="Concise paragraph summarizing the main ideas and conclusions">
                        <input type="radio" name="doc-mode" value="summary" checked> Summary
                    </label>
                    <label class="doc-mode-option" title="Structured hierarchical outline with headings and sub-points">
                        <input type="radio" name="doc-mode" value="outline"> Outline
                    </label>
                    <label class="doc-mode-option" title="Bullet list of the most important facts, findings, and takeaways">
                        <input type="radio" name="doc-mode" value="extract_key_points"> Key Points
                    </label>
                    <label class="doc-mode-option" title="Run all three modes and display results together">
                        <input type="radio" name="doc-mode" value="all"> All
                    </label>
                </div>
                <div class="doc-mode-separator"></div>
                <label class="doc-mode-option doc-mode-option--image" title="Describe visual content: what the image depicts, colors, composition, and notable elements">
                    <input type="radio" name="doc-mode" value="image"> Image
                </label>
                <button class="btn btn--primary" onclick="_processDocInput()" id="doc-process-btn"${!isUp ? ' disabled' : ''}>
                    Process
                </button>
            </div>

            <!-- Results area -->
            <div id="doc-result-area"></div>

            <!-- Processing history -->
            <div id="doc-history-section"></div>
        </div>
    </div>`;
    showToolHelpChat(tid);

    // Load and render history from server
    await _loadDocHistory();
    _refreshDocHistory();
}

// ── Document processor helpers ──

function _getDocMode() {
    const checked = document.querySelector('input[name="doc-mode"]:checked');
    return checked ? checked.value : 'summary';
}

function _getDocEngine() {
    const checked = document.querySelector('input[name="doc-engine"]:checked');
    return checked ? checked.value : 'ollama';
}

function _onDocEngineChange() {
    const engine = _getDocEngine();
    const modelGroup = document.getElementById('doc-engine-model-group');
    const modelSelect = document.getElementById('doc-model-select');
    if (engine === 'smart') {
        if (modelGroup) modelGroup.classList.add('doc-engine-bar__model--disabled');
        if (modelSelect) modelSelect.disabled = true;
    } else {
        if (modelGroup) modelGroup.classList.remove('doc-engine-bar__model--disabled');
        if (modelSelect) modelSelect.disabled = false;
    }
}

function _svgToPng(svgText) {
    return new Promise((resolve, reject) => {
        const img = new Image();
        const blob = new Blob([svgText], { type: 'image/svg+xml;charset=utf-8' });
        const url = URL.createObjectURL(blob);
        img.onload = () => {
            const w = img.naturalWidth || 1024;
            const h = img.naturalHeight || 1024;
            const canvas = document.createElement('canvas');
            canvas.width = w;
            canvas.height = h;
            const ctx = canvas.getContext('2d');
            ctx.fillStyle = '#ffffff';
            ctx.fillRect(0, 0, w, h);
            ctx.drawImage(img, 0, 0, w, h);
            URL.revokeObjectURL(url);
            canvas.toBlob(b => b ? resolve(b) : reject(new Error('toBlob failed')), 'image/png');
        };
        img.onerror = () => { URL.revokeObjectURL(url); reject(new Error('SVG render failed')); };
        img.src = url;
    });
}

function _handleDocDrop(event) {
    event.preventDefault();
    const zone = document.getElementById('doc-dropzone');
    if (zone) zone.classList.remove('doc-dropzone--hover');

    const files = event.dataTransfer.files;
    if (files.length > 0) {
        _processDocFile(files[0]);
    }
}

function _handleDocFileSelect(input) {
    if (input.files.length > 0) {
        _processDocFile(input.files[0]);
    }
}

async function _processDocInput() {
    // Check URL field first
    const urlInput = document.getElementById('doc-url-input');
    if (urlInput && urlInput.value.trim().match(/^https?:\/\/.+/)) {
        _processDocUrl(urlInput.value.trim());
        return;
    }
    // Check if there's text pasted
    const textInput = document.getElementById('doc-summarize-input');
    if (textInput && textInput.value.trim().length > 30) {
        _processDocText(textInput.value.trim());
    } else {
        // Trigger file browser
        const fileInput = document.getElementById('doc-file-input');
        if (fileInput) fileInput.click();
    }
}

async function _processDocFile(file) {
    const resultArea = document.getElementById('doc-result-area');
    const btn = document.getElementById('doc-process-btn');
    if (!resultArea) return;

    const sizeMB = (file.size / 1048576).toFixed(1);
    const mode = _getDocMode();
    const engine = _getDocEngine();

    // Determine file type icon
    const ext = file.name.split('.').pop().toLowerCase();
    let typeLabel = 'File';
    if (['pdf'].includes(ext)) typeLabel = 'PDF';
    else if (['docx', 'doc'].includes(ext)) typeLabel = 'Word';
    else if (['xlsx', 'xls'].includes(ext)) typeLabel = 'Excel';
    else if (['html', 'htm'].includes(ext)) typeLabel = 'HTML';
    else if (['csv'].includes(ext)) typeLabel = 'CSV';
    else if (['json'].includes(ext)) typeLabel = 'JSON';
    else if (['png', 'jpg', 'jpeg', 'gif', 'bmp', 'webp', 'tiff', 'tif'].includes(ext)) typeLabel = 'Image';
    else if (['mp4', 'avi', 'mov', 'mkv', 'webm', 'flv', 'wmv'].includes(ext)) typeLabel = 'Video';
    else typeLabel = ext.toUpperCase();

    // SVG + image mode: render to PNG in browser so vision model can see it
    if (ext === 'svg' && mode === 'image') {
        try {
            const svgText = await file.text();
            const pngBlob = await _svgToPng(svgText);
            const pngFile = new File([pngBlob], file.name.replace(/\.svg$/i, '.png'), { type: 'image/png' });
            file = pngFile;
        } catch (e) {
            console.warn('SVG to PNG conversion failed, sending as-is:', e);
        }
    }

    if (btn) { btn.disabled = true; btn.textContent = 'Processing...'; }

    // "All" mode: run all 3 modes sequentially
    if (mode === 'all') {
        const allModes = ['summary', 'outline', 'extract_key_points'];
        const results = [];
        const modelSelect = document.getElementById('doc-model-select');
        const selectedModel = modelSelect ? modelSelect.value : '';

        for (let i = 0; i < allModes.length; i++) {
            const m = allModes[i];
            const mLabels = { summary: 'Summary', outline: 'Outline', extract_key_points: 'Key Points' };
            resultArea.innerHTML = `<div class="doc-processing">
                <div class="doc-processing__spinner"></div>
                <div class="doc-processing__info">
                    <strong>Processing: ${escapeHtml(file.name)}</strong>
                    <span>${typeLabel} -- ${sizeMB} MB -- running ${mLabels[m]} (${i + 1}/3)</span>
                    <span class="doc-processing__status">Analyzing...</span>
                </div>
            </div>`;
            try {
                const formData = new FormData();
                formData.append('file', file);
                formData.append('mode', m);
                formData.append('engine', engine);
                if (selectedModel) formData.append('model', selectedModel);
                const resp = await fetch('/api/doc-processor/process', { method: 'POST', body: formData });
                const data = await resp.json();
                if (data.ok) results.push(data);
            } catch { /* skip failed mode */ }
        }
        if (results.length > 0) {
            _renderDocResultAll(results);
            _addDocHistoryEntry(results, file.name, true);
        } else {
            resultArea.innerHTML = `<div class="doc-error"><strong>[X] All modes failed</strong></div>`;
        }
        if (btn) { btn.disabled = false; btn.textContent = 'Process'; }
        return;
    }

    // Single mode
    resultArea.innerHTML = `<div class="doc-processing">
        <div class="doc-processing__spinner"></div>
        <div class="doc-processing__info">
            <strong>Processing: ${escapeHtml(file.name)}</strong>
            <span>${typeLabel} -- ${sizeMB} MB -- mode: ${mode}</span>
            <span class="doc-processing__status" id="doc-processing-status">Uploading and extracting content...</span>
        </div>
    </div>`;

    const modelSelect = document.getElementById('doc-model-select');
    const selectedModel = modelSelect ? modelSelect.value : '';
    const formData = new FormData();
    formData.append('file', file);
    formData.append('mode', mode);
    formData.append('engine', engine);
    if (selectedModel) formData.append('model', selectedModel);

    try {
        const resp = await fetch('/api/doc-processor/process', {
            method: 'POST',
            body: formData,
        });
        const data = await resp.json();

        if (data.ok) {
            _renderDocResult(data);
            _addDocHistoryEntry(data, file.name, false);
        } else {
            resultArea.innerHTML = `<div class="doc-error">
                <strong>[X] Processing failed</strong>
                <p>${escapeHtml(data.error || 'Unknown error')}</p>
            </div>`;
        }
    } catch (err) {
        resultArea.innerHTML = `<div class="doc-error">
            <strong>[X] Upload failed</strong>
            <p>${escapeHtml(err.message)}</p>
        </div>`;
    } finally {
        if (btn) { btn.disabled = false; btn.textContent = 'Process'; }
    }
}

async function _processDocText(text) {
    const resultArea = document.getElementById('doc-result-area');
    const btn = document.getElementById('doc-process-btn');
    if (!resultArea) return;

    const mode = _getDocMode();
    const engine = _getDocEngine();
    const selectedModel = (document.getElementById('doc-model-select') || {}).value || '';
    const engineLabel = engine === 'smart' ? 'Claude' : 'local AI model';

    if (btn) { btn.disabled = true; btn.textContent = 'Processing...'; }

    // "All" mode: run all 3 modes sequentially
    if (mode === 'all') {
        const allModes = ['summary', 'outline', 'extract_key_points'];
        const results = [];
        for (let i = 0; i < allModes.length; i++) {
            const m = allModes[i];
            const mLabels = { summary: 'Summary', outline: 'Outline', extract_key_points: 'Key Points' };
            resultArea.innerHTML = `<div class="doc-processing">
                <div class="doc-processing__spinner"></div>
                <div class="doc-processing__info">
                    <strong>Processing pasted text</strong>
                    <span>${text.length.toLocaleString()} characters -- running ${mLabels[m]} (${i + 1}/3)</span>
                    <span class="doc-processing__status">Analyzing with ${engineLabel}...</span>
                </div>
            </div>`;
            try {
                const resp = await fetch('/api/doc-processor/summarize', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ text, mode: m, model: selectedModel, engine }),
                });
                const data = await resp.json();
                if (data.ok) results.push(data);
            } catch { /* skip failed mode */ }
        }
        if (results.length > 0) {
            _renderDocResultAll(results);
            _addDocHistoryEntry(results, 'Pasted text', true);
        } else {
            resultArea.innerHTML = `<div class="doc-error"><strong>[X] All modes failed</strong></div>`;
        }
        if (btn) { btn.disabled = false; btn.textContent = 'Process'; }
        return;
    }

    resultArea.innerHTML = `<div class="doc-processing">
        <div class="doc-processing__spinner"></div>
        <div class="doc-processing__info">
            <strong>Processing pasted text</strong>
            <span>${text.length.toLocaleString()} characters -- mode: ${mode}</span>
            <span class="doc-processing__status">Analyzing with ${engineLabel}...</span>
        </div>
    </div>`;

    try {
        const resp = await fetch('/api/doc-processor/summarize', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ text, mode, model: selectedModel, engine }),
        });
        const data = await resp.json();

        if (data.ok) {
            _renderDocResult(data);
            _addDocHistoryEntry(data, 'Pasted text', false);
        } else {
            resultArea.innerHTML = `<div class="doc-error">
                <strong>[X] Processing failed</strong>
                <p>${escapeHtml(data.error || 'Unknown error')}</p>
            </div>`;
        }
    } catch (err) {
        resultArea.innerHTML = `<div class="doc-error">
            <strong>[X] Processing failed</strong>
            <p>${escapeHtml(err.message)}</p>
        </div>`;
    } finally {
        if (btn) { btn.disabled = false; btn.textContent = 'Process'; }
    }
}

async function _processDocUrl(url) {
    const resultArea = document.getElementById('doc-result-area');
    const btn = document.getElementById('doc-process-btn');
    if (!resultArea) return;

    const mode = _getDocMode();
    const engine = _getDocEngine();
    const selectedModel = (document.getElementById('doc-model-select') || {}).value || '';
    const engineLabel = engine === 'smart' ? 'Claude' : 'local AI model';

    if (btn) { btn.disabled = true; btn.textContent = 'Processing...'; }

    // Truncate display URL for the spinner
    const displayUrl = url.length > 60 ? url.substring(0, 57) + '...' : url;

    // "All" mode: run all 3 modes sequentially
    if (mode === 'all') {
        const allModes = ['summary', 'outline', 'extract_key_points'];
        const results = [];
        for (let i = 0; i < allModes.length; i++) {
            const m = allModes[i];
            const mLabels = { summary: 'Summary', outline: 'Outline', extract_key_points: 'Key Points' };
            resultArea.innerHTML = `<div class="doc-processing">
                <div class="doc-processing__spinner"></div>
                <div class="doc-processing__info">
                    <strong>Fetching: ${escapeHtml(displayUrl)}</strong>
                    <span>URL -- running ${mLabels[m]} (${i + 1}/3)</span>
                    <span class="doc-processing__status">${i === 0 ? 'Fetching page...' : `Analyzing with ${engineLabel}...`}</span>
                </div>
            </div>`;
            try {
                const resp = await fetch('/api/doc-processor/fetch-url', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ url, mode: m, model: selectedModel, engine }),
                });
                const data = await resp.json();
                if (data.ok) results.push(data);
                else if (i === 0) {
                    // First mode failed — show error and stop
                    resultArea.innerHTML = `<div class="doc-error">
                        <strong>[X] Fetch failed</strong>
                        <p>${escapeHtml(data.error || 'Unknown error')}</p>
                    </div>`;
                    if (btn) { btn.disabled = false; btn.textContent = 'Process'; }
                    return;
                }
            } catch { /* skip failed mode */ }
        }
        if (results.length > 0) {
            _renderDocResultAll(results);
            _addDocHistoryEntry(results, displayUrl, true);
        } else {
            resultArea.innerHTML = `<div class="doc-error"><strong>[X] All modes failed</strong></div>`;
        }
        if (btn) { btn.disabled = false; btn.textContent = 'Process'; }
        return;
    }

    // Single mode
    resultArea.innerHTML = `<div class="doc-processing">
        <div class="doc-processing__spinner"></div>
        <div class="doc-processing__info">
            <strong>Fetching: ${escapeHtml(displayUrl)}</strong>
            <span>URL -- mode: ${mode}</span>
            <span class="doc-processing__status" id="doc-processing-status">Fetching and analyzing with ${engineLabel}...</span>
        </div>
    </div>`;

    try {
        const resp = await fetch('/api/doc-processor/fetch-url', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ url, mode, model: selectedModel, engine }),
        });
        const data = await resp.json();

        if (data.ok) {
            _renderDocResult(data);
            _addDocHistoryEntry(data, displayUrl, false);
        } else {
            resultArea.innerHTML = `<div class="doc-error">
                <strong>[X] Fetch failed</strong>
                <p>${escapeHtml(data.error || 'Unknown error')}</p>
            </div>`;
        }
    } catch (err) {
        resultArea.innerHTML = `<div class="doc-error">
            <strong>[X] Fetch failed</strong>
            <p>${escapeHtml(err.message)}</p>
        </div>`;
    } finally {
        if (btn) { btn.disabled = false; btn.textContent = 'Process'; }
    }
}

function _renderDocResult(data) {
    const resultArea = document.getElementById('doc-result-area');
    if (!resultArea) return;

    const stats = data.stats || {};
    const mode = data.mode || 'summary';
    const modeLabels = { summary: 'Summary', outline: 'Outline', extract_key_points: 'Key Points', image: 'Image' };

    // Stats bar
    let statsHtml = '<div class="doc-result-stats">';
    if (stats.url) statsHtml += `<span class="doc-result-stat"><strong>URL:</strong> <a href="${escapeHtml(stats.url)}" target="_blank" rel="noopener" style="color:var(--color-accent)">${escapeHtml(data.filename || stats.url)}</a></span>`;
    else if (data.filename) statsHtml += `<span class="doc-result-stat"><strong>File:</strong> ${escapeHtml(data.filename)}</span>`;
    if (data.file_type) statsHtml += `<span class="doc-result-stat"><strong>Type:</strong> ${escapeHtml(data.file_type)}</span>`;
    if (stats.content_size) statsHtml += `<span class="doc-result-stat"><strong>Page size:</strong> ${(stats.content_size / 1024).toFixed(0)} KB</span>`;
    if (stats.pages) statsHtml += `<span class="doc-result-stat"><strong>Pages:</strong> ${stats.pages}</span>`;
    if (stats.input_words) statsHtml += `<span class="doc-result-stat"><strong>Input:</strong> ${stats.input_words.toLocaleString()} words</span>`;
    if (stats.output_words) statsHtml += `<span class="doc-result-stat"><strong>Output:</strong> ${stats.output_words.toLocaleString()} words</span>`;
    if (stats.compression_pct) statsHtml += `<span class="doc-result-stat doc-result-stat--highlight"><strong>Compression:</strong> ${stats.compression_pct}%</span>`;
    if (stats.frames_extracted) statsHtml += `<span class="doc-result-stat"><strong>Frames:</strong> ${stats.frames_extracted} extracted</span>`;
    if (stats.file_size) statsHtml += `<span class="doc-result-stat"><strong>Size:</strong> ${(stats.file_size / 1024).toFixed(0)} KB</span>`;
    if (stats.elapsed_seconds) statsHtml += `<span class="doc-result-stat"><strong>Time:</strong> ${stats.elapsed_seconds}s</span>`;
    if (data.model) statsHtml += `<span class="doc-result-stat"><strong>Model:</strong> ${escapeHtml(data.model)}</span>`;
    statsHtml += '</div>';

    // Format the summary text with basic markdown rendering
    const summaryLines = (data.summary || '').split('\n').map(line => {
        let l = escapeHtml(line);
        // Bold: **text**
        l = l.replace(/\*\*(.+?)\*\*/g, '<strong>$1</strong>');
        // Bullet points
        if (l.match(/^\s*[-*]\s/)) l = '<li>' + l.replace(/^\s*[-*]\s/, '') + '</li>';
        // Headings
        if (l.match(/^#{1,3}\s/)) {
            const level = l.match(/^(#+)/)[1].length;
            l = `<h${level + 2} style="margin:var(--space-2) 0 var(--space-1)">${l.replace(/^#+\s/, '')}</h${level + 2}>`;
        }
        return l;
    }).join('\n');

    // Wrap consecutive <li> in <ul>
    const formatted = summaryLines.replace(/(<li>.*?<\/li>\n?)+/gs, match => `<ul style="margin:var(--space-1) 0;padding-left:var(--space-4)">${match}</ul>`);

    // Extracted text preview (for documents)
    let previewHtml = '';
    if (data.extracted_text_preview) {
        previewHtml = `<div class="doc-result-preview">
            <div class="doc-result-preview__header" onclick="this.parentElement.classList.toggle('doc-result-preview--open')">
                <span>Extracted Text Preview</span>
                <span class="doc-result-preview__toggle">[+]</span>
            </div>
            <div class="doc-result-preview__body"><pre>${escapeHtml(data.extracted_text_preview)}</pre></div>
        </div>`;
    }

    resultArea.innerHTML = `<div class="doc-result">
        <div class="doc-result__header">
            <span class="doc-result__mode-badge">${modeLabels[mode] || mode}</span>
            <button class="btn btn--sm" onclick="navigator.clipboard.writeText(document.getElementById('doc-result-text').innerText);showToast('Copied to clipboard','success')">Copy</button>
        </div>
        ${statsHtml}
        <div class="doc-result__body" id="doc-result-text">${formatted}</div>
        ${previewHtml}
    </div>`;
}

function _formatDocMarkdown(text) {
    const lines = (text || '').split('\n').map(line => {
        let l = escapeHtml(line);
        l = l.replace(/\*\*(.+?)\*\*/g, '<strong>$1</strong>');
        if (l.match(/^\s*[-*]\s/)) l = '<li>' + l.replace(/^\s*[-*]\s/, '') + '</li>';
        if (l.match(/^#{1,3}\s/)) {
            const level = l.match(/^(#+)/)[1].length;
            l = `<h${level + 2} style="margin:var(--space-2) 0 var(--space-1)">${l.replace(/^#+\s/, '')}</h${level + 2}>`;
        }
        return l;
    }).join('\n');
    return lines.replace(/(<li>.*?<\/li>\n?)+/gs, match => `<ul style="margin:var(--space-1) 0;padding-left:var(--space-4)">${match}</ul>`);
}

function _renderDocResultAll(results) {
    const resultArea = document.getElementById('doc-result-area');
    if (!resultArea) return;

    const modeLabels = { summary: 'Summary', outline: 'Outline', extract_key_points: 'Key Points', image: 'Image' };

    // Build stats from first result (shared metadata)
    const first = results[0];
    const stats = first.stats || {};
    let statsHtml = '<div class="doc-result-stats">';
    if (first.filename) statsHtml += `<span class="doc-result-stat"><strong>File:</strong> ${escapeHtml(first.filename)}</span>`;
    if (first.file_type) statsHtml += `<span class="doc-result-stat"><strong>Type:</strong> ${escapeHtml(first.file_type)}</span>`;
    if (stats.pages) statsHtml += `<span class="doc-result-stat"><strong>Pages:</strong> ${stats.pages}</span>`;
    if (stats.input_words) statsHtml += `<span class="doc-result-stat"><strong>Input:</strong> ${stats.input_words.toLocaleString()} words</span>`;
    const totalTime = results.reduce((s, r) => s + ((r.stats || {}).elapsed_seconds || 0), 0);
    if (totalTime) statsHtml += `<span class="doc-result-stat"><strong>Total time:</strong> ${totalTime.toFixed(1)}s</span>`;
    if (first.model) statsHtml += `<span class="doc-result-stat"><strong>Model:</strong> ${escapeHtml(first.model)}</span>`;
    statsHtml += '</div>';

    // Build sections for each mode
    const sectionsHtml = results.map(data => {
        const mode = data.mode || 'summary';
        const formatted = _formatDocMarkdown(data.summary);
        return `<div class="doc-result-all__section">
            <div class="doc-result-all__section-header">
                <span class="doc-result__mode-badge">${modeLabels[mode] || mode}</span>
            </div>
            <div class="doc-result__body">${formatted}</div>
        </div>`;
    }).join('');

    // Extracted text preview from first result
    let previewHtml = '';
    if (first.extracted_text_preview) {
        previewHtml = `<div class="doc-result-preview">
            <div class="doc-result-preview__header" onclick="this.parentElement.classList.toggle('doc-result-preview--open')">
                <span>Extracted Text Preview</span>
                <span class="doc-result-preview__toggle">[+]</span>
            </div>
            <div class="doc-result-preview__body"><pre>${escapeHtml(first.extracted_text_preview)}</pre></div>
        </div>`;
    }

    resultArea.innerHTML = `<div class="doc-result">
        <div class="doc-result__header">
            <span class="doc-result__mode-badge">All Modes</span>
            <button class="btn btn--sm" onclick="navigator.clipboard.writeText(document.getElementById('doc-result-all-content').innerText);showToast('Copied to clipboard','success')">Copy All</button>
        </div>
        ${statsHtml}
        <div id="doc-result-all-content">${sectionsHtml}</div>
        ${previewHtml}
    </div>`;
}

/* ── System Health Monitor ── */

async function renderHealthMonitorPanel(tool) {
    const tid = tool.id;
    const status = await fetchServiceStatus('health_monitor');

    // Fetch real health data
    let healthData = {};
    try {
        const resp = await fetch('/api/health-monitor/state');
        healthData = await resp.json();
    } catch {}

    const targetStatus = healthData.target_status || {};
    const lastAlerts = healthData.last_alerts || {};
    const paused = healthData.paused || false;

    // Categorize targets
    const services = [];
    const schedulers = [];
    const websites = [];
    const apis = [];

    for (const [key, val] of Object.entries(targetStatus)) {
        const name = key.replace(/^(service|scheduler|website|api):/, '');
        const isHealthy = val.ok === true || val.status === 'healthy';
        const isStale = val.status === 'stale';
        const dotStatus = isHealthy ? 'up' : isStale ? 'stale' : 'down';
        const detail = val.last_checked ? timeAgo(val.last_checked) : '';

        if (key.startsWith('service:')) {
            services.push({ name, status: dotStatus, detail });
        } else if (key.startsWith('scheduler:')) {
            const hrs = val.hours_since != null ? Math.round(val.hours_since) + 'h ago' : '';
            schedulers.push({ name, status: dotStatus, detail: hrs });
        } else if (key.startsWith('website:')) {
            websites.push({ name, status: dotStatus, detail });
        } else if (key.startsWith('api:')) {
            apis.push({ name, status: dotStatus, detail });
        }
    }

    // Count by status
    const allItems = [...services, ...schedulers, ...websites, ...apis];
    const upCount = allItems.filter(i => i.status === 'up').length;
    const downCount = allItems.filter(i => i.status === 'down').length;
    const staleCount = allItems.filter(i => i.status === 'stale').length;

    // Active alerts as log items
    const alertItems = Object.entries(lastAlerts)
        .sort((a, b) => (b[1].timestamp || '').localeCompare(a[1].timestamp || ''))
        .slice(0, 15)
        .map(([key, alert]) => ({
            time: fmtTime(alert.timestamp),
            status: alert.severity === 'error' ? 'error' : 'warning',
            message: (alert.message || '').replace(/^(Service DOWN|Scheduler stale|Scheduler never run|Website DOWN|API credential missing): /, ''),
            detail: alert.message || '',
        }));

    const pausedBanner = paused ? '<div style="padding:var(--space-2) var(--space-3);background:rgba(250,166,26,0.15);border-radius:var(--radius-md);margin-bottom:var(--space-3);font-size:var(--font-size-sm);color:var(--color-warning)">[!] Health monitor is paused -- data may be stale</div>' : '';

    dom.toolPanelContent.innerHTML = `<div class="tool-dashboard">
        ${toolHeader(tool, statusDotHtml(status.status))}
        ${pausedBanner}
        ${statRow(
            statCard('Healthy', upCount, { color: 'success' }) +
            statCard('Down', downCount, { color: downCount > 0 ? 'error' : 'success' }) +
            statCard('Stale', staleCount, { color: staleCount > 0 ? 'warning' : 'success' }) +
            statCard('Total', allItems.length)
        )}
        ${services.length > 0 ? configSectionFull('Services (' + services.length + ')', statusGrid(services)) : ''}
        ${schedulers.length > 0 ? configSectionFull('Schedulers (' + schedulers.length + ')', statusGrid(schedulers)) : ''}
        ${websites.length > 0 ? configSectionFull('Websites (' + websites.length + ')', statusGrid(websites)) : ''}
        ${apis.length > 0 ? configSectionFull('API Keys (' + apis.length + ')', statusGrid(apis)) : ''}
        ${configSectionFull('Recent Alerts (' + alertItems.length + ')', activityLog(alertItems, { emptyMsg: 'No active alerts' }))}
        ${configSection('Alert Settings',
            configCard('Channel', '#system-health') +
            configCard('Cooldown', '60 minute deduplication')
        )}
    </div>`;
    showToolHelpChat(tid);
}

/* ── LLM Manager ── */

async function renderLLMManagerPanel(tool) {
    const [data, runningData] = await Promise.all([
        fetchLLMModels(),
        fetch('/api/llm/running').then(r => r.json()).catch(() => ({ status: 'down', models: [] })),
    ]);
    const isUp = data.status === 'up';
    const models = data.models || [];
    const runningModels = runningData.models || [];

    // VRAM usage from running models
    let vramHtml = '';
    if (isUp && runningModels.length > 0) {
        const totalVram = runningModels.reduce((sum, m) => sum + (m.vram_bytes || 0), 0);
        const totalVramGB = (totalVram / 1073741824).toFixed(1);
        const gpuVram = 12.0; // RTX 3080 default, could be dynamic
        vramHtml = progressBar(parseFloat(totalVramGB), gpuVram, { label: 'GPU Memory', suffix: 'GB' });
    } else if (isUp) {
        vramHtml = `<div style="font-size:var(--font-size-sm);color:var(--color-text-muted);margin-bottom:var(--space-3)">No models loaded in memory</div>`;
    }

    // Running models indicator
    let runningHtml = '';
    if (runningModels.length > 0) {
        runningHtml = configSectionFull('Running Now', statusGrid(runningModels.map(m => ({
            name: m.name,
            status: 'up',
            detail: m.expires_at ? 'expires ' + timeAgo(m.expires_at) : '',
        }))));
    }

    // Model list
    let modelsHtml = '';
    if (!isUp) {
        modelsHtml = '<p class="tool-config-card__value--muted" style="margin-top:var(--space-3)">Ollama is not running. Start it to manage models.</p>';
    } else if (models.length === 0) {
        modelsHtml = '<p class="tool-config-card__value--muted" style="margin-top:var(--space-3)">No models installed. Pull one below.</p>';
    } else {
        modelsHtml = `<div class="llm-model-list">${models.map(m => {
            const family = m.family ? escapeHtml(m.family) : '';
            const params = m.parameter_size ? escapeHtml(m.parameter_size) : '';
            const quant = m.quantization ? escapeHtml(m.quantization) : '';
            const meta = [params, quant].filter(Boolean).join(' / ');
            const isRunning = runningModels.some(r => r.name === m.name);
            return `
            <div class="llm-model-item"${isRunning ? ' style="border-color:var(--color-success)"' : ''}>
                <div class="llm-model-item__info">
                    <span class="llm-model-item__name">${escapeHtml(m.name)}${isRunning ? ' <span style="color:var(--color-success);font-size:var(--font-size-xs)">[LOADED]</span>' : ''}</span>
                    <span class="llm-model-item__size">${escapeHtml(m.size)}${family ? ' -- ' + family : ''}</span>
                    ${meta ? `<span class="llm-model-item__size">${meta}</span>` : ''}
                </div>
                <div class="llm-model-item__actions">
                    <button class="btn--danger btn--sm" onclick="deleteLLMModel('${escapeHtml(m.name)}')">Remove</button>
                </div>
            </div>`;
        }).join('')}
        </div>`;
    }

    // Quick-pull suggestions
    const quickPullHtml = isUp ? `<div class="tool-quick-pull">
        <button class="tool-quick-pull__btn" onclick="document.getElementById('llm-pull-input').value='llama3.2:1b';pullLLMModel()">llama3.2:1b</button>
        <button class="tool-quick-pull__btn" onclick="document.getElementById('llm-pull-input').value='qwen3.5:9b';pullLLMModel()">qwen3.5:9b</button>
        <button class="tool-quick-pull__btn" onclick="document.getElementById('llm-pull-input').value='gemma3:4b';pullLLMModel()">gemma3:4b</button>
        <button class="tool-quick-pull__btn" onclick="document.getElementById('llm-pull-input').value='qwen2.5-coder:7b';pullLLMModel()">qwen2.5-coder:7b</button>
    </div>` : '';

    dom.toolPanelContent.innerHTML = `<div class="tool-dashboard">
        ${toolHeader(tool, statusDotHtml(isUp ? 'up' : 'down'))}
        ${vramHtml}
        ${runningHtml}
        ${configSection('Installed Models (' + models.length + ')', modelsHtml)}
        ${isUp ? `
        <div class="tool-config-section">
            <h3 class="tool-config-section__title">Pull New Model</h3>
            <div class="llm-pull-form">
                <input type="text" id="llm-pull-input" placeholder="e.g. llama3.2:1b, qwen3:8b, gemma3:4b">
                <button class="btn btn--primary btn--sm" onclick="pullLLMModel()">Pull</button>
            </div>
            ${quickPullHtml}
            <div class="llm-pull-progress" id="llm-pull-progress"></div>
        </div>` : ''}
    </div>`;
}

async function fetchLLMModels() {
    try {
        const resp = await fetch('/api/llm/models');
        if (!resp.ok) return { status: 'down', models: [] };
        return await resp.json();
    } catch { return { status: 'down', models: [] }; }
}

async function pullLLMModel() {
    const input = $('#llm-pull-input');
    const progress = $('#llm-pull-progress');
    if (!input || !input.value.trim()) return;

    const model = input.value.trim();
    progress.textContent = 'Pulling ' + model + '...';

    try {
        const resp = await fetch('/api/llm/pull', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ model }),
        });
        const data = await resp.json();
        if (data.error) {
            progress.textContent = 'Error: ' + data.error;
        } else {
            progress.textContent = 'Pull started for ' + model + '. This may take a few minutes...';
            pollLLMPull(model);
        }
    } catch (err) {
        progress.textContent = 'Failed to start pull: ' + err.message;
    }
}

async function pollLLMPull(model) {
    const progress = $('#llm-pull-progress');
    const check = async () => {
        const data = await fetchLLMModels();
        const found = (data.models || []).some(m => m.name === model || m.name.startsWith(model.split(':')[0]));
        if (found) {
            progress.textContent = model + ' installed successfully.';
            const tool = (state.tools || []).find(t => t.id === 'llm_manager');
            if (tool) renderLLMManagerPanel(tool);
        } else {
            progress.textContent = 'Pulling ' + model + '...';
            setTimeout(check, 5000);
        }
    };
    setTimeout(check, 5000);
}

async function deleteLLMModel(name) {
    if (!confirm('Remove model "' + name + '"?')) return;
    try {
        const resp = await fetch('/api/llm/models/' + encodeURIComponent(name), { method: 'DELETE' });
        const data = await resp.json();
        if (data.error) {
            showToast('Failed to remove: ' + data.error, 'error');
        } else {
            showToast('Removed ' + name, 'success');
            const tool = (state.tools || []).find(t => t.id === 'llm_manager');
            if (tool) renderLLMManagerPanel(tool);
        }
    } catch (err) {
        showToast('Failed: ' + err.message, 'error');
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

function _extractAgentIdFromDm(channelId) {
    // dm-python_developer -> python_developer
    // dm-python_developer-2 -> python_developer
    const stripped = channelId.replace(/^dm-/, '');
    // Remove trailing -N suffix (number only)
    return stripped.replace(/-\d+$/, '');
}

function renderAgentChats() {
    if (!dom.agentChatList) return;

    const SKIP_IDS = new Set(['user', 'system']);

    // Build lookup: agentId -> latest DM channel (if any)
    const dmChannels = state.channels.filter(ch => ch.id.startsWith('dm-'));
    const dmByAgent = {};
    for (const ch of dmChannels) {
        const agentId = _extractAgentIdFromDm(ch.id);
        dmByAgent[agentId] = ch;
    }

    // Collect all agents from profiles registry, grouped
    const groups = {};
    for (const [agentId, profile] of Object.entries(state.agentProfiles)) {
        if (SKIP_IDS.has(agentId)) continue;
        const group = profile.group || 'Other';
        if (!groups[group]) groups[group] = [];
        groups[group].push({ agentId, profile });
    }

    // Sort groups by GROUP_ORDER
    const sortedGroupNames = Object.keys(groups).sort((a, b) => {
        const ia = GROUP_ORDER.indexOf(a);
        const ib = GROUP_ORDER.indexOf(b);
        return (ia === -1 ? 999 : ia) - (ib === -1 ? 999 : ib);
    });

    // Load persisted collapse state
    const collapseKey = 'cohort_agent_chat_groups';
    let groupState = {};
    try { groupState = JSON.parse(localStorage.getItem(collapseKey) || '{}'); } catch { /* ignore */ }

    let html = '';
    for (const groupName of sortedGroupNames) {
        const agents = groups[groupName];
        // Sort agents alphabetically by display name
        agents.sort((a, b) => {
            const na = a.profile.nickname || a.profile.name || a.agentId;
            const nb = b.profile.nickname || b.profile.name || b.agentId;
            return na.localeCompare(nb);
        });

        const isOpen = groupState[groupName] !== false; // default open
        html += `<li class="agent-chat-group ${isOpen ? 'open' : ''}" data-agent-group="${escapeHtml(groupName)}">
            <div class="agent-chat-group__header" onclick="toggleAgentChatGroup('${escapeHtml(groupName)}')">
                <span class="sidebar-nav__toggle">></span>
                <span class="agent-chat-group__name">${escapeHtml(groupName)}</span>
                <span class="agent-chat-group__count">${agents.length}</span>
            </div>
            <ul class="agent-chat-group__body">`;

        for (const { agentId, profile } of agents) {
            const dm = dmByAgent[agentId];
            const displayName = profile.name || profile.nickname || agentId.replace(/_/g, ' ');
            const hasChat = !!dm;
            const isActive = dm && dm.id === state.currentChannel;

            // Status from live agent state; offline if not connected
            const liveAgent = state.agents.find(a => a.agent_id === agentId);
            const status = liveAgent ? (liveAgent.status || 'idle') : 'offline';

            // Click: open existing chat or start new one
            const clickAction = hasChat
                ? `switchChannel('${escapeHtml(dm.id)}')`
                : `openChatForAgent('${escapeHtml(agentId)}')`;

            html += `
                <li class="channel-item ${isActive ? 'active' : ''}"
                    onclick="${clickAction}">
                    <span class="agent-chat-dot agent-chat-dot--${status}"></span>
                    <span class="channel-item__name">${escapeHtml(displayName)}</span>
                    ${hasChat ? `<button class="item-edit-btn" onclick="event.stopPropagation(); openEditMenu(this, 'dm', '${escapeHtml(dm.id)}')" title="Edit">[:]</button>` : ''}
                </li>`;
        }

        html += `</ul></li>`;
    }

    dom.agentChatList.innerHTML = html;
}

function toggleAgentChatGroup(groupName) {
    const collapseKey = 'cohort_agent_chat_groups';
    let gs = {};
    try { gs = JSON.parse(localStorage.getItem(collapseKey) || '{}'); } catch { /* ignore */ }
    gs[groupName] = !(gs[groupName] !== false);
    localStorage.setItem(collapseKey, JSON.stringify(gs));
    renderAgentChats();
}

function _formatShortDate(isoString) {
    try {
        const d = new Date(isoString);
        return d.toLocaleDateString('en-US', { month: 'short', day: 'numeric' });
    } catch { return ''; }
}

function _isCurrentChannelArchived() {
    return state.archivedChannels.some(ch => ch.id === state.currentChannel);
}

function _getResponseMode() {
    if (!state.currentChannel) return 'smarter';
    return state.responseModeChannels[state.currentChannel] || 'smarter';
}

function toggleResponseMode() {
    if (!state.currentChannel) return;
    const current = _getResponseMode();

    let next;
    if (current === 'smart') {
        next = 'smarter';
    } else if (current === 'smarter') {
        next = state.smartestAvailable ? 'smartest' : 'smart';
    } else {
        // smartest -> smart
        next = 'smart';
    }

    if (next === 'smarter') {
        // Default -- remove from map
        delete state.responseModeChannels[state.currentChannel];
    } else {
        state.responseModeChannels[state.currentChannel] = next;
    }

    _updateResponseModeBtn();

    const toasts = {
        smart: 'Smart mode -- fast responses, no thinking',
        smarter: 'Smarter mode -- thinking enabled, full reasoning',
        smartest: 'Smartest mode -- Qwen reasoning + Claude polish',
    };
    showToast(toasts[next], 'info');
}

function _updateResponseModeBtn() {
    const btn = document.getElementById('response-mode-btn');
    if (!btn) return;

    const mode = _getResponseMode();
    btn.classList.remove('smart', 'smartest');

    if (mode === 'smart') {
        btn.classList.add('smart');
        btn.textContent = '[S]';
        btn.title = 'Smart mode (no thinking) -- click for Smarter';
    } else if (mode === 'smartest') {
        btn.classList.add('smartest');
        btn.textContent = '[S++]';
        btn.title = 'Smartest mode (Qwen + Claude) -- click for Smart';
    } else {
        // smarter (default)
        btn.textContent = '[S+]';
        btn.title = 'Smarter mode (thinking enabled) -- click for '
            + (state.smartestAvailable ? 'Smartest' : 'Smart');
    }
}

function renderArchivedChats() {
    if (!dom.archivedChatList) return;

    const archivedDms = state.archivedChannels.filter(ch => ch.id.startsWith('dm-'));

    if (archivedDms.length === 0) {
        dom.archivedChatList.innerHTML = '<li style="padding: 8px 16px; color: var(--color-text-muted); font-size: 12px;">No archived chats</li>';
        return;
    }

    // Group by agent, newest first within each agent
    const byAgent = {};
    for (const ch of archivedDms) {
        const agentId = _extractAgentIdFromDm(ch.id);
        if (!byAgent[agentId]) byAgent[agentId] = [];
        byAgent[agentId].push(ch);
    }

    let html = '';
    for (const [agentId, channels] of Object.entries(byAgent)) {
        const profile = state.agentProfiles[agentId];
        const displayName = profile ? (profile.nickname || profile.name || agentId) : agentId.replace(/_/g, ' ');

        // Sort newest first (by archived_at or created_at)
        channels.sort((a, b) => (b.archived_at || b.created_at || '').localeCompare(a.archived_at || a.created_at || ''));

        for (const ch of channels) {
            const isActive = ch.id === state.currentChannel;
            const dateLabel = _formatShortDate(ch.archived_at || ch.created_at);
            html += `
                <li class="channel-item channel-item--archived ${isActive ? 'active' : ''}"
                    data-channel="${escapeHtml(ch.id)}"
                    onclick="switchChannel('${escapeHtml(ch.id)}')">
                    <span class="channel-item__name">@ ${escapeHtml(displayName)}${dateLabel ? ' - ' + escapeHtml(dateLabel) : ''}</span>
                </li>`;
        }
    }

    dom.archivedChatList.innerHTML = html;
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

// =====================================================================
// Sidebar section collapse (localStorage-persisted)
// =====================================================================

function loadSidebarSectionState() {
    try {
        const raw = localStorage.getItem('cohort_sidebar_sections');
        if (raw) return JSON.parse(raw);
    } catch { /* ignore */ }
    return {};  // default: all open
}

function saveSidebarSectionState(state) {
    localStorage.setItem('cohort_sidebar_sections', JSON.stringify(state));
}

function toggleSidebarSection(sectionName) {
    const el = document.querySelector(`.sidebar-nav__section[data-section="${sectionName}"]`);
    if (!el) return;
    const isOpen = el.classList.toggle('open');
    const ss = loadSidebarSectionState();
    ss[sectionName] = isOpen;
    saveSidebarSectionState(ss);
}

function restoreSidebarSections() {
    const ss = loadSidebarSectionState();
    document.querySelectorAll('.sidebar-nav__section').forEach(el => {
        const name = el.dataset.section;
        if (name && ss[name] === false) {
            el.classList.remove('open');
        }
    });
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
    // Exclude dm-* channels (shown in Agent Chats section) and foldered channels
    return state.channels.filter(ch => !folderedIds.has(ch.id) && !ch.id.startsWith('dm-'));
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
    } else if (itemType === 'dm') {
        menu.innerHTML = `
            <button class="item-edit-menu__option" data-action="archive">Archive</button>`;
    } else {
        // regular channel
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
        if (action === 'archive') handleArchiveChannel(itemId);
    });

    // Position as fixed overlay near the button
    const rect = btnEl.getBoundingClientRect();
    document.body.appendChild(menu);
    menu.style.top = rect.bottom + 2 + 'px';
    menu.style.left = rect.left + 'px';
    // Keep menu on screen if it overflows right edge
    requestAnimationFrame(() => {
        const menuRect = menu.getBoundingClientRect();
        if (menuRect.right > window.innerWidth) {
            menu.style.left = (window.innerWidth - menuRect.width - 8) + 'px';
        }
    });
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
        // Channel rename via server
        const ch = state.channels.find(c => c.id === itemId);
        const currentName = ch ? (ch.name || ch.id) : itemId;
        const newName = prompt('Rename channel:', currentName);
        if (newName && newName.trim() && newName.trim() !== currentName) {
            state.socket.emit('rename_channel',
                { channel_id: itemId, name: newName.trim() },
                (resp) => {
                    if (resp && resp.success) {
                        if (ch) ch.name = newName.trim();
                        renderChannels();
                        showToast(`Channel renamed to "${newName.trim()}"`, 'success');
                    } else {
                        showToast(resp?.error || 'Failed to rename channel', 'error');
                    }
                }
            );
        }
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
    } else if (itemType === 'dm') {
        if (!confirm('Delete this chat and all its messages? This cannot be undone.')) return;
        if (state.socket && state.connected) {
            state.socket.emit('delete_channel', { channel_id: itemId }, (resp) => {
                if (resp && resp.success) {
                    if (state.currentChannel === itemId) switchPanel('team');
                    showToast('Chat deleted', 'success');
                } else {
                    showToast('Failed to delete chat: ' + (resp?.error || 'unknown'), 'error');
                }
            });
        }
    } else {
        if (!confirm('Delete this channel and all its messages? This cannot be undone.')) return;
        if (state.socket && state.connected) {
            state.socket.emit('delete_channel', { channel_id: itemId }, (resp) => {
                if (resp && resp.success) {
                    if (state.currentChannel === itemId) switchPanel('team');
                    showToast('Channel deleted', 'success');
                } else {
                    showToast('Failed to delete channel: ' + (resp?.error || 'unknown'), 'error');
                }
            });
        }
    }
}

function handleArchiveChannel(channelId) {
    if (!state.socket || !state.connected) return;
    state.socket.emit('archive_channel', { channel_id: channelId });
    // If viewing this channel, switch to another
    if (state.currentChannel === channelId) {
        const remaining = state.channels.filter(ch => ch.id !== channelId);
        if (remaining.length > 0) {
            switchChannel(remaining[0].id);
        }
    }
    showToast('Chat archived', 'success');
}

function _updateArchivedBanner() {
    // Remove any existing banner
    const existing = document.querySelector('.archived-banner');
    if (existing) existing.remove();
    // Show banner if viewing an archived channel
    if (_isCurrentChannelArchived() && dom.messageForm) {
        const banner = document.createElement('div');
        banner.className = 'archived-banner';
        banner.textContent = 'This chat is archived. Send a message to continue the conversation.';
        banner.onclick = () => {
            if (state.socket && state.connected) {
                state.socket.emit('unarchive_channel', { channel_id: state.currentChannel });
                showToast('Chat unarchived', 'success');
            }
        };
        dom.messageForm.parentElement.insertBefore(banner, dom.messageForm);
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
    state.currentTool = null;

    // Switch to chat panel if not already there
    if (state.currentPanel !== 'chat') {
        switchPanel('chat');
    }

    renderSidebarTools();

    // Join channel via Socket.IO
    if (state.socket && state.connected) {
        state.socket.emit('join_channel', { channel_id: channelId });
    }

    renderChannels();
    renderAgentChats();
    renderArchivedChats();
    renderFolders();
    renderMessages();
    updateParticipants();
    // Show archived banner if viewing an archived channel
    _updateArchivedBanner();
    // Sync deep mode button state for this channel
    _updateResponseModeBtn();
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
        // User (human operator) is always online since they're the one using the app.
        // Agents are online if they exist in the team snapshot OR the agent registry.
        const isHuman = agentId === 'user';
        const isOnline = isHuman || agentState != null || !!state.agentProfiles[agentId];
        const status = isHuman ? 'Online' : (agentState ? (agentState.status || 'Online') : (state.agentProfiles[agentId] ? 'Online' : 'Offline'));
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

    const aid = escapeHtml(agent.agent_id);
    const hasExistingChat = state.channels.some(ch => ch.id === `dm-${agent.agent_id}` || ch.id.startsWith(`dm-${agent.agent_id}-`));

    return `
    <div class="agent-card" data-agent-id="${aid}">
        <div class="agent-card__header">
            <h3 class="agent-card__name">${escapeHtml(agent.name || agent.agent_id)}</h3>
            <div class="agent-card__header-actions">
                <button class="agent-card__gear" onclick="event.stopPropagation(); openToolPerms('${aid}')" title="Tool permissions">&#9881;</button>
                <div class="agent-card__status">
                    <span class="agent-card__status-dot agent-card__status-dot--${agent.status}"></span>
                    ${agent.status}
                </div>
            </div>
        </div>
        <div class="agent-card__skills">${skills}</div>
        ${taskHtml}
        <div class="agent-card__footer">
            <span class="agent-card__stat">${agent.tasks_completed || 0} tasks completed</span>
            <div class="agent-card__actions">
                <button class="btn btn--small btn--secondary${hasExistingChat ? '' : ' btn--disabled'}" onclick="openChatForAgent('${aid}')"${hasExistingChat ? '' : ' disabled'}>Chat</button>
                <button class="btn btn--small btn--primary" onclick="openNewChatForAgent('${aid}')">New Chat</button>
                <button class="btn btn--small btn--secondary" onclick="openAssignForAgent('${aid}')">Assign</button>
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

const GROUP_ORDER = ['Leadership', 'Core Developers', 'Quality & Security', 'Marketing & Content', 'Social Media', 'Support', 'Operators'];

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
                    <div class="agent-grid${agents.length === 4 ? ' agent-grid--cols2' : ''}">
                        ${agents.map(renderAgentCard).join('')}
                    </div>
                </div>
            </div>`;
    }).join('')}</div>`;

    dom.teamBadge.textContent = state.agents.length;
    updatePanelCount();
}

function renderQueue() {
    renderWorkQueue();
    renderTaskList();
    // Badge: queued/active work items only
    const wqCount = state.workQueue.filter((i) => i.status === 'queued' || i.status === 'active').length;
    dom.queueBadge.textContent = wqCount;
    // Task badge
    const taskCount = state.tasks.filter((t) => t.status !== 'complete').length;
    if (dom.taskBadge) dom.taskBadge.textContent = taskCount;
    updatePanelCount();
}

function renderWorkQueue() {
    if (!dom.workQueueList) return;
    const items = state.workQueue.filter((i) => i.status !== 'completed' && i.status !== 'failed' && i.status !== 'cancelled');

    if (items.length === 0) {
        dom.workQueueList.innerHTML =
            '<div class="empty-state" id="wq-empty">' +
            '<p class="empty-state__text">Queue is empty</p>' +
            '<p class="empty-state__hint">Items execute one at a time in priority order</p>' +
            '</div>';
        return;
    }

    dom.workQueueList.innerHTML = items.map((item, idx) => {
        const isActive = item.status === 'active';
        const priorityColor = { critical: '#e74c3c', high: '#e67e22', medium: '#3498db', low: '#95a5a6' }[item.priority] || '#3498db';
        const statusLabel = isActive ? 'RUNNING' : `#${idx + 1}`;
        const agentStr = item.agent_id ? ` @${item.agent_id}` : '';
        const desc = (item.description || '').slice(0, 100);
        const borderStyle = isActive ? 'border-left: 3px solid #2ecc71; animation: pulse 2s infinite;' : 'border-left: 3px solid ' + priorityColor + ';';

        return '<div class="wq-item" style="' + borderStyle + ' padding: 0.5rem 0.75rem; margin-bottom: 0.4rem; background: var(--card-bg, #1e1e2e); border-radius: 4px;">' +
            '<div style="display: flex; justify-content: space-between; align-items: center;">' +
            '<span style="font-weight: 600; font-size: 0.8rem; color: ' + (isActive ? '#2ecc71' : 'var(--text-secondary, #888)') + ';">' + statusLabel + '</span>' +
            '<span style="font-size: 0.7rem; color: ' + priorityColor + '; text-transform: uppercase; font-weight: 600;">' + item.priority + '</span>' +
            '</div>' +
            '<p style="margin: 0.25rem 0 0; font-size: 0.85rem; color: var(--text-primary, #ccc);">' + desc + agentStr + '</p>' +
            '<span style="font-size: 0.7rem; color: var(--text-secondary, #666);">' + item.id + '</span>' +
            '</div>';
    }).join('');
}

function renderTaskList() {
    if (!dom.taskList) return;
    let tasks = state.tasks;
    if (state.filter !== 'all') {
        tasks = tasks.filter((t) => t.status === state.filter);
    }

    if (tasks.length === 0) {
        dom.taskList.innerHTML =
            '<div class="empty-state" id="tasks-empty">' +
            '<p class="empty-state__text">No tasks assigned</p>' +
            '<p class="empty-state__hint">Assign tasks to agents from the Team panel or click below</p>' +
            '</div>';
        return;
    }

    dom.taskList.innerHTML = tasks.map(renderTaskCard).join('');
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

    // Include pending social media posts in the review panel
    const socialPosts = state.pendingSocialPosts || [];
    const showSocial = state.filter === 'all' || state.filter === 'needs_review';

    const taskCardsHtml = filtered.map(renderOutputCard).join('');
    const socialCardsHtml = showSocial ? socialPosts.map(renderSocialPostOutputCard).join('') : '';
    const combinedHtml = socialCardsHtml + taskCardsHtml;

    if (!combinedHtml) {
        dom.outputList.innerHTML = '';
        const emptyEl = createEmpty('output-empty', 'No outputs to review', 'Completed task outputs and pending social posts will appear here');
        dom.outputList.appendChild(emptyEl);
    } else {
        dom.outputList.innerHTML = combinedHtml;
    }

    const taskReviewCount = outputs.filter((t) => !t.review).length;
    const totalPending = taskReviewCount + socialPosts.length;
    dom.outputBadge.textContent = totalPending;
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

    sock.on('connect', async () => {
        state.connected = true;
        dom.connectionStatus.textContent = 'Connected';
        dom.connectionStatus.className = 'sidebar__status connected';
        sock.emit('join', {});
        sock.emit('get_channels', {});
        sock.emit('get_archived_channels', {});
        fetchTools();
        fetchSessions();
        fetchPendingSocialPosts();
        // Load settings (admin mode + user display name)
        fetch('/api/settings').then(r => r.json()).then(d => {
            state.adminMode = !!d.admin_mode;
            state.smartestAvailable = !!d.smartest_available;
            applyUserIdentity(d.user_display_name || '', d.user_display_role || '', d.user_display_avatar || '');
        }).catch(() => {});
        showToast('Connected to Cohort', 'success');

        // Always init setup wizard (wires re-run button etc.), auto-show on first run
        try {
            const resp = await fetch('/api/setup/status');
            const data = await resp.json();
            setupWizard.init(data);
            if (!data.setup_completed) {
                setupWizard.show();
            }
        } catch (e) { /* setup endpoints not available -- skip */ }
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
        renderAgentChats();
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

    sock.on('cohort:work_queue_update', (data) => {
        state.workQueue = data.items || [];
        renderQueue();
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
        fetchSessions();
        showToast(`Status: ${data.event_type || 'change'}`, 'info');
    });

    sock.on('cohort:error', (data) => {
        showToast(data.message || 'An error occurred', 'error');
    });

    // -- Chat events --

    sock.on('channels_list', (data) => {
        state.channels = data.channels || [];
        renderChannels();
        renderAgentChats();
        renderArchivedChats();
        renderFolders();
    });

    sock.on('archived_channels_list', (data) => {
        state.archivedChannels = data.channels || [];
        renderArchivedChats();
    });

    sock.on('channel_messages', (data) => {
        state.messages[data.channel_id] = data.messages || [];

        // Backfill members from message history (sender + @mentions)
        (data.messages || []).forEach(msg => autoAddMembersFromMessage(data.channel_id, msg));

        if (data.channel_id === state.currentChannel) {
            renderMessages();
        }

        // Render tool-help mini-chat if this is the active help channel
        if (_toolHelpChannel && data.channel_id === _toolHelpChannel) {
            renderToolHelpMessages();
        }
        // Update chat badge with total message count
        let total = 0;
        for (const ch in state.messages) {
            total += state.messages[ch].length;
        }
        if (dom.chatBadge) dom.chatBadge.textContent = total;
    });

    sock.on('new_message', (message) => {
        // Add to state with dedup
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

        // Also render in tool-help mini-chat if matching
        if (_toolHelpChannel && message.channel_id === _toolHelpChannel) {
            renderToolHelpMessages();
            const mc = document.getElementById('tool-help-messages');
            if (mc) mc.scrollTop = mc.scrollHeight;
            // Hide typing indicator when response arrives
            const ti = document.getElementById('tool-help-typing');
            if (ti) { ti.style.display = 'none'; ti.innerHTML = ''; }
        }
    });

    // -- Message deletion broadcast --
    sock.on('message_deleted', (data) => {
        const msgs = state.messages[data.channel_id];
        if (msgs) {
            state.messages[data.channel_id] = msgs.filter(m => m.id !== data.message_id);
            if (data.channel_id === state.currentChannel) {
                renderMessages();
            }
        }
    });

    // -- Typing indicator for agent responses --
    sock.on('user_typing', (data) => {
        // Tool-help mini-chat typing indicator
        if (_toolHelpChannel && data.channel_id === _toolHelpChannel) {
            const ti = document.getElementById('tool-help-typing');
            if (ti) {
                if (data.typing) {
                    const profile = getAgentProfile(data.sender);
                    ti.innerHTML = `<span class="tool-help-typing__text">${escapeHtml(profile.nickname)} is thinking...</span>`;
                    ti.style.display = 'block';
                    const mc = document.getElementById('tool-help-messages');
                    if (mc) mc.scrollTop = mc.scrollHeight;
                } else {
                    ti.style.display = 'none';
                    ti.innerHTML = '';
                }
            }
        }

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

function _getDmChannelsForAgent(agentId) {
    // Find all DM channels for this agent: dm-{agentId} or dm-{agentId}-{n}
    return state.channels.filter(ch =>
        ch.id === `dm-${agentId}` || ch.id.startsWith(`dm-${agentId}-`)
    );
}

function openChatForAgent(agentId) {
    // Continue the most recent DM channel for this agent
    const dmChannels = _getDmChannelsForAgent(agentId);
    if (dmChannels.length === 0) {
        // No existing chat -- start a new one instead
        openNewChatForAgent(agentId);
        return;
    }

    // Pick the most recently created channel (last in the list, or highest suffix)
    const channelId = dmChannels[dmChannels.length - 1].id;

    addChannelMember(channelId, 'user');
    addChannelMember(channelId, agentId);
    switchChannel(channelId);
}

function openNewChatForAgent(agentId) {
    // Archive all existing active DM channels for this agent
    const existing = _getDmChannelsForAgent(agentId);
    if (existing.length > 0 && state.socket && state.connected) {
        for (const ch of existing) {
            state.socket.emit('archive_channel', { channel_id: ch.id });
        }
    }

    // Calculate next channel ID, checking both active and archived to avoid collisions
    let channelId;
    if (existing.length === 0 && state.archivedChannels.filter(ch =>
        ch.id === `dm-${agentId}` || ch.id.startsWith(`dm-${agentId}-`)
    ).length === 0) {
        channelId = `dm-${agentId}`;
    } else {
        let maxNum = 1;
        // Check active channels
        for (const ch of existing) {
            const match = ch.id.match(/^dm-.*-(\d+)$/);
            if (match) maxNum = Math.max(maxNum, parseInt(match[1], 10));
        }
        // Check archived channels too
        for (const ch of state.archivedChannels) {
            if (ch.id === `dm-${agentId}` || ch.id.startsWith(`dm-${agentId}-`)) {
                const match = ch.id.match(/^dm-.*-(\d+)$/);
                if (match) maxNum = Math.max(maxNum, parseInt(match[1], 10));
            }
        }
        channelId = `dm-${agentId}-${maxNum + 1}`;
    }

    addChannelMember(channelId, 'user');
    addChannelMember(channelId, agentId);
    switchChannel(channelId);

    // Refresh channel list so sidebar picks it up
    if (state.socket && state.connected) {
        state.socket.emit('get_channels', {});
    }
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

function switchSettingsTab(tabName) {
    document.querySelectorAll('.settings-tab').forEach(t => {
        t.classList.toggle('active', t.dataset.settingsTab === tabName);
    });
    document.querySelectorAll('.settings-panel').forEach(p => {
        p.classList.toggle('active', p.dataset.settingsPanel === tabName);
    });
    if (tabName === 'data') loadDeletedChannels();
}

function openSettings() {
    // Load current settings from server
    fetch('/api/settings')
        .then(r => r.json())
        .then(data => {
            if (dom.settingsUserName) dom.settingsUserName.value = data.user_display_name || '';
            if (dom.settingsUserRole) dom.settingsUserRole.value = data.user_display_role || '';
            if (dom.settingsUserAvatar) dom.settingsUserAvatar.value = data.user_display_avatar || '';
            if (dom.settingsApiKey) dom.settingsApiKey.value = data.api_key_masked || '';
            if (dom.settingsClaudeCmd) dom.settingsClaudeCmd.value = data.claude_cmd || '';
            if (dom.settingsAgentsRoot) dom.settingsAgentsRoot.value = data.agents_root || '';
            if (dom.settingsResponseTimeout) dom.settingsResponseTimeout.value = data.response_timeout || 300;
            if (dom.settingsExecBackend) dom.settingsExecBackend.value = data.execution_backend || 'cli';

            // Claude Code enabled toggle
            const claudeEnabled = document.getElementById('settings-claude-enabled');
            const claudeBody = document.getElementById('claude-connection-body');
            const isEnabled = !!data.claude_enabled;
            if (claudeEnabled) claudeEnabled.checked = isEnabled;
            if (claudeBody) claudeBody.classList.toggle('settings-section__body--collapsed', !isEnabled);

            // Force to Claude Code toggle
            const forceClaudeToggle = document.getElementById('settings-force-claude-code');
            if (forceClaudeToggle) forceClaudeToggle.checked = !!data.force_to_claude_code;

            // Admin mode toggle
            state.adminMode = !!data.admin_mode;
            const adminToggle = document.getElementById('settings-admin-mode');
            if (adminToggle) adminToggle.checked = state.adminMode;

            // Show connection status
            updateSettingsConnectionStatus(data.claude_code_connected ? 'ok' : 'unknown',
                data.claude_code_connected ? 'Claude CLI found' : 'Not tested');
        })
        .catch(() => {
            // Fields stay empty if server can't be reached
        });

    if (dom.settingsModal) dom.settingsModal.hidden = false;
    switchSettingsTab('general');
}

function loadDeletedChannels() {
    const container = document.getElementById('deleted-channels-list');
    if (!container || !state.socket || !state.connected) return;

    state.socket.emit('list_deleted_channels', {}, (resp) => {
        if (!resp || resp.error) {
            container.innerHTML = '<p style="color: var(--color-text-muted); font-size: 12px;">Could not load deleted channels</p>';
            return;
        }
        const channels = resp.channels || [];
        if (channels.length === 0) {
            container.innerHTML = '<p style="color: var(--color-text-muted); font-size: 12px;">No deleted channels</p>';
            return;
        }

        container.innerHTML = channels.map(ch => {
            const daysAgo = Math.floor((Date.now() - new Date(ch.deleted_at).getTime()) / 86400000);
            const daysLeft = Math.max(0, 30 - daysAgo);
            const name = ch.name || ch.id;
            return `
                <div class="deleted-channel-row">
                    <div class="deleted-channel-info">
                        <span class="deleted-channel-name">${escapeHtml(name)}</span>
                        <span class="deleted-channel-meta">${ch.message_count} messages -- expires in ${daysLeft} days</span>
                    </div>
                    <div class="deleted-channel-actions">
                        <button class="btn btn--small btn--primary" onclick="restoreDeletedChannel('${escapeHtml(ch.id)}')">Restore</button>
                        <button class="btn btn--small btn--danger" onclick="permDeleteChannel('${escapeHtml(ch.id)}')">Delete</button>
                    </div>
                </div>`;
        }).join('');
    });
}

function restoreDeletedChannel(channelId) {
    if (!state.socket || !state.connected) return;
    state.socket.emit('restore_channel', { channel_id: channelId }, (resp) => {
        if (resp && resp.success) {
            showToast('Channel restored', 'success');
            loadDeletedChannels();
        } else {
            showToast('Failed to restore: ' + (resp?.error || 'unknown'), 'error');
        }
    });
}

function permDeleteChannel(channelId) {
    if (!confirm('Permanently delete this channel? This cannot be undone.')) return;
    if (!state.socket || !state.connected) return;
    state.socket.emit('permanently_delete_channel', { channel_id: channelId }, (resp) => {
        if (resp && resp.success) {
            showToast('Channel permanently deleted', 'success');
            loadDeletedChannels();
        } else {
            showToast('Failed to delete: ' + (resp?.error || 'unknown'), 'error');
        }
    });
}

function closeSettings() {
    if (dom.settingsModal) dom.settingsModal.hidden = true;
}

function saveSettings(e) {
    e.preventDefault();

    const adminToggle = document.getElementById('settings-admin-mode');
    const claudeToggle = document.getElementById('settings-claude-enabled');
    const forceClaudeCode = document.getElementById('settings-force-claude-code');
    const payload = {
        user_display_name: dom.settingsUserName ? dom.settingsUserName.value.trim() : '',
        user_display_role: dom.settingsUserRole ? dom.settingsUserRole.value.trim() : '',
        user_display_avatar: dom.settingsUserAvatar ? dom.settingsUserAvatar.value.trim().toUpperCase() : '',
        claude_enabled: claudeToggle ? claudeToggle.checked : false,
        claude_cmd: dom.settingsClaudeCmd ? dom.settingsClaudeCmd.value.trim() : '',
        agents_root: dom.settingsAgentsRoot ? dom.settingsAgentsRoot.value.trim() : '',
        response_timeout: dom.settingsResponseTimeout ? parseInt(dom.settingsResponseTimeout.value, 10) : 300,
        execution_backend: dom.settingsExecBackend ? dom.settingsExecBackend.value : 'cli',
        admin_mode: adminToggle ? adminToggle.checked : false,
        force_to_claude_code: forceClaudeCode ? forceClaudeCode.checked : false,
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
                state.adminMode = payload.admin_mode;
                applyUserIdentity(payload.user_display_name, payload.user_display_role, payload.user_display_avatar);
                showToast('Settings saved', 'success');
                closeSettings();
                // Re-render current tool panel if open (tier visibility may have changed)
                if (state.currentTool && state.currentPanel === 'tool') {
                    const tool = (state.tools || []).find(t => t.id === state.currentTool);
                    if (tool) renderToolPanel(tool.id);
                }
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
    anthropic:  { name: 'Anthropic API',     color: '#D97757', icon: 'AN' },
    youtube:    { name: 'YouTube Data API',  color: '#FF0000', icon: 'YT' },
    linkedin:   { name: 'LinkedIn API',      color: '#0A66C2', icon: 'LI' },
    rss:        { name: 'RSS Feed Reader',   color: '#F99830', icon: 'RS' },
    email_smtp: { name: 'Email (SMTP)',      color: '#4CAF50', icon: 'SM' },
    email_imap: { name: 'Email (IMAP)',      color: '#2196F3', icon: 'IM' },
    resend:     { name: 'Resend Email API', color: '#000000', icon: 'RE' },
    github:     { name: 'GitHub API',        color: '#333',    icon: 'GH' },
    slack:      { name: 'Slack Webhook',     color: '#4A154B', icon: 'SL' },
    discord:    { name: 'Discord Webhook',   color: '#5865F2', icon: 'DC' },
    openai:     { name: 'OpenAI API',        color: '#10A37F', icon: 'OA' },
    google:     { name: 'Google Cloud API',  color: '#4285F4', icon: 'GC' },
    cloudflare: { name: 'Cloudflare API',    color: '#F6821F', icon: 'CF' },
    aws:        { name: 'AWS Credentials',   color: '#FF9900', icon: 'AW' },
    twitter:    { name: 'Twitter/X API',     color: '#1DA1F2', icon: 'TW' },
    reddit:     { name: 'Reddit API',        color: '#FF4500', icon: 'RD' },
    internal_web: { name: 'Internal Web Accessor', color: '#00BCD4', icon: 'IW', local: true },
    webhook:    { name: 'Custom Webhook',    color: '#9C27B0', icon: 'WH' },
    custom:     { name: 'Custom Service',    color: '#607D8B', icon: 'CS' },
};

// Per-service-type field schemas.
// Fields with key === 'key' map to the main credential; others go into extra JSON.
const SERVICE_SCHEMAS = {
    anthropic:    [{ key: 'key', label: 'API Key', type: 'password', placeholder: 'sk-ant-...' }],
    youtube:      [{ key: 'key', label: 'API Key', type: 'password', placeholder: 'AIza...' }],
    github:       [{ key: 'key', label: 'Token', type: 'password', placeholder: 'ghp_...' }],
    openai:       [{ key: 'key', label: 'API Key', type: 'password', placeholder: 'sk-...' }],
    cloudflare:   [{ key: 'key', label: 'API Token', type: 'password', placeholder: '' }],
    resend:       [{ key: 'key', label: 'API Key', type: 'password', placeholder: 're_...' }],
    slack:        [{ key: 'key', label: 'Webhook URL', type: 'password', placeholder: 'https://hooks.slack.com/...' }],
    discord:      [{ key: 'key', label: 'Webhook URL', type: 'password', placeholder: 'https://discord.com/api/webhooks/...' }],
    email_smtp:   [
        { key: 'key', label: 'Password', type: 'password', placeholder: 'App password' },
        { key: 'SMTP_HOST', label: 'SMTP Host', type: 'text', placeholder: 'smtp.gmail.com' },
        { key: 'SMTP_PORT', label: 'SMTP Port', type: 'text', placeholder: '587' },
        { key: 'SMTP_USER', label: 'Username/Email', type: 'text', placeholder: 'you@gmail.com' },
    ],
    email_imap:   [
        { key: 'key', label: 'Password', type: 'password', placeholder: 'App password' },
        { key: 'IMAP_HOST', label: 'IMAP Host', type: 'text', placeholder: 'imap.gmail.com' },
        { key: 'IMAP_PORT', label: 'IMAP Port', type: 'text', placeholder: '993' },
        { key: 'IMAP_USER', label: 'Username/Email', type: 'text', placeholder: 'you@gmail.com' },
    ],
    aws:          [
        { key: 'key', label: 'Access Key ID', type: 'password', placeholder: 'AKIA...' },
        { key: 'AWS_SECRET_ACCESS_KEY', label: 'Secret Access Key', type: 'password', placeholder: '' },
        { key: 'AWS_DEFAULT_REGION', label: 'Default Region', type: 'text', placeholder: 'us-east-1' },
    ],
    google:       [
        { key: 'key', label: 'API Key', type: 'password', placeholder: 'AIza...' },
        { key: 'GOOGLE_APPLICATION_CREDENTIALS', label: 'Service Account JSON Path', type: 'text', placeholder: '/path/to/service-account.json' },
    ],
    linkedin:     [
        { key: 'key', label: 'Client ID', type: 'password', placeholder: '' },
        { key: 'LINKEDIN_CLIENT_SECRET', label: 'Client Secret', type: 'password', placeholder: '' },
    ],
    twitter:      [
        { key: 'key', label: 'API Key', type: 'password', placeholder: '' },
        { key: 'TWITTER_API_SECRET', label: 'API Secret', type: 'password', placeholder: '' },
        { key: 'TWITTER_BEARER_TOKEN', label: 'Bearer Token', type: 'password', placeholder: '' },
    ],
    reddit:       [
        { key: 'key', label: 'Client ID', type: 'password', placeholder: '' },
        { key: 'REDDIT_CLIENT_SECRET', label: 'Client Secret', type: 'password', placeholder: '' },
    ],
    webhook:      [
        { key: 'key', label: 'API Key', type: 'password', placeholder: '' },
        { key: 'WEBHOOK_URL', label: 'Webhook URL', type: 'text', placeholder: 'https://...' },
    ],
    custom:       [
        { key: 'key', label: 'API Key / Token', type: 'password', placeholder: '' },
    ],
};

// Default services to pre-populate when no services exist yet.
// These are the common integrations most teams will need -- users just fill in keys.
const DEFAULT_SERVICE_PRESETS = [
    { type: 'anthropic',  name: 'Anthropic API' },
    { type: 'github',     name: 'GitHub API' },
    { type: 'youtube',    name: 'YouTube Data API' },
    { type: 'linkedin',   name: 'LinkedIn API' },
    { type: 'google',     name: 'Google Cloud API' },
    { type: 'openai',     name: 'OpenAI API' },
    { type: 'cloudflare', name: 'Cloudflare API' },
    { type: 'twitter',    name: 'Twitter/X API' },
    { type: 'reddit',     name: 'Reddit API' },
    { type: 'slack',      name: 'Slack Webhook' },
    { type: 'discord',    name: 'Discord Webhook' },
];

// Map common .env variable names to service types.
// Keys are lowercased prefixes; first match wins.
const ENV_KEY_MAP = [
    { pattern: 'anthropic_api_key',       type: 'anthropic',  name: 'Anthropic API' },
    { pattern: 'anthropic_key',           type: 'anthropic',  name: 'Anthropic API' },
    { pattern: 'claude_api_key',          type: 'anthropic',  name: 'Anthropic API' },
    { pattern: 'github_token',            type: 'github',     name: 'GitHub API' },
    { pattern: 'github_api',              type: 'github',     name: 'GitHub API' },
    { pattern: 'gh_token',               type: 'github',     name: 'GitHub API' },
    { pattern: 'youtube_api',             type: 'youtube',    name: 'YouTube Data API' },
    { pattern: 'youtube_key',             type: 'youtube',    name: 'YouTube Data API' },
    { pattern: 'google_api_key',          type: 'google',     name: 'Google Cloud API' },
    { pattern: 'google_cloud',            type: 'google',     name: 'Google Cloud API' },
    { pattern: 'gcp_',                    type: 'google',     name: 'Google Cloud API' },
    { pattern: 'openai_api_key',          type: 'openai',     name: 'OpenAI API' },
    { pattern: 'openai_key',              type: 'openai',     name: 'OpenAI API' },
    { pattern: 'linkedin_',              type: 'linkedin',   name: 'LinkedIn API' },
    { pattern: 'cloudflare_api',          type: 'cloudflare', name: 'Cloudflare API' },
    { pattern: 'cf_api',                  type: 'cloudflare', name: 'Cloudflare API' },
    { pattern: 'aws_access_key',          type: 'aws',        name: 'AWS Credentials' },
    { pattern: 'aws_secret',              type: 'aws',        name: 'AWS Credentials' },
    { pattern: 'slack_webhook',           type: 'slack',      name: 'Slack Webhook' },
    { pattern: 'slack_token',             type: 'slack',      name: 'Slack Webhook' },
    { pattern: 'slack_bot',               type: 'slack',      name: 'Slack Webhook' },
    { pattern: 'discord_webhook',         type: 'discord',    name: 'Discord Webhook' },
    { pattern: 'discord_token',           type: 'discord',    name: 'Discord Webhook' },
    { pattern: 'discord_bot',             type: 'discord',    name: 'Discord Webhook' },
    { pattern: 'twitter_',               type: 'twitter',    name: 'Twitter/X API' },
    { pattern: 'x_api',                   type: 'twitter',    name: 'Twitter/X API' },
    { pattern: 'reddit_',                type: 'reddit',     name: 'Reddit API' },
    { pattern: 'smtp_',                  type: 'email_smtp', name: 'Email (SMTP)' },
    { pattern: 'email_smtp',             type: 'email_smtp', name: 'Email (SMTP)' },
    { pattern: 'imap_',                  type: 'email_imap', name: 'Email (IMAP)' },
    { pattern: 'email_imap',             type: 'email_imap', name: 'Email (IMAP)' },
    { pattern: 'resend_api_key',        type: 'resend',     name: 'Resend Email API' },
    { pattern: 'rss_',                   type: 'rss',        name: 'RSS Feed Reader' },
];

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
            // Auto-populate with common service presets on first open
            if (permState.services.length === 0) {
                permState.services = DEFAULT_SERVICE_PRESETS.map(preset => ({
                    id: preset.type + '_default',
                    type: preset.type,
                    name: preset.name,
                    has_key: false,
                    key_masked: '',
                    extra: '',
                }));
            }
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

    // Save service keys + agent access
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

    // Also save tool defaults if that tab has been rendered
    if (dom.toolDefaultsGrid && dom.toolDefaultsGrid.innerHTML) {
        saveToolDefaults();
    }

    // Also save file permissions if that tab has been rendered
    if (dom.filePermsGrid && dom.filePermsGrid.innerHTML) {
        saveFilePerms();
    }
}

function switchPermTab(tabName) {
    document.querySelectorAll('.perm-tab').forEach(t => {
        t.classList.toggle('active', t.dataset.permTab === tabName);
    });
    if (dom.permPanelServices) dom.permPanelServices.style.display = tabName === 'services' ? '' : 'none';
    if (dom.permPanelAgents) dom.permPanelAgents.style.display = tabName === 'agents' ? '' : 'none';
    if (dom.permPanelToolDefaults) dom.permPanelToolDefaults.style.display = tabName === 'tool-defaults' ? '' : 'none';
    if (dom.permPanelFilePerms) dom.permPanelFilePerms.style.display = tabName === 'file-perms' ? '' : 'none';
    if (tabName === 'tool-defaults') renderToolDefaults();
    if (tabName === 'file-perms') renderFilePerms();
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
        const isLocal = meta.local || false;

        let statusClass, statusText, keyDisplay;
        if (isLocal) {
            // Local services don't need API keys -- show availability status
            const checkId = `local-status-${idx}`;
            statusClass = 'checking';
            statusText = 'Checking...';
            keyDisplay = '(local service -- no key needed)';
            // Async status check for local services
            if (svc.type === 'internal_web') {
                fetch('/api/internal-web/status')
                    .then(r => r.json())
                    .then(data => {
                        const el = document.querySelector(`[data-service-idx="${idx}"] .service-key-card__status`);
                        const keyEl = document.querySelector(`[data-service-idx="${idx}"] .service-key-card__key`);
                        if (el) {
                            if (data.available) {
                                el.className = 'service-key-card__status service-key-card__status--active';
                                el.textContent = 'Available';
                                svc.has_key = true;  // mark as "configured" for Agent Access grid
                            } else {
                                el.className = 'service-key-card__status service-key-card__status--missing';
                                el.textContent = 'Unavailable';
                                svc.has_key = false;
                            }
                        }
                        if (keyEl) {
                            const parts = [];
                            parts.push(data.web_adapter ? 'WebAdapter: OK' : 'WebAdapter: missing');
                            parts.push(data.playwright ? 'Playwright: OK' : 'Playwright: missing');
                            keyEl.textContent = parts.join(' | ');
                        }
                        // Re-render Agent Access grid now that has_key is resolved
                        renderPermGrid();
                    })
                    .catch(() => {
                        const el = document.querySelector(`[data-service-idx="${idx}"] .service-key-card__status`);
                        if (el) {
                            el.className = 'service-key-card__status service-key-card__status--missing';
                            el.textContent = 'Error';
                        }
                    });
            }
        } else {
            statusClass = svc.has_key ? 'active' : 'missing';
            statusText = svc.has_key ? 'Configured' : 'No key';
            keyDisplay = svc.key_masked || '(not set)';
        }

        const editBtn = isLocal
            ? ''
            : `<button class="btn btn--small btn--secondary" onclick="editServiceKey(${idx})" title="Edit key">Edit</button>`;

        return `
            <div class="service-key-card" data-service-idx="${idx}">
                <div class="service-key-card__icon" style="background-color: ${meta.color}">${meta.icon}</div>
                <div class="service-key-card__info">
                    <div class="service-key-card__name">${escapeHtml(displayName)}</div>
                    <div class="service-key-card__key">${escapeHtml(keyDisplay)}</div>
                </div>
                <span class="service-key-card__status service-key-card__status--${statusClass}">${statusText}</span>
                <div class="service-key-card__actions">
                    ${editBtn}
                    <button class="btn btn--small btn--danger" onclick="removeServiceKey(${idx})" title="Remove">&times;</button>
                </div>
            </div>`;
    }).join('');
}

function renderPermGrid() {
    if (!dom.permGridHead || !dom.permGridBody) return;

    // Only show services that have a key configured
    const activeServices = permState.services.filter(svc => svc.has_key);

    if (activeServices.length === 0) {
        dom.permGridHead.innerHTML = '';
        dom.permGridBody.innerHTML = '<tr><td colspan="99" class="perm-empty">No services with API keys configured. Add keys in the Service Keys tab first.</td></tr>';
        return;
    }

    // Header: Agent | Service1 | Service2 | ...
    dom.permGridHead.innerHTML = `<tr>
        <th>Agent</th>
        ${activeServices.map(svc => {
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
        const cells = activeServices.map(svc => {
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

// --- .env file drag-and-drop import ---

function parseEnvFile(text) {
    // Parse KEY=VALUE lines from a .env file. Returns [{key, value}].
    const entries = [];
    for (const line of text.split(/\r?\n/)) {
        const trimmed = line.trim();
        if (!trimmed || trimmed.startsWith('#')) continue;
        const eqIdx = trimmed.indexOf('=');
        if (eqIdx < 1) continue;
        const key = trimmed.substring(0, eqIdx).trim();
        let value = trimmed.substring(eqIdx + 1).trim();
        // Strip surrounding quotes
        if ((value.startsWith('"') && value.endsWith('"')) ||
            (value.startsWith("'") && value.endsWith("'"))) {
            value = value.slice(1, -1);
        }
        if (key && value) entries.push({ key, value });
    }
    return entries;
}

function matchEnvKeyToService(envKey) {
    // Match a .env variable name to a service type using ENV_KEY_MAP.
    const lower = envKey.toLowerCase();
    for (const mapping of ENV_KEY_MAP) {
        if (lower.startsWith(mapping.pattern) || lower === mapping.pattern) {
            return mapping;
        }
    }
    return null;
}

function importEnvEntries(entries) {
    // Merge parsed .env entries into permState.services.
    // Updates existing services or creates new ones.
    let matched = 0;
    let skipped = 0;
    const unmatched = [];

    for (const { key, value } of entries) {
        const mapping = matchEnvKeyToService(key);
        if (!mapping) {
            unmatched.push(key);
            continue;
        }

        // Find existing service of this type
        const existing = permState.services.find(s => s.type === mapping.type);
        if (existing) {
            // If this type already has a key and this is a secondary env var
            // (e.g. AWS_SECRET_ACCESS_KEY when AWS_ACCESS_KEY_ID already set),
            // append to extra rather than overwriting the key
            if (existing.has_key && existing.new_key) {
                const prev = existing.extra ? JSON.parse(existing.extra || '{}') : {};
                prev[key] = value;
                existing.extra = JSON.stringify(prev);
            } else {
                existing.new_key = value;
                existing.has_key = true;
                existing.key_masked = value.length > 8 ? '...' + value.slice(-4) : '...(set)';
            }
            matched++;
        } else {
            // Create a new service entry
            permState.services.push({
                id: mapping.type + '_' + Date.now() + '_' + matched,
                type: mapping.type,
                name: mapping.name,
                new_key: value,
                has_key: true,
                key_masked: value.length > 8 ? '...' + value.slice(-4) : '...(set)',
                extra: '',
            });
            matched++;
        }
    }

    return { matched, skipped, unmatched };
}

function handleEnvDrop(e) {
    e.preventDefault();
    e.stopPropagation();
    const panel = dom.permPanelServices;
    if (panel) panel.classList.remove('perm-panel--drag-over');

    const file = e.dataTransfer && e.dataTransfer.files && e.dataTransfer.files[0];
    if (!file) return;

    // Accept .env files or any small text file
    if (file.size > 100000) {
        showToast('File too large -- .env files should be small', 'error');
        return;
    }

    const reader = new FileReader();
    reader.onload = () => {
        const entries = parseEnvFile(reader.result);
        if (entries.length === 0) {
            showToast('No KEY=VALUE entries found in file', 'error');
            return;
        }

        const { matched, unmatched } = importEnvEntries(entries);
        renderServiceKeys();
        renderPermGrid();

        let msg = matched + ' key' + (matched !== 1 ? 's' : '') + ' imported';
        if (unmatched.length > 0) {
            msg += ' (' + unmatched.length + ' unrecognized: ' + unmatched.slice(0, 3).join(', ');
            if (unmatched.length > 3) msg += '...';
            msg += ')';
        }
        showToast(msg + ' -- review and save', 'info');
    };
    reader.onerror = () => showToast('Failed to read file', 'error');
    reader.readAsText(file);
}

function initEnvDropZone() {
    const panel = dom.permPanelServices;
    if (!panel) return;

    panel.addEventListener('dragover', (e) => {
        e.preventDefault();
        e.stopPropagation();
        panel.classList.add('perm-panel--drag-over');
    });
    panel.addEventListener('dragleave', (e) => {
        e.preventDefault();
        e.stopPropagation();
        // Only remove if leaving the panel itself (not entering a child)
        if (!panel.contains(e.relatedTarget)) {
            panel.classList.remove('perm-panel--drag-over');
        }
    });
    panel.addEventListener('drop', handleEnvDrop);
}

function renderServiceFields(serviceType, existingValues) {
    // Render dynamic form fields based on SERVICE_SCHEMAS for the given type.
    // existingValues: { key: '...', extra_field: '...' } for pre-populating on edit.
    const container = dom.serviceDynamicFields;
    if (\!container) return;
    container.innerHTML = '';

    const meta = SERVICE_TYPES[serviceType] || SERVICE_TYPES.custom;
    const isLocal = meta.local || false;
    if (isLocal) return; // No fields for local services

    const schema = SERVICE_SCHEMAS[serviceType] || SERVICE_SCHEMAS.custom;
    const values = existingValues || {};

    schema.forEach((fieldDef) => {
        const group = document.createElement('div');
        group.className = 'form-group';

        const label = document.createElement('label');
        label.textContent = fieldDef.label;
        label.setAttribute('for', 'svc-field-' + fieldDef.key);
        group.appendChild(label);

        if (fieldDef.type === 'password') {
            const wrapper = document.createElement('div');
            wrapper.className = 'settings-secret-field';

            const input = document.createElement('input');
            input.type = 'password';
            input.className = 'form-input';
            input.id = 'svc-field-' + fieldDef.key;
            input.dataset.fieldKey = fieldDef.key;
            input.placeholder = fieldDef.placeholder || '';
            input.autocomplete = 'off';
            if (values[fieldDef.key] \!== undefined) input.value = values[fieldDef.key];

            const toggleBtn = document.createElement('button');
            toggleBtn.type = 'button';
            toggleBtn.className = 'btn btn--icon settings-toggle-vis';
            toggleBtn.textContent = '[*]';
            toggleBtn.addEventListener('click', () => {
                input.type = input.type === 'password' ? 'text' : 'password';
            });

            wrapper.appendChild(input);
            wrapper.appendChild(toggleBtn);
            group.appendChild(wrapper);
        } else {
            const input = document.createElement('input');
            input.type = 'text';
            input.className = 'form-input';
            input.id = 'svc-field-' + fieldDef.key;
            input.dataset.fieldKey = fieldDef.key;
            input.placeholder = fieldDef.placeholder || '';
            if (values[fieldDef.key] \!== undefined) input.value = values[fieldDef.key];
            group.appendChild(input);
        }

        container.appendChild(group);
    });
}

function collectServiceFields() {
    // Collect values from dynamically rendered fields.
    // Returns { key: mainKeyValue, extra: JSON string of extra fields }.
    const container = dom.serviceDynamicFields;
    if (\!container) return { key: '', extra: '' };

    let mainKey = '';
    const extraObj = {};

    container.querySelectorAll('[data-field-key]').forEach((input) => {
        const fieldKey = input.dataset.fieldKey;
        const val = input.value.trim();
        if (fieldKey === 'key') {
            mainKey = val;
        } else if (val) {
            extraObj[fieldKey] = val;
        }
    });

    const extra = Object.keys(extraObj).length > 0 ? JSON.stringify(extraObj) : '';
    return { key: mainKey, extra };
}

function openAddService() {
    if (dom.serviceTypeSelect) dom.serviceTypeSelect.value = 'anthropic';
    if (dom.serviceCustomNameGroup) dom.serviceCustomNameGroup.style.display = 'none';
    if (dom.serviceCustomName) dom.serviceCustomName.value = '';
    renderServiceFields('anthropic', {});
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
    const collected = collectServiceFields();
    const key = collected.key;
    const extra = collected.extra;

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
        const meta = SERVICE_TYPES[type] || SERVICE_TYPES.custom;
        const isLocal = meta.local || false;
        permState.services.push({
            id,
            type,
            name,
            new_key: isLocal ? '' : key,
            has_key: isLocal ? true : !!key,
            key_masked: isLocal ? '(local)' : (key ? (key.length > 8 ? '...' + key.slice(-4) : '...(set)') : ''),
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

    // Parse extra JSON to pre-populate multi-field values
    const existingValues = {};
    if (svc.extra) {
        try {
            const parsed = JSON.parse(svc.extra);
            Object.assign(existingValues, parsed);
        } catch (_) { /* non-JSON extra, ignore */ }
    }
    // Leave main key blank on edit (user must re-enter or leave blank to keep)
    renderServiceFields(svc.type, existingValues);
    // Update main key placeholder for edit mode
    const mainKeyInput = dom.serviceDynamicFields ? dom.serviceDynamicFields.querySelector('[data-field-key="key"]') : null;
    if (mainKeyInput) {
        mainKeyInput.placeholder = svc.has_key ? '(leave blank to keep current)' : 'Paste API key...';
    }

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
    // Toggle is now handled per-field in renderServiceFields
}

// =====================================================================
// Tool Permissions (per-agent, opened from agent card gear icon)
// =====================================================================

const TOOL_DESCRIPTIONS = {
    Read: 'Read file contents',
    Write: 'Create or overwrite files',
    Edit: 'Edit specific lines in files',
    Bash: 'Execute shell commands',
    Glob: 'Find files by pattern',
    Grep: 'Search file contents',
    WebSearch: 'Search the web (Claude CLI only)',
    WebFetch: 'Fetch web page content (Claude CLI only)',
};

let toolPermsState = {
    currentAgentId: null,
    data: null,
};

function openToolPerms(agentId) {
    fetch('/api/tool-permissions')
        .then(r => r.json())
        .then(data => {
            toolPermsState.data = data;
            toolPermsState.currentAgentId = agentId;
            renderToolPermsModal(agentId, data);
            if (dom.toolPermsModal) dom.toolPermsModal.hidden = false;
        })
        .catch(err => showToast('Failed to load tool permissions: ' + err.message, 'error'));
}

function closeToolPerms() {
    if (dom.toolPermsModal) dom.toolPermsModal.hidden = true;
    toolPermsState.currentAgentId = null;
}

function renderToolPermsModal(agentId, data) {
    const agent = (data.agents || []).find(a => a.agent_id === agentId);
    if (!agent) {
        showToast('Agent not found: ' + agentId, 'error');
        return;
    }

    if (dom.toolPermsTitle) {
        dom.toolPermsTitle.textContent = 'Tool Permissions: ' + (agent.name || agentId);
    }

    if (dom.toolPermsProfile) {
        const sourceLabel = {
            'agent_config': 'agent config',
            'agent_type_default': 'agent type: ' + agent.agent_type,
            'fallback': 'default fallback',
        }[agent.profile_source] || agent.profile_source;

        dom.toolPermsProfile.innerHTML = agent.has_override
            ? '<strong>Custom override</strong> (set from dashboard)'
            : 'Profile: <strong>' + escapeHtml(agent.profile_name) + '</strong> (from ' + escapeHtml(sourceLabel) + ')';
    }

    const allTools = data.all_tools || Object.keys(TOOL_DESCRIPTIONS);
    const agentTools = new Set(agent.allowed_tools || []);
    const deniedGlobally = new Set(data.denied_tools || []);

    if (dom.toolPermsGrid) {
        dom.toolPermsGrid.innerHTML = allTools.map(tool => {
            const checked = agentTools.has(tool) ? 'checked' : '';
            const denied = deniedGlobally.has(tool);
            const desc = TOOL_DESCRIPTIONS[tool] || '';
            const disabledAttr = denied ? 'disabled' : '';
            const deniedNote = denied ? ' <span class="tool-perms__denied-tag">(globally denied)</span>' : '';

            return `<label class="tool-perms__tool${denied ? ' tool-perms__tool--denied' : ''}">
                <input type="checkbox" class="tool-perms__check" data-tool="${escapeHtml(tool)}" ${checked} ${disabledAttr}>
                <span class="tool-perms__tool-name">${escapeHtml(tool)}</span>
                <span class="tool-perms__tool-desc">${escapeHtml(desc)}${deniedNote}</span>
            </label>`;
        }).join('');
    }

    if (dom.toolPermsOverrideNote) {
        dom.toolPermsOverrideNote.textContent = agent.has_override
            ? 'This agent has a custom tool override. Click "Reset to Profile" to revert to profile defaults.'
            : '';
    }

    // File permissions section in per-agent modal
    const filePermsSection = document.getElementById('tool-perms-file-section');
    if (filePermsSection) filePermsSection.remove();

    if (dom.toolPermsGrid) {
        const agentFilePerms = agent.file_permissions || [];
        const hasFileOverride = (data.agent_overrides || {})[agentId] && (data.agent_overrides[agentId]).file_permissions_override;

        const rulesHtml = agentFilePerms.map((rule, i) =>
            `<div class="file-perms__rule" data-idx="${i}">
                <input type="text" class="file-perms__path-input tool-perms-file-path" value="${escapeHtml(rule.path || '')}" placeholder="G:/**" spellcheck="false">
                <select class="file-perms__access-select tool-perms-file-access">
                    <option value="write"${rule.access === 'write' ? ' selected' : ''}>Write</option>
                    <option value="read"${rule.access === 'read' ? ' selected' : ''}>Read</option>
                    <option value="none"${rule.access === 'none' ? ' selected' : ''}>None</option>
                </select>
                <button class="btn btn--small btn--danger file-perms__remove" onclick="removeFilePermRule(this)">&times;</button>
            </div>`
        ).join('');

        const sectionHtml = `<div id="tool-perms-file-section" class="tool-perms__file-section">
            <h4 style="margin: var(--space-3) 0 var(--space-1) 0; font-size: var(--font-size-sm);">File Permissions</h4>
            <p class="file-perms__empty" style="margin-bottom: var(--space-1);">${hasFileOverride ? 'Custom override' : 'Inherited from group'}</p>
            <div class="file-perms__rules" id="tool-perms-file-rules">
                ${rulesHtml || '<div class="file-perms__empty">No path rules (unrestricted)</div>'}
            </div>
            <button class="btn btn--small btn--secondary file-perms__add-btn" onclick="addAgentFilePermRule()">+ Add Rule</button>
        </div>`;

        dom.toolPermsGrid.insertAdjacentHTML('afterend', sectionHtml);
    }
}

function addAgentFilePermRule() {
    const rulesContainer = document.getElementById('tool-perms-file-rules');
    if (!rulesContainer) return;

    const empty = rulesContainer.querySelector('.file-perms__empty');
    if (empty) empty.remove();

    const idx = rulesContainer.querySelectorAll('.file-perms__rule').length;
    const div = document.createElement('div');
    div.innerHTML = `<div class="file-perms__rule" data-idx="${idx}">
        <input type="text" class="file-perms__path-input tool-perms-file-path" value="" placeholder="G:/**" spellcheck="false">
        <select class="file-perms__access-select tool-perms-file-access">
            <option value="write">Write</option>
            <option value="read" selected>Read</option>
            <option value="none">None</option>
        </select>
        <button class="btn btn--small btn--danger file-perms__remove" onclick="removeFilePermRule(this)">&times;</button>
    </div>`;
    rulesContainer.appendChild(div.firstElementChild);
}

function collectAgentFilePerms() {
    const rulesContainer = document.getElementById('tool-perms-file-rules');
    if (!rulesContainer) return null;
    const rules = [];
    rulesContainer.querySelectorAll('.file-perms__rule').forEach(ruleEl => {
        const path = (ruleEl.querySelector('.tool-perms-file-path') || {}).value || '';
        const access = (ruleEl.querySelector('.tool-perms-file-access') || {}).value || 'read';
        if (path.trim()) rules.push({ path: path.trim(), access });
    });
    return rules.length > 0 ? rules : null;
}

function saveToolPerms() {
    if (!toolPermsState.data || !toolPermsState.currentAgentId) return;

    const agentId = toolPermsState.currentAgentId;
    const data = toolPermsState.data;

    const checkedTools = [];
    document.querySelectorAll('.tool-perms__check').forEach(cb => {
        if (cb.checked) checkedTools.push(cb.dataset.tool);
    });

    // Collect file permission overrides
    const filePermsOverride = collectAgentFilePerms();

    // Check if this matches the profile default (no override needed)
    const agent = (data.agents || []).find(a => a.agent_id === agentId);
    const profileName = agent ? agent.profile_name : '';
    const profileTools = (data.profiles[profileName] || {}).allowed_tools || [];
    const profileSet = new Set(profileTools);
    const checkedSet = new Set(checkedTools);
    const isDefault = profileSet.size === checkedSet.size && [...profileSet].every(t => checkedSet.has(t));

    const overrides = Object.assign({}, data.agent_overrides || {});
    if (isDefault && !filePermsOverride) {
        delete overrides[agentId];
    } else {
        overrides[agentId] = overrides[agentId] || {};
        if (!isDefault) {
            overrides[agentId].allowed_tools_override = checkedTools;
        } else {
            delete overrides[agentId].allowed_tools_override;
        }
        if (filePermsOverride) {
            overrides[agentId].file_permissions_override = filePermsOverride;
        } else {
            delete overrides[agentId].file_permissions_override;
        }
        // Clean up empty override objects
        if (!overrides[agentId].allowed_tools_override && !overrides[agentId].file_permissions_override) {
            delete overrides[agentId];
        }
    }

    fetch('/api/tool-permissions', {
        method: 'PUT',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ agent_overrides: overrides }),
    })
        .then(r => r.json())
        .then(result => {
            if (result.success) {
                showToast('Tool permissions saved for ' + (agent ? agent.name : agentId), 'success');
                closeToolPerms();
            } else {
                showToast(result.error || 'Failed to save', 'error');
            }
        })
        .catch(err => showToast('Save failed: ' + err.message, 'error'));
}

function resetToolPerms() {
    if (!toolPermsState.data || !toolPermsState.currentAgentId) return;

    const agentId = toolPermsState.currentAgentId;
    const data = toolPermsState.data;

    const overrides = Object.assign({}, data.agent_overrides || {});
    delete overrides[agentId];

    fetch('/api/tool-permissions', {
        method: 'PUT',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ agent_overrides: overrides }),
    })
        .then(r => r.json())
        .then(result => {
            if (result.success) {
                showToast('Reset to profile defaults', 'success');
                openToolPerms(agentId);
            } else {
                showToast(result.error || 'Failed to reset', 'error');
            }
        })
        .catch(err => showToast('Reset failed: ' + err.message, 'error'));
}

// --- Tool Defaults tab (in Permissions modal) ---

function renderToolDefaults() {
    fetch('/api/tool-permissions')
        .then(r => r.json())
        .then(data => {
            renderToolDefaultsGrid(data);
            renderToolDefaultsDenied(data);
        })
        .catch(() => {});
}

function renderToolDefaultsGrid(data) {
    if (!dom.toolDefaultsGrid) return;

    const profiles = data.profiles || {};
    const defaults = data.agent_defaults || {};
    const agents = data.agents || [];
    const profileNames = Object.keys(profiles);

    // Build groups from agent store data
    const groupMap = {};  // group_key -> { label, agents: [...] }
    for (const agent of agents) {
        const groupLabel = agent.group || 'Agents';
        const groupKey = groupLabel.toLowerCase().replace(/[^a-z0-9]+/g, '_');
        if (!groupMap[groupKey]) {
            groupMap[groupKey] = { label: groupLabel, agents: [] };
        }
        groupMap[groupKey].agents.push(agent);
    }

    // Sort groups by label
    const groups = Object.entries(groupMap).sort((a, b) => a[1].label.localeCompare(b[1].label));

    dom.toolDefaultsGrid.innerHTML = groups.map(([groupKey, group]) => {
        const current = defaults[groupKey] || 'minimal';
        const options = profileNames.map(p =>
            `<option value="${escapeHtml(p)}" ${p === current ? 'selected' : ''}>${escapeHtml(p)}</option>`
        ).join('');

        const profile = profiles[current] || {};
        const tools = (profile.allowed_tools || []).join(', ') || '(none)';
        const agentNames = group.agents.map(a => a.nickname || a.name).sort().join(', ');

        return `<div class="tool-defaults__row">
            <div>
                <span class="tool-defaults__type-label">${escapeHtml(group.label)}</span>
                <span class="tool-defaults__tools-preview">${escapeHtml(tools)}</span>
                <span class="tool-defaults__agents-list">${escapeHtml(agentNames)}</span>
            </div>
            <select class="tool-defaults__profile-select" data-agent-type="${escapeHtml(groupKey)}" onchange="onToolDefaultChange()">
                ${options}
            </select>
        </div>`;
    }).join('');
}

function renderToolDefaultsDenied(data) {
    if (!dom.toolDefaultsDenied) return;

    const allTools = data.all_tools || Object.keys(TOOL_DESCRIPTIONS);
    const denied = new Set(data.denied_tools || []);

    dom.toolDefaultsDenied.innerHTML = allTools.map(tool => {
        const checked = denied.has(tool) ? 'checked' : '';
        return `<label class="tool-defaults__denied-item">
            <input type="checkbox" class="tool-defaults__denied-check" data-tool="${escapeHtml(tool)}" ${checked}>
            <span>${escapeHtml(tool)}</span>
        </label>`;
    }).join('');
}

function onToolDefaultChange() {
    if (!dom.toolDefaultsGrid) return;
    fetch('/api/tool-permissions')
        .then(r => r.json())
        .then(data => {
            const profiles = data.profiles || {};
            dom.toolDefaultsGrid.querySelectorAll('.tool-defaults__row').forEach(row => {
                const select = row.querySelector('.tool-defaults__profile-select');
                const preview = row.querySelector('.tool-defaults__tools-preview');
                if (select && preview) {
                    const profile = profiles[select.value] || {};
                    preview.textContent = (profile.allowed_tools || []).join(', ') || '(none)';
                }
            });
        })
        .catch(() => {});
}

function saveToolDefaults() {
    const agentDefaults = {};
    if (dom.toolDefaultsGrid) {
        dom.toolDefaultsGrid.querySelectorAll('.tool-defaults__profile-select').forEach(sel => {
            agentDefaults[sel.dataset.agentType] = sel.value;
        });
    }

    const deniedTools = [];
    if (dom.toolDefaultsDenied) {
        dom.toolDefaultsDenied.querySelectorAll('.tool-defaults__denied-check').forEach(cb => {
            if (cb.checked) deniedTools.push(cb.dataset.tool);
        });
    }

    fetch('/api/tool-permissions', {
        method: 'PUT',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ agent_defaults: agentDefaults, denied_tools: deniedTools }),
    })
        .then(r => r.json())
        .then(result => {
            if (result.success) {
                showToast('Tool defaults saved', 'success');
            } else {
                showToast(result.error || 'Failed to save defaults', 'error');
            }
        })
        .catch(err => showToast('Save failed: ' + err.message, 'error'));
}


// =====================================================================
// File Permissions (tab in Permissions modal)
// =====================================================================

let _filePermsData = null;  // cached API data for file perms tab

function renderFilePerms() {
    if (!dom.filePermsGrid) return;
    fetch('/api/tool-permissions')
        .then(r => r.json())
        .then(data => {
            _filePermsData = data;
            renderFilePermsGrid(data);
        })
        .catch(err => {
            dom.filePermsGrid.innerHTML = '<div class="file-perms__empty">Failed to load file permissions.</div>';
        });
}

function renderFilePermsGrid(data) {
    if (!dom.filePermsGrid) return;

    const agents = data.agents || [];
    const filePerms = data.file_permissions || {};
    const defaults = filePerms.defaults || {};

    // Build groups from agent store data (same pattern as renderToolDefaultsGrid)
    const groupMap = {};
    for (const agent of agents) {
        const groupLabel = agent.group || 'Agents';
        const groupKey = groupLabel.toLowerCase().replace(/[^a-z0-9]+/g, '_');
        if (!groupMap[groupKey]) {
            groupMap[groupKey] = { label: groupLabel, agents: [] };
        }
        groupMap[groupKey].agents.push(agent);
    }

    const groups = Object.entries(groupMap).sort((a, b) => a[1].label.localeCompare(b[1].label));

    dom.filePermsGrid.innerHTML = groups.map(([groupKey, group]) => {
        const rules = defaults[groupKey] || [];
        const agentNames = group.agents.map(a => a.nickname || a.name).sort().join(', ');

        const rulesHtml = rules.map((rule, idx) => filePermRuleHtml(groupKey, idx, rule)).join('');

        return `<div class="file-perms__card" data-group="${escapeHtml(groupKey)}">
            <div class="file-perms__card-header">
                <span class="tool-defaults__type-label">${escapeHtml(group.label)}</span>
                <span class="tool-defaults__agents-list">${escapeHtml(agentNames)}</span>
            </div>
            <div class="file-perms__rules">${rulesHtml || '<div class="file-perms__empty">No path rules (unrestricted)</div>'}</div>
            <button class="btn btn--small btn--secondary file-perms__add-btn" onclick="addFilePermRule('${escapeHtml(groupKey)}')">+ Add Rule</button>
        </div>`;
    }).join('');
}

function filePermRuleHtml(groupKey, idx, rule) {
    const path = rule ? rule.path || '' : '';
    const access = rule ? rule.access || 'read' : 'read';
    return `<div class="file-perms__rule" data-group="${escapeHtml(groupKey)}" data-idx="${idx}">
        <input type="text" class="file-perms__path-input" value="${escapeHtml(path)}" placeholder="G:/**" spellcheck="false">
        <select class="file-perms__access-select">
            <option value="write"${access === 'write' ? ' selected' : ''}>Write</option>
            <option value="read"${access === 'read' ? ' selected' : ''}>Read</option>
            <option value="none"${access === 'none' ? ' selected' : ''}>None</option>
        </select>
        <button class="btn btn--small btn--danger file-perms__remove" onclick="removeFilePermRule(this)">&times;</button>
    </div>`;
}

function addFilePermRule(groupKey) {
    const card = dom.filePermsGrid.querySelector(`.file-perms__card[data-group="${groupKey}"]`);
    if (!card) return;
    const rulesContainer = card.querySelector('.file-perms__rules');
    if (!rulesContainer) return;

    // Remove "No path rules" placeholder if present
    const empty = rulesContainer.querySelector('.file-perms__empty');
    if (empty) empty.remove();

    const idx = rulesContainer.querySelectorAll('.file-perms__rule').length;
    const div = document.createElement('div');
    div.innerHTML = filePermRuleHtml(groupKey, idx, null);
    rulesContainer.appendChild(div.firstElementChild);
}

function removeFilePermRule(btn) {
    const ruleEl = btn.closest('.file-perms__rule');
    if (!ruleEl) return;
    const rulesContainer = ruleEl.parentElement;
    ruleEl.remove();

    // Restore placeholder if no rules remain
    if (rulesContainer && rulesContainer.querySelectorAll('.file-perms__rule').length === 0) {
        rulesContainer.innerHTML = '<div class="file-perms__empty">No path rules (unrestricted)</div>';
    }
}

function collectFilePerms() {
    const defaults = {};
    if (!dom.filePermsGrid) return defaults;

    dom.filePermsGrid.querySelectorAll('.file-perms__card').forEach(card => {
        const groupKey = card.dataset.group;
        const rules = [];
        card.querySelectorAll('.file-perms__rule').forEach(ruleEl => {
            const pathInput = ruleEl.querySelector('.file-perms__path-input');
            const accessSelect = ruleEl.querySelector('.file-perms__access-select');
            const path = pathInput ? pathInput.value.trim() : '';
            const access = accessSelect ? accessSelect.value : 'read';
            if (path) {
                rules.push({ path, access });
            }
        });
        defaults[groupKey] = rules;
    });
    return defaults;
}

function saveFilePerms() {
    const defaults = collectFilePerms();
    // Preserve existing agent_overrides
    const existingOverrides = (_filePermsData && _filePermsData.file_permissions)
        ? _filePermsData.file_permissions.agent_overrides || {}
        : {};

    fetch('/api/tool-permissions', {
        method: 'PUT',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
            file_permissions: {
                defaults: defaults,
                agent_overrides: existingOverrides,
            },
        }),
    })
        .then(r => r.json())
        .then(result => {
            if (result.success) {
                showToast('File permissions saved', 'success');
            } else {
                showToast(result.error || 'Failed to save file permissions', 'error');
            }
        })
        .catch(err => showToast('Save failed: ' + err.message, 'error'));
}


// =====================================================================
// Event listeners
// =====================================================================

function init() {
    initDom();
    restoreSidebarSections();

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
                fetchSessions();
                fetchPendingSocialPosts();
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

            // Pass response mode: smart (default) or quick (opt-in)
            outgoing.response_mode = _getResponseMode();

            // Attach thread_id if replying to a message
            if (state.replyingTo) {
                outgoing.thread_id = state.replyingTo;
            }

            // Auto-unarchive if sending in an archived channel
            if (_isCurrentChannelArchived()) {
                state.socket.emit('unarchive_channel', { channel_id: state.currentChannel });
                // Remove archived banner
                const banner = document.querySelector('.archived-banner');
                if (banner) banner.remove();
            }

            state.socket.emit('send_message', outgoing);

            // Immediately add sender + mentioned agents as members
            autoAddMembersFromMessage(state.currentChannel, outgoing);

            // Intercept messages in the roundtable setup channel
            if (state.currentChannel === SESSION_SETUP_CHANNEL) {
                handleRoundtableSetupMessage(content);
            }

            // Clear reply state
            cancelReply();
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


    // [+] Session button -- opens guided session setup chat
    if (dom.addSessionBtn) {
        dom.addSessionBtn.addEventListener('click', () => {
            startRoundtableSetup();
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

    // Claude Code enabled toggle -- collapse/expand the connection fields
    const claudeEnabledToggle = document.getElementById('settings-claude-enabled');
    const claudeConnectionBody = document.getElementById('claude-connection-body');
    if (claudeEnabledToggle && claudeConnectionBody) {
        claudeEnabledToggle.addEventListener('change', () => {
            claudeConnectionBody.classList.toggle('settings-section__body--collapsed', !claudeEnabledToggle.checked);
        });
    }
    if (dom.toggleApiKeyVis) dom.toggleApiKeyVis.addEventListener('click', toggleApiKeyVisibility);

    // Permissions modal
    if (dom.permissionsBtn) dom.permissionsBtn.addEventListener('click', openPermissions);
    if (dom.permissionsClose) dom.permissionsClose.addEventListener('click', closePermissions);
    if (dom.permissionsCancel) dom.permissionsCancel.addEventListener('click', closePermissions);
    if (dom.permissionsSave) dom.permissionsSave.addEventListener('click', savePermissions);
    initEnvDropZone();
    if (dom.addServiceKeyBtn) dom.addServiceKeyBtn.addEventListener('click', openAddService);
    if (dom.addServiceClose) dom.addServiceClose.addEventListener('click', closeAddService);

    // Tool permissions modal (per-agent)
    if (dom.toolPermsClose) dom.toolPermsClose.addEventListener('click', closeToolPerms);
    if (dom.toolPermsCancel) dom.toolPermsCancel.addEventListener('click', closeToolPerms);
    if (dom.toolPermsSave) dom.toolPermsSave.addEventListener('click', saveToolPerms);
    if (dom.toolPermsReset) dom.toolPermsReset.addEventListener('click', resetToolPerms);
    if (dom.addServiceCancel) dom.addServiceCancel.addEventListener('click', closeAddService);
    if (dom.addServiceForm) dom.addServiceForm.addEventListener('submit', submitAddService);
    // Toggle visibility now handled per-field in renderServiceFields

    // Permissions tab switching
    if (dom.permTabs) {
        dom.permTabs.addEventListener('click', (e) => {
            const tab = e.target.closest('.perm-tab');
            if (tab && tab.dataset.permTab) switchPermTab(tab.dataset.permTab);
        });
    }

    // Show/hide custom name field and render dynamic fields based on service type
    if (dom.serviceTypeSelect) {
        dom.serviceTypeSelect.addEventListener('change', () => {
            const type = dom.serviceTypeSelect.value;
            const isCustom = type === 'custom' || type === 'webhook';
            if (dom.serviceCustomNameGroup) dom.serviceCustomNameGroup.style.display = isCustom ? '' : 'none';
            renderServiceFields(type, {});
        });
    }

    // Close modals on backdrop click
    [dom.assignTaskModal, dom.reviewModal, dom.settingsModal, dom.permissionsModal, dom.addServiceModal, dom.createChannelModal, dom.createFolderModal, dom.setupWizard, dom.toolPermsModal].forEach((modal) => {
        if (modal) {
            modal.addEventListener('click', (e) => {
                if (e.target === modal) {
                    modal.hidden = true;
                }
            });
        }
    });

    // Load agent registry, restore user identity from localStorage, then connect
    fetchAgentRegistry().then(() => {
        restoreUserIdentity();
        connectSocket();
    });

    // Initial render
    switchPanel('team');
}

// =====================================================================
// Setup Wizard
// =====================================================================

const setupWizard = {
    currentStep: 1,
    totalSteps: 7,
    stepsDone: new Set(),
    hwData: null,
    ollamaData: null,
    topicsData: null,
    selectedTopic: null,
    selectedFeeds: [],
    mcpData: null,
    claudeData: null,

    show() {
        dom.setupWizard.hidden = false;
        this.goToStep(1);
    },

    hide() {
        dom.setupWizard.hidden = true;
    },

    async init(statusData) {
        // Wire up navigation
        dom.setupWizardClose.onclick = () => this.hide();
        dom.setupBackBtn.onclick = () => this.goToStep(this.currentStep - 1);
        dom.setupNextBtn.onclick = () => this.advance();
        dom.setupSkipBtn.onclick = () => this.advance();
        dom.setupFinishBtn.onclick = () => this.finish();

        // Step indicator clicks
        document.querySelectorAll('[data-setup-step]').forEach(btn => {
            btn.onclick = () => {
                const s = parseInt(btn.dataset.setupStep);
                if (s <= this.currentStep || this.stepsDone.has(s)) this.goToStep(s);
            };
        });

        // Step-specific buttons
        const recheckBtn = $('#setup-ollama-recheck');
        if (recheckBtn) recheckBtn.onclick = () => this.runStep2();

        const downloadBtn = $('#setup-download-btn');
        if (downloadBtn) downloadBtn.onclick = () => this.startModelPull();

        const verifyBtn = $('#setup-verify-btn');
        if (verifyBtn) verifyBtn.onclick = () => this.runStep4();

        const topicBackBtn = $('#setup-topic-back');
        if (topicBackBtn) topicBackBtn.onclick = () => {
            $('#setup-topic-picker').style.display = '';
            $('#setup-feed-picker').style.display = 'none';
        };

        const saveFeedsBtn = $('#setup-save-feeds');
        if (saveFeedsBtn) saveFeedsBtn.onclick = () => this.saveFeeds();

        // Step 6: MCP Server buttons
        const mcpWriteBtn = $('#setup-mcp-write-btn');
        if (mcpWriteBtn) mcpWriteBtn.onclick = () => this.writeMcpConfig();

        // Step 7: Claude Code buttons
        const claudeTestBtn = $('#setup-claude-test-btn');
        if (claudeTestBtn) claudeTestBtn.onclick = () => this.testClaudeSetup();

        const claudeSaveBtn = $('#setup-claude-save-btn');
        if (claudeSaveBtn) claudeSaveBtn.onclick = () => this.saveClaudeSettings();

        // Re-run button in settings
        if (dom.setupRerunBtn) {
            dom.setupRerunBtn.onclick = () => {
                dom.settingsModal.hidden = true;
                this.stepsDone.clear();
                this.show();
                this.runStep1();
            };
        }

        // Socket.IO events for model pull
        if (state.socket) {
            state.socket.on('setup:progress', (data) => this.onPullProgress(data));
            state.socket.on('setup:complete', (data) => this.onPullComplete(data));
        }

        // Pre-populate from status if available
        if (statusData && statusData.hardware_info) {
            this.hwData = statusData.hardware_info;
        }

        // Start step 1 automatically (only if wizard is visible)
        if (!dom.setupWizard.hidden) {
            this.runStep1();
        }
    },

    goToStep(n) {
        if (n < 1 || n > this.totalSteps) return;
        this.currentStep = n;

        // Show/hide step bodies
        document.querySelectorAll('.setup-wizard__step').forEach(el => {
            el.style.display = (parseInt(el.dataset.step) === n) ? '' : 'none';
        });

        // Update step indicators
        document.querySelectorAll('.setup-step').forEach(btn => {
            const s = parseInt(btn.dataset.setupStep);
            btn.classList.toggle('setup-step--active', s === n);
            btn.classList.toggle('setup-step--done', this.stepsDone.has(s));
        });

        // Update footer buttons
        dom.setupBackBtn.style.display = (n > 1) ? '' : 'none';
        dom.setupNextBtn.style.display = (n < this.totalSteps) ? '' : 'none';
        dom.setupSkipBtn.style.display = (n < this.totalSteps) ? '' : 'none';
        dom.setupFinishBtn.style.display = (n === this.totalSteps) ? '' : 'none';
    },

    advance() {
        if (this.currentStep < this.totalSteps) {
            const next = this.currentStep + 1;
            this.goToStep(next);
            // Auto-run step logic
            if (next === 1) this.runStep1();
            else if (next === 2) this.runStep2();
            else if (next === 3) this.runStep3();
            else if (next === 4) this.runStep4Auto();
            else if (next === 5) this.runStep5();
            else if (next === 6) this.runStep6();
            else if (next === 7) this.runStep7();
        }
    },

    markDone(step) {
        this.stepsDone.add(step);
        const ind = $(`#step-ind-${step}`);
        if (ind) { ind.textContent = '[OK]'; ind.classList.add('done'); }
        document.querySelectorAll('.setup-step').forEach(btn => {
            const s = parseInt(btn.dataset.setupStep);
            if (s === step) btn.classList.add('setup-step--done');
        });
    },

    // -- Step 1: Hardware Detection --
    async runStep1() {
        const container = $('#setup-hw-result');
        container.innerHTML = '<div class="setup-wizard__loading">[*] Detecting hardware...</div>';
        try {
            const resp = await fetch('/api/setup/detect', { method: 'POST' });
            const data = await resp.json();
            this.hwData = data;

            const vramGB = (data.vram_mb / 1024).toFixed(1);
            const quality = data.vram_mb >= 8192 ? "that's excellent!"
                          : data.vram_mb >= 6144 ? "that's solid!"
                          : data.vram_mb >= 4096 ? "that'll work well!"
                          : "we'll make it work!";

            let html = '<div class="setup-wizard__status setup-wizard__status--ok">[OK] Detected your system:</div>';
            html += '<div class="setup-wizard__info-grid">';
            html += `<span class="text-muted">Computer:</span><span>${data.platform === 'windows' ? 'Windows PC' : data.platform === 'darwin' ? 'Mac' : 'Linux'}</span>`;
            if (!data.cpu_only) {
                const gpus = data.gpus || [];
                if (gpus.length > 1) {
                    // Multi-GPU display
                    html += `<span class="text-muted">Graphics cards:</span><span>${gpus.length} GPUs detected</span>`;
                    for (const gpu of gpus) {
                        const gpuVramGB = (gpu.vram_mb / 1024).toFixed(1);
                        const marker = (gpu.vram_mb === data.vram_mb && gpu.name === data.gpu_name)
                            ? ' <span class="text-muted" style="font-size:var(--font-size-xs)">[recommendation]</span>' : '';
                        html += `<span class="text-muted" style="padding-left:var(--space-3)">GPU ${gpu.index}:</span>`
                            + `<span>${escapeHtml(gpu.name)} -- ${gpu.vram_mb.toLocaleString()} MB (${gpuVramGB} GB)${marker}</span>`;
                    }
                    const totalGB = ((data.total_vram_mb || 0) / 1024).toFixed(1);
                    html += `<span class="text-muted">Total memory:</span><span>${(data.total_vram_mb || 0).toLocaleString()} MB (${totalGB} GB)</span>`;
                } else {
                    // Single GPU display
                    html += `<span class="text-muted">Graphics card:</span><span>${escapeHtml(data.gpu_name)}</span>`;
                    html += `<span class="text-muted">Graphics memory:</span><span>${data.vram_mb.toLocaleString()} MB (${vramGB} GB) -- ${quality}</span>`;
                }
            } else {
                html += `<span class="text-muted">Graphics card:</span><span>CPU-only mode (no dedicated GPU)</span>`;
            }
            html += `<span class="text-muted">Recommended model:</span><span><strong>${escapeHtml(data.recommended_model)}</strong> (${escapeHtml(data.model_size)})</span>`;
            html += `<span class="text-muted">Description:</span><span>${escapeHtml(data.model_summary)}</span>`;
            html += '</div>';
            container.innerHTML = html;
            this.markDone(1);
        } catch (e) {
            container.innerHTML = '<div class="setup-wizard__status setup-wizard__status--err">[X] Could not detect hardware. Continuing with defaults.</div>';
            this.markDone(1);
        }
    },

    // -- Step 2: Ollama Check --
    async runStep2() {
        const container = $('#setup-ollama-result');
        const installSection = $('#setup-ollama-install');
        container.innerHTML = '<div class="setup-wizard__loading">[*] Checking for Ollama...</div>';
        installSection.style.display = 'none';

        try {
            const resp = await fetch('/api/setup/check-ollama', { method: 'POST' });
            const data = await resp.json();
            this.ollamaData = data;

            if (data.running) {
                container.innerHTML = '<div class="setup-wizard__status setup-wizard__status--ok">[OK] Ollama is installed and running!</div>'
                    + `<div class="text-muted" style="margin-top:var(--space-2)">${data.models.length} model(s) installed</div>`;
                this.markDone(2);
            } else if (data.on_path) {
                container.innerHTML = '<div class="setup-wizard__status setup-wizard__status--warn">[!] Ollama is installed but not running.</div>'
                    + '<p class="text-muted" style="margin-top:var(--space-2)">Start it with: <code>ollama serve</code></p>';
            } else {
                container.innerHTML = '<div class="setup-wizard__status setup-wizard__status--warn">[!] Ollama is not installed yet.</div>';
                const instructions = $('#setup-ollama-instructions');
                if (data.platform === 'windows') {
                    instructions.textContent = 'Download the installer and run it. After installation, Ollama starts automatically.';
                } else if (data.platform === 'darwin') {
                    instructions.innerHTML = 'Install via Homebrew: <code>brew install ollama</code><br>Or download from the link below.';
                } else {
                    instructions.innerHTML = 'Run: <code>curl -fsSL https://ollama.com/install.sh | sh</code>';
                }
                installSection.style.display = '';
            }
        } catch (e) {
            container.innerHTML = '<div class="setup-wizard__status setup-wizard__status--err">[X] Could not check Ollama status.</div>';
        }
    },

    // -- Step 3: Model Download --
    async runStep3() {
        const nameEl = $('#setup-model-name');
        const descEl = $('#setup-model-desc');
        const actionsEl = $('#setup-model-actions');
        const doneEl = $('#setup-model-done');
        const progressEl = $('#setup-model-progress');

        if (this.hwData) {
            nameEl.textContent = this.hwData.recommended_model || 'Unknown model';
            descEl.textContent = `${this.hwData.model_size || ''} -- ${this.hwData.model_summary || ''}`;
        }

        // Check if already installed
        if (this.ollamaData && this.ollamaData.model_installed) {
            actionsEl.style.display = 'none';
            progressEl.style.display = 'none';
            doneEl.style.display = '';
            this.markDone(3);
        } else {
            actionsEl.style.display = '';
            doneEl.style.display = 'none';
        }
    },

    async startModelPull() {
        const actionsEl = $('#setup-model-actions');
        const progressEl = $('#setup-model-progress');
        actionsEl.style.display = 'none';
        progressEl.style.display = '';

        try {
            await fetch('/api/setup/pull-model', { method: 'POST' });
            // Progress comes via Socket.IO
        } catch (e) {
            $('#setup-progress-text').textContent = '[X] Failed to start download. Is Ollama running?';
        }
    },

    onPullProgress(data) {
        const fill = $('#setup-progress-fill');
        const text = $('#setup-progress-text');
        if (data.total > 0) {
            const pct = Math.round((data.completed / data.total) * 100);
            fill.style.width = pct + '%';
            const dlMB = (data.completed / 1048576).toFixed(0);
            const totalMB = (data.total / 1048576).toFixed(0);
            text.textContent = `${data.status || 'Downloading'}... ${dlMB} MB / ${totalMB} MB (${pct}%)`;
        } else {
            text.textContent = data.status || 'Preparing...';
        }
    },

    onPullComplete(data) {
        const progressEl = $('#setup-model-progress');
        const doneEl = $('#setup-model-done');
        if (data.success) {
            progressEl.style.display = 'none';
            doneEl.style.display = '';
            doneEl.innerHTML = '<div class="setup-wizard__status setup-wizard__status--ok">[OK] Model downloaded and ready!</div>';
            this.markDone(3);
        } else {
            $('#setup-progress-text').textContent = `[X] Download failed: ${data.error || 'Unknown error'}`;
        }
    },

    // -- Step 4: Verify --
    runStep4Auto() {
        // Don't auto-run verify -- let user click the button
        const resultEl = $('#setup-verify-result');
        const loadingEl = $('#setup-verify-loading');
        const btnEl = $('#setup-verify-btn');
        resultEl.style.display = 'none';
        loadingEl.style.display = 'none';
        btnEl.style.display = '';
    },

    async runStep4() {
        const resultEl = $('#setup-verify-result');
        const loadingEl = $('#setup-verify-loading');
        const btnEl = $('#setup-verify-btn');

        btnEl.style.display = 'none';
        loadingEl.style.display = '';
        resultEl.style.display = 'none';

        try {
            const resp = await fetch('/api/setup/verify', { method: 'POST' });
            const data = await resp.json();
            loadingEl.style.display = 'none';
            resultEl.style.display = '';

            if (data.success) {
                resultEl.innerHTML = '<div class="setup-wizard__status setup-wizard__status--ok">[OK] Everything works!</div>'
                    + `<blockquote class="setup-wizard__quote">${data.text}</blockquote>`
                    + `<div class="text-muted">Response generated in ${data.elapsed_seconds.toFixed(1)} seconds</div>`;
                this.markDone(4);
            } else {
                resultEl.innerHTML = `<div class="setup-wizard__status setup-wizard__status--err">[X] ${data.error}</div>`;
                btnEl.style.display = '';
                btnEl.textContent = 'Try Again';
            }
        } catch (e) {
            loadingEl.style.display = 'none';
            resultEl.style.display = '';
            resultEl.innerHTML = '<div class="setup-wizard__status setup-wizard__status--err">[X] Could not reach the model. Is Ollama running?</div>';
            btnEl.style.display = '';
        }
    },

    // -- Step 5: Content Pipeline --
    async runStep5() {
        const gridEl = $('#setup-topic-grid');
        if (this.topicsData) {
            this.renderTopicGrid(gridEl);
            return;
        }

        gridEl.innerHTML = '<div class="setup-wizard__loading">[*] Loading topics...</div>';
        try {
            const resp = await fetch('/api/setup/topics');
            const data = await resp.json();
            this.topicsData = data.topics;
            this.renderTopicGrid(gridEl);
        } catch (e) {
            gridEl.innerHTML = '<div class="text-muted">Could not load topics. You can set this up later.</div>';
        }
    },

    renderTopicGrid(gridEl) {
        gridEl.innerHTML = '';
        const topics = Object.keys(this.topicsData).sort();
        topics.forEach(topic => {
            const btn = document.createElement('button');
            btn.className = 'setup-wizard__topic-btn';
            btn.textContent = topic;
            btn.onclick = () => this.selectTopic(topic);
            gridEl.appendChild(btn);
        });
    },

    selectTopic(topic) {
        this.selectedTopic = topic;
        $('#setup-topic-picker').style.display = 'none';
        $('#setup-feed-picker').style.display = '';

        const feeds = this.topicsData[topic] || [];
        const listEl = $('#setup-feed-list');
        listEl.innerHTML = '';
        this.selectedFeeds = feeds.map(() => true);

        feeds.forEach((feed, i) => {
            const row = document.createElement('label');
            row.className = 'setup-wizard__feed-row';
            row.innerHTML = `<input type="checkbox" checked data-feed-idx="${i}">
                <span class="setup-wizard__feed-name">${feed.name}</span>
                <span class="setup-wizard__feed-url text-muted">${feed.url}</span>`;
            row.querySelector('input').onchange = (e) => {
                this.selectedFeeds[i] = e.target.checked;
            };
            listEl.appendChild(row);
        });
    },

    async saveFeeds() {
        const feeds = (this.topicsData[this.selectedTopic] || [])
            .filter((_, i) => this.selectedFeeds[i]);

        try {
            await fetch('/api/setup/save-config', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ topic: this.selectedTopic, feeds }),
            });
            this.markDone(5);
            showToast('Content pipeline configured!', 'success');
        } catch (e) {
            showToast('Failed to save content config', 'error');
        }
    },

    // -- Step 6: MCP Server Setup --
    async runStep6() {
        const resultEl = $('#setup-mcp-result');
        const configEl = $('#setup-mcp-config');
        resultEl.innerHTML = '<div class="setup-wizard__loading">[*] Checking MCP dependencies...</div>';
        resultEl.style.display = '';
        configEl.style.display = 'none';

        try {
            const resp = await fetch('/api/setup/check-mcp', { method: 'POST' });
            const data = await resp.json();
            this.mcpData = data;

            let statusHtml = '';
            // Dependencies check
            if (data.all_deps_ok) {
                statusHtml += '<div class="setup-wizard__status setup-wizard__status--ok">[OK] MCP packages installed (fastmcp, mcp)</div>';
            } else {
                statusHtml += '<div class="setup-wizard__status setup-wizard__status--warn">'
                    + '[!] Missing packages: ' + escapeHtml(data.missing.join(', ')) + '</div>'
                    + '<p class="text-muted" style="margin-top:var(--space-2)">'
                    + 'Install them with: <code>pip install cohort[claude]</code></p>';
            }

            // Ollama check
            if (data.ollama_ok) {
                statusHtml += '<div class="setup-wizard__status setup-wizard__status--ok" style="margin-top:var(--space-2)">[OK] Ollama is reachable</div>';
            } else {
                statusHtml += '<div class="setup-wizard__status setup-wizard__status--warn" style="margin-top:var(--space-2)">'
                    + '[!] Ollama not responding -- MCP server needs Ollama running</div>';
            }

            // Model check
            if (data.model_name) {
                if (data.model_installed) {
                    statusHtml += '<div class="setup-wizard__status setup-wizard__status--ok" style="margin-top:var(--space-2)">'
                        + '[OK] Model ' + escapeHtml(data.model_name) + ' available for MCP inference</div>';
                } else {
                    statusHtml += '<div class="setup-wizard__status setup-wizard__status--warn" style="margin-top:var(--space-2)">'
                        + '[!] Model ' + escapeHtml(data.model_name) + ' not found -- pull it first</div>';
                }
            }

            // Already configured?
            if (data.mcp_configured) {
                statusHtml += '<div class="setup-wizard__status setup-wizard__status--ok" style="margin-top:var(--space-2)">'
                    + '[OK] MCP config already written to .claude/settings.local.json</div>';
                this.markDone(6);
            }

            resultEl.innerHTML = statusHtml;

            // Show config snippet + write button if deps are available
            if (data.all_deps_ok) {
                const snippetEl = $('#setup-mcp-snippet');
                const codeEl = $('#setup-mcp-snippet-code');
                const snippet = JSON.stringify({
                    mcpServers: {
                        local_llm: {
                            command: "python",
                            args: ["-m", "cohort.mcp.local_llm_server"]
                        }
                    }
                }, null, 2);
                if (codeEl) codeEl.textContent = snippet;
                if (snippetEl) snippetEl.style.display = '';
                configEl.style.display = '';

                // Hide write button if already configured
                const writeBtn = $('#setup-mcp-write-btn');
                if (writeBtn && data.mcp_configured) {
                    writeBtn.textContent = 'Re-write MCP Config';
                }
            }
        } catch (e) {
            resultEl.innerHTML = '<div class="setup-wizard__status setup-wizard__status--err">'
                + '[X] Could not check MCP status.</div>';
        }
    },

    async writeMcpConfig() {
        const writeBtn = $('#setup-mcp-write-btn');
        if (writeBtn) {
            writeBtn.disabled = true;
            writeBtn.textContent = 'Writing...';
        }

        try {
            const resp = await fetch('/api/setup/write-mcp-config', { method: 'POST' });
            const data = await resp.json();
            if (data.success) {
                this.markDone(6);
                showToast('MCP config written! Claude Code will detect it automatically.', 'success');
                if (writeBtn) {
                    writeBtn.textContent = 'Re-write MCP Config';
                    writeBtn.disabled = false;
                }
            } else {
                showToast(data.error || 'Failed to write MCP config', 'error');
                if (writeBtn) {
                    writeBtn.textContent = 'Write MCP Config';
                    writeBtn.disabled = false;
                }
            }
        } catch (e) {
            showToast('Failed to write MCP config', 'error');
            if (writeBtn) {
                writeBtn.textContent = 'Write MCP Config';
                writeBtn.disabled = false;
            }
        }
    },

    // -- Step 7: Claude Code Connection --
    async runStep7() {
        const resultEl = $('#setup-claude-result');
        const configEl = $('#setup-claude-config');
        resultEl.innerHTML = '<div class="setup-wizard__loading">[*] Looking for Claude CLI...</div>';
        resultEl.style.display = '';
        configEl.style.display = 'none';

        try {
            const resp = await fetch('/api/setup/detect-claude', { method: 'POST' });
            const data = await resp.json();
            this.claudeData = data;

            if (data.found) {
                resultEl.innerHTML = '<div class="setup-wizard__status setup-wizard__status--ok">'
                    + '[OK] Found Claude CLI!</div>'
                    + '<div class="setup-wizard__info-grid">'
                    + '<span class="text-muted">Path:</span><span>' + escapeHtml(data.claude_path) + '</span>'
                    + (data.version ? '<span class="text-muted">Version:</span><span>' + escapeHtml(data.version) + '</span>' : '')
                    + '</div>';
            } else {
                resultEl.innerHTML = '<div class="setup-wizard__status setup-wizard__status--warn">'
                    + '[!] Claude CLI not found on your system.</div>'
                    + '<p class="text-muted" style="margin-top:var(--space-2)">'
                    + 'Install it from <a href="https://docs.anthropic.com/en/docs/claude-code" target="_blank">'
                    + 'docs.anthropic.com</a>, or skip this step and configure it later in Settings.</p>';
            }

            // Pre-populate fields from detection or existing settings
            const cmdInput = $('#setup-claude-cmd');
            const rootInput = $('#setup-agents-root');
            if (cmdInput) cmdInput.value = data.existing_claude_cmd || data.claude_path || '';
            if (rootInput) rootInput.value = data.existing_agents_root || data.agents_root_detected || '';

            configEl.style.display = '';
        } catch (e) {
            resultEl.innerHTML = '<div class="setup-wizard__status setup-wizard__status--err">'
                + '[X] Could not check for Claude CLI.</div>';
            configEl.style.display = '';
        }
    },

    async testClaudeSetup() {
        const dotEl = $('#setup-claude-dot');
        const textEl = $('#setup-claude-text');
        const cmdVal = ($('#setup-claude-cmd') || {}).value || '';

        if (dotEl) dotEl.className = 'settings-connection-dot testing';
        if (textEl) textEl.textContent = 'Testing...';

        if (!cmdVal.trim()) {
            if (dotEl) dotEl.className = 'settings-connection-dot error';
            if (textEl) textEl.textContent = 'No CLI path specified';
            return;
        }

        // Save fields first so test-connection can read from settings.json
        await this._saveClaudeFieldsQuiet();

        try {
            const resp = await fetch('/api/settings/test-connection', { method: 'POST' });
            const data = await resp.json();
            if (data.success) {
                if (dotEl) dotEl.className = 'settings-connection-dot ok';
                if (textEl) textEl.textContent = data.message || 'Connected';
            } else {
                if (dotEl) dotEl.className = 'settings-connection-dot error';
                if (textEl) textEl.textContent = data.error || 'Connection failed';
            }
        } catch (err) {
            if (dotEl) dotEl.className = 'settings-connection-dot error';
            if (textEl) textEl.textContent = 'Request failed';
        }
    },

    async _saveClaudeFieldsQuiet() {
        const payload = {
            claude_cmd: ($('#setup-claude-cmd') || {}).value || '',
            agents_root: ($('#setup-agents-root') || {}).value || '',
            execution_backend: ($('#setup-exec-backend') || {}).value || 'cli',
        };
        try {
            await fetch('/api/settings', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify(payload),
            });
        } catch (e) { /* silent */ }
    },

    async saveClaudeSettings() {
        const payload = {
            claude_cmd: ($('#setup-claude-cmd') || {}).value || '',
            agents_root: ($('#setup-agents-root') || {}).value || '',
            response_timeout: 300,
            execution_backend: ($('#setup-exec-backend') || {}).value || 'cli',
        };

        try {
            const resp = await fetch('/api/settings', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify(payload),
            });
            const data = await resp.json();
            if (data.success) {
                this.markDone(7);
                showToast('Claude Code settings saved!', 'success');
            } else {
                showToast(data.error || 'Failed to save', 'error');
            }
        } catch (e) {
            showToast('Failed to save Claude settings', 'error');
        }
    },

    async finish() {
        // If no feeds selected, still mark setup complete
        if (!this.stepsDone.has(5)) {
            try {
                await fetch('/api/setup/save-config', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ topic: '', feeds: [] }),
                });
            } catch (e) { /* ignore */ }
        }

        this.hide();
        switchPanel('team');
        showToast('Setup complete! Your agents are ready.', 'success');
    },
};


// Boot
document.addEventListener('DOMContentLoaded', init);
