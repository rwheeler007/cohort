/**
 * Cohort - Setup Wizard Module
 *
 * 7-step onboarding: hardware detection, Ollama, model download,
 * verification, content pipeline, MCP server, Claude Code connection.
 *
 * Dependencies (from cohort.js globals):
 *   state, dom, $, escapeHtml(), showToast(), switchPanel()
 */

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
                let thinkingHtml = '';
                if (data.thinking_ok) {
                    thinkingHtml = '<div class="setup-wizard__status setup-wizard__status--ok" style="margin-top:var(--space-2)">'
                        + '[OK] Thinking mode works -- Smarter [S+] responses enabled</div>';
                } else {
                    thinkingHtml = '<div class="setup-wizard__status setup-wizard__status--warn" style="margin-top:var(--space-2)">'
                        + '[!] Thinking mode not available -- Smart [S] mode will be used'
                        + (data.thinking_error ? ': ' + escapeHtml(data.thinking_error) : '')
                        + '</div>';
                }
                resultEl.innerHTML = '<div class="setup-wizard__status setup-wizard__status--ok">[OK] Everything works!</div>'
                    + `<blockquote class="setup-wizard__quote">${escapeHtml(data.text)}</blockquote>`
                    + `<div class="text-muted">Response generated in ${data.elapsed_seconds.toFixed(1)} seconds</div>`
                    + thinkingHtml;
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
            // Fetch detection + current settings in parallel
            const [detectResp, settingsResp] = await Promise.all([
                fetch('/api/setup/detect-claude', { method: 'POST' }),
                fetch('/api/settings'),
            ]);
            const data = await detectResp.json();
            const settings = await settingsResp.json();
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
            const backendInput = $('#setup-exec-backend');
            const timeoutInput = $('#setup-response-timeout');
            const forceClaudeInput = $('#setup-force-claude');

            if (cmdInput) cmdInput.value = data.existing_claude_cmd || data.claude_path || '';
            if (rootInput) rootInput.value = data.existing_agents_root || data.agents_root_detected || '';
            if (backendInput) backendInput.value = settings.execution_backend || 'cli';
            if (timeoutInput) timeoutInput.value = settings.response_timeout || 300;
            if (forceClaudeInput) forceClaudeInput.checked = settings.force_to_claude_code || false;

            // Smartest mode status
            const smartestEl = $('#setup-smartest-status');
            if (smartestEl) {
                if (settings.smartest_available) {
                    smartestEl.innerHTML = '<div class="setup-wizard__status setup-wizard__status--ok">'
                        + '[OK] Smartest mode available -- Claude CLI detected and working</div>';
                } else if (data.found) {
                    smartestEl.innerHTML = '<div class="setup-wizard__status setup-wizard__status--warn">'
                        + '[!] Claude CLI found but Smartest mode could not be verified. '
                        + 'Save settings and test the connection to enable it.</div>';
                } else {
                    smartestEl.innerHTML = '<div class="text-muted" style="font-size:var(--font-size-sm)">'
                        + 'Smartest mode requires Claude CLI. Install it to unlock [S++] responses.</div>';
                }
            }

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

    _gatherClaudeFields() {
        const timeoutVal = parseInt(($('#setup-response-timeout') || {}).value) || 300;
        return {
            claude_cmd: ($('#setup-claude-cmd') || {}).value || '',
            agents_root: ($('#setup-agents-root') || {}).value || '',
            execution_backend: ($('#setup-exec-backend') || {}).value || 'cli',
            response_timeout: Math.max(30, Math.min(600, timeoutVal)),
            force_to_claude_code: ($('#setup-force-claude') || {}).checked || false,
        };
    },

    async _saveClaudeFieldsQuiet() {
        try {
            await fetch('/api/settings', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify(this._gatherClaudeFields()),
            });
        } catch (e) { /* silent */ }
    },

    async saveClaudeSettings() {
        const payload = this._gatherClaudeFields();

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
