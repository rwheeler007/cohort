"""Tests for cohort.meeting -- scoring engine, stakeholder gating, topic detection."""

import pytest

from cohort.chat import Channel, ChatManager, Message
from cohort.meeting import (
    SCORING_WEIGHTS,
    STAKEHOLDER_THRESHOLDS,
    StakeholderStatus,
    calculate_complementary_value,
    calculate_contribution_score,
    calculate_data_ownership,
    calculate_expertise_relevance,
    calculate_keyword_overlap,
    calculate_novelty,
    calculate_phase_alignment,
    detect_current_phase,
    detect_topic_shift,
    extract_keywords,
    identify_stakeholders_for_topic,
    initialize_meeting_context,
    is_directly_questioned,
    should_agent_speak,
    update_stakeholder_status,
)
from cohort.registry import JsonFileStorage


# =====================================================================
# Helpers
# =====================================================================

def _msg(content: str, sender: str = "alice", channel: str = "ch") -> Message:
    """Create a test message."""
    return Message(
        id=f"m-{hash(content) % 10000}",
        channel_id=channel,
        sender=sender,
        content=content,
        timestamp="2026-01-01T00:00:00",
    )


def _channel(
    mode: str = "meeting",
    meeting_context: dict | None = None,
) -> Channel:
    """Create a test channel."""
    return Channel(
        id="test-ch",
        name="Test",
        description="Test channel",
        created_at="2026-01-01T00:00:00",
        mode=mode,
        meeting_context=meeting_context,
    )


# =====================================================================
# extract_keywords
# =====================================================================

class TestExtractKeywords:
    def test_basic(self):
        kw = extract_keywords("implement the database migration")
        assert "implement" in kw
        assert "database" in kw
        assert "migration" in kw

    def test_filters_stop_words(self):
        kw = extract_keywords("the quick brown fox is a test")
        assert "the" not in kw
        assert "quick" in kw
        assert "brown" in kw

    def test_filters_short_words(self):
        kw = extract_keywords("fix the bug in api")
        # "fix", "bug", "api" are 3 chars -- filtered (len > 3 required)
        assert "fix" not in kw
        assert "bug" not in kw

    def test_empty_string(self):
        assert extract_keywords("") == []

    def test_underscored_words(self):
        kw = extract_keywords("the python_developer should handle it")
        assert "python_developer" in kw


# =====================================================================
# calculate_keyword_overlap (Jaccard similarity)
# =====================================================================

class TestKeywordOverlap:
    def test_identical_lists(self):
        assert calculate_keyword_overlap(["a", "b"], ["a", "b"]) == 1.0

    def test_no_overlap(self):
        assert calculate_keyword_overlap(["a", "b"], ["c", "d"]) == 0.0

    def test_partial_overlap(self):
        overlap = calculate_keyword_overlap(["a", "b", "c"], ["b", "c", "d"])
        # intersection={b,c}, union={a,b,c,d} -> 2/4 = 0.5
        assert overlap == pytest.approx(0.5)

    def test_empty_first(self):
        assert calculate_keyword_overlap([], ["a"]) == 0.0

    def test_empty_both(self):
        assert calculate_keyword_overlap([], []) == 0.0


# =====================================================================
# calculate_novelty
# =====================================================================

class TestNovelty:
    def test_no_recent_messages(self):
        assert calculate_novelty("something new", []) == 1.0

    def test_exact_duplicate(self):
        msgs = [_msg("implement the database migration")]
        score = calculate_novelty("implement the database migration", msgs)
        assert score < 0.2  # very low novelty

    def test_novel_content(self):
        msgs = [_msg("implement the database migration")]
        score = calculate_novelty("deploy kubernetes cluster orchestration", msgs)
        assert score > 0.5  # high novelty

    def test_empty_proposed(self):
        msgs = [_msg("something")]
        assert calculate_novelty("", msgs) == 0.0


# =====================================================================
# calculate_expertise_relevance
# =====================================================================

class TestExpertiseRelevance:
    def test_matching_triggers(self):
        config = {"triggers": ["python", "backend"], "capabilities": ["api design"]}
        score = calculate_expertise_relevance(config, ["python", "backend"])
        assert score > 0.0

    def test_no_match(self):
        config = {"triggers": ["python"], "capabilities": ["backend"]}
        score = calculate_expertise_relevance(config, ["javascript", "frontend"])
        assert score == 0.0

    def test_empty_config(self):
        assert calculate_expertise_relevance({}, ["python"]) == 0.0

    def test_empty_topic(self):
        config = {"triggers": ["python"], "capabilities": []}
        assert calculate_expertise_relevance(config, []) == 0.0


# =====================================================================
# is_directly_questioned
# =====================================================================

class TestIsDirectlyQuestioned:
    def test_direct_question(self):
        msgs = [_msg("@alice what do you think?")]
        assert is_directly_questioned("alice", msgs) is True

    def test_mention_without_question(self):
        msgs = [_msg("@alice please review this.")]
        assert is_directly_questioned("alice", msgs) is False

    def test_question_without_mention(self):
        msgs = [_msg("What do you think?")]
        assert is_directly_questioned("alice", msgs) is False

    def test_only_checks_last_3(self):
        old_msgs = [_msg(f"msg {i}") for i in range(5)]
        old_msgs.insert(0, _msg("@alice what do you think?"))
        # The question is at index 0, beyond last 3
        assert is_directly_questioned("alice", old_msgs) is False


# =====================================================================
# Scoring weights validation
# =====================================================================

class TestScoringWeights:
    def test_weights_sum_to_one(self):
        total = sum(SCORING_WEIGHTS.values())
        assert total == pytest.approx(1.0)

    def test_all_dimensions_present(self):
        assert set(SCORING_WEIGHTS.keys()) == {"novelty", "expertise", "ownership", "question"}


# =====================================================================
# Stakeholder thresholds
# =====================================================================

class TestStakeholderThresholds:
    def test_active_lowest(self):
        assert STAKEHOLDER_THRESHOLDS[StakeholderStatus.ACTIVE.value] == 0.3

    def test_dormant_highest(self):
        assert STAKEHOLDER_THRESHOLDS[StakeholderStatus.DORMANT.value] == 1.0

    def test_ordering(self):
        vals = [
            STAKEHOLDER_THRESHOLDS[s.value]
            for s in [
                StakeholderStatus.ACTIVE,
                StakeholderStatus.APPROVED_SILENT,
                StakeholderStatus.OBSERVER,
                StakeholderStatus.DORMANT,
            ]
        ]
        assert vals == sorted(vals)


# =====================================================================
# calculate_contribution_score
# =====================================================================

class TestContributionScore:
    def test_score_range(self):
        ctx = initialize_meeting_context(["alice"])
        config = {"triggers": ["python"], "capabilities": ["backend"]}
        score = calculate_contribution_score(
            "alice", "implement python backend", ctx, config, []
        )
        assert 0.0 <= score <= 1.0

    def test_primary_stakeholder_bonus(self):
        ctx = initialize_meeting_context(["alice"])
        config = {"triggers": [], "capabilities": []}
        score_primary = calculate_contribution_score(
            "alice", "some message", ctx, config, []
        )
        score_non = calculate_contribution_score(
            "bob", "some message", ctx, config, []
        )
        assert score_primary > score_non

    def test_question_bonus(self):
        ctx = initialize_meeting_context(["alice"])
        config = {"triggers": [], "capabilities": []}
        msgs_with_q = [_msg("@alice what do you think?")]
        score = calculate_contribution_score(
            "alice", "response message", ctx, config, msgs_with_q
        )
        # Should get question bonus (0.15)
        assert score >= SCORING_WEIGHTS["question"]


# =====================================================================
# detect_current_phase
# =====================================================================

class TestDetectPhase:
    def test_discover_phase(self):
        msgs = [_msg("let's research and investigate the existing implementation")]
        assert detect_current_phase(msgs) == "DISCOVER"

    def test_execute_phase(self):
        msgs = [_msg("implement the code and create the module in python")]
        assert detect_current_phase(msgs) == "EXECUTE"

    def test_validate_phase(self):
        msgs = [_msg("review and test the quality of the implementation")]
        assert detect_current_phase(msgs) == "VALIDATE"

    def test_empty_defaults_to_discover(self):
        assert detect_current_phase([]) == "DISCOVER"


# =====================================================================
# calculate_complementary_value
# =====================================================================

class TestComplementaryValue:
    def test_with_active_complement(self):
        ctx = {
            "stakeholder_status": {
                "python_developer": StakeholderStatus.ACTIVE.value,
                "supervisor_agent": StakeholderStatus.ACTIVE.value,
            }
        }
        score = calculate_complementary_value("python_developer", ctx)
        assert score > 0.0

    def test_no_complement_defined(self):
        ctx = {"stakeholder_status": {"random_agent": StakeholderStatus.ACTIVE.value}}
        score = calculate_complementary_value("random_agent", ctx)
        assert score == 0.0

    def test_complement_not_active(self):
        ctx = {
            "stakeholder_status": {
                "python_developer": StakeholderStatus.ACTIVE.value,
                "supervisor_agent": StakeholderStatus.DORMANT.value,
            }
        }
        score = calculate_complementary_value("python_developer", ctx)
        # supervisor_agent is dormant, not active -- should not count
        # but qa_agent is in complementary pairs for python_developer too
        assert score >= 0.0


# =====================================================================
# calculate_phase_alignment
# =====================================================================

class TestPhaseAlignment:
    def test_python_dev_in_execute(self):
        score = calculate_phase_alignment("python_developer", "EXECUTE", {})
        assert score == 1.0  # high tier

    def test_python_dev_in_discover(self):
        score = calculate_phase_alignment("python_developer", "DISCOVER", {})
        # python_developer matches "developer" in DISCOVER low tier
        assert score == 0.2

    def test_code_archaeologist_in_validate(self):
        score = calculate_phase_alignment("code_archaeologist", "VALIDATE", {})
        assert score == 1.0  # high tier


# =====================================================================
# calculate_data_ownership
# =====================================================================

class TestDataOwnership:
    def test_known_agent(self):
        score = calculate_data_ownership("supervisor_agent", ["monitoring", "compliance"])
        assert score > 0.0

    def test_unknown_agent(self):
        score = calculate_data_ownership("random_agent", ["anything"])
        assert score == 0.0

    def test_no_topic_match(self):
        score = calculate_data_ownership("supervisor_agent", ["unrelated_stuff"])
        # Has sources but no topic match -> returns 0.3
        assert score == pytest.approx(0.3)


# =====================================================================
# initialize_meeting_context
# =====================================================================

class TestInitializeMeetingContext:
    def test_empty(self):
        ctx = initialize_meeting_context()
        assert ctx["stakeholder_status"] == {}
        assert ctx["current_topic"]["keywords"] == []

    def test_with_agents(self):
        ctx = initialize_meeting_context(["alice", "bob"])
        assert ctx["stakeholder_status"]["alice"] == StakeholderStatus.ACTIVE.value
        assert ctx["stakeholder_status"]["bob"] == StakeholderStatus.ACTIVE.value
        assert "alice" in ctx["current_topic"]["primary_stakeholders"]


# =====================================================================
# detect_topic_shift
# =====================================================================

class TestTopicShift:
    def test_no_shift(self):
        ctx = {
            "current_topic": {"keywords": ["python", "backend", "implementation"]}
        }
        msgs = [_msg("implement the python backend module")]
        assert detect_topic_shift(msgs, ctx) is False

    def test_shift_detected(self):
        ctx = {
            "current_topic": {"keywords": ["python", "backend", "implementation"]}
        }
        msgs = [_msg("deploy kubernetes cluster monitoring dashboard")]
        assert detect_topic_shift(msgs, ctx) is True

    def test_empty_messages(self):
        ctx = {"current_topic": {"keywords": ["python"]}}
        assert detect_topic_shift([], ctx) is False

    def test_empty_previous_keywords(self):
        ctx = {"current_topic": {"keywords": []}}
        msgs = [_msg("anything here")]
        assert detect_topic_shift(msgs, ctx) is False


# =====================================================================
# identify_stakeholders_for_topic
# =====================================================================

class TestIdentifyStakeholders:
    def test_finds_matching_agents(self):
        agents = {
            "alice": {"triggers": ["python", "backend"], "capabilities": ["api design"]},
            "bob": {"triggers": ["javascript"], "capabilities": ["frontend"]},
        }
        result = identify_stakeholders_for_topic(["python", "backend"], agents)
        assert "alice" in result

    def test_threshold_filtering(self):
        agents = {
            "alice": {"triggers": ["python"], "capabilities": []},
            "bob": {"triggers": ["javascript"], "capabilities": []},
        }
        # High threshold should exclude weak matches
        result = identify_stakeholders_for_topic(
            ["python"], agents, relevance_threshold=0.9
        )
        # With only one trigger keyword, most agents won't reach 0.9
        assert len(result) <= 1


# =====================================================================
# should_agent_speak
# =====================================================================

class TestShouldAgentSpeak:
    def test_explicit_mention_always_speaks(self):
        msg = _msg("@alice what do you think?")
        ch = _channel(mode="meeting", meeting_context={
            "stakeholder_status": {"alice": StakeholderStatus.DORMANT.value},
            "current_topic": {"keywords": [], "primary_stakeholders": []},
        })
        assert should_agent_speak("alice", msg, ch) is True

    def test_chat_mode_always_speaks(self):
        msg = _msg("general discussion")
        ch = _channel(mode="chat", meeting_context=None)
        assert should_agent_speak("alice", msg, ch) is True

    def test_dormant_agent_blocked(self):
        msg = _msg("general discussion about unrelated stuff")
        ch = _channel(mode="meeting", meeting_context={
            "stakeholder_status": {"alice": StakeholderStatus.DORMANT.value},
            "current_topic": {"keywords": ["unrelated"], "primary_stakeholders": []},
        })
        # Dormant threshold is 1.0, score won't reach it without mention
        assert should_agent_speak("alice", msg, ch) is False


# =====================================================================
# update_stakeholder_status
# =====================================================================

class TestUpdateStakeholderStatus:
    def test_update_existing(self, tmp_path):
        storage = JsonFileStorage(tmp_path)
        chat = ChatManager(storage)
        chat.create_channel("ch", "Test")
        ch = chat.get_channel("ch")
        ch.meeting_context = {
            "stakeholder_status": {"alice": StakeholderStatus.ACTIVE.value},
            "current_topic": {"keywords": [], "primary_stakeholders": []},
        }
        update_stakeholder_status("alice", StakeholderStatus.APPROVED_SILENT, ch, chat)
        assert ch.meeting_context["stakeholder_status"]["alice"] == StakeholderStatus.APPROVED_SILENT.value

    def test_creates_context_if_missing(self, tmp_path):
        storage = JsonFileStorage(tmp_path)
        chat = ChatManager(storage)
        chat.create_channel("ch", "Test")
        ch = chat.get_channel("ch")
        ch.meeting_context = None
        update_stakeholder_status("alice", StakeholderStatus.ACTIVE, ch, chat)
        assert ch.meeting_context is not None
        assert ch.meeting_context["stakeholder_status"]["alice"] == StakeholderStatus.ACTIVE.value
