/**
 * Cohort - Settings Module
 *
 * Handles: Settings modal (general, connection, data management).
 *
 * Dependencies (from cohort.js globals):
 *   state, dom, escapeHtml(), showToast(), applyUserIdentity(),
 *   renderToolPanel()
 */

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

            // Global agents toggle
            const globalAgentsToggle = document.getElementById('settings-global-agents');
            if (globalAgentsToggle) globalAgentsToggle.checked = !!data.global_agents_linked;

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
    const globalAgentsToggle = document.getElementById('settings-global-agents');
    const wantsGlobalAgents = globalAgentsToggle ? globalAgentsToggle.checked : false;
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
        global_agents_linked: wantsGlobalAgents,
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
