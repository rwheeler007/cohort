/**
 * Cohort Interactive Simulator
 *
 * Replays pre-recorded multi-agent conversations with branching choices.
 * No LLM calls -- all content is pre-generated JSON.
 */

class CohortSimulator {
  constructor(containerEl, scenario) {
    this.container = containerEl;
    this.scenario = scenario;
    this.choices = {};         // { choice_1: "compat", choice_2: "security" }
    this.currentPhaseIdx = 0;
    this.currentStepIdx = 0;
    this.isAnimating = false;
    this.animationSpeed = 800;  // ms between steps
    this.activeTooltip = null;

    // Running cost/token counters
    this.totalTokensIn = 0;
    this.totalTokensOut = 0;
    this.totalCost = 0;           // Cohort cost (smartest only)
    this.totalTimeSaved = 0;      // ms saved by gating
    this.gatedResponses = 0;      // responses Cohort prevented
    this.withoutCohortTokens = 0; // what tokens would be without gating

    // Build the UI skeleton
    this._buildUI();
    this._renderAgentPanel();
    this._startPhase(0);
  }

  // ================================================================
  // UI Construction
  // ================================================================

  _buildUI() {
    this.container.innerHTML = '';
    this.container.className = 'sim-container';

    // Header
    const header = document.createElement('div');
    header.className = 'sim-header';
    header.innerHTML = `
      <h2 class="sim-title">${this.scenario.title}</h2>
      <p class="sim-description">${this.scenario.description}</p>
    `;
    this.container.appendChild(header);

    // Cost ticker bar (sticky)
    this.costTicker = document.createElement('div');
    this.costTicker.className = 'sim-cost-ticker';
    this.costTicker.innerHTML = this._buildTickerHTML();
    this.container.appendChild(this.costTicker);

    // Main layout: agents panel + conversation
    const layout = document.createElement('div');
    layout.className = 'sim-layout';

    // Agent sidebar
    this.agentPanel = document.createElement('div');
    this.agentPanel.className = 'sim-agents-panel';
    layout.appendChild(this.agentPanel);

    // Conversation area
    const convWrapper = document.createElement('div');
    convWrapper.className = 'sim-conversation-wrapper';

    // Phase indicator
    this.phaseBar = document.createElement('div');
    this.phaseBar.className = 'sim-phase-bar';
    convWrapper.appendChild(this.phaseBar);

    // Messages area
    this.messageArea = document.createElement('div');
    this.messageArea.className = 'sim-messages';
    convWrapper.appendChild(this.messageArea);

    layout.appendChild(convWrapper);
    this.container.appendChild(layout);

    // Speed control
    const controls = document.createElement('div');
    controls.className = 'sim-controls';
    controls.innerHTML = `
      <button class="sim-btn sim-btn-speed" data-speed="fast" title="Fast playback">
        <span class="sim-speed-icon">&#9654;&#9654;</span>
      </button>
      <button class="sim-btn sim-btn-speed active" data-speed="normal" title="Normal playback">
        <span class="sim-speed-icon">&#9654;</span>
      </button>
      <button class="sim-btn sim-btn-speed" data-speed="slow" title="Slow playback">
        <span class="sim-speed-icon">&#9646;&#9654;</span>
      </button>
    `;
    controls.querySelectorAll('.sim-btn-speed').forEach(btn => {
      btn.addEventListener('click', () => {
        controls.querySelectorAll('.sim-btn-speed').forEach(b => b.classList.remove('active'));
        btn.classList.add('active');
        const speeds = { fast: 400, normal: 800, slow: 1500 };
        this.animationSpeed = speeds[btn.dataset.speed];
      });
    });
    this.container.appendChild(controls);
  }

  _buildTickerHTML() {
    const smarter = this.scenario.tier_config.smarter;
    const smartest = this.scenario.tier_config.smartest;
    return `
      <div class="sim-ticker-section">
        <div class="sim-ticker-label">Tokens used</div>
        <div class="sim-ticker-value" id="ticker-tokens">${(this.totalTokensIn + this.totalTokensOut).toLocaleString()}</div>
      </div>
      <div class="sim-ticker-divider"></div>
      <div class="sim-ticker-section">
        <div class="sim-ticker-label">Cohort cost</div>
        <div class="sim-ticker-value sim-ticker-green" id="ticker-cost">$${this.totalCost.toFixed(3)}</div>
      </div>
      <div class="sim-ticker-divider"></div>
      <div class="sim-ticker-section">
        <div class="sim-ticker-label">Without Cohort</div>
        <div class="sim-ticker-value sim-ticker-red" id="ticker-wasted">$${(this.withoutCohortTokens * 0.003 / 1000).toFixed(3)}</div>
      </div>
      <div class="sim-ticker-divider"></div>
      <div class="sim-ticker-section">
        <div class="sim-ticker-label">Responses gated</div>
        <div class="sim-ticker-value sim-ticker-green" id="ticker-gated">${this.gatedResponses}</div>
      </div>
    `;
  }

  _updateTicker() {
    const el = (id) => document.getElementById(id);
    const tokEl = el('ticker-tokens');
    const costEl = el('ticker-cost');
    const wastedEl = el('ticker-wasted');
    const gatedEl = el('ticker-gated');

    if (tokEl) tokEl.textContent = (this.totalTokensIn + this.totalTokensOut).toLocaleString();
    if (costEl) costEl.textContent = '$' + this.totalCost.toFixed(3);
    if (wastedEl) {
      const wastedCost = this.withoutCohortTokens * 0.003 / 1000;
      wastedEl.textContent = '$' + wastedCost.toFixed(3);
    }
    if (gatedEl) gatedEl.textContent = this.gatedResponses;

    // Pulse animation on update
    this.costTicker.classList.add('sim-ticker-pulse');
    setTimeout(() => this.costTicker.classList.remove('sim-ticker-pulse'), 400);
  }

  _renderAgentPanel() {
    this.agentPanel.innerHTML = '<h3 class="sim-panel-title">Team</h3>';
    for (const [id, agent] of Object.entries(this.scenario.agents)) {
      const card = document.createElement('div');
      card.className = 'sim-agent-card';
      card.id = `agent-card-${id}`;

      // Context sources tooltip content
      const ctx = agent.context_sources || {};
      const contextHTML = ctx.persona ? `
        <div class="sim-agent-context" id="agent-context-${id}">
          <div class="sim-context-toggle" title="What informs this agent's responses">&#9432; Context</div>
          <div class="sim-context-details hidden">
            ${ctx.persona ? `<div class="sim-context-row"><span class="sim-ctx-icon">&#9632;</span> <strong>Persona:</strong> ${ctx.persona}</div>` : ''}
            ${ctx.memory ? `<div class="sim-context-row"><span class="sim-ctx-icon">&#9632;</span> <strong>Memory:</strong> ${ctx.memory}</div>` : ''}
            ${ctx.grounding ? `<div class="sim-context-row"><span class="sim-ctx-icon">&#9632;</span> <strong>Grounding:</strong> ${ctx.grounding}</div>` : ''}
          </div>
        </div>
      ` : '';

      card.innerHTML = `
        <div class="sim-agent-avatar" style="background: ${agent.color}">${agent.avatar}</div>
        <div class="sim-agent-info">
          <div class="sim-agent-name">${agent.name}</div>
          <div class="sim-agent-role">${agent.role}</div>
          <div class="sim-agent-status" id="agent-status-${id}">ACTIVE</div>
          <div class="sim-agent-score-bar">
            <div class="sim-agent-score-fill" id="agent-score-${id}" style="width: 0%"></div>
          </div>
          ${contextHTML}
        </div>
      `;

      // Toggle context details
      card.addEventListener('click', (e) => {
        const details = card.querySelector('.sim-context-details');
        if (details) details.classList.toggle('hidden');
      });

      this.agentPanel.appendChild(card);
    }
  }

  _updateAgentCard(agentId, score, status) {
    const statusEl = document.getElementById(`agent-status-${agentId}`);
    const scoreEl = document.getElementById(`agent-score-${agentId}`);
    const cardEl = document.getElementById(`agent-card-${agentId}`);

    if (statusEl) {
      statusEl.textContent = status;
      statusEl.className = 'sim-agent-status sim-status-' + status.toLowerCase().replace(/[^a-z]/g, '');
    }
    if (scoreEl) {
      scoreEl.style.width = (score * 100) + '%';
    }
    if (cardEl) {
      cardEl.classList.remove('sim-agent-speaking', 'sim-agent-gated', 'sim-agent-dormant', 'sim-agent-reengaged');
      if (status === 'DORMANT') cardEl.classList.add('sim-agent-dormant');
      if (status.includes('RE-ENGAGED') || status.includes('re-engaged')) cardEl.classList.add('sim-agent-reengaged');
    }
  }

  // ================================================================
  // Phase Navigation
  // ================================================================

  _getVisiblePhases() {
    return this.scenario.phases.filter(phase => {
      if (!phase.branch) return true;
      // Check if this branch matches a choice we've made
      for (const choiceId of Object.values(this.choices)) {
        if (phase.branch === choiceId) return true;
      }
      return false;
    });
  }

  _startPhase(idx) {
    const phases = this._getVisiblePhases();
    if (idx >= phases.length) return;

    this.currentPhaseIdx = idx;
    this.currentStepIdx = 0;
    const phase = phases[idx];

    // Update phase bar
    if (phase.name) {
      this._updatePhaseBar(phase.name, phase.description);
    }

    // Handle choice phases
    if (phase.type === 'choice') {
      this._renderChoice(phase);
      return;
    }

    // Animate steps
    this._animateNextStep();
  }

  _updatePhaseBar(name, description) {
    const phaseNames = ['DISCOVER', 'PLAN', 'EXECUTE', 'VALIDATE', 'TOPIC SHIFT', 'DEEP EXECUTE', 'OUTCOME'];
    const currentIdx = phaseNames.indexOf(name);

    this.phaseBar.innerHTML = `
      <div class="sim-phase-label">${name}</div>
      <div class="sim-phase-description">${description || ''}</div>
      <div class="sim-phase-dots">
        ${phaseNames.slice(0, 4).map((p, i) =>
          `<span class="sim-phase-dot ${i <= currentIdx ? 'active' : ''}" title="${p}"></span>`
        ).join('')}
      </div>
    `;
  }

  // ================================================================
  // Step Animation
  // ================================================================

  _animateNextStep() {
    const phases = this._getVisiblePhases();
    const phase = phases[this.currentPhaseIdx];
    if (!phase || !phase.steps) return;

    if (this.currentStepIdx >= phase.steps.length) {
      // Phase complete, move to next
      setTimeout(() => this._startPhase(this.currentPhaseIdx + 1), this.animationSpeed);
      return;
    }

    const step = phase.steps[this.currentStepIdx];
    this.currentStepIdx++;

    switch (step.type) {
      case 'message':
        this._renderMessage(step);
        break;
      case 'scoring':
        this._renderScoring(step);
        break;
      case 'gate_event':
        this._renderGateEvent(step);
        break;
      case 'narration':
        this._renderNarration(step);
        break;
      case 'outcome_summary':
        this._renderOutcome();
        return; // Don't auto-advance
      default:
        this._animateNextStep();
        return;
    }

    // Auto-advance after delay
    const delay = step.type === 'scoring' ? this.animationSpeed * 2.5 : this.animationSpeed;
    setTimeout(() => this._animateNextStep(), delay);
  }

  // ================================================================
  // Renderers
  // ================================================================

  _renderMessage(step) {
    const agent = this.scenario.agents[step.sender];
    const meta = step.meta || {};
    const tierConfig = meta.tier ? this.scenario.tier_config[meta.tier] : null;

    // Update running totals
    if (meta.tokens_in) this.totalTokensIn += meta.tokens_in;
    if (meta.tokens_out) this.totalTokensOut += meta.tokens_out;
    if (meta.tier === 'smartest' && meta.tokens_claude) {
      this.totalCost += meta.tokens_claude * 0.003 / 1000;
    }
    // Estimate what "without Cohort" would cost -- every agent responds
    if (meta.tokens_in) {
      const agentCount = Object.keys(this.scenario.agents).length;
      this.withoutCohortTokens += (meta.tokens_in + meta.tokens_out) * agentCount;
    }

    // Build meta bar HTML
    let metaHTML = '';
    if (meta.tier) {
      const tierColor = tierConfig ? tierConfig.color : '#888';
      const tierBadge = tierConfig ? tierConfig.badge : '?';
      const timeStr = meta.time_ms ? (meta.time_ms / 1000).toFixed(1) + 's' : '';
      const tokStr = meta.tokens_in ? `${meta.tokens_in.toLocaleString()} in / ${meta.tokens_out.toLocaleString()} out` : '';
      const modelStr = meta.model || '';

      // Context source pills
      let contextPills = '';
      if (meta.context_used && meta.context_used.length) {
        contextPills = meta.context_used.map(c => {
          const icons = { persona: '&#9632;', memory: '&#9670;', grounding: '&#9650;' };
          return `<span class="sim-meta-ctx" title="${c}">${icons[c] || ''}${c}</span>`;
        }).join('');
      }

      metaHTML = `
        <div class="sim-msg-meta">
          <span class="sim-meta-tier" style="background: ${tierColor}20; color: ${tierColor}; border-color: ${tierColor}40">[${tierBadge}] ${meta.tier === 'smartest' ? tierConfig.label : tierConfig.label}</span>
          <span class="sim-meta-model">${modelStr}</span>
          <span class="sim-meta-tokens">${tokStr}</span>
          <span class="sim-meta-time">${timeStr}</span>
          ${contextPills ? `<span class="sim-meta-ctx-group">${contextPills}</span>` : ''}
          ${meta.tier === 'smarter' ? '<span class="sim-meta-free">FREE</span>' : ''}
          ${meta.tier === 'smartest' && meta.tokens_claude ? `<span class="sim-meta-cost">~$${(meta.tokens_claude * 0.003 / 1000).toFixed(4)}</span>` : ''}
        </div>
      `;

      // Smartest note (expandable)
      if (meta.smartest_note) {
        metaHTML += `
          <div class="sim-msg-smartest-note">
            <span class="sim-smartest-why">Why Smartest?</span> ${meta.smartest_note}
          </div>
        `;
      }
    }

    const el = document.createElement('div');
    el.className = 'sim-message';
    el.innerHTML = `
      <div class="sim-msg-avatar" style="background: ${agent.color}">${agent.avatar}</div>
      <div class="sim-msg-body">
        <div class="sim-msg-sender">${agent.name}</div>
        <div class="sim-msg-text">${step.text}</div>
        ${metaHTML}
      </div>
    `;
    this._appendAndScroll(el);
    this._updateTicker();

    // Pulse the agent card
    const card = document.getElementById(`agent-card-${step.sender}`);
    if (card) {
      card.classList.add('sim-agent-speaking');
      setTimeout(() => card.classList.remove('sim-agent-speaking'), 600);
    }
  }

  _renderScoring(step) {
    const el = document.createElement('div');
    el.className = 'sim-scoring';

    const header = document.createElement('div');
    header.className = 'sim-scoring-header';
    header.innerHTML = `
      <span class="sim-scoring-icon">&#9878;</span>
      <span class="sim-scoring-title">${step.title}</span>
      <span class="sim-scoring-toggle" title="Click for details">&#9660;</span>
    `;

    const details = document.createElement('div');
    details.className = 'sim-scoring-details';

    if (step.explanation) {
      details.innerHTML += `<p class="sim-scoring-explanation">${step.explanation}</p>`;
    }

    // Score table
    let tableHTML = '<table class="sim-score-table"><thead><tr><th>Rank</th><th>Agent</th><th>Score</th><th>Status</th><th></th><th>Why</th></tr></thead><tbody>';
    step.scores.forEach((s, i) => {
      const agent = this.scenario.agents[s.agent];
      const decisionClass = s.decision === 'SPEAK' ? 'speak' : 'silent';
      tableHTML += `
        <tr class="sim-score-row sim-score-${decisionClass}">
          <td>${i + 1}.</td>
          <td><span class="sim-score-agent-dot" style="background:${agent.color}"></span>${agent.name}</td>
          <td class="sim-score-value">${s.score.toFixed(2)}</td>
          <td><span class="sim-status-badge sim-status-${s.status.toLowerCase().replace(/[^a-z]/g, '')}">${s.status}</span></td>
          <td><span class="sim-decision-badge sim-decision-${decisionClass}">${s.decision}</span></td>
          <td class="sim-score-reason">${s.reason}</td>
        </tr>
      `;

      // Count gated responses
      if (s.decision === 'SILENT') {
        this.gatedResponses++;
        // Estimate saved tokens (~3500 avg per gated response)
        this.withoutCohortTokens += 3500;
      }

      // Update agent card
      this._updateAgentCard(s.agent, s.score, s.status);
    });
    tableHTML += '</tbody></table>';
    details.innerHTML += tableHTML;

    if (step.insight) {
      details.innerHTML += `<div class="sim-scoring-insight"><strong>Key insight:</strong> ${step.insight}</div>`;
    }

    if (step.cost_note) {
      details.innerHTML += `<div class="sim-scoring-cost-note"><strong>Cost impact:</strong> ${step.cost_note}</div>`;
    }

    // Start collapsed for non-first scoring
    const isFirst = !this.messageArea.querySelector('.sim-scoring');
    if (!isFirst) {
      details.classList.add('collapsed');
      header.querySelector('.sim-scoring-toggle').innerHTML = '&#9654;';
    }

    header.addEventListener('click', () => {
      details.classList.toggle('collapsed');
      header.querySelector('.sim-scoring-toggle').innerHTML = details.classList.contains('collapsed') ? '&#9654;' : '&#9660;';
    });

    el.appendChild(header);
    el.appendChild(details);
    this._appendAndScroll(el);
    this._updateTicker();
  }

  _renderGateEvent(step) {
    const agent = this.scenario.agents[step.agent];
    const el = document.createElement('div');
    const isReengaged = step.decision === 'RE-ENGAGED';
    el.className = `sim-gate-event ${isReengaged ? 'sim-gate-reengaged' : 'sim-gate-silenced'}`;
    el.innerHTML = `
      <span class="sim-gate-icon">${isReengaged ? '&#10548;' : '&#10550;'}</span>
      <span class="sim-gate-agent" style="color: ${agent.color}">${agent.name}</span>
      <span class="sim-gate-decision">${step.decision}</span>
      <span class="sim-gate-reason">${step.reason}</span>
    `;
    this._appendAndScroll(el);

    // Update agent card
    if (isReengaged) {
      this._updateAgentCard(step.agent, 0.6, 'ACTIVE');
    }
  }

  _renderNarration(step) {
    const el = document.createElement('div');
    el.className = 'sim-narration';
    el.innerHTML = `<p>${step.text}</p>`;
    this._appendAndScroll(el);
  }

  _renderChoice(phase) {
    const el = document.createElement('div');
    el.className = 'sim-choice';
    el.innerHTML = `
      <div class="sim-choice-prompt">${phase.prompt}</div>
      <div class="sim-choice-options">
        ${phase.options.map(opt => `
          <button class="sim-choice-btn" data-choice-id="${phase.id}" data-option-id="${opt.id}">
            <div class="sim-choice-label">${opt.label}</div>
            <div class="sim-choice-desc">${opt.description}</div>
            <div class="sim-choice-consequence">${opt.consequence_preview}</div>
          </button>
        `).join('')}
      </div>
    `;

    el.querySelectorAll('.sim-choice-btn').forEach(btn => {
      btn.addEventListener('click', () => {
        // Record choice
        this.choices[btn.dataset.choiceId] = btn.dataset.optionId;

        // Visual feedback
        el.querySelectorAll('.sim-choice-btn').forEach(b => {
          if (b === btn) {
            b.classList.add('selected');
          } else {
            b.classList.add('not-selected');
          }
        });

        // Disable buttons
        el.querySelectorAll('.sim-choice-btn').forEach(b => b.disabled = true);

        // Continue to next phase after a beat
        setTimeout(() => {
          this._startPhase(this.currentPhaseIdx + 1);
        }, this.animationSpeed);
      });
    });

    this._appendAndScroll(el);
  }

  _renderOutcome() {
    const key1 = this.choices['choice_1'] || 'compat';
    const key2 = this.choices['choice_2'] || 'security';
    const outcomeKey = `${key1}+${key2}`;
    const outcome = this.scenario.outcomes[outcomeKey];

    if (!outcome) return;

    const cost = outcome.cost_comparison;

    const el = document.createElement('div');
    el.className = 'sim-outcome';

    // Journey visualization
    let journeyHTML = '<div class="sim-journey">';
    journeyHTML += '<h4>Agent Journeys</h4>';
    journeyHTML += '<div class="sim-journey-grid">';
    for (const [agentId, statuses] of Object.entries(outcome.agent_journeys)) {
      const agent = this.scenario.agents[agentId];
      journeyHTML += `
        <div class="sim-journey-row">
          <span class="sim-journey-agent" style="color: ${agent.color}">${agent.name}</span>
          <div class="sim-journey-statuses">
            ${statuses.map((s, i) => {
              const phaseLabels = ['DISCOVER', 'PLAN', 'EXECUTE', 'RESOLVE'];
              return `<span class="sim-journey-status sim-jstatus-${s.toLowerCase().replace(/[^a-z]/g, '')}" title="${phaseLabels[i] || ''}: ${s}">${s}</span>`;
            }).join('<span class="sim-journey-arrow">&#8594;</span>')}
          </div>
        </div>
      `;
    }
    journeyHTML += '</div></div>';

    // Cost comparison section
    let costHTML = '';
    if (cost) {
      const smarterPct = Math.round(cost.cohort_smarter_tokens / (cost.cohort_smarter_tokens + cost.cohort_smartest_tokens) * 100);
      costHTML = `
        <div class="sim-outcome-cost">
          <h4>Cost Breakdown</h4>
          <div class="sim-cost-comparison">
            <div class="sim-cost-col sim-cost-cohort">
              <div class="sim-cost-col-header">With Cohort</div>
              <div class="sim-cost-big">$${cost.cohort_total_cost.toFixed(3)}</div>
              <div class="sim-cost-detail">
                <div class="sim-cost-bar-row">
                  <span class="sim-cost-bar-label">[S+] Smarter (free)</span>
                  <div class="sim-cost-bar"><div class="sim-cost-bar-fill sim-cost-bar-green" style="width: ${smarterPct}%"></div></div>
                  <span class="sim-cost-bar-val">${cost.cohort_smarter_tokens.toLocaleString()} tok</span>
                </div>
                <div class="sim-cost-bar-row">
                  <span class="sim-cost-bar-label">[S++] Smartest</span>
                  <div class="sim-cost-bar"><div class="sim-cost-bar-fill sim-cost-bar-purple" style="width: ${100 - smarterPct}%"></div></div>
                  <span class="sim-cost-bar-val">${cost.cohort_smartest_tokens.toLocaleString()} tok</span>
                </div>
              </div>
              <div class="sim-cost-note">Smarter runs on your GPU -- $0. Only Smartest hits Claude API.</div>
            </div>
            <div class="sim-cost-vs">vs</div>
            <div class="sim-cost-col sim-cost-without">
              <div class="sim-cost-col-header">Without Cohort</div>
              <div class="sim-cost-big">$${cost.without_cohort_cost.toFixed(3)}</div>
              <div class="sim-cost-detail">
                <div class="sim-cost-bar-row">
                  <span class="sim-cost-bar-label">All agents respond</span>
                  <div class="sim-cost-bar"><div class="sim-cost-bar-fill sim-cost-bar-red" style="width: 100%"></div></div>
                  <span class="sim-cost-bar-val">${cost.without_cohort_tokens.toLocaleString()} tok</span>
                </div>
              </div>
              <div class="sim-cost-note">Every agent generates a response to every message. No gating, no scoring, no silence.</div>
            </div>
          </div>
          <div class="sim-cost-savings">
            <span class="sim-cost-savings-pct">${cost.savings_pct}%</span>
            <span class="sim-cost-savings-label">cost reduction with Cohort's scoring engine</span>
          </div>
          <div class="sim-cost-quality-note">
            And the responses are <strong>better</strong> -- each agent draws on its persona, memory from prior sessions, and grounding from the conversation. A gated agent with 14 learned facts and 5 prior sessions produces sharper output in 1 response than a generic agent does in 3.
          </div>
        </div>
      `;
    }

    el.innerHTML = `
      <h3 class="sim-outcome-title">${outcome.title}</h3>
      <p class="sim-outcome-summary">${outcome.summary}</p>
      <div class="sim-outcome-stats">
        <div class="sim-stat">
          <div class="sim-stat-value">${outcome.stats.agents_who_spoke}</div>
          <div class="sim-stat-label">Agents contributed</div>
        </div>
        <div class="sim-stat">
          <div class="sim-stat-value">${outcome.stats.messages_total}</div>
          <div class="sim-stat-label">Messages</div>
        </div>
        <div class="sim-stat">
          <div class="sim-stat-value">${outcome.stats.topic_shifts_detected}</div>
          <div class="sim-stat-label">Topic shifts</div>
        </div>
        <div class="sim-stat">
          <div class="sim-stat-value">${outcome.stats.gates_enforced}</div>
          <div class="sim-stat-label">Gates enforced</div>
        </div>
      </div>
      ${journeyHTML}
      ${costHTML}
      <div class="sim-outcome-key-moment">
        <strong>Key moment:</strong> ${outcome.key_moment}
      </div>
      <div class="sim-outcome-cta">
        <button class="sim-btn sim-btn-restart" onclick="location.reload()">Try Different Choices</button>
        <p class="sim-outcome-note">There are ${Object.keys(this.scenario.outcomes).length} possible outcomes. Your choices caused genuinely different agent behavior -- not just different text.</p>
      </div>
    `;

    this._appendAndScroll(el);
  }

  // ================================================================
  // Helpers
  // ================================================================

  _appendAndScroll(el) {
    el.style.opacity = '0';
    el.style.transform = 'translateY(12px)';
    this.messageArea.appendChild(el);

    // Trigger animation
    requestAnimationFrame(() => {
      el.style.transition = 'opacity 0.4s ease, transform 0.4s ease';
      el.style.opacity = '1';
      el.style.transform = 'translateY(0)';
    });

    // Scroll to bottom
    this.messageArea.scrollTop = this.messageArea.scrollHeight;
  }
}

// ================================================================
// Bootstrap
// ================================================================

document.addEventListener('DOMContentLoaded', async () => {
  const container = document.getElementById('simulator');
  if (!container) return;

  try {
    const resp = await fetch('scenario-api-redesign.json');
    const scenario = await resp.json();
    new CohortSimulator(container, scenario);
  } catch (err) {
    container.innerHTML = `<p style="color: #ff6b6b; padding: 2rem;">Failed to load scenario: ${err.message}</p>`;
  }
});
