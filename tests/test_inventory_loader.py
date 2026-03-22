"""Tests for cohort.inventory_loader — all three parsers + merge logic."""

import json
import textwrap
from pathlib import Path

import pytest

from cohort.inventory_schema import InventoryEntry
from cohort.inventory_loader import (
    load_registry,
    load_exports,
    load_yaml_inventories,
    load_merged_inventory,
    _extract_keywords,
    _slugify,
    _basic_yaml_list_loader,
)


# ── Fixtures ─────────────────────────────────────────────────────────────

@pytest.fixture
def registry_file(tmp_path: Path) -> Path:
    """Create a mock VS Code extension settings file with project_registry."""
    settings = {
        "setup_completed": True,
        "project_registry": [
            {
                "name": "BOSS",
                "path": str(tmp_path / "BOSS"),
                "registered_at": "2026-03-22T10:00:00Z",
                "profile": "developer",
                "has_cohort_dir": True,
                "has_exports": True,
            },
            {
                "name": "cohort",
                "path": str(tmp_path / "cohort"),
                "registered_at": "2026-03-22T10:00:00Z",
                "profile": "developer",
                "has_cohort_dir": True,
                "has_exports": False,
            },
        ],
    }
    path = tmp_path / "settings.json"
    path.write_text(json.dumps(settings), encoding="utf-8")
    return path


@pytest.fixture
def project_with_exports(tmp_path: Path) -> Path:
    """Create a mock project directory with a CLAUDE.md containing Exports."""
    proj = tmp_path / "BOSS"
    proj.mkdir()
    claude_md = proj / "CLAUDE.md"
    claude_md.write_text(textwrap.dedent("""\
        # BOSS Project

        ## Key Locations

        Some intro text.

        ## Exports

        | Capability | Entry Point | What It Does |
        |------------|-------------|--------------|
        | llm-router | `tools/llm_router/llm_router.py` | Unified local/cloud LLM routing |
        | comms-service | `tools/comms_service/service.py` | FastAPI email send/receive |
        | code-health | `tools/code_health/analyzer.py` | Tiered code quality scanner |

        ## Context Management

        More text after exports.
    """), encoding="utf-8")
    return proj


@pytest.fixture
def yaml_inventory_dir(tmp_path: Path) -> Path:
    """Create a mock BOSS root with YAML inventory files."""
    boss = tmp_path / "boss_root"
    (boss / "data").mkdir(parents=True)
    (boss / "golden_patterns").mkdir(parents=True)

    (boss / "data" / "tool_inventory.yaml").write_text(textwrap.dedent("""\
        # Tool Inventory
        - id: llm_router
          path: tools/llm_router/
          keywords: [model, inference, routing, GPU]
          use_when: "Routing inference requests to local models"

        - id: comms_service
          path: tools/comms_service/
          keywords: [email, send, calendar]
          use_when: "Sending emails or managing calendar"
    """), encoding="utf-8")

    (boss / "data" / "project_inventory.yaml").write_text(textwrap.dedent("""\
        - id: boss
          path: G:/BOSS
          tech: [Python, Flask]
          keywords: [orchestration, agent, workflow]
          description: "Multi-agent orchestration framework"
    """), encoding="utf-8")

    (boss / "golden_patterns" / "INDEX.yaml").write_text(textwrap.dedent("""\
        - id: cli-first-development
          keywords: [CLI, command line, skill, testable]
          use_when: "Building new features with CLI access"
          core_doc: cli-first-development/cli-first-development-pattern.md
    """), encoding="utf-8")

    return boss


# ── Source 1: Registry ───────────────────────────────────────────────────

class TestLoadRegistry:
    def test_loads_projects(self, registry_file: Path):
        entries = load_registry(registry_file)
        assert len(entries) == 2
        assert entries[0].id == "BOSS"
        assert entries[0].type == "project"
        assert entries[0].status == "active"
        assert entries[1].id == "cohort"

    def test_missing_file_returns_empty(self, tmp_path: Path):
        entries = load_registry(tmp_path / "nonexistent.json")
        assert entries == []

    def test_malformed_json_returns_empty(self, tmp_path: Path):
        bad = tmp_path / "bad.json"
        bad.write_text("{not valid json", encoding="utf-8")
        entries = load_registry(bad)
        assert entries == []

    def test_empty_registry_returns_empty(self, tmp_path: Path):
        path = tmp_path / "empty.json"
        path.write_text("{}", encoding="utf-8")
        entries = load_registry(path)
        assert entries == []


# ── Source 2: Exports ────────────────────────────────────────────────────

class TestLoadExports:
    def test_parses_exports_table(self, project_with_exports: Path):
        entries = load_exports(str(project_with_exports), "BOSS")
        assert len(entries) == 3
        assert entries[0].id == "llm-router"
        assert entries[0].type == "export"
        assert entries[0].source_project == "BOSS"
        assert entries[0].entry_point == "tools/llm_router/llm_router.py"
        assert "routing" in entries[0].keywords

    def test_no_claude_md_returns_empty(self, tmp_path: Path):
        entries = load_exports(str(tmp_path / "nonexistent"), "test")
        assert entries == []

    def test_claude_md_without_exports_returns_empty(self, tmp_path: Path):
        proj = tmp_path / "no_exports"
        proj.mkdir()
        (proj / "CLAUDE.md").write_text("# Project\n\n## Key Locations\n\nStuff\n")
        entries = load_exports(str(proj), "test")
        assert entries == []


# ── Source 3: YAML inventories ───────────────────────────────────────────

class TestLoadYamlInventories:
    def test_loads_all_three_files(self, yaml_inventory_dir: Path):
        entries = load_yaml_inventories(yaml_inventory_dir)
        types = {e.type for e in entries}
        assert "tool" in types
        assert "project" in types
        assert "pattern" in types
        assert len(entries) == 4  # 2 tools + 1 project + 1 pattern

    def test_tool_entries(self, yaml_inventory_dir: Path):
        entries = load_yaml_inventories(yaml_inventory_dir)
        tools = [e for e in entries if e.type == "tool"]
        assert len(tools) == 2
        assert tools[0].id == "llm_router"
        assert "model" in tools[0].keywords

    def test_pattern_entries(self, yaml_inventory_dir: Path):
        entries = load_yaml_inventories(yaml_inventory_dir)
        patterns = [e for e in entries if e.type == "pattern"]
        assert len(patterns) == 1
        assert patterns[0].id == "cli-first-development"

    def test_missing_root_returns_empty(self):
        entries = load_yaml_inventories(Path("/nonexistent/path"))
        assert entries == []


# ── Merge ────────────────────────────────────────────────────────────────

class TestMergedInventory:
    def test_deduplicates_by_type_and_id(self, registry_file, yaml_inventory_dir, project_with_exports):
        entries = load_merged_inventory(
            settings_path=registry_file,
            boss_root=yaml_inventory_dir,
        )
        # Count entries with id "boss" — should be 1 (registry wins over YAML)
        boss_entries = [e for e in entries if e.id.lower() in ("boss",)]
        # registry has type=project id=BOSS, YAML has type=project id=boss
        # dedup key is (type, id) — these should collapse
        project_boss = [e for e in entries if e.type == "project" and e.id.lower() == "boss"]
        assert len(project_boss) == 1

    def test_empty_entries_skipped(self, tmp_path):
        # Empty settings file
        path = tmp_path / "settings.json"
        path.write_text(json.dumps({"project_registry": []}), encoding="utf-8")
        entries = load_merged_inventory(settings_path=path, boss_root=Path("/nonexistent"))
        assert entries == []


# ── Helpers ──────────────────────────────────────────────────────────────

class TestHelpers:
    def test_slugify(self):
        assert _slugify("LLM Router") == "llm-router"
        assert _slugify("code-health") == "code-health"
        assert _slugify("  Foo Bar  ") == "foo-bar"

    def test_extract_keywords(self):
        kw = _extract_keywords("llm-router", "Unified local/cloud LLM routing")
        assert "llm" in kw
        assert "routing" in kw
        assert "the" not in kw

    def test_basic_yaml_loader(self):
        content = textwrap.dedent("""\
            # Comment
            - id: foo
              keywords: [a, b, c]
              description: "something"

            - id: bar
              path: tools/bar/
        """)
        items = _basic_yaml_list_loader(content)
        assert len(items) == 2
        assert items[0]["id"] == "foo"
        assert items[0]["keywords"] == ["a", "b", "c"]
        assert items[1]["id"] == "bar"


# ── Schema ───────────────────────────────────────────────────────────────

class TestInventoryEntry:
    def test_roundtrip(self):
        entry = InventoryEntry(
            id="test", source_project="BOSS", entry_point="tools/test.py",
            keywords=["test"], description="A test tool", type="tool",
        )
        d = entry.to_dict()
        restored = InventoryEntry.from_dict(d)
        assert restored.id == entry.id
        assert restored.keywords == entry.keywords

    def test_to_inventory_line(self):
        entry = InventoryEntry(
            id="llm-router", source_project="BOSS",
            entry_point="tools/llm_router.py",
            keywords=["model", "inference"], description="LLM routing",
            type="tool",
        )
        line = entry.to_inventory_line()
        assert "[tool: llm-router]" in line
        assert "LLM routing" in line

    def test_from_dict_with_path_fallback(self):
        """YAML entries use 'path' instead of 'entry_point'."""
        entry = InventoryEntry.from_dict({"id": "foo", "path": "tools/foo/"})
        assert entry.entry_point == "tools/foo/"
