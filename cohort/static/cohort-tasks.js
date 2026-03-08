/**
 * Cohort Tasks & Schedules UI
 *
 * Separate module for schedule management, task sub-tabs, and the
 * redesigned assign/schedule modal. Depends on shared utilities
 * exposed by cohort.js (state, escapeHtml, formatTimeAgo, showToast,
 * switchChannel, populateAgentSelect).
 */

// =====================================================================
// CohortTasks namespace
// =====================================================================

const CohortTasks = (() => {
    'use strict';

    // -- Local state --
    let currentView = 'active';  // 'active' | 'scheduled' | 'completed'
    let schedules = [];
    let schedulerStatus = { running: false };

    // -- DOM cache (populated on init) --
    let _dom = {};

    function _initDom() {
        _dom = {
            // Sub-tabs
            subTabs: document.querySelectorAll('.task-sub-tab'),
            viewActive: document.getElementById('task-view-active'),
            viewScheduled: document.getElementById('task-view-scheduled'),
            viewCompleted: document.getElementById('task-view-completed'),
            scheduleList: document.getElementById('schedule-list'),
            completedTaskList: document.getElementById('completed-task-list'),
            scheduleBadge: document.getElementById('schedule-badge'),
            // Scheduler heartbeat
            schedulerDot: document.getElementById('scheduler-dot'),
            schedulerText: document.getElementById('scheduler-text'),
            // Modal
            modalTabs: document.querySelectorAll('.modal-tab'),
            assignForm: document.getElementById('assign-task-form'),
            scheduleForm: document.getElementById('schedule-task-form'),
            scheduleAgentSelect: document.getElementById('schedule-agent-select'),
            scheduleDescInput: document.getElementById('schedule-description-input'),
            scheduleDescCount: document.getElementById('schedule-desc-count'),
            schedulePrioritySelect: document.getElementById('schedule-priority-select'),
            customFields: document.getElementById('schedule-custom-fields'),
            typeSelect: document.getElementById('schedule-type-select'),
            intervalGroup: document.getElementById('schedule-interval-group'),
            cronGroup: document.getElementById('schedule-cron-group'),
            intervalInput: document.getElementById('schedule-interval-input'),
            cronInput: document.getElementById('schedule-cron-input'),
        };
    }

    // =====================================================================
    // Sub-tab switching
    // =====================================================================

    function switchView(view) {
        currentView = view;

        // Update tab active states
        _dom.subTabs.forEach(tab => {
            const isActive = tab.dataset.taskView === view;
            tab.classList.toggle('active', isActive);
            tab.setAttribute('aria-selected', isActive ? 'true' : 'false');
        });

        // Show/hide views
        if (_dom.viewActive) _dom.viewActive.style.display = view === 'active' ? '' : 'none';
        if (_dom.viewScheduled) _dom.viewScheduled.style.display = view === 'scheduled' ? '' : 'none';
        if (_dom.viewCompleted) _dom.viewCompleted.style.display = view === 'completed' ? '' : 'none';

        // Re-render the selected view
        if (view === 'scheduled') renderScheduleList();
        if (view === 'completed') renderCompletedTasks();
    }

    // =====================================================================
    // Modal tab switching
    // =====================================================================

    function switchModalTab(tab) {
        _dom.modalTabs.forEach(t => {
            const isActive = t.dataset.modalTab === tab;
            t.classList.toggle('active', isActive);
            t.setAttribute('aria-selected', isActive ? 'true' : 'false');
        });

        // Show/hide forms
        document.querySelectorAll('.modal-tab-content').forEach(content => {
            content.style.display = content.dataset.modalTabContent === tab ? '' : 'none';
        });

        // Update modal title
        const title = document.getElementById('task-modal-title');
        if (title) {
            title.textContent = tab === 'schedule' ? 'Schedule Task' : 'Assign Task';
        }

        // Populate agent select for schedule tab
        if (tab === 'schedule' && _dom.scheduleAgentSelect) {
            _populateScheduleAgentSelect();
        }
    }

    function _populateScheduleAgentSelect() {
        if (!_dom.scheduleAgentSelect || !window.state) return;
        _dom.scheduleAgentSelect.innerHTML = state.agents
            .map(a => `<option value="${escapeHtml(a.agent_id)}">${escapeHtml(a.name || a.agent_id)}</option>`)
            .join('');
    }

    function closeModal() {
        const modal = document.getElementById('assign-task-modal');
        if (modal) modal.hidden = true;
        if (_dom.scheduleForm) _dom.scheduleForm.reset();
        if (_dom.assignForm) _dom.assignForm.reset();
        // Reset to assign tab
        switchModalTab('assign');
    }

    // Open modal directly on the schedule tab
    function openScheduleModal(agentId) {
        const modal = document.getElementById('assign-task-modal');
        if (modal) modal.hidden = false;
        switchModalTab('schedule');
        if (agentId && _dom.scheduleAgentSelect) {
            _populateScheduleAgentSelect();
            _dom.scheduleAgentSelect.value = agentId;
        }
    }

    // =====================================================================
    // Schedule rendering
    // =====================================================================

    function renderScheduleList() {
        if (!_dom.scheduleList) return;

        if (schedules.length === 0) {
            _dom.scheduleList.innerHTML =
                '<div class="empty-state" id="schedules-empty">' +
                '<p class="empty-state__text">No scheduled tasks</p>' +
                '<p class="empty-state__hint">Create recurring tasks that run on a schedule</p>' +
                '</div>';
            return;
        }

        _dom.scheduleList.innerHTML = schedules.map(renderScheduleCard).join('');

        // Update badge
        if (_dom.scheduleBadge) {
            _dom.scheduleBadge.textContent = schedules.filter(s => s.enabled).length;
        }
    }

    function renderScheduleCard(schedule) {
        const isEnabled = schedule.enabled;
        const priority = schedule.priority || 'medium';
        const priorityColors = { critical: '#e74c3c', high: '#e67e22', medium: '#3498db', low: '#95a5a6' };
        const priorityColor = priorityColors[priority] || '#3498db';

        const lastRun = schedule.last_run_at ? formatTimeAgo(schedule.last_run_at) : 'never';
        const nextRun = schedule.next_run_at ? _formatNextRun(schedule.next_run_at) : 'n/a';
        const statusIcon = schedule.last_status === 'failed' ? '[X]' : schedule.last_status === 'success' ? '[OK]' : '[-]';
        const statusClass = schedule.last_status === 'failed' ? 'schedule-card__status--failed' : schedule.last_status === 'success' ? 'schedule-card__status--success' : '';

        const scheduleLabel = _scheduleLabel(schedule.schedule_type, schedule.schedule_expr);
        const disabledClass = isEnabled ? '' : ' schedule-card--disabled';
        const failureWarning = schedule.failure_streak >= 2
            ? `<span class="schedule-card__warning">[!] ${schedule.failure_streak} consecutive failures</span>`
            : '';

        return `
        <div class="schedule-card${disabledClass}" data-schedule-id="${escapeHtml(schedule.id)}">
            <div class="schedule-card__header">
                <div class="schedule-card__agent-row">
                    <span class="schedule-card__agent">${escapeHtml(schedule.agent_id)}</span>
                    <span class="schedule-card__priority" style="color: ${priorityColor}">${priority}</span>
                </div>
                <label class="schedule-toggle" title="${isEnabled ? 'Disable' : 'Enable'} schedule">
                    <input type="checkbox" ${isEnabled ? 'checked' : ''} onchange="CohortTasks.toggleSchedule('${escapeHtml(schedule.id)}')">
                    <span class="schedule-toggle__slider" role="switch" aria-checked="${isEnabled}"></span>
                </label>
            </div>
            <p class="schedule-card__description">${escapeHtml(schedule.description)}</p>
            <div class="schedule-card__schedule">
                <span class="schedule-card__frequency">${escapeHtml(scheduleLabel)}</span>
                ${failureWarning}
            </div>
            <div class="schedule-card__meta">
                <span class="schedule-card__status ${statusClass}" title="Last run status">${statusIcon} Last: ${lastRun}</span>
                <span class="schedule-card__next" title="Next scheduled run">Next: ${nextRun}</span>
                <span class="schedule-card__runs">${schedule.run_count || 0} runs</span>
            </div>
            <div class="schedule-card__actions">
                <button class="btn btn--small btn--secondary" onclick="CohortTasks.forceRun('${escapeHtml(schedule.id)}')" ${isEnabled ? '' : 'disabled'}>Run Now</button>
                <button class="btn btn--small btn--danger" onclick="CohortTasks.deleteSchedule('${escapeHtml(schedule.id)}')">Delete</button>
            </div>
        </div>`;
    }

    function _scheduleLabel(type, expr) {
        if (type === 'interval') {
            const secs = parseInt(expr, 10);
            if (secs < 3600) return `Every ${Math.round(secs / 60)} minutes`;
            if (secs < 86400) return `Every ${Math.round(secs / 3600)} hours`;
            return `Every ${Math.round(secs / 86400)} days`;
        }
        if (type === 'cron') return `Cron: ${expr}`;
        if (type === 'once') return 'One-time';
        return type;
    }

    function _formatNextRun(isoStr) {
        if (!isoStr) return 'n/a';
        const dt = new Date(isoStr);
        const now = new Date();
        const diffMs = dt - now;
        if (diffMs < 0) return 'overdue';
        if (diffMs < 60000) return 'in <1 min';
        if (diffMs < 3600000) return `in ${Math.round(diffMs / 60000)} min`;
        if (diffMs < 86400000) return `in ${Math.round(diffMs / 3600000)}h`;
        return dt.toLocaleDateString();
    }

    // =====================================================================
    // Completed tasks rendering
    // =====================================================================

    function renderCompletedTasks() {
        if (!_dom.completedTaskList) return;
        const completed = (state.tasks || []).filter(t => t.status === 'complete' || t.status === 'failed');

        if (completed.length === 0) {
            _dom.completedTaskList.innerHTML =
                '<div class="empty-state">' +
                '<p class="empty-state__text">No completed tasks</p>' +
                '<p class="empty-state__hint">Completed task runs will appear here</p>' +
                '</div>';
            return;
        }

        _dom.completedTaskList.innerHTML = completed.map(task => {
            const isScheduled = !!task.schedule_id;
            const statusClass = task.status === 'failed' ? 'task-card--failed' : 'task-card--complete';
            const recurringBadge = isScheduled ? '<span class="task-card__badge task-card__badge--recurring">[R]</span>' : '';
            const timeStr = formatTimeAgo(task.completed_at || task.updated_at);

            return `
            <div class="task-card ${statusClass}" data-task-id="${escapeHtml(task.task_id)}">
                <div class="task-card__header">
                    <span class="task-card__agent">${escapeHtml(task.agent_id)} ${recurringBadge}</span>
                    <span class="task-card__priority task-card__priority--${task.priority || 'medium'}">${task.priority || 'medium'}</span>
                </div>
                <p class="task-card__description">${escapeHtml(task.description)}</p>
                <div class="task-card__footer">
                    <span class="task-card__status">${task.status}</span>
                    <span class="task-card__time">${timeStr}</span>
                </div>
            </div>`;
        }).join('');
    }

    // =====================================================================
    // Scheduler heartbeat
    // =====================================================================

    function updateSchedulerHeartbeat(status) {
        schedulerStatus = status;
        if (!_dom.schedulerDot || !_dom.schedulerText) return;

        if (status.running) {
            _dom.schedulerDot.className = 'scheduler-heartbeat__dot scheduler-heartbeat__dot--active';
            const tickInfo = status.last_tick ? `, tick ${formatTimeAgo(status.last_tick)}` : '';
            _dom.schedulerText.textContent = `Scheduler: active (${status.active_schedules || 0} schedules${tickInfo})`;
        } else {
            _dom.schedulerDot.className = 'scheduler-heartbeat__dot scheduler-heartbeat__dot--inactive';
            _dom.schedulerText.textContent = 'Scheduler: inactive';
        }
    }

    // =====================================================================
    // Socket.IO event handlers (called from cohort.js wiring)
    // =====================================================================

    function onSchedulesUpdate(data) {
        schedules = data.schedules || [];
        if (data.scheduler) updateSchedulerHeartbeat(data.scheduler);

        // Update badge in all views
        if (_dom.scheduleBadge) {
            _dom.scheduleBadge.textContent = schedules.filter(s => s.enabled).length;
        }

        // Re-render if we're on the scheduled view
        if (currentView === 'scheduled') renderScheduleList();
    }

    function onScheduleRun(data) {
        showToast(`Scheduled task fired: ${data.agent_id}`, 'info');
    }

    function onScheduleDisabled(data) {
        showToast(
            `Schedule auto-disabled: ${data.description || data.schedule_id} (${data.failure_streak} failures)`,
            'error'
        );
        // Refresh schedules
        if (state.socket && state.connected) {
            state.socket.emit('request_schedules', {});
        }
    }

    function onSchedulerHeartbeat(data) {
        updateSchedulerHeartbeat(data);
    }

    // =====================================================================
    // Actions
    // =====================================================================

    function toggleSchedule(scheduleId) {
        if (!state.socket || !state.connected) {
            showToast('Not connected', 'error');
            return;
        }
        state.socket.emit('toggle_schedule', { schedule_id: scheduleId }, (response) => {
            if (response && response.error) {
                showToast(response.error, 'error');
            }
        });
    }

    function deleteSchedule(scheduleId) {
        if (!confirm('Delete this schedule? Existing task runs will be preserved.')) return;
        if (!state.socket || !state.connected) {
            showToast('Not connected', 'error');
            return;
        }
        state.socket.emit('delete_schedule', { schedule_id: scheduleId }, (response) => {
            if (response && response.error) {
                showToast(response.error, 'error');
            } else {
                showToast('Schedule deleted', 'success');
            }
        });
    }

    function forceRun(scheduleId) {
        if (!state.socket || !state.connected) {
            showToast('Not connected', 'error');
            return;
        }
        state.socket.emit('force_run_schedule', { schedule_id: scheduleId }, (response) => {
            if (response && response.error) {
                showToast(response.error, 'error');
            } else {
                showToast('Schedule triggered manually', 'success');
            }
        });
    }

    function submitSchedule() {
        if (!state.socket || !state.connected) {
            showToast('Not connected', 'error');
            return;
        }

        const agentId = _dom.scheduleAgentSelect ? _dom.scheduleAgentSelect.value : '';
        const description = _dom.scheduleDescInput ? _dom.scheduleDescInput.value.trim() : '';
        const priority = _dom.schedulePrioritySelect ? _dom.schedulePrioritySelect.value : 'medium';

        if (!agentId || !description) {
            showToast('Agent and description are required', 'error');
            return;
        }

        // Determine schedule type/expr from radio selection
        const selectedPreset = document.querySelector('input[name="schedule-preset"]:checked');
        if (!selectedPreset) {
            showToast('Select a schedule frequency', 'error');
            return;
        }

        const presetValue = selectedPreset.value;

        if (presetValue === 'custom') {
            // Custom schedule
            const type = _dom.typeSelect ? _dom.typeSelect.value : 'interval';
            let expr;
            if (type === 'interval') {
                const minutes = parseInt(_dom.intervalInput ? _dom.intervalInput.value : '60', 10);
                if (minutes < 5) {
                    showToast('Minimum interval is 5 minutes', 'error');
                    return;
                }
                expr = String(minutes * 60);  // Convert to seconds
            } else {
                expr = _dom.cronInput ? _dom.cronInput.value.trim() : '';
                if (!expr) {
                    showToast('Cron expression is required', 'error');
                    return;
                }
            }

            state.socket.emit('create_schedule', {
                agent_id: agentId,
                description: description,
                priority: priority,
                schedule_type: type,
                schedule_expr: expr,
            }, _handleScheduleResponse);
        } else {
            // Preset schedule
            state.socket.emit('create_schedule', {
                agent_id: agentId,
                description: description,
                priority: priority,
                preset: presetValue,
            }, _handleScheduleResponse);
        }
    }

    function _handleScheduleResponse(response) {
        if (response && response.error) {
            showToast(response.error, 'error');
        } else {
            showToast('Schedule created', 'success');
            closeModal();
            switchView('scheduled');
        }
    }

    // =====================================================================
    // Initialization
    // =====================================================================

    function init() {
        _initDom();

        // Schedule form submission
        if (_dom.scheduleForm) {
            _dom.scheduleForm.addEventListener('submit', (e) => {
                e.preventDefault();
                submitSchedule();
            });
        }

        // Description character counter
        if (_dom.scheduleDescInput && _dom.scheduleDescCount) {
            _dom.scheduleDescInput.addEventListener('input', () => {
                _dom.scheduleDescCount.textContent = `${_dom.scheduleDescInput.value.length} / 500`;
            });
        }

        // Custom preset toggle
        document.querySelectorAll('input[name="schedule-preset"]').forEach(radio => {
            radio.addEventListener('change', () => {
                if (_dom.customFields) {
                    _dom.customFields.style.display = radio.value === 'custom' ? '' : 'none';
                }
            });
        });

        // Custom type toggle (interval vs cron)
        if (_dom.typeSelect) {
            _dom.typeSelect.addEventListener('change', () => {
                const isCron = _dom.typeSelect.value === 'cron';
                if (_dom.intervalGroup) _dom.intervalGroup.style.display = isCron ? 'none' : '';
                if (_dom.cronGroup) _dom.cronGroup.style.display = isCron ? '' : 'none';
            });
        }

        // Request schedules on connect
        if (state.socket && state.connected) {
            state.socket.emit('request_schedules', {});
        }
    }

    // =====================================================================
    // Public API
    // =====================================================================

    return {
        init,
        switchView,
        switchModalTab,
        closeModal,
        openScheduleModal,
        renderScheduleList,
        renderCompletedTasks,
        updateSchedulerHeartbeat,
        // Socket handlers
        onSchedulesUpdate,
        onScheduleRun,
        onScheduleDisabled,
        onSchedulerHeartbeat,
        // Actions
        toggleSchedule,
        deleteSchedule,
        forceRun,
        submitSchedule,
    };
})();
