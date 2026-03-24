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

            // Cloud provider fields
            const cloudProvider = document.getElementById('settings-cloud-provider');
            const cloudApiKey = document.getElementById('settings-cloud-api-key');
            const cloudModel = document.getElementById('settings-cloud-model');
            const cloudBaseUrl = document.getElementById('settings-cloud-base-url');
            if (cloudProvider) cloudProvider.value = data.cloud_provider || '';
            if (cloudApiKey) cloudApiKey.value = data.cloud_api_key_masked || '';
            if (cloudModel) cloudModel.value = data.cloud_model || '';
            if (cloudBaseUrl) cloudBaseUrl.value = data.cloud_base_url || '';
            toggleCloudFields(data.cloud_provider || '');

            // Dev mode toggle
            const devToggle = document.getElementById('settings-dev-mode');
            if (devToggle) devToggle.checked = !!data.dev_mode;
            toggleDevModeVisibility(!!data.dev_mode);

            // Model tier settings -- store for after model list populates dropdowns
            state._pendingTierSettings = data.tier_settings || {};
            fetchTierModelOptions(state._pendingTierSettings);

            // Token usage display
            const usage = data.token_usage || {};
            const todayUsage = usage.today || {};
            const monthUsage = usage.month || {};
            const limits = usage.limits || {};
            const fmt = (n) => n != null ? n.toLocaleString() : '--';
            const todayEl = document.getElementById('usage-today-tokens');
            const monthEl = document.getElementById('usage-month-tokens');
            const msgsEl = document.getElementById('usage-today-messages');
            if (todayEl) todayEl.textContent = `${fmt(todayUsage.tokens_total)} tokens` + (limits.daily_token_limit ? ` / ${fmt(limits.daily_token_limit)}` : '');
            if (monthEl) monthEl.textContent = `${fmt(monthUsage.tokens_total)} tokens` + (limits.monthly_token_limit ? ` / ${fmt(limits.monthly_token_limit)}` : '');
            if (msgsEl) msgsEl.textContent = fmt(todayUsage.messages);

            // Budget limit inputs
            const budgetDaily = document.getElementById('settings-budget-daily');
            const budgetMonthly = document.getElementById('settings-budget-monthly');
            const budgetEsc = document.getElementById('settings-budget-escalation');
            if (budgetDaily) budgetDaily.value = limits.daily_token_limit || '';
            if (budgetMonthly) budgetMonthly.value = limits.monthly_token_limit || '';
            if (budgetEsc) budgetEsc.value = limits.escalation_per_hour || '';

            // Show connection status
            updateSettingsConnectionStatus(data.claude_code_connected ? 'ok' : 'unknown',
                data.claude_code_connected ? 'Connected' : 'Not tested');
        })
        .catch(() => {
            // Fields stay empty if server can't be reached
        });

    if (dom.settingsModal) dom.settingsModal.hidden = false;
    switchSettingsTab('general');
}

/**
 * Fetch installed models from /api/llm/models and populate tier dropdowns.
 * Preserves the static options (auto-detect, cloud_api, none) and appends
 * real model names so users pick from a validated list.
 */
function fetchTierModelOptions(tierSettings) {
    fetch('/api/llm/models')
        .then(r => r.json())
        .then(data => {
            const models = (data.models || []).sort((a, b) => {
                // Sort by size descending (largest first)
                return (b.size_bytes || 0) - (a.size_bytes || 0);
            });

            for (const tier of ['smart', 'smarter', 'smartest']) {
                const primaryEl = document.getElementById(`settings-tier-${tier}-primary`);
                const fallbackEl = document.getElementById(`settings-tier-${tier}-fallback`);

                for (const sel of [primaryEl, fallbackEl]) {
                    if (!sel) continue;
                    // Remove any previously added model options (keep static ones)
                    const staticValues = new Set(['', 'cloud_api', 'channel']);
                    for (let i = sel.options.length - 1; i >= 0; i--) {
                        if (!staticValues.has(sel.options[i].value)) {
                            sel.remove(i);
                        }
                    }
                    // Add installed models
                    for (const m of models) {
                        const opt = document.createElement('option');
                        opt.value = m.name;
                        opt.textContent = `${m.name} (${m.size || m.parameter_size || '?'})`;
                        sel.appendChild(opt);
                    }
                }

                // Restore saved values
                const cfg = (tierSettings || {})[tier] || {};
                if (primaryEl) primaryEl.value = cfg.primary || '';
                if (fallbackEl) fallbackEl.value = cfg.fallback || '';
            }
        })
        .catch(err => {
            console.warn('Could not fetch model list for tier dropdowns:', err);
            // Fall back: just set saved values on the static options
            for (const tier of ['smart', 'smarter', 'smartest']) {
                const cfg = (tierSettings || {})[tier] || {};
                const primaryEl = document.getElementById(`settings-tier-${tier}-primary`);
                const fallbackEl = document.getElementById(`settings-tier-${tier}-fallback`);
                if (primaryEl) primaryEl.value = cfg.primary || '';
                if (fallbackEl) fallbackEl.value = cfg.fallback || '';
            }
        });
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
    const devModeToggle = document.getElementById('settings-dev-mode');
    const cloudProviderEl = document.getElementById('settings-cloud-provider');
    const cloudModelEl = document.getElementById('settings-cloud-model');
    const cloudBaseUrlEl = document.getElementById('settings-cloud-base-url');
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
        dev_mode: devModeToggle ? devModeToggle.checked : false,
        cloud_provider: cloudProviderEl ? cloudProviderEl.value : '',
        cloud_model: cloudModelEl ? cloudModelEl.value.trim() : '',
        cloud_base_url: cloudBaseUrlEl ? cloudBaseUrlEl.value.trim() : '',
    };

    // Model tier settings
    const tierSettings = {};
    for (const tier of ['smart', 'smarter', 'smartest']) {
        const primaryEl = document.getElementById(`settings-tier-${tier}-primary`);
        const fallbackEl = document.getElementById(`settings-tier-${tier}-fallback`);
        tierSettings[tier] = {
            primary: primaryEl ? primaryEl.value.trim() || null : null,
            fallback: fallbackEl ? fallbackEl.value.trim() || null : null,
        };
    }
    // Budget limits
    const budgetDaily = document.getElementById('settings-budget-daily');
    const budgetMonthly = document.getElementById('settings-budget-monthly');
    const budgetEsc = document.getElementById('settings-budget-escalation');
    tierSettings.budget = {
        daily_token_limit: budgetDaily ? parseInt(budgetDaily.value, 10) || 0 : 0,
        monthly_token_limit: budgetMonthly ? parseInt(budgetMonthly.value, 10) || 0 : 0,
        escalation_per_hour: budgetEsc ? parseInt(budgetEsc.value, 10) || 0 : 0,
    };
    payload.tier_settings = tierSettings;

    // Only include API key if user typed a real value (not the masked placeholder)
    const apiKeyVal = dom.settingsApiKey ? dom.settingsApiKey.value.trim() : '';
    if (apiKeyVal && !apiKeyVal.startsWith('sk-...')) {
        payload.api_key = apiKeyVal;
    }

    // Only include cloud API key if changed (not masked)
    const cloudKeyEl = document.getElementById('settings-cloud-api-key');
    const cloudKeyVal = cloudKeyEl ? cloudKeyEl.value.trim() : '';
    if (cloudKeyVal && !cloudKeyVal.startsWith('sk-...')) {
        payload.cloud_api_key = cloudKeyVal;
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

function toggleCloudFields(provider) {
    const fieldsContainer = document.getElementById('settings-cloud-fields');
    const baseUrlGroup = document.getElementById('settings-cloud-base-url-group');
    if (fieldsContainer) fieldsContainer.style.display = provider ? '' : 'none';
    if (baseUrlGroup) baseUrlGroup.style.display = provider === 'openai' ? '' : 'none';
}

function toggleCloudKeyVisibility() {
    const keyInput = document.getElementById('settings-cloud-api-key');
    const btn = document.getElementById('toggleCloudKeyVis');
    if (!keyInput) return;
    const isPassword = keyInput.type === 'password';
    keyInput.type = isPassword ? 'text' : 'password';
    if (btn) btn.textContent = isPassword ? '[.]' : '[*]';
}

function toggleDevModeVisibility(enabled) {
    // Show/hide "Force cloud API" toggle -- only relevant in dev mode
    const forceClaudeGroup = document.getElementById('settings-force-claude-code');
    if (forceClaudeGroup) {
        const section = forceClaudeGroup.closest('.settings-section__body, .form-group');
        // Walk up to the toggle-label parent to hide the whole row
        const row = forceClaudeGroup.closest('label.toggle-label');
        if (row && row.parentElement) {
            row.parentElement.style.display = enabled ? '' : 'none';
        }
    }
}

// Wire up event listeners after DOM is ready
document.addEventListener('DOMContentLoaded', function() {
    const cloudProviderSelect = document.getElementById('settings-cloud-provider');
    if (cloudProviderSelect) {
        cloudProviderSelect.addEventListener('change', function() {
            toggleCloudFields(this.value);
        });
    }
    const devModeToggle = document.getElementById('settings-dev-mode');
    if (devModeToggle) {
        devModeToggle.addEventListener('change', function() {
            toggleDevModeVisibility(this.checked);
        });
    }
    const cloudKeyToggle = document.getElementById('toggleCloudKeyVis');
    if (cloudKeyToggle) {
        cloudKeyToggle.addEventListener('click', toggleCloudKeyVisibility);
    }
});
