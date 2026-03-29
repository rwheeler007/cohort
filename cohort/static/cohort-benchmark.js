/**
 * Cohort Benchmark A/B UI
 *
 * Handles benchmark scenario display, live run progress via Socket.IO,
 * scoring interface, and results summary. Depends on shared utilities
 * from cohort.js (state, escapeHtml, switchPanel, socket).
 */

const CohortBenchmark = (() => {
    'use strict';

    let _scenarios = [];
    let _currentRun = null;
    let _runs = [];
    let _scores = { a: {}, b: {} };
    let _initialized = false;
    let _enabled = false;

    // =====================================================================
    // Init
    // =====================================================================

    function init() {
        if (_initialized) return;
        _initialized = true;

        _checkEnabled();
    }

    async function _checkEnabled() {
        try {
            const resp = await fetch('/api/benchmark/status');
            const data = await resp.json();
            _enabled = data.enabled === true;
        } catch (e) {
            _enabled = false;
        }

        const section = document.getElementById('benchmark-section');
        if (section) {
            section.style.display = _enabled ? '' : 'none';
        }

        if (_enabled) {
            _loadScenarios();
            _loadRuns();
            _setupSocketListeners();
            _populateSidebar();
        }
    }

    // =====================================================================
    // Data loading
    // =====================================================================

    async function _loadScenarios() {
        try {
            const resp = await fetch('/api/benchmark/scenarios');
            const data = await resp.json();
            _scenarios = data.scenarios || [];
            _renderScenarioCards();
            _populateSidebar();
        } catch (e) {
            console.error('[Benchmark] Failed to load scenarios:', e);
        }
    }

    async function _loadRuns() {
        try {
            const resp = await fetch('/api/benchmark/runs');
            const data = await resp.json();
            _runs = data.runs || [];
            _renderHistory();
        } catch (e) {
            console.error('[Benchmark] Failed to load runs:', e);
        }
    }

    // =====================================================================
    // Sidebar
    // =====================================================================

    function _populateSidebar() {
        const list = document.getElementById('benchmark-scenario-list');
        if (!list) return;

        if (_scenarios.length === 0) {
            list.innerHTML = '<li class="sidebar-nav__empty">Loading...</li>';
            return;
        }

        list.innerHTML = _scenarios.map(s => `
            <li>
                <button class="sidebar-nav__channel-btn" onclick="CohortBenchmark.openPanel('${s.id}')">
                    <span class="sidebar-nav__channel-prefix">[AB]</span>
                    <span class="sidebar-nav__channel-name">${_esc(s.name)}</span>
                </button>
            </li>
        `).join('');
    }

    function openPanel(scenarioId) {
        switchPanel('benchmark');
        init();
        if (scenarioId) {
            _highlightScenario(scenarioId);
        }
    }

    // =====================================================================
    // Scenario cards
    // =====================================================================

    function _renderScenarioCards() {
        const container = document.getElementById('benchmark-scenario-cards');
        if (!container) return;

        container.innerHTML = _scenarios.map(s => {
            const categoryColors = {
                code_review: '#e74c3c',
                architecture: '#3498db',
                triage: '#2ecc71',
            };
            const color = categoryColors[s.category] || '#95a5a6';
            return `
                <div class="benchmark-card" data-scenario="${s.id}" onclick="CohortBenchmark.startRun('${s.id}')">
                    <div class="benchmark-card__category" style="border-left: 3px solid ${color}">
                        ${_esc(s.category.replace('_', ' ').toUpperCase())}
                    </div>
                    <h4 class="benchmark-card__name">${_esc(s.name)}</h4>
                    <p class="benchmark-card__desc">${_esc(s.description)}</p>
                    <div class="benchmark-card__agents">
                        ${s.agents.map(a => `<span class="benchmark-card__agent">@${_esc(a)}</span>`).join(' ')}
                    </div>
                    <div class="benchmark-card__criteria">${s.eval_criteria.length} evaluation criteria</div>
                    <button class="btn btn--primary btn--small benchmark-card__run-btn">Run A/B Test</button>
                </div>
            `;
        }).join('');
    }

    function _highlightScenario(scenarioId) {
        document.querySelectorAll('.benchmark-card').forEach(card => {
            card.classList.toggle('benchmark-card--selected', card.dataset.scenario === scenarioId);
        });
    }

    // =====================================================================
    // Start a run
    // =====================================================================

    async function startRun(scenarioId) {
        if (_currentRun && _currentRun.status === 'running') {
            if (typeof showToast === 'function') {
                showToast('A benchmark is already running', 'warning');
            }
            return;
        }

        try {
            const resp = await fetch('/api/benchmark/start', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ scenario_id: scenarioId }),
            });
            const data = await resp.json();
            if (data.error) {
                if (typeof showToast === 'function') {
                    showToast(data.error, 'error');
                }
                return;
            }
            _currentRun = data.run;
            _scores = { a: {}, b: {} };
            _renderRun();
            if (typeof showToast === 'function') {
                showToast('Benchmark started! Watch the progress below.', 'success');
            }
        } catch (e) {
            console.error('[Benchmark] Failed to start run:', e);
        }
    }

    // =====================================================================
    // Live run rendering
    // =====================================================================

    function _renderRun() {
        const runEl = document.getElementById('benchmark-run');
        const headerEl = document.getElementById('benchmark-header');
        if (!runEl || !_currentRun) return;

        runEl.style.display = '';
        // Collapse scenario cards while running
        if (headerEl) headerEl.style.display = _currentRun.status === 'running' ? 'none' : '';

        // Status badge
        const badge = document.getElementById('benchmark-run-badge');
        const runId = document.getElementById('benchmark-run-id');
        if (badge) {
            const statusMap = {
                running: 'Running...',
                scoring: 'Auto-scoring...',
                complete: 'Complete',
                scored: 'Scored',
                error: 'Error',
            };
            badge.textContent = statusMap[_currentRun.status] || _currentRun.status;
            badge.className = 'benchmark-run__badge benchmark-run__badge--' + _currentRun.status;
        }
        if (runId) {
            const scenario = _scenarios.find(s => s.id === _currentRun.scenario_id);
            runId.textContent = scenario ? scenario.name : _currentRun.scenario_id;
        }

        // Render arms
        _renderArm('a', _currentRun.arm_a);
        _renderArm('b', _currentRun.arm_b);

        // Show results if scored (auto or manual)
        const resultsEl = document.getElementById('benchmark-results');
        if (resultsEl) {
            resultsEl.style.display = _currentRun.status === 'scored' ? '' : 'none';
            if (_currentRun.status === 'scored') {
                // Populate _scores from run data for override editing
                _scores.a = { ...(_currentRun.arm_a.scores || {}) };
                _scores.b = { ...(_currentRun.arm_b.scores || {}) };
                _renderResults();
            }
        }

        // Show manual scoring override for scored or complete runs
        const scoringEl = document.getElementById('benchmark-scoring');
        if (scoringEl) {
            const showScoring = _currentRun.status === 'complete' || _currentRun.status === 'scored';
            scoringEl.style.display = showScoring ? '' : 'none';
            if (showScoring) {
                _renderScoringGrid();
            }
        }
    }

    function _renderArm(armKey, armData) {
        const statusEl = document.getElementById(`arm-${armKey}-status`);
        const metaEl = document.getElementById(`arm-${armKey}-meta`);
        const responsesEl = document.getElementById(`arm-${armKey}-responses`);

        if (statusEl) {
            const statusLabels = { pending: 'Waiting...', running: 'Running...', complete: 'Done', error: 'Error' };
            statusEl.textContent = statusLabels[armData.status] || armData.status;
            statusEl.className = 'benchmark-arm__status benchmark-arm__status--' + armData.status;
        }

        if (metaEl && armData.status === 'complete') {
            metaEl.innerHTML = `
                <span>${armData.total_elapsed.toFixed(1)}s total</span>
                <span>${armData.total_tokens_in.toLocaleString()} tok in</span>
                <span>${armData.total_tokens_out.toLocaleString()} tok out</span>
            `;
        }

        if (responsesEl) {
            responsesEl.innerHTML = armData.responses.map(r => {
                const localTok = `${r.tokens_in}+${r.tokens_out} local tok`;
                const claudeTok = (r.claude_tokens_in || r.claude_tokens_out)
                    ? ` | ~${r.claude_tokens_in}+${r.claude_tokens_out} claude tok`
                    : '';
                return `
                <div class="benchmark-response">
                    <div class="benchmark-response__header">
                        <strong>@${_esc(r.agent_id)}</strong>
                        <span class="benchmark-response__meta">
                            ${_esc(r.model || 'unknown')} | ${_esc(r.pipeline)} | ${localTok}${claudeTok} | ${r.elapsed_seconds.toFixed(1)}s
                        </span>
                    </div>
                    <div class="benchmark-response__content">${_renderMarkdown(r.content)}</div>
                </div>
            `}).join('');
        }
    }

    // =====================================================================
    // Scoring
    // =====================================================================

    function _renderScoringGrid() {
        const grid = document.getElementById('benchmark-scoring-grid');
        if (!grid || !_currentRun) return;

        const scenario = _scenarios.find(s => s.id === _currentRun.scenario_id);
        if (!scenario) return;

        const isAutoScored = _currentRun && _currentRun.status === 'scored'
            && _currentRun.arm_a.scores && Object.keys(_currentRun.arm_a.scores).length > 0;
        const headerNote = isAutoScored
            ? '<p class="benchmark-scoring__note">Auto-scored by LLM judge. Click values below to override, then Submit.</p>'
            : '';

        grid.innerHTML = `
            ${headerNote}
            <div class="benchmark-scoring__row benchmark-scoring__row--header">
                <div class="benchmark-scoring__criterion">Criterion</div>
                <div class="benchmark-scoring__arm-label">Arm A (Local)</div>
                <div class="benchmark-scoring__arm-label">Arm B (Hybrid)</div>
            </div>
            ${scenario.eval_criteria.map(c => `
                <div class="benchmark-scoring__row" data-criterion="${c.id}">
                    <div class="benchmark-scoring__criterion">
                        <strong>${_esc(c.label)}</strong>
                        ${c.weight !== 1.0 ? `<span class="benchmark-scoring__weight">(x${c.weight})</span>` : ''}
                        <p class="benchmark-scoring__desc">${_esc(c.description)}</p>
                    </div>
                    <div class="benchmark-scoring__input">
                        ${_renderScoreInput('a', c.id)}
                    </div>
                    <div class="benchmark-scoring__input">
                        ${_renderScoreInput('b', c.id)}
                    </div>
                </div>
            `).join('')}
        `;
    }

    function _renderScoreInput(arm, criterionId) {
        // Pre-populate from auto-scores if available
        const autoScore = _currentRun
            ? (arm === 'a' ? _currentRun.arm_a.scores : _currentRun.arm_b.scores)[criterionId]
            : undefined;
        const current = _scores[arm][criterionId] !== undefined
            ? _scores[arm][criterionId]
            : (autoScore !== undefined ? autoScore : null);

        return [0, 1, 2, 3, 4, 5].map(v => `
            <button class="benchmark-score-btn ${v === current ? 'benchmark-score-btn--active' : ''}"
                    onclick="CohortBenchmark.setScore('${arm}', '${criterionId}', ${v})"
                    title="${v}/5">${v}</button>
        `).join('');
    }

    function setScore(arm, criterionId, value) {
        _scores[arm][criterionId] = value;
        // Re-render just the scoring grid to update active states
        _renderScoringGrid();
    }

    async function submitScores() {
        if (!_currentRun) return;

        // Validate all criteria are scored for both arms
        const scenario = _scenarios.find(s => s.id === _currentRun.scenario_id);
        if (!scenario) return;

        for (const c of scenario.eval_criteria) {
            if (_scores.a[c.id] === undefined || _scores.b[c.id] === undefined) {
                if (typeof showToast === 'function') {
                    showToast(`Score all criteria before submitting. Missing: ${c.label}`, 'warning');
                }
                return;
            }
        }

        try {
            // Submit arm A scores
            await fetch(`/api/benchmark/runs/${_currentRun.id}/score`, {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ arm: 'a', scores: _scores.a }),
            });

            // Submit arm B scores
            const resp = await fetch(`/api/benchmark/runs/${_currentRun.id}/score`, {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ arm: 'b', scores: _scores.b }),
            });

            const data = await resp.json();
            if (data.run) {
                _currentRun = data.run;
                _renderRun();
                _loadRuns();  // Refresh history
            }
            if (typeof showToast === 'function') {
                showToast('Scores submitted!', 'success');
            }
        } catch (e) {
            console.error('[Benchmark] Failed to submit scores:', e);
        }
    }

    // =====================================================================
    // Results
    // =====================================================================

    function _renderResults() {
        const summary = document.getElementById('benchmark-results-summary');
        if (!summary || !_currentRun) return;

        const scenario = _scenarios.find(s => s.id === _currentRun.scenario_id);
        if (!scenario) return;

        const armA = _currentRun.arm_a;
        const armB = _currentRun.arm_b;

        // Calculate weighted scores
        let scoreA = 0, scoreB = 0, totalWeight = 0;
        for (const c of scenario.eval_criteria) {
            const w = c.weight;
            scoreA += (armA.scores[c.id] || 0) * w;
            scoreB += (armB.scores[c.id] || 0) * w;
            totalWeight += w * 5;  // max per criterion is 5
        }

        const pctA = totalWeight > 0 ? ((scoreA / totalWeight) * 100).toFixed(1) : '0';
        const pctB = totalWeight > 0 ? ((scoreB / totalWeight) * 100).toFixed(1) : '0';
        const winner = scoreA > scoreB ? 'A (Local)' : scoreB > scoreA ? 'B (Hybrid)' : 'Tie';
        const winnerClass = scoreA > scoreB ? 'arm-a' : scoreB > scoreA ? 'arm-b' : 'tie';

        // Token breakdown
        const claudeInB = armB.total_claude_in || 0;
        const claudeOutB = armB.total_claude_out || 0;
        const hasClaudeTokens = claudeInB > 0 || claudeOutB > 0;

        const tokenBreakdownA = `
            <div class="benchmark-results__tokens">
                <span class="benchmark-results__token-label">Local:</span>
                ${armA.total_tokens_in.toLocaleString()} in / ${armA.total_tokens_out.toLocaleString()} out
            </div>`;

        const tokenBreakdownB = `
            <div class="benchmark-results__tokens">
                <span class="benchmark-results__token-label">Local:</span>
                ${armB.total_tokens_in.toLocaleString()} in / ${armB.total_tokens_out.toLocaleString()} out
            </div>
            ${hasClaudeTokens ? `<div class="benchmark-results__tokens benchmark-results__tokens--claude">
                <span class="benchmark-results__token-label">Claude:</span>
                ~${claudeInB.toLocaleString()} in / ~${claudeOutB.toLocaleString()} out
            </div>` : ''}`;

        summary.innerHTML = `
            <div class="benchmark-results__winner benchmark-results__winner--${winnerClass}">
                Winner: ${winner}
            </div>
            <div class="benchmark-results__scores">
                <div class="benchmark-results__score">
                    <h4>Arm A: Local Only</h4>
                    <div class="benchmark-results__pct">${pctA}%</div>
                    <div class="benchmark-results__time">${armA.total_elapsed.toFixed(1)}s</div>
                    ${tokenBreakdownA}
                </div>
                <div class="benchmark-results__score">
                    <h4>Arm B: Hybrid</h4>
                    <div class="benchmark-results__pct">${pctB}%</div>
                    <div class="benchmark-results__time">${armB.total_elapsed.toFixed(1)}s</div>
                    ${tokenBreakdownB}
                </div>
            </div>
            <table class="benchmark-results__table">
                <thead>
                    <tr><th>Criterion</th><th>Weight</th><th>Arm A</th><th>Arm B</th><th>Delta</th></tr>
                </thead>
                <tbody>
                    ${scenario.eval_criteria.map(c => {
                        const a = armA.scores[c.id] || 0;
                        const b = armB.scores[c.id] || 0;
                        const delta = b - a;
                        const deltaClass = delta > 0 ? 'positive' : delta < 0 ? 'negative' : '';
                        return `<tr>
                            <td>${_esc(c.label)}</td>
                            <td>x${c.weight}</td>
                            <td>${a}/5</td>
                            <td>${b}/5</td>
                            <td class="benchmark-delta--${deltaClass}">${delta > 0 ? '+' : ''}${delta}</td>
                        </tr>`;
                    }).join('')}
                </tbody>
            </table>
        `;
    }

    // =====================================================================
    // History
    // =====================================================================

    function _renderHistory() {
        const list = document.getElementById('benchmark-history-list');
        if (!list) return;

        if (_runs.length === 0) {
            list.innerHTML = `
                <div class="empty-state">
                    <p class="empty-state__text">No benchmark runs yet</p>
                    <p class="empty-state__hint">Select a scenario above to start your first A/B comparison</p>
                </div>
            `;
            return;
        }

        list.innerHTML = _runs.map(r => {
            const scenario = r.scenario;
            const name = scenario ? scenario.name : r.scenario_id;
            const statusBadge = `<span class="benchmark-run__badge benchmark-run__badge--${r.status}">${r.status}</span>`;

            let scoreInfo = '';
            if (r.status === 'scored' && scenario) {
                let sA = 0, sB = 0, tw = 0;
                for (const c of scenario.eval_criteria) {
                    const w = c.weight;
                    sA += (r.arm_a.scores[c.id] || 0) * w;
                    sB += (r.arm_b.scores[c.id] || 0) * w;
                    tw += w * 5;
                }
                const pA = tw > 0 ? ((sA / tw) * 100).toFixed(0) : '0';
                const pB = tw > 0 ? ((sB / tw) * 100).toFixed(0) : '0';
                scoreInfo = `<span class="benchmark-history__scores">A: ${pA}% | B: ${pB}%</span>`;
            }

            return `
                <div class="benchmark-history__item" onclick="CohortBenchmark.viewRun('${r.id}')">
                    <div class="benchmark-history__name">${_esc(name)}</div>
                    <div class="benchmark-history__meta">
                        ${statusBadge}
                        ${scoreInfo}
                        <span class="benchmark-history__date">${_formatDate(r.started_at)}</span>
                    </div>
                </div>
            `;
        }).join('');
    }

    async function viewRun(runId) {
        try {
            const resp = await fetch(`/api/benchmark/runs/${runId}`);
            const data = await resp.json();
            if (data.error) return;
            _currentRun = data;

            // Populate scores from the run data
            _scores.a = { ...(data.arm_a.scores || {}) };
            _scores.b = { ...(data.arm_b.scores || {}) };

            _renderRun();
        } catch (e) {
            console.error('[Benchmark] Failed to load run:', e);
        }
    }

    // =====================================================================
    // Socket.IO listeners
    // =====================================================================

    function _setupSocketListeners() {
        if (typeof socket === 'undefined') return;

        socket.on('benchmark:started', (data) => {
            _currentRun = data;
            _renderRun();
            // Auto-switch to benchmark panel
            switchPanel('benchmark');
        });

        socket.on('benchmark:arm_started', (data) => {
            if (_currentRun && _currentRun.id === data.run_id) {
                const arm = data.arm === 'a' ? _currentRun.arm_a : _currentRun.arm_b;
                arm.status = 'running';
                _renderArm(data.arm, arm);
            }
        });

        socket.on('benchmark:agent_started', (data) => {
            if (!_currentRun || _currentRun.id !== data.run_id) return;
            const armKey = data.arm;
            const responsesEl = document.getElementById(`arm-${armKey}-responses`);
            if (responsesEl) {
                responsesEl.innerHTML += `
                    <div class="benchmark-response benchmark-response--loading" id="bench-loading-${armKey}-${data.agent_id}">
                        <div class="benchmark-response__header">
                            <strong>@${_esc(data.agent_id)}</strong>
                            <span class="benchmark-response__meta">Thinking...</span>
                        </div>
                        <div class="benchmark-response__content"><span class="loading-dots">...</span></div>
                    </div>
                `;
            }
        });

        socket.on('benchmark:agent_complete', (data) => {
            if (!_currentRun || _currentRun.id !== data.run_id) return;
            // Remove loading indicator
            const loadingEl = document.getElementById(`bench-loading-${data.arm}-${data.agent_id}`);
            if (loadingEl) loadingEl.remove();

            // Add actual response
            const r = data.response;
            const responsesEl = document.getElementById(`arm-${data.arm}-responses`);
            if (responsesEl) {
                responsesEl.innerHTML += `
                    <div class="benchmark-response">
                        <div class="benchmark-response__header">
                            <strong>@${_esc(r.agent_id)}</strong>
                            <span class="benchmark-response__meta">
                                ${_esc(r.model || 'unknown')} | ${_esc(r.pipeline)} | ${r.elapsed_seconds.toFixed(1)}s
                            </span>
                        </div>
                        <div class="benchmark-response__content">${_renderMarkdown(r.content)}</div>
                    </div>
                `;
            }

            // Update arm data
            const arm = data.arm === 'a' ? _currentRun.arm_a : _currentRun.arm_b;
            arm.responses.push(r);
        });

        socket.on('benchmark:arm_complete', (data) => {
            if (_currentRun && _currentRun.id === data.run_id) {
                if (data.arm === 'a') _currentRun.arm_a = data.arm_data;
                else _currentRun.arm_b = data.arm_data;
                _renderArm(data.arm, data.arm_data);
            }
        });

        socket.on('benchmark:scoring', (data) => {
            if (_currentRun && _currentRun.id === data.run_id) {
                _currentRun.status = 'scoring';
                _renderRun();
            }
        });

        socket.on('benchmark:complete', (data) => {
            _currentRun = data;
            _renderRun();
            _loadRuns();
            const msg = data.status === 'scored'
                ? 'Benchmark scored! Review results below.'
                : 'Benchmark complete!';
            if (typeof showToast === 'function') {
                showToast(msg, 'success');
            }
        });

        socket.on('benchmark:scored', (data) => {
            if (_currentRun && _currentRun.id === data.id) {
                _currentRun = data;
                _renderRun();
            }
            _loadRuns();
        });
    }

    // =====================================================================
    // Helpers
    // =====================================================================

    function _esc(str) {
        if (typeof escapeHtml === 'function') return escapeHtml(str || '');
        const el = document.createElement('span');
        el.textContent = str || '';
        return el.innerHTML;
    }

    function _renderMarkdown(text) {
        if (typeof marked !== 'undefined' && marked.parse) {
            try {
                return marked.parse(text || '');
            } catch (e) {
                return _esc(text);
            }
        }
        // Fallback: escape and preserve newlines
        return _esc(text).replace(/\n/g, '<br>');
    }

    function _formatDate(isoStr) {
        if (!isoStr) return '';
        try {
            const d = new Date(isoStr);
            return d.toLocaleDateString() + ' ' + d.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' });
        } catch (e) {
            return isoStr;
        }
    }

    // =====================================================================
    // Public API
    // =====================================================================

    return {
        init,
        openPanel,
        startRun,
        setScore,
        submitScores,
        viewRun,
    };
})();

// Auto-init when DOM is ready
document.addEventListener('DOMContentLoaded', () => {
    CohortBenchmark.init();
});
