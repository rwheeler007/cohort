/**
 * Cohort - Permissions Module
 *
 * Handles: Service Keys, Agent Access grid, Tool Permissions (per-agent),
 * Tool Defaults, File Permissions, and .env drag-and-drop import.
 *
 * Dependencies (from cohort.js globals):
 *   state, dom, escapeHtml(), showToast()
 */

// =====================================================================
// Service type metadata (display names + colors)
// =====================================================================

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
    rss:          [{ key: 'key', label: 'API Key (optional)', type: 'password', placeholder: '' }],
    custom:       [
        { key: 'key', label: 'API Key / Token', type: 'password', placeholder: '' },
    ],
};

// Default services to pre-populate when no services exist yet.
// These are the common integrations most teams will need -- users just fill in keys.
const DEFAULT_SERVICE_PRESETS = [
    { type: 'anthropic',    name: 'Anthropic API' },
    { type: 'github',       name: 'GitHub API' },
    { type: 'internal_web', name: 'Internal Web Accessor' },
    { type: 'youtube',      name: 'YouTube Data API' },
    { type: 'linkedin',     name: 'LinkedIn API' },
    { type: 'google',       name: 'Google Cloud API' },
    { type: 'openai',       name: 'OpenAI API' },
    { type: 'cloudflare',   name: 'Cloudflare API' },
    { type: 'twitter',      name: 'Twitter/X API' },
    { type: 'reddit',       name: 'Reddit API' },
    { type: 'slack',        name: 'Slack Webhook' },
    { type: 'discord',      name: 'Discord Webhook' },
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

// =====================================================================
// Permissions state
// =====================================================================

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
            } else {
                // Ensure local services are always present (they don't need keys
                // so users would never manually add them via the Service Keys tab)
                const LOCAL_PRESETS = DEFAULT_SERVICE_PRESETS.filter(p => {
                    const meta = SERVICE_TYPES[p.type];
                    return meta && meta.local;
                });
                for (const preset of LOCAL_PRESETS) {
                    if (!permState.services.some(s => s.type === preset.type)) {
                        permState.services.push({
                            id: preset.type + '_default',
                            type: preset.type,
                            name: preset.name,
                            has_key: false,
                            key_masked: '',
                            extra: '',
                        });
                    }
                }
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

// =====================================================================
// Service Keys tab
// =====================================================================

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
                            parts.push(data.playwright ? 'Fetch: OK' : 'Fetch: missing');
                            parts.push(data.ddgs ? 'Search: OK' : 'Search: missing');
                            parts.push(data.browser_backend ? 'Browser: OK' : 'Browser: N/A');
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

        const testBtn = (svc.has_key || isLocal)
            ? `<button class="btn btn--small btn--test" onclick="testServiceKey(${idx})" title="Test connection" data-test-idx="${idx}">Test</button>`
            : '';

        return `
            <div class="service-key-card" data-service-idx="${idx}">
                <div class="service-key-card__icon" style="background-color: ${meta.color}">${meta.icon}</div>
                <div class="service-key-card__info">
                    <div class="service-key-card__name">${escapeHtml(displayName)}</div>
                    <div class="service-key-card__key">${escapeHtml(keyDisplay)}</div>
                </div>
                <span class="service-key-card__status service-key-card__status--${statusClass}">${statusText}</span>
                <div class="service-key-card__actions">
                    ${testBtn}
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
    if (!container) return;
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
            if (values[fieldDef.key] !== undefined) input.value = values[fieldDef.key];

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
            if (values[fieldDef.key] !== undefined) input.value = values[fieldDef.key];
            group.appendChild(input);
        }

        container.appendChild(group);
    });
}

function collectServiceFields() {
    // Collect values from dynamically rendered fields.
    // Returns { key: mainKeyValue, extra: JSON string of extra fields }.
    const container = dom.serviceDynamicFields;
    if (!container) return { key: '', extra: '' };

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

function testServiceKey(idx) {
    const svc = permState.services[idx];
    if (!svc) return;

    const btn = document.querySelector(`[data-test-idx="${idx}"]`);
    const statusEl = document.querySelector(`[data-service-idx="${idx}"] .service-key-card__status`);
    if (!btn) return;

    // Set loading state
    btn.disabled = true;
    btn.textContent = '...';
    btn.classList.add('btn--test-loading');
    if (statusEl) {
        statusEl.className = 'service-key-card__status service-key-card__status--checking';
        statusEl.textContent = 'Testing...';
    }

    fetch('/api/service-keys/test', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ service_id: svc.id }),
    })
        .then(r => {
            if (!r.ok && r.status === 404) throw new Error('Server needs restart to load test endpoint');
            return r.json();
        })
        .then(data => {
            btn.disabled = false;
            btn.classList.remove('btn--test-loading');

            if (data.success) {
                btn.textContent = 'Pass';
                btn.classList.add('btn--test-pass');
                if (statusEl) {
                    statusEl.className = 'service-key-card__status service-key-card__status--active';
                    statusEl.textContent = 'Connected';
                    if (data.latency_ms) statusEl.title = `${data.latency_ms}ms`;
                }
                showToast(`${svc.name}: ${data.message}`, 'success');
            } else {
                btn.textContent = 'Fail';
                btn.classList.add('btn--test-fail');
                if (statusEl) {
                    statusEl.className = 'service-key-card__status service-key-card__status--missing';
                    statusEl.textContent = 'Failed';
                }
                showToast(`${svc.name}: ${data.message}`, 'error');
            }

            // Reset button text after 4 seconds
            setTimeout(() => {
                btn.textContent = 'Test';
                btn.classList.remove('btn--test-pass', 'btn--test-fail');
            }, 4000);
        })
        .catch(err => {
            btn.disabled = false;
            btn.textContent = 'Fail';
            btn.classList.remove('btn--test-loading');
            btn.classList.add('btn--test-fail');
            if (statusEl) {
                statusEl.className = 'service-key-card__status service-key-card__status--missing';
                statusEl.textContent = 'Error';
            }
            showToast(`${svc.name}: Connection test failed -- ${err.message}`, 'error');
            setTimeout(() => {
                btn.textContent = 'Test';
                btn.classList.remove('btn--test-fail');
            }, 4000);
        });
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
    WebSearch: 'Search the web (cloud API)',
    WebFetch: 'Fetch web page content (cloud API)',
    InternalWebSearch: 'Search the web locally via DuckDuckGo (free)',
    InternalWebFetch: 'Fetch web pages locally via Playwright (free)',
    BrowserBrowse: 'Browse: navigate, read pages, screenshots, accessibility snapshots (free, local)',
    BrowserInteract: 'Interact: click, fill forms, type, drag, mouse control, resize (free, local)',
    BrowserAdvanced: 'Full Control: JS eval, cookies, storage, network mocking, PDF export (free, local)',
};

// Browser tools are tiered: Advanced includes Interact includes Browse
const BROWSER_TIER_TOOLS = ['BrowserBrowse', 'BrowserInteract', 'BrowserAdvanced'];
const BROWSER_TIER_HIERARCHY = {
    BrowserAdvanced: ['BrowserBrowse', 'BrowserInteract', 'BrowserAdvanced'],
    BrowserInteract: ['BrowserBrowse', 'BrowserInteract'],
    BrowserBrowse: ['BrowserBrowse'],
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
        const standardTools = allTools.filter(t => !BROWSER_TIER_TOOLS.includes(t));
        const browserTools = allTools.filter(t => BROWSER_TIER_TOOLS.includes(t));

        const renderTool = (tool) => {
            const checked = agentTools.has(tool) ? 'checked' : '';
            const denied = deniedGlobally.has(tool);
            const desc = TOOL_DESCRIPTIONS[tool] || '';
            const disabledAttr = denied ? 'disabled' : '';
            const deniedNote = denied ? ' <span class="tool-perms__denied-tag">(globally denied)</span>' : '';
            // Friendly names for browser tiers
            const displayName = {
                BrowserBrowse: 'Browse',
                BrowserInteract: 'Interact',
                BrowserAdvanced: 'Full Control',
            }[tool] || tool;

            return `<label class="tool-perms__tool${denied ? ' tool-perms__tool--denied' : ''}">
                <input type="checkbox" class="tool-perms__check" data-tool="${escapeHtml(tool)}" ${checked} ${disabledAttr}>
                <span class="tool-perms__tool-name">${escapeHtml(displayName)}</span>
                <span class="tool-perms__tool-desc">${escapeHtml(desc)}${deniedNote}</span>
            </label>`;
        };

        let html = standardTools.map(renderTool).join('');
        if (browserTools.length > 0) {
            html += `<div class="tool-perms__section-header" style="margin-top: var(--space-3); padding-top: var(--space-2); border-top: 1px solid var(--clr-border); font-size: var(--font-size-sm); font-weight: 600; color: var(--clr-text-secondary);">Browser Automation (Playwright)</div>`;
            html += browserTools.map(renderTool).join('');
        }
        dom.toolPermsGrid.innerHTML = html;

        // Browser tier hierarchy: checking a higher tier auto-checks lower tiers
        dom.toolPermsGrid.querySelectorAll('.tool-perms__check').forEach(cb => {
            if (BROWSER_TIER_TOOLS.includes(cb.dataset.tool)) {
                cb.addEventListener('change', () => {
                    const tool = cb.dataset.tool;
                    if (cb.checked && BROWSER_TIER_HIERARCHY[tool]) {
                        // Auto-check all lower tiers
                        BROWSER_TIER_HIERARCHY[tool].forEach(t => {
                            const el = dom.toolPermsGrid.querySelector(`[data-tool="${t}"]`);
                            if (el && !el.disabled) el.checked = true;
                        });
                    } else if (!cb.checked) {
                        // Unchecking a lower tier unchecks higher tiers
                        const idx = BROWSER_TIER_TOOLS.indexOf(tool);
                        for (let i = idx + 1; i < BROWSER_TIER_TOOLS.length; i++) {
                            const el = dom.toolPermsGrid.querySelector(`[data-tool="${BROWSER_TIER_TOOLS[i]}"]`);
                            if (el) el.checked = false;
                        }
                    }
                });
            }
        });
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
