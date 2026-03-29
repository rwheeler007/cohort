/**
 * Cohort ROI Calculator
 * Pure JS, no dependencies. Under 5KB.
 * Reads pricing data from data/pricing-comparison.json.
 */
(function () {
    'use strict';

    // All pricing from published sources (March 2026):
    // GPT-4o: openai.com/api/pricing -- $2.50/$10.00 per 1M tokens
    // Claude Sonnet 4.6: platform.claude.com -- $3.00/$15.00 per 1M tokens
    var DEFAULTS = {
        agents: 5,
        turns: 20,
        conversations: 3,
        // Per-turn token profile (realistic agent call)
        firstCallInput: 3000,   // system prompt + persona + memory + first message
        firstCallOutput: 2000,  // full agent response
        inputGrowthPerTurn: 700,  // each turn adds ~700 input (prior messages resent)
        outputPerTurn: 800,       // response size stays roughly constant
        // Claude Sonnet 4.6 pricing (per 1K tokens, for Cohort escalation)
        claudeIn: 0.003,    // $3.00 per 1M = $0.003 per 1K
        claudeOut: 0.015,   // $15.00 per 1M = $0.015 per 1K
        // GPT-4o pricing (per 1K tokens, for cloud-only competitor calc)
        gpt4In: 0.0025,     // $2.50 per 1M = $0.0025 per 1K
        gpt4Out: 0.01,      // $10.00 per 1M = $0.01 per 1K
        // Cohort specifics
        cohortApiPct: 5,
        distillReduction: 70
    };

    function $(id) { return document.getElementById(id); }

    function formatCost(n) {
        if (n < 0.01) return '$0.00';
        if (n < 1) return '$' + n.toFixed(2);
        if (n >= 1000) return '$' + n.toFixed(0).replace(/\B(?=(\d{3})+(?!\d))/g, ',');
        return '$' + n.toFixed(2);
    }

    function calculate() {
        var agents = parseInt($('roi-agents').value) || DEFAULTS.agents;
        var turns = parseInt($('roi-turns').value) || DEFAULTS.turns;
        var convos = parseInt($('roi-conversations').value) || DEFAULTS.conversations;

        // Cloud-only: cumulative context -- every API call resends the full
        // conversation history. Turn 1 sends 3K input. Turn 10 sends ~10K+.
        // Each agent in the conversation contributes to this growth.
        //
        // Per-conversation input sum (arithmetic series):
        //   turn 1: firstCallInput
        //   turn 2: firstCallInput + inputGrowthPerTurn
        //   turn N: firstCallInput + (N-1) * inputGrowthPerTurn
        //   total = N * firstCallInput + inputGrowthPerTurn * N*(N-1)/2
        var perConvoInputTotal = turns * DEFAULTS.firstCallInput +
            DEFAULTS.inputGrowthPerTurn * turns * (turns - 1) / 2;
        var perConvoOutputTotal = turns * DEFAULTS.outputPerTurn;

        // Scale by agents and conversations per day
        var cloudInputDaily = perConvoInputTotal * agents * convos;
        var cloudOutputDaily = perConvoOutputTotal * agents * convos;

        // CrewAI / LangGraph: every turn is a cloud API call with full history
        var competitorDaily = (cloudInputDaily / 1000) * DEFAULTS.gpt4In +
                              (cloudOutputDaily / 1000) * DEFAULTS.gpt4Out;
        var competitorMonthly = competitorDaily * 22; // work days

        // Cohort: 95% runs locally (free). Only 5% escalate to API.
        // Escalated calls use distillation -- Cohort sends a focused briefing,
        // not the raw cumulative history. No context snowball.
        var totalTurns = agents * turns * convos;
        var escalatedTurns = totalTurns * (DEFAULTS.cohortApiPct / 100);
        // Distilled input: flat per-turn (no accumulation), reduced 70%
        var distilledPerTurn = (DEFAULTS.firstCallInput + DEFAULTS.outputPerTurn) *
            (1 - DEFAULTS.distillReduction / 100);
        var escalatedInput = escalatedTurns * distilledPerTurn * 0.6;
        var escalatedOutput = escalatedTurns * distilledPerTurn * 0.4;
        var cohortDaily = (escalatedInput / 1000) * DEFAULTS.claudeIn +
                          (escalatedOutput / 1000) * DEFAULTS.claudeOut;
        var cohortMonthly = cohortDaily * 22;

        // Savings
        var savingsMonthly = competitorMonthly - cohortMonthly;
        var savingsMultiplier = competitorMonthly > 0 ? Math.round(competitorMonthly / Math.max(cohortMonthly, 0.01)) : 0;

        // Update DOM
        updateValue('roi-result-competitor', formatCost(competitorMonthly) + '/mo');
        updateValue('roi-result-cohort', formatCost(cohortMonthly) + '/mo');
        updateValue('roi-result-savings', formatCost(savingsMonthly) + '/mo');
        updateValue('roi-result-multiplier', savingsMultiplier + 'x');

        // Update slider value displays
        updateValue('roi-agents-val', agents);
        updateValue('roi-turns-val', turns);
        updateValue('roi-conversations-val', convos);
    }

    function updateValue(id, val) {
        var el = $(id);
        if (el) el.textContent = val;
    }

    function init() {
        var form = $('roi-calculator-form');
        if (!form) return;

        // Set initial values
        $('roi-agents').value = DEFAULTS.agents;
        $('roi-turns').value = DEFAULTS.turns;
        $('roi-conversations').value = DEFAULTS.conversations;

        // Bind events
        ['roi-agents', 'roi-turns', 'roi-conversations'].forEach(function (id) {
            var el = $(id);
            if (el) {
                el.addEventListener('input', calculate);
            }
        });

        // Initial calculation
        calculate();
    }

    // Show math toggle
    function initShowMath() {
        var btn = $('roi-show-math');
        var detail = $('roi-math-detail');
        if (!btn || !detail) return;
        btn.addEventListener('click', function () {
            var expanded = detail.style.display !== 'none';
            detail.style.display = expanded ? 'none' : 'block';
            btn.textContent = expanded ? '[>>] Show your math' : '[<<] Hide math';
            btn.setAttribute('aria-expanded', !expanded);
        });
    }

    document.addEventListener('DOMContentLoaded', function () {
        init();
        initShowMath();
    });
})();
