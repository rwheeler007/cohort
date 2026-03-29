/**
 * Cohort - Setup Wizard Module
 *
 * 7-step onboarding: hardware detection, Ollama, model download,
 * verification, content pipeline, MCP server, cloud connection.
 *
 * Dependencies (from cohort.js globals):
 *   state, dom, $, escapeHtml(), showToast(), switchPanel()
 */

// =====================================================================
// Setup Wizard
// =====================================================================

const setupWizard = {
    currentStep: 1,
    totalSteps: 8,
    stepsDone: new Set(),
    hwData: null,
    ollamaData: null,
    topicsData: null,
    categoriesData: null,
    keywordsData: null,
    selectedTopic: null,
    selectedFeeds: [],
    selectedKeywords: [],
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

        const kwAddBtn = $('#setup-keyword-add-btn');
        const kwInput = $('#setup-keyword-input');
        if (kwAddBtn && kwInput) {
            kwAddBtn.onclick = () => this.addCustomKeyword();
            kwInput.onkeydown = (e) => { if (e.key === 'Enter') { e.preventDefault(); this.addCustomKeyword(); } };
        }

        // Step 6: MCP Server buttons
        const mcpWriteBtn = $('#setup-mcp-write-btn');
        if (mcpWriteBtn) mcpWriteBtn.onclick = () => this.writeMcpConfig();

        // Step 7: Claude Code buttons
        const claudeTestBtn = $('#setup-claude-test-btn');
        if (claudeTestBtn) claudeTestBtn.onclick = () => this.testClaudeSetup();

        const claudeSaveBtn = $('#setup-claude-save-btn');
        if (claudeSaveBtn) claudeSaveBtn.onclick = () => this.saveClaudeSettings();

        // Step 8: Security buttons
        const securitySaveBtn = $('#setup-security-save-btn');
        if (securitySaveBtn) securitySaveBtn.onclick = () => this.saveSecuritySettings();

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
            else if (next === 8) this.runStep8_Security();
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
            this.categoriesData = data.categories || null;
            this.keywordsData = data.topic_keywords || null;
            this.renderTopicGrid(gridEl);
        } catch (e) {
            gridEl.innerHTML = '<div class="text-muted">Could not load topics. You can set this up later.</div>';
        }
    },

    renderTopicGrid(gridEl) {
        gridEl.innerHTML = '';
        // If we have categories, render grouped; otherwise flat fallback
        if (this.categoriesData) {
            Object.entries(this.categoriesData).forEach(([category, topicKeys]) => {
                const heading = document.createElement('div');
                heading.className = 'setup-wizard__topic-category';
                heading.textContent = category;
                gridEl.appendChild(heading);

                const group = document.createElement('div');
                group.className = 'setup-wizard__topic-group';
                topicKeys.forEach(topic => {
                    if (!this.topicsData[topic]) return;
                    const btn = document.createElement('button');
                    btn.className = 'setup-wizard__topic-btn';
                    btn.textContent = topic;
                    btn.onclick = () => this.selectTopic(topic);
                    group.appendChild(btn);
                });
                gridEl.appendChild(group);
            });
        } else {
            const topics = Object.keys(this.topicsData).sort();
            topics.forEach(topic => {
                const btn = document.createElement('button');
                btn.className = 'setup-wizard__topic-btn';
                btn.textContent = topic;
                btn.onclick = () => this.selectTopic(topic);
                gridEl.appendChild(btn);
            });
        }
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

        // Render suggested keywords for this topic
        const suggested = (this.keywordsData && this.keywordsData[topic]) || [];
        this.selectedKeywords = [...suggested];  // all on by default
        this.renderKeywordChips();
    },

    renderKeywordChips() {
        const chipsEl = $('#setup-keyword-chips');
        if (!chipsEl) return;
        chipsEl.innerHTML = '';

        this.selectedKeywords.forEach((kw, i) => {
            const chip = document.createElement('span');
            chip.className = 'setup-wizard__keyword-chip';
            chip.innerHTML = escapeHtml(kw)
                + ' <button class="setup-wizard__keyword-remove" title="Remove">&times;</button>';
            chip.querySelector('button').onclick = () => {
                this.selectedKeywords.splice(i, 1);
                this.renderKeywordChips();
            };
            chipsEl.appendChild(chip);
        });

        if (this.selectedKeywords.length === 0) {
            chipsEl.innerHTML = '<span class="text-muted" style="font-size:var(--font-size-xs)">No keywords selected</span>';
        }
    },

    addCustomKeyword() {
        const input = $('#setup-keyword-input');
        if (!input) return;
        const kw = input.value.trim().toLowerCase();
        if (kw && !this.selectedKeywords.includes(kw)) {
            this.selectedKeywords.push(kw);
            this.renderKeywordChips();
        }
        input.value = '';
        input.focus();
    },

    async saveFeeds() {
        const feeds = (this.topicsData[this.selectedTopic] || [])
            .filter((_, i) => this.selectedFeeds[i]);

        try {
            await fetch('/api/setup/save-config', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({
                    topic: this.selectedTopic,
                    feeds,
                    interest_keywords: this.selectedKeywords,
                }),
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
                    + 'Install them with: <code>pip install cohort[mcp]</code></p>';
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
                    + '[OK] MCP config already written</div>';
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
                showToast('MCP config written! Your MCP client will detect it automatically.', 'success');
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

    // -- Step 7: Cloud & Advanced Setup --
    async runStep7() {
        const resultEl = $('#setup-claude-result');
        const configEl = $('#setup-claude-config');
        resultEl.innerHTML = '<div class="setup-wizard__loading">[*] Checking cloud setup...</div>';
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
                    + '[OK] CLI backend detected</div>'
                    + '<div class="setup-wizard__info-grid">'
                    + '<span class="text-muted">Path:</span><span>' + escapeHtml(data.claude_path) + '</span>'
                    + (data.version ? '<span class="text-muted">Version:</span><span>' + escapeHtml(data.version) + '</span>' : '')
                    + '</div>';
            } else {
                resultEl.innerHTML = '<div class="setup-wizard__status setup-wizard__status--info">'
                    + '[i] No CLI backend found -- this is fine for most users.</div>'
                    + '<p class="text-muted" style="margin-top:var(--space-2)">'
                    + 'Configure a Cloud API provider below for Smartest [S++] responses, or skip this step entirely.</p>';
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

            // Cloud provider fields
            const cloudProviderEl = $('#setup-cloud-provider');
            const cloudApiKeyEl = $('#setup-cloud-api-key');
            const cloudModelEl = $('#setup-cloud-model');
            const cloudFieldsEl = $('#setup-cloud-fields');
            if (cloudProviderEl && settings.cloud_provider) cloudProviderEl.value = settings.cloud_provider;
            if (cloudApiKeyEl && settings.cloud_api_key_masked) cloudApiKeyEl.value = settings.cloud_api_key_masked;
            if (cloudModelEl && settings.cloud_model) cloudModelEl.value = settings.cloud_model;
            // Show/hide cloud fields based on provider
            if (cloudFieldsEl) cloudFieldsEl.style.display = settings.cloud_provider ? '' : 'none';

            // Dev mode
            const devModeEl = $('#setup-dev-mode');
            if (devModeEl) devModeEl.checked = settings.dev_mode || false;
            // Show/hide force-claude based on dev mode
            const forceClaudeGroup = $('#setup-force-claude-group');
            if (forceClaudeGroup) forceClaudeGroup.style.display = settings.dev_mode ? '' : 'none';

            // Wire up cloud provider toggle
            if (cloudProviderEl) {
                cloudProviderEl.addEventListener('change', function() {
                    if (cloudFieldsEl) cloudFieldsEl.style.display = this.value ? '' : 'none';
                });
            }
            // Wire up dev mode toggle
            if (devModeEl) {
                devModeEl.addEventListener('change', function() {
                    if (forceClaudeGroup) forceClaudeGroup.style.display = this.checked ? '' : 'none';
                });
            }

            // Smartest mode status
            const smartestEl = $('#setup-smartest-status');
            if (smartestEl) {
                if (settings.smartest_available && settings.cloud_provider) {
                    smartestEl.innerHTML = '<div class="setup-wizard__status setup-wizard__status--ok">'
                        + '[OK] Smartest mode available -- Cloud API configured</div>';
                } else if (settings.smartest_available && settings.dev_mode) {
                    smartestEl.innerHTML = '<div class="setup-wizard__status setup-wizard__status--ok">'
                        + '[OK] Smartest mode available -- Dev mode CLI</div>';
                } else if (settings.smartest_available && data.found) {
                    smartestEl.innerHTML = '<div class="setup-wizard__status setup-wizard__status--ok">'
                        + '[OK] Smartest mode available -- Claude Code handoff'
                        + '</div>'
                        + '<div class="text-muted" style="font-size:var(--font-size-xs);margin-top:var(--space-1)">'
                        + 'Qwen handles reasoning locally, then hands off to Claude Code for execution. '
                        + 'You drive the session -- no API key needed.</div>';
                } else {
                    smartestEl.innerHTML = '<div class="text-muted" style="font-size:var(--font-size-sm)">'
                        + 'Smartest mode requires a Cloud API key or Claude Code CLI. '
                        + 'Configure a provider above or set the CLI path to unlock [S++] responses.</div>';
                }
            }

            configEl.style.display = '';
        } catch (e) {
            resultEl.innerHTML = '<div class="setup-wizard__status setup-wizard__status--err">'
                + '[X] Could not check cloud setup.</div>';
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
        const payload = {
            claude_cmd: ($('#setup-claude-cmd') || {}).value || '',
            agents_root: ($('#setup-agents-root') || {}).value || '',
            execution_backend: ($('#setup-exec-backend') || {}).value || 'cli',
            response_timeout: Math.max(30, Math.min(600, timeoutVal)),
            force_to_claude_code: ($('#setup-force-claude') || {}).checked || false,
            dev_mode: ($('#setup-dev-mode') || {}).checked || false,
            cloud_provider: ($('#setup-cloud-provider') || {}).value || '',
            cloud_model: (($('#setup-cloud-model') || {}).value || '').trim(),
        };
        // Only include cloud API key if a value was entered
        const cloudKey = (($('#setup-cloud-api-key') || {}).value || '').trim();
        if (cloudKey) payload.cloud_api_key = cloudKey;
        return payload;
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
                showToast('Settings saved!', 'success');
                // Make agents available globally
                this._linkGlobalAgents();
            } else {
                showToast(data.error || 'Failed to save', 'error');
            }
        } catch (e) {
            showToast('Failed to save settings', 'error');
        }
    },

    async _linkGlobalAgents() {
        try {
            const resp = await fetch('/api/setup/global-agents', { method: 'POST' });
            const data = await resp.json();
            if (data.success) {
                showToast('Agents available in all your projects!', 'success');
            }
            // Silently ignore failures -- non-blocking quality-of-life feature
        } catch (e) { /* non-blocking */ }
    },

    runStep8_Security() {
        // Pre-populate from existing settings if available
        const settings = state.settings || {};
        const perms = settings.default_permissions || {};
        const profile = perms.profile || 'readonly';
        const maxTurns = perms.max_turns || 15;
        const denyPaths = (perms.deny_paths || []).join('\n');

        const radios = document.querySelectorAll('input[name="setup-security-profile"]');
        radios.forEach(r => { r.checked = (r.value === profile); });

        const turnsSlider = $('#setup-max-turns');
        const turnsLabel = $('#setup-max-turns-label');
        if (turnsSlider) { turnsSlider.value = maxTurns; }
        if (turnsLabel) { turnsLabel.textContent = maxTurns; }

        const denyInput = $('#setup-deny-paths');
        if (denyInput) { denyInput.value = denyPaths; }
    },

    async saveSecuritySettings() {
        const selected = document.querySelector('input[name="setup-security-profile"]:checked');
        const profile = selected ? selected.value : 'readonly';
        const maxTurns = parseInt(($('#setup-max-turns') || {}).value) || 15;
        const denyRaw = ($('#setup-deny-paths') || {}).value || '';
        const denyPaths = denyRaw.split('\n').map(p => p.trim()).filter(Boolean);

        const payload = {
            default_permissions: {
                profile: profile,
                deny_paths: denyPaths,
                max_turns: maxTurns,
            },
        };

        try {
            const resp = await fetch('/api/settings', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify(payload),
            });
            const data = await resp.json();
            if (data.success !== false) {
                this.markDone(8);
                showToast('Security settings saved!', 'success');
            } else {
                showToast(data.error || 'Failed to save', 'error');
            }
        } catch (e) {
            showToast('Failed to save security settings', 'error');
        }
    },

    async finish() {
        // If security step was skipped, apply readonly as secure default
        if (!this.stepsDone.has(8)) {
            try {
                await fetch('/api/settings', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({
                        default_permissions: {
                            profile: 'readonly',
                            deny_paths: [],
                            max_turns: 15,
                        },
                    }),
                });
            } catch (e) { /* ignore */ }
        }

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

        // Open a starter agent DM so the user lands in a conversation,
        // not an empty Team panel.  Pick the first available agent from
        // the registry (prefer python_developer as a safe default).
        const starterAgents = ['python_developer', 'web_developer', 'cohort_orchestrator'];
        const picked = starterAgents.find(id => state.agentProfiles[id]) || Object.keys(state.agentProfiles).find(id => id !== 'user');
        if (picked) {
            openNewChatForAgent(picked);
            const profile = state.agentProfiles[picked];
            const name = (profile && (profile.nickname || profile.name)) || picked.replace(/_/g, ' ');
            showToast(`Setup complete! Say hello to ${name}.`, 'success');
        } else {
            // Fallback: no agents in registry (unlikely but safe)
            switchPanel('team');
            showToast('Setup complete!', 'success');
        }
    },
};


// =====================================================================

const importPrefs = {
    chatgptData: null,
    facts: [],
    _welcomed: false,

    init() {
        // Modal close
        const closeBtn = $('#import-modal-close');
        if (closeBtn) closeBtn.onclick = () => this.hideModal();

        // Welcome banner
        const importBtn = $('#welcome-import-btn');
        if (importBtn) importBtn.onclick = () => { this.hideBanner(); this.showModal(); };
        const dismissBtn = $('#welcome-dismiss-btn');
        if (dismissBtn) dismissBtn.onclick = () => this.hideBanner();

        // Import source buttons
        const promptBtn = $('#setup-import-prompt-btn');
        if (promptBtn) promptBtn.onclick = () => this.showProfilePrompt();
        const copyBtn = $('#setup-import-copy-prompt');
        if (copyBtn) copyBtn.onclick = () => this.copyProfilePrompt();
        const pasteBtn = $('#setup-import-paste-btn');
        if (pasteBtn) pasteBtn.onclick = () => this.parseProfilePaste();

        const chatgptBtn = $('#setup-import-chatgpt-btn');
        const chatgptFile = $('#setup-import-chatgpt-file');
        if (chatgptBtn && chatgptFile) {
            chatgptBtn.onclick = () => chatgptFile.click();
            chatgptFile.onchange = (e) => this.onChatGPTFileSelected(e);
        }

        const claudeBtn = $('#setup-import-claude-btn');
        if (claudeBtn) claudeBtn.onclick = () => this.importClaudeMemory();

        const configBtn = $('#setup-import-config-btn');
        const configFiles = $('#setup-import-config-files');
        if (configBtn && configFiles) {
            configBtn.onclick = () => configFiles.click();
            configFiles.onchange = (e) => this.onConfigFilesSelected(e);
        }

        const selectAll = $('#setup-import-select-all');
        const selectNone = $('#setup-import-select-none');
        if (selectAll) selectAll.onclick = () => this.toggleAll(true);
        if (selectNone) selectNone.onclick = () => this.toggleAll(false);

        const extractBtn = $('#setup-import-chatgpt-extract-btn');
        if (extractBtn) extractBtn.onclick = () => this.extractChatGPTFacts();

        const saveBtn = $('#setup-import-save-btn');
        if (saveBtn) saveBtn.onclick = () => this.saveFacts();
        const discardBtn = $('#setup-import-discard-btn');
        if (discardBtn) discardBtn.onclick = () => { this.facts = []; this.resetUI(); this.hideModal(); };
    },

    // -- Modal --
    showModal() {
        this.resetUI();
        const modal = $('#import-modal');
        if (modal) modal.hidden = false;
    },

    hideModal() {
        const modal = $('#import-modal');
        if (modal) modal.hidden = true;
    },

    // -- Welcome Banner --
    maybeShowBanner() {
        // Show once per session, only if no learned facts exist yet
        if (this._welcomed) return;
        this._welcomed = true;
        // Check via a lightweight API call
        fetch('/api/setup/import-claude-detect', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: '{}' })
            .then(r => r.json())
            .then(data => {
                // Show banner regardless -- it's about onboarding, not detection
                const banner = $('#welcome-banner');
                if (banner) banner.hidden = false;
            })
            .catch(() => {});
    },

    hideBanner() {
        const banner = $('#welcome-banner');
        if (banner) banner.hidden = true;
    },

    resetUI() {
        const ids = ['setup-import-sources', 'setup-import-prompt-flow', 'setup-import-chatgpt-picker',
                     'setup-import-progress', 'setup-import-preview'];
        ids.forEach(id => { const el = $('#' + id); if (el) el.style.display = (id === 'setup-import-sources') ? '' : 'none'; });
    },

    // -- Profile Prompt --
    async showProfilePrompt() {
        try {
            const resp = await fetch('/api/setup/import-profile-prompt');
            const data = await resp.json();
            $('#setup-import-prompt-text').textContent = data.prompt;
            $('#setup-import-sources').style.display = 'none';
            $('#setup-import-prompt-flow').style.display = '';
        } catch (e) { showToast('Failed to load prompt: ' + e.message, 'error'); }
    },

    async copyProfilePrompt() {
        const text = $('#setup-import-prompt-text').textContent;
        try { await navigator.clipboard.writeText(text); showToast('Copied!', 'success'); }
        catch (e) { showToast('Select and copy manually (Ctrl+C)', 'info'); }
    },

    async parseProfilePaste() {
        const text = ($('#setup-import-paste-area') || {}).value || '';
        if (!text.trim()) { showToast('Paste the AI response first', 'error'); return; }
        try {
            const resp = await fetch('/api/setup/import-profile-paste', {
                method: 'POST', headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ text }),
            });
            if (!resp.ok) throw new Error('Parse failed');
            const result = await resp.json();
            if (!result.facts || !result.facts.length) { showToast('No preferences found.', 'error'); return; }
            this.facts = result.facts;
            $('#setup-import-prompt-flow').style.display = 'none';
            this.renderPreview();
        } catch (e) { showToast('Parse failed: ' + e.message, 'error'); }
    },

    // -- Config Files --
    async onConfigFilesSelected(event) {
        const files = event.target.files;
        if (!files || !files.length) return;
        const fc = {};
        for (const f of files) { try { fc[f.name] = await f.text(); } catch (e) {} }
        if (!Object.keys(fc).length) { showToast('Could not read files', 'error'); return; }
        try {
            const resp = await fetch('/api/setup/import-config-files', {
                method: 'POST', headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ files: fc }),
            });
            if (!resp.ok) throw new Error('Parse failed');
            const result = await resp.json();
            if (!result.facts || !result.facts.length) { showToast('No preferences found.', 'error'); return; }
            this.facts = result.facts;
            $('#setup-import-sources').style.display = 'none';
            this.renderPreview();
        } catch (e) { showToast('Config parsing failed: ' + e.message, 'error'); }
    },

    // -- ChatGPT --
    async onChatGPTFileSelected(event) {
        const file = event.target.files[0];
        if (!file) return;
        try {
            const text = await file.text();
            const data = JSON.parse(text);
            this.chatgptData = Array.isArray(data) ? data : [data];
            const resp = await fetch('/api/setup/import-chatgpt-titles', {
                method: 'POST', headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ conversations: this.chatgptData }),
            });
            if (!resp.ok) throw new Error('Server error');
            const result = await resp.json();
            const listEl = $('#setup-import-chatgpt-list');
            listEl.innerHTML = '';
            for (const c of result.titles) {
                const item = document.createElement('label');
                item.className = 'setup-wizard__import-item';
                const date = c.create_time ? new Date(c.create_time * 1000).toLocaleDateString() : '';
                item.innerHTML = `<input type="checkbox" checked data-conv-id="${escapeHtml(c.id)}"><span class="setup-wizard__import-item-title">${escapeHtml(c.title)}</span><span class="setup-wizard__import-item-meta">${c.message_count} msgs ${date ? '| ' + date : ''}</span>`;
                listEl.appendChild(item);
            }
            $('#setup-import-sources').style.display = 'none';
            $('#setup-import-chatgpt-picker').style.display = '';
        } catch (e) { showToast('Failed to parse: ' + e.message, 'error'); }
    },

    toggleAll(checked) { $('#setup-import-chatgpt-list').querySelectorAll('input[type="checkbox"]').forEach(cb => cb.checked = checked); },

    async extractChatGPTFacts() {
        const ids = []; $('#setup-import-chatgpt-list').querySelectorAll('input:checked').forEach(cb => ids.push(cb.dataset.convId));
        if (!ids.length) { showToast('No conversations selected', 'error'); return; }
        $('#setup-import-chatgpt-picker').style.display = 'none';
        $('#setup-import-progress').style.display = '';
        const prog = $('#setup-import-progress-text'); const all = []; let done = 0;
        try {
            for (let i = 0; i < ids.length; i += 5) {
                const batch = ids.slice(i, i + 5); done += batch.length;
                prog.textContent = `[*] Processing ${done} of ${ids.length}...`;
                const r = await fetch('/api/setup/import-chatgpt-extract', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ conversations: this.chatgptData, selected_ids: batch }) });
                if (!r.ok) throw new Error('Extraction failed');
                const d = await r.json(); if (d.facts) all.push(...d.facts);
            }
            const seen = new Set();
            this.facts = all.filter(f => { const k = f.fact.toLowerCase(); if (seen.has(k)) return false; seen.add(k); return true; });
            this.renderPreview();
        } catch (e) { showToast('Extraction failed: ' + e.message, 'error'); this.resetUI(); }
    },

    // -- Claude Code --
    async importClaudeMemory() {
        const s = $('#setup-import-claude-status'); s.style.display = '';
        s.innerHTML = '<div class="setup-wizard__loading">[*] Scanning...</div>';
        try {
            const r = await fetch('/api/setup/import-claude-detect', { method: 'POST', headers: { 'Content-Type': 'application/json' } });
            if (!r.ok) throw new Error('Failed');
            const d = await r.json();
            if (!d.exists) { s.innerHTML = '<div class="setup-wizard__status setup-wizard__status--warn">[!] No ~/.claude/ found</div>'; return; }
            if (!d.facts || !d.facts.length) { s.innerHTML = '<div class="setup-wizard__status setup-wizard__status--warn">[!] No preference data</div>'; return; }
            s.innerHTML = `<div class="setup-wizard__status setup-wizard__status--ok">[OK] Found ${d.count} preference(s)</div>`;
            this.facts = d.facts; $('#setup-import-sources').style.display = 'none'; this.renderPreview();
        } catch (e) { s.innerHTML = `<div class="setup-wizard__status setup-wizard__status--err">[X] ${escapeHtml(e.message)}</div>`; }
    },

    // -- Preview & Save --
    renderPreview() {
        $('#setup-import-progress').style.display = 'none';
        $('#setup-import-preview').style.display = '';
        $('#setup-import-result').style.display = 'none';
        const list = $('#setup-import-facts-list'); list.innerHTML = '';
        this.facts.forEach((f, i) => {
            const el = document.createElement('label'); el.className = 'setup-wizard__import-item';
            const badge = f.category === 'preference' ? 'pref' : f.category === 'tool_usage' ? 'tool' : f.category === 'correction' ? 'rule' : 'fact';
            el.innerHTML = `<input type="checkbox" checked data-fact-idx="${i}"><span class="setup-wizard__import-item-title">${escapeHtml(f.fact)}</span><span class="setup-wizard__import-item-meta">${badge}</span>`;
            list.appendChild(el);
        });
    },

    async saveFacts() {
        const sel = [];
        $('#setup-import-facts-list').querySelectorAll('input:checked').forEach(cb => {
            const i = parseInt(cb.dataset.factIdx); if (this.facts[i]) sel.push(this.facts[i]);
        });
        if (!sel.length) { showToast('No facts selected', 'error'); return; }
        try {
            const r = await fetch('/api/setup/import-commit', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ facts: sel }) });
            if (!r.ok) throw new Error('Save failed');
            const d = await r.json();
            const res = $('#setup-import-result'); res.style.display = '';
            res.innerHTML = `<div class="setup-wizard__status setup-wizard__status--ok">[OK] Saved ${d.stored} preference(s). Your agents will use these right away.</div>`;
            // Auto-close modal after short delay
            setTimeout(() => this.hideModal(), 3000);
        } catch (e) { showToast('Save failed: ' + e.message, 'error'); }
    },
};
