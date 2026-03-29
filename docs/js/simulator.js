/**
 * Cohort Interactive Simulator
 * Ported from cohort/website/cohort/simulator/simulator.js
 *
 * Replays pre-recorded multi-agent conversations with branching choices.
 * No LLM calls -- all content is pre-generated JSON.
 */

class CohortSimulator {
  constructor(containerEl, scenario, onBack) {
    this.container = containerEl;
    this.scenario = scenario;
    this.onBack = onBack;           // callback to return to scenario picker
    this.choices = {};
    this.currentPhaseIdx = 0;
    this.currentStepIdx = 0;
    this.isAnimating = false;
    this.animationSpeed = 800;
    this.activeTooltip = null;
    this.destroyed = false;

    // Running cost/token counters
    this.totalTokensIn = 0;
    this.totalTokensOut = 0;
    this.totalCost = 0;
    this.totalTimeSaved = 0;
    this.gatedResponses = 0;
    this.withoutCohortTokens = 0;

    this._buildUI();
    this._renderAgentPanel();
    this._startPhase(0);
  }

  destroy() {
    this.destroyed = true;
    this.container.innerHTML = '';
  }

  // ================================================================
  // UI Construction
  // ================================================================

  _buildUI() {
    this.container.innerHTML = '';
    this.container.className = 'sim-container';

    // Back button
    if (this.onBack) {
      var backBtn = document.createElement('button');
      backBtn.className = 'sim-back-btn';
      backBtn.innerHTML = '&#8592; Try Another Scenario';
      backBtn.addEventListener('click', () => {
        this.destroy();
        this.onBack();
      });
      this.container.appendChild(backBtn);
    }

    // Header
    var header = document.createElement('div');
    header.className = 'sim-header';
    header.innerHTML =
      '<h2 class="sim-title">' + this.scenario.title + '</h2>' +
      '<p class="sim-description">' + this.scenario.description + '</p>';
    this.container.appendChild(header);

    // Cost ticker bar (sticky)
    this.costTicker = document.createElement('div');
    this.costTicker.className = 'sim-cost-ticker';
    this.costTicker.innerHTML = this._buildTickerHTML();
    this.container.appendChild(this.costTicker);

    // Main layout: agents panel + conversation
    var layout = document.createElement('div');
    layout.className = 'sim-layout';

    // Agent sidebar
    this.agentPanel = document.createElement('div');
    this.agentPanel.className = 'sim-agents-panel';
    layout.appendChild(this.agentPanel);

    // Conversation area
    var convWrapper = document.createElement('div');
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
    var controls = document.createElement('div');
    controls.className = 'sim-controls';
    controls.innerHTML =
      '<button class="sim-btn sim-btn-speed" data-speed="fast" title="Fast playback">' +
        '<span class="sim-speed-icon">&#9654;&#9654;</span>' +
      '</button>' +
      '<button class="sim-btn sim-btn-speed active" data-speed="normal" title="Normal playback">' +
        '<span class="sim-speed-icon">&#9654;</span>' +
      '</button>' +
      '<button class="sim-btn sim-btn-speed" data-speed="slow" title="Slow playback">' +
        '<span class="sim-speed-icon">&#9646;&#9654;</span>' +
      '</button>';
    var self = this;
    controls.querySelectorAll('.sim-btn-speed').forEach(function(btn) {
      btn.addEventListener('click', function() {
        controls.querySelectorAll('.sim-btn-speed').forEach(function(b) { b.classList.remove('active'); });
        btn.classList.add('active');
        var speeds = { fast: 400, normal: 800, slow: 1500 };
        self.animationSpeed = speeds[btn.dataset.speed];
      });
    });
    this.container.appendChild(controls);
  }

  _buildTickerHTML() {
    return '<div class="sim-ticker-section">' +
        '<div class="sim-ticker-label">Tokens used</div>' +
        '<div class="sim-ticker-value" id="ticker-tokens">' + (this.totalTokensIn + this.totalTokensOut).toLocaleString() + '</div>' +
      '</div>' +
      '<div class="sim-ticker-divider"></div>' +
      '<div class="sim-ticker-section">' +
        '<div class="sim-ticker-label">Cohort cost</div>' +
        '<div class="sim-ticker-value sim-ticker-green" id="ticker-cost">$' + this.totalCost.toFixed(3) + '</div>' +
      '</div>' +
      '<div class="sim-ticker-divider"></div>' +
      '<div class="sim-ticker-section">' +
        '<div class="sim-ticker-label">Standard approach</div>' +
        '<div class="sim-ticker-value sim-ticker-red" id="ticker-wasted">$' + (this.withoutCohortTokens * 0.003 / 1000).toFixed(3) + '</div>' +
      '</div>' +
      '<div class="sim-ticker-divider"></div>' +
      '<div class="sim-ticker-section">' +
        '<div class="sim-ticker-label">Responses gated</div>' +
        '<div class="sim-ticker-value sim-ticker-green" id="ticker-gated">' + this.gatedResponses + '</div>' +
      '</div>';
  }

  _updateTicker() {
    var el = function(id) { return document.getElementById(id); };
    var tokEl = el('ticker-tokens');
    var costEl = el('ticker-cost');
    var wastedEl = el('ticker-wasted');
    var gatedEl = el('ticker-gated');

    if (tokEl) tokEl.textContent = (this.totalTokensIn + this.totalTokensOut).toLocaleString();
    if (costEl) costEl.textContent = '$' + this.totalCost.toFixed(3);
    if (wastedEl) {
      var wastedCost = this.withoutCohortTokens * 0.003 / 1000;
      wastedEl.textContent = '$' + wastedCost.toFixed(3);
    }
    if (gatedEl) gatedEl.textContent = this.gatedResponses;

    // Pulse animation on update
    this.costTicker.classList.add('sim-ticker-pulse');
    var ticker = this.costTicker;
    setTimeout(function() { ticker.classList.remove('sim-ticker-pulse'); }, 400);
  }

  _renderAgentPanel() {
    this.agentPanel.innerHTML = '<h3 class="sim-panel-title">Team</h3>';
    var scenario = this.scenario;
    for (var id in scenario.agents) {
      if (!scenario.agents.hasOwnProperty(id)) continue;
      var agent = scenario.agents[id];
      var card = document.createElement('div');
      card.className = 'sim-agent-card';
      card.id = 'agent-card-' + id;

      // Context sources tooltip content
      var ctx = agent.context_sources || {};
      var contextHTML = ctx.persona ? (
        '<div class="sim-agent-context" id="agent-context-' + id + '">' +
          '<div class="sim-context-toggle" title="What informs this agent\'s responses">&#9432; Context</div>' +
          '<div class="sim-context-details hidden">' +
            (ctx.persona ? '<div class="sim-context-row"><span class="sim-ctx-icon">&#9632;</span> <strong>Persona:</strong> ' + ctx.persona + '</div>' : '') +
            (ctx.memory ? '<div class="sim-context-row"><span class="sim-ctx-icon">&#9632;</span> <strong>Memory:</strong> ' + ctx.memory + '</div>' : '') +
            (ctx.grounding ? '<div class="sim-context-row"><span class="sim-ctx-icon">&#9632;</span> <strong>Grounding:</strong> ' + ctx.grounding + '</div>' : '') +
          '</div>' +
        '</div>'
      ) : '';

      card.innerHTML =
        '<div class="sim-agent-avatar" style="background: ' + agent.color + '">' + agent.avatar + '</div>' +
        '<div class="sim-agent-info">' +
          '<div class="sim-agent-name">' + agent.name + '</div>' +
          '<div class="sim-agent-role">' + agent.role + '</div>' +
          '<div class="sim-agent-status" id="agent-status-' + id + '">ACTIVE</div>' +
          '<div class="sim-agent-score-bar">' +
            '<div class="sim-agent-score-fill" id="agent-score-' + id + '" style="width: 0%"></div>' +
          '</div>' +
          contextHTML +
        '</div>';

      // Toggle context details
      card.addEventListener('click', function() {
        var details = this.querySelector('.sim-context-details');
        if (details) details.classList.toggle('hidden');
      });

      this.agentPanel.appendChild(card);
    }
  }

  _updateAgentCard(agentId, score, status) {
    var statusEl = document.getElementById('agent-status-' + agentId);
    var scoreEl = document.getElementById('agent-score-' + agentId);
    var cardEl = document.getElementById('agent-card-' + agentId);

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
      if (status.indexOf('RE-ENGAGED') >= 0 || status.indexOf('re-engaged') >= 0) cardEl.classList.add('sim-agent-reengaged');
    }
  }

  // ================================================================
  // Phase Navigation
  // ================================================================

  _getVisiblePhases() {
    var choices = this.choices;
    return this.scenario.phases.filter(function(phase) {
      if (!phase.branch) return true;
      for (var choiceId in choices) {
        if (choices.hasOwnProperty(choiceId) && phase.branch === choices[choiceId]) return true;
      }
      return false;
    });
  }

  _startPhase(idx) {
    if (this.destroyed) return;
    var phases = this._getVisiblePhases();
    if (idx >= phases.length) return;

    this.currentPhaseIdx = idx;
    this.currentStepIdx = 0;
    var phase = phases[idx];

    if (phase.name) {
      this._updatePhaseBar(phase.name, phase.description);
    }

    if (phase.type === 'choice') {
      this._renderChoice(phase);
      return;
    }

    this._animateNextStep();
  }

  _updatePhaseBar(name, description) {
    var phaseNames = ['DISCOVER', 'PLAN', 'EXECUTE', 'VALIDATE', 'TOPIC SHIFT', 'DEEP EXECUTE', 'OUTCOME'];
    var currentIdx = phaseNames.indexOf(name);

    this.phaseBar.innerHTML =
      '<div class="sim-phase-label">' + name + '</div>' +
      '<div class="sim-phase-description">' + (description || '') + '</div>' +
      '<div class="sim-phase-dots">' +
        phaseNames.slice(0, 4).map(function(p, i) {
          return '<span class="sim-phase-dot ' + (i <= currentIdx ? 'active' : '') + '" title="' + p + '"></span>';
        }).join('') +
      '</div>';
  }

  // ================================================================
  // Step Animation
  // ================================================================

  _animateNextStep() {
    if (this.destroyed) return;
    var phases = this._getVisiblePhases();
    var phase = phases[this.currentPhaseIdx];
    if (!phase || !phase.steps) return;

    if (this.currentStepIdx >= phase.steps.length) {
      var self = this;
      setTimeout(function() { self._startPhase(self.currentPhaseIdx + 1); }, this.animationSpeed);
      return;
    }

    var step = phase.steps[this.currentStepIdx];
    this.currentStepIdx++;

    switch (step.type) {
      case 'message':
        this._renderMessage(step);
        break;
      case 'scoring':
        this._renderScoring(step);
        if (!this.autoPlayScoring) {
          this._renderContinueButton();
          return; // Wait for click to advance
        }
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

    var self = this;
    setTimeout(function() { self._animateNextStep(); }, this.animationSpeed);
  }

  // ================================================================
  // Renderers
  // ================================================================

  _renderMessage(step) {
    var agent = this.scenario.agents[step.sender];
    var meta = step.meta || {};
    var tierConfig = meta.tier ? this.scenario.tier_config[meta.tier] : null;

    // Update running totals
    if (meta.tokens_in) this.totalTokensIn += meta.tokens_in;
    if (meta.tokens_out) this.totalTokensOut += meta.tokens_out;
    if (meta.tier === 'smartest' && meta.tokens_claude) {
      this.totalCost += meta.tokens_claude * 0.003 / 1000;
    }
    if (meta.tokens_in) {
      var agentCount = Object.keys(this.scenario.agents).length;
      this.withoutCohortTokens += (meta.tokens_in + meta.tokens_out) * agentCount;
    }

    // Build meta bar HTML
    var metaHTML = '';
    if (meta.tier) {
      var tierColor = tierConfig ? tierConfig.color : '#888';
      var tierBadge = tierConfig ? tierConfig.badge : '?';
      var timeStr = meta.time_ms ? (meta.time_ms / 1000).toFixed(1) + 's' : '';
      var tokStr = meta.tokens_in ? meta.tokens_in.toLocaleString() + ' in / ' + meta.tokens_out.toLocaleString() + ' out' : '';
      var modelStr = meta.model || '';

      var contextPills = '';
      if (meta.context_used && meta.context_used.length) {
        contextPills = meta.context_used.map(function(c) {
          var icons = { persona: '&#9632;', memory: '&#9670;', grounding: '&#9650;' };
          return '<span class="sim-meta-ctx" title="' + c + '">' + (icons[c] || '') + c + '</span>';
        }).join('');
      }

      metaHTML =
        '<div class="sim-msg-meta">' +
          '<span class="sim-meta-tier" style="background: ' + tierColor + '20; color: ' + tierColor + '; border-color: ' + tierColor + '40">[' + tierBadge + '] ' + tierConfig.label + '</span>' +
          '<span class="sim-meta-model">' + modelStr + '</span>' +
          '<span class="sim-meta-tokens">' + tokStr + '</span>' +
          '<span class="sim-meta-time">' + timeStr + '</span>' +
          (contextPills ? '<span class="sim-meta-ctx-group">' + contextPills + '</span>' : '') +
          (meta.tier === 'smarter' ? '<span class="sim-meta-free">FREE</span>' : '') +
          (meta.tier === 'smartest' && meta.tokens_claude ? '<span class="sim-meta-cost">~$' + (meta.tokens_claude * 0.003 / 1000).toFixed(4) + '</span>' : '') +
        '</div>';

      if (meta.smartest_note) {
        metaHTML +=
          '<div class="sim-msg-smartest-note">' +
            '<span class="sim-smartest-why">Why Smartest?</span> ' + meta.smartest_note +
          '</div>';
      }
    }

    var el = document.createElement('div');
    el.className = 'sim-message';
    el.innerHTML =
      '<div class="sim-msg-avatar" style="background: ' + agent.color + '">' + agent.avatar + '</div>' +
      '<div class="sim-msg-body">' +
        '<div class="sim-msg-sender">' + agent.name + '</div>' +
        '<div class="sim-msg-text">' + step.text + '</div>' +
        metaHTML +
      '</div>';
    this._appendAndScroll(el);
    this._updateTicker();

    // Pulse the agent card
    var card = document.getElementById('agent-card-' + step.sender);
    if (card) {
      card.classList.add('sim-agent-speaking');
      setTimeout(function() { card.classList.remove('sim-agent-speaking'); }, 600);
    }
  }

  _renderScoring(step) {
    var scenario = this.scenario;
    var self = this;
    var el = document.createElement('div');
    el.className = 'sim-scoring';

    var header = document.createElement('div');
    header.className = 'sim-scoring-header';
    header.innerHTML =
      '<span class="sim-scoring-icon">&#9878;</span>' +
      '<span class="sim-scoring-title">' + step.title + '</span>' +
      '<span class="sim-scoring-toggle" title="Click for details">&#9660;</span>';

    var details = document.createElement('div');
    details.className = 'sim-scoring-details';

    if (step.explanation) {
      details.innerHTML += '<p class="sim-scoring-explanation">' + step.explanation + '</p>';
    }

    var tableHTML = '<table class="sim-score-table"><thead><tr><th>Rank</th><th>Agent</th><th>Score</th><th>Status</th><th></th><th>Why</th></tr></thead><tbody>';
    step.scores.forEach(function(s, i) {
      var agent = scenario.agents[s.agent];
      var decisionClass = s.decision === 'SPEAK' ? 'speak' : 'silent';
      tableHTML +=
        '<tr class="sim-score-row sim-score-' + decisionClass + '">' +
          '<td>' + (i + 1) + '.</td>' +
          '<td><span class="sim-score-agent-dot" style="background:' + agent.color + '"></span>' + agent.name + '</td>' +
          '<td class="sim-score-value">' + s.score.toFixed(2) + '</td>' +
          '<td><span class="sim-status-badge sim-status-' + s.status.toLowerCase().replace(/[^a-z]/g, '') + '">' + s.status + '</span></td>' +
          '<td><span class="sim-decision-badge sim-decision-' + decisionClass + '">' + s.decision + '</span></td>' +
          '<td class="sim-score-reason">' + s.reason + '</td>' +
        '</tr>';

      if (s.decision === 'SILENT') {
        self.gatedResponses++;
        self.withoutCohortTokens += 3500;
      }

      self._updateAgentCard(s.agent, s.score, s.status);
    });
    tableHTML += '</tbody></table>';
    details.innerHTML += tableHTML;

    if (step.insight) {
      details.innerHTML += '<div class="sim-scoring-insight"><strong>Key insight:</strong> ' + step.insight + '</div>';
    }

    if (step.cost_note) {
      details.innerHTML += '<div class="sim-scoring-cost-note"><strong>Cost impact:</strong> ' + step.cost_note + '</div>';
    }

    // Start collapsed for non-first scoring
    var isFirst = !this.messageArea.querySelector('.sim-scoring');
    if (!isFirst) {
      details.classList.add('collapsed');
      header.querySelector('.sim-scoring-toggle').innerHTML = '&#9654;';
    }

    header.addEventListener('click', function() {
      details.classList.toggle('collapsed');
      header.querySelector('.sim-scoring-toggle').innerHTML = details.classList.contains('collapsed') ? '&#9654;' : '&#9660;';
    });

    el.appendChild(header);
    el.appendChild(details);
    this._appendAndScroll(el);
    this._updateTicker();
  }

  _renderGateEvent(step) {
    var agent = this.scenario.agents[step.agent];
    var el = document.createElement('div');
    var isReengaged = step.decision === 'RE-ENGAGED';
    el.className = 'sim-gate-event ' + (isReengaged ? 'sim-gate-reengaged' : 'sim-gate-silenced');
    el.innerHTML =
      '<span class="sim-gate-icon">' + (isReengaged ? '&#10548;' : '&#10550;') + '</span>' +
      '<span class="sim-gate-agent" style="color: ' + agent.color + '">' + agent.name + '</span>' +
      '<span class="sim-gate-decision">' + step.decision + '</span>' +
      '<span class="sim-gate-reason">' + step.reason + '</span>';
    this._appendAndScroll(el);

    if (isReengaged) {
      this._updateAgentCard(step.agent, 0.6, 'ACTIVE');
    }
  }

  _renderContinueButton() {
    var self = this;
    var wrap = document.createElement('div');
    wrap.className = 'sim-continue-wrap';
    wrap.innerHTML =
      '<button class="sim-btn sim-continue-btn">Continue</button>' +
      '<button class="sim-btn sim-skip-btn">Auto-play scoring</button>' +
      '<span class="sim-continue-hint">The scores are where the magic happens</span>';
    wrap.querySelector('.sim-continue-btn').addEventListener('click', function() {
      wrap.remove();
      self._animateNextStep();
    });
    wrap.querySelector('.sim-skip-btn').addEventListener('click', function() {
      wrap.remove();
      self.autoPlayScoring = true;
      self._animateNextStep();
    });
    this._appendAndScroll(wrap);
  }

  _renderNarration(step) {
    var el = document.createElement('div');
    el.className = 'sim-narration';
    el.innerHTML = '<p>' + step.text + '</p>';
    this._appendAndScroll(el);
  }

  _renderChoice(phase) {
    var self = this;
    var el = document.createElement('div');
    el.className = 'sim-choice';
    el.innerHTML =
      '<div class="sim-choice-prompt">' + phase.prompt + '</div>' +
      '<div class="sim-choice-options">' +
        phase.options.map(function(opt) {
          return '<button class="sim-choice-btn" data-choice-id="' + phase.id + '" data-option-id="' + opt.id + '">' +
            '<div class="sim-choice-label">' + opt.label + '</div>' +
            '<div class="sim-choice-desc">' + opt.description + '</div>' +
            '<div class="sim-choice-consequence">' + opt.consequence_preview + '</div>' +
          '</button>';
        }).join('') +
      '</div>';

    el.querySelectorAll('.sim-choice-btn').forEach(function(btn) {
      btn.addEventListener('click', function() {
        self.choices[btn.dataset.choiceId] = btn.dataset.optionId;

        el.querySelectorAll('.sim-choice-btn').forEach(function(b) {
          if (b === btn) {
            b.classList.add('selected');
          } else {
            b.classList.add('not-selected');
          }
        });

        el.querySelectorAll('.sim-choice-btn').forEach(function(b) { b.disabled = true; });

        setTimeout(function() {
          self._startPhase(self.currentPhaseIdx + 1);
        }, self.animationSpeed);
      });
    });

    this._appendAndScroll(el);
  }

  _renderOutcome() {
    var key1 = this.choices['choice_1'] || 'compat';
    var key2 = this.choices['choice_2'] || 'security';
    var outcomeKey = key1 + '+' + key2;
    var outcome = this.scenario.outcomes[outcomeKey];

    if (!outcome) return;

    var cost = outcome.cost_comparison;
    var scenario = this.scenario;
    var self = this;

    // Journey visualization
    var journeyHTML = '<div class="sim-journey">';
    journeyHTML += '<h4>Agent Journeys</h4>';
    journeyHTML += '<div class="sim-journey-grid">';
    for (var agentId in outcome.agent_journeys) {
      if (!outcome.agent_journeys.hasOwnProperty(agentId)) continue;
      var statuses = outcome.agent_journeys[agentId];
      var agent = scenario.agents[agentId];
      var phaseLabels = ['DISCOVER', 'PLAN', 'EXECUTE', 'RESOLVE'];
      journeyHTML +=
        '<div class="sim-journey-row">' +
          '<span class="sim-journey-agent" style="color: ' + agent.color + '">' + agent.name + '</span>' +
          '<div class="sim-journey-statuses">' +
            statuses.map(function(s, i) {
              return '<span class="sim-journey-status sim-jstatus-' + s.toLowerCase().replace(/[^a-z]/g, '') + '" title="' + (phaseLabels[i] || '') + ': ' + s + '">' + s + '</span>';
            }).join('<span class="sim-journey-arrow">&#8594;</span>') +
          '</div>' +
        '</div>';
    }
    journeyHTML += '</div></div>';

    // Cost comparison section
    var costHTML = '';
    if (cost) {
      var smarterPct = Math.round(cost.cohort_smarter_tokens / (cost.cohort_smarter_tokens + cost.cohort_smartest_tokens) * 100);
      costHTML =
        '<div class="sim-outcome-cost">' +
          '<h4>Cost Breakdown</h4>' +
          '<div class="sim-cost-comparison">' +
            '<div class="sim-cost-col sim-cost-cohort">' +
              '<div class="sim-cost-col-header">With Cohort</div>' +
              '<div class="sim-cost-big">$' + cost.cohort_total_cost.toFixed(3) + '</div>' +
              '<div class="sim-cost-detail">' +
                '<div class="sim-cost-bar-row">' +
                  '<span class="sim-cost-bar-label">[S+] Smarter (free)</span>' +
                  '<div class="sim-cost-bar"><div class="sim-cost-bar-fill sim-cost-bar-green" style="width: ' + smarterPct + '%"></div></div>' +
                  '<span class="sim-cost-bar-val">' + cost.cohort_smarter_tokens.toLocaleString() + ' tok</span>' +
                '</div>' +
                '<div class="sim-cost-bar-row">' +
                  '<span class="sim-cost-bar-label">[S++] Smartest</span>' +
                  '<div class="sim-cost-bar"><div class="sim-cost-bar-fill sim-cost-bar-purple" style="width: ' + (100 - smarterPct) + '%"></div></div>' +
                  '<span class="sim-cost-bar-val">' + cost.cohort_smartest_tokens.toLocaleString() + ' tok</span>' +
                '</div>' +
              '</div>' +
              '<div class="sim-cost-note">Smarter runs on your GPU -- $0. Only Smartest hits Claude API.</div>' +
            '</div>' +
            '<div class="sim-cost-vs">vs</div>' +
            '<div class="sim-cost-col sim-cost-without">' +
              '<div class="sim-cost-col-header">Without Cohort</div>' +
              '<div class="sim-cost-big">$' + cost.without_cohort_cost.toFixed(3) + '</div>' +
              '<div class="sim-cost-detail">' +
                '<div class="sim-cost-bar-row">' +
                  '<span class="sim-cost-bar-label">All agents respond</span>' +
                  '<div class="sim-cost-bar"><div class="sim-cost-bar-fill sim-cost-bar-red" style="width: 100%"></div></div>' +
                  '<span class="sim-cost-bar-val">' + cost.without_cohort_tokens.toLocaleString() + ' tok</span>' +
                '</div>' +
              '</div>' +
              '<div class="sim-cost-note">Every agent generates a response to every message. No gating, no scoring, no silence.</div>' +
            '</div>' +
          '</div>' +
          '<div class="sim-cost-savings">' +
            '<span class="sim-cost-savings-pct">' + cost.savings_pct + '%</span>' +
            '<span class="sim-cost-savings-label">cost reduction with Cohort\'s scoring engine</span>' +
          '</div>' +
          '<div class="sim-cost-quality-note">' +
            'And the responses are <strong>better</strong> -- each agent draws on its persona, memory from prior sessions, and grounding from the conversation. A gated agent with 14 learned facts and 5 prior sessions produces sharper output in 1 response than a generic agent does in 3.' +
          '</div>' +
        '</div>';
    }

    var el = document.createElement('div');
    el.className = 'sim-outcome';
    el.innerHTML =
      '<h3 class="sim-outcome-title">' + outcome.title + '</h3>' +
      '<p class="sim-outcome-summary">' + outcome.summary + '</p>' +
      '<div class="sim-outcome-stats">' +
        '<div class="sim-stat"><div class="sim-stat-value">' + outcome.stats.agents_who_spoke + '</div><div class="sim-stat-label">Agents contributed</div></div>' +
        '<div class="sim-stat"><div class="sim-stat-value">' + outcome.stats.messages_total + '</div><div class="sim-stat-label">Messages</div></div>' +
        '<div class="sim-stat"><div class="sim-stat-value">' + outcome.stats.topic_shifts_detected + '</div><div class="sim-stat-label">Topic shifts</div></div>' +
        '<div class="sim-stat"><div class="sim-stat-value">' + outcome.stats.gates_enforced + '</div><div class="sim-stat-label">Gates enforced</div></div>' +
      '</div>' +
      journeyHTML +
      costHTML +
      '<div class="sim-outcome-key-moment"><strong>Key moment:</strong> ' + outcome.key_moment + '</div>' +
      '<div class="sim-outcome-cta">' +
        '<button class="sim-btn sim-btn-restart" id="sim-restart-btn">Try Another Scenario</button>' +
        '<p class="sim-outcome-note">There are ' + Object.keys(scenario.outcomes).length + ' possible outcomes. Your choices caused genuinely different agent behavior -- not just different text.</p>' +
      '</div>';

    this._appendAndScroll(el);

    // Wire up restart button
    var restartBtn = document.getElementById('sim-restart-btn');
    if (restartBtn && this.onBack) {
      restartBtn.addEventListener('click', function() {
        self.destroy();
        self.onBack();
      });
    } else if (restartBtn) {
      restartBtn.addEventListener('click', function() { location.reload(); });
    }
  }

  // ================================================================
  // Helpers
  // ================================================================

  _appendAndScroll(el) {
    if (this.destroyed) return;
    el.style.opacity = '0';
    el.style.transform = 'translateY(12px)';
    this.messageArea.appendChild(el);

    requestAnimationFrame(function() {
      el.style.transition = 'opacity 0.4s ease, transform 0.4s ease';
      el.style.opacity = '1';
      el.style.transform = 'translateY(0)';
    });

    this.messageArea.scrollTop = this.messageArea.scrollHeight;
  }
}

// Export to window for use by simulator-data.js
window.CohortSimulator = CohortSimulator;
