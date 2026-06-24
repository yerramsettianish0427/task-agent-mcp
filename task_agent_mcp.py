"""
Autonomous Task Decomposition & Execution Agent — MCP Server

An MCP server that turns any AI client (Claude Desktop, Cursor, VS Code)
into an autonomous code refactoring engine. Give it a high-level goal like
"add error handling to my RAG pipeline" and it:

  1. Reads your codebase and understands the structure
  2. Decomposes the goal into concrete, ordered subtasks
  3. Executes each subtask by generating and applying code changes
  4. Verifies each change by running your test suite
  5. Rolls back any subtask that breaks tests
  6. Commits each successful change with a descriptive message

Architecture:
  - FastMCP for MCP protocol compliance (tool discovery, JSON-RPC, stdio)
  - Anthropic API for planning and code generation (Claude as the reasoning engine)
  - subprocess for shell execution (git, pytest, linting)
  - Stateful task tracking with per-subtask rollback support

Author: Anish Yerramsetti
"""

import os
import json
import subprocess
from pathlib import Path
from datetime import datetime

from anthropic import Anthropic
from mcp.server.fastmcp import FastMCP


# ═══════════════════════════════════════════════════════════════════════
# SERVER + CLIENT INIT
# ═══════════════════════════════════════════════════════════════════════

mcp = FastMCP("task-agent")
client = Anthropic()  # Reads ANTHROPIC_API_KEY from environment


# ═══════════════════════════════════════════════════════════════════════
# TASK STATE: tracks subtasks, changes, rollback history
# ═══════════════════════════════════════════════════════════════════════

class TaskState:
    """Holds the full state of an active task execution."""

    def __init__(self, goal: str, project_path: str):
        self.goal = goal
        self.project_path = project_path
        self.subtasks: list[str] = []
        self.results: list[dict] = []
        self.current_idx: int = 0
        self.file_snapshots: dict[str, str] = {}  # path → original content
        self.git_commits: list[str] = []
        self.started_at = datetime.now().isoformat()


# Global state — one task at a time
_state: TaskState | None = None


# ═══════════════════════════════════════════════════════════════════════
# FILE I/O HELPERS
# ═══════════════════════════════════════════════════════════════════════

def _read_file(path: str) -> str:
    """Read a file's contents."""
    return Path(path).read_text(encoding="utf-8")


def _write_file(path: str, content: str) -> None:
    """Write to a file. Snapshots the original for rollback on first write."""
    p = Path(path)
    if _state and str(p) not in _state.file_snapshots and p.exists():
        _state.file_snapshots[str(p)] = _read_file(str(p))
    p.write_text(content, encoding="utf-8")


def _rollback_files() -> list[str]:
    """Restore all modified files to their pre-task state."""
    rolled_back = []
    if _state:
        for path, original in _state.file_snapshots.items():
            Path(path).write_text(original, encoding="utf-8")
            rolled_back.append(path)
        _state.file_snapshots.clear()
    return rolled_back


# ═══════════════════════════════════════════════════════════════════════
# SHELL / GIT HELPERS
# ═══════════════════════════════════════════════════════════════════════

def _run(cmd: str, cwd: str | None = None) -> tuple[str, str, int]:
    """Run a shell command. Returns (stdout, stderr, returncode)."""
    try:
        r = subprocess.run(
            cmd, shell=True, capture_output=True, text=True,
            timeout=60, cwd=cwd or (_state.project_path if _state else None),
        )
        return r.stdout, r.stderr, r.returncode
    except subprocess.TimeoutExpired:
        return "", "Timed out after 60s", 1
    except Exception as e:
        return "", str(e), 1


def _git_commit(message: str) -> bool:
    """Stage all changes and commit."""
    _run("git add -A")
    _, err, code = _run(f'git commit -m "[TaskAgent] {message}"')
    if code == 0:
        if _state:
            _state.git_commits.append(message)
        return True
    return False


def _git_undo_last() -> bool:
    """Undo the most recent commit but keep files staged."""
    _, _, code = _run("git reset --soft HEAD~1")
    return code == 0


def _run_tests() -> tuple[bool, str]:
    """Run pytest. Returns (passed, output)."""
    out, err, code = _run("python -m pytest -v --tb=short 2>&1")
    combined = out + err
    passed = code == 0
    return passed, combined[:2000]  # Truncate for context window


def _scan_project(project_path: str) -> list[dict]:
    """Return a list of Python files with line counts."""
    files = []
    for p in Path(project_path).rglob("*.py"):
        if ".venv" in str(p) or "node_modules" in str(p) or "__pycache__" in str(p):
            continue
        try:
            lines = len(p.read_text(encoding="utf-8").splitlines())
            files.append({"path": str(p), "lines": lines})
        except:
            pass
    return sorted(files, key=lambda f: f["lines"], reverse=True)


# ═══════════════════════════════════════════════════════════════════════
# CLAUDE CALLS: planning + code generation
# ═══════════════════════════════════════════════════════════════════════

MODEL = "claude-sonnet-4-20250514"


def _call_claude(system: str, prompt: str, max_tokens: int = 2000) -> str:
    """Single Claude API call with system prompt."""
    response = client.messages.create(
        model=MODEL,
        max_tokens=max_tokens,
        system=system,
        messages=[{"role": "user", "content": prompt}],
    )
    return response.content[0].text


def _decompose(goal: str, context: str) -> list[str]:
    """Claude breaks a goal into 4-8 concrete subtasks."""
    text = _call_claude(
        system="You are a senior engineer. You decompose tasks into subtasks.",
        prompt=f"""Break this goal into 4-8 concrete subtasks.
Each subtask = one focused code change in one file.
Order them by dependency (do prerequisites first).

GOAL: {goal}

CODEBASE CONTEXT:
{context}

Return ONLY a JSON array of strings. No markdown, no explanation.
Example: ["Add import for asyncio in main.py", "Convert fetch() to async def"]""",
    )
    try:
        start = text.index("[")
        end = text.rindex("]") + 1
        return json.loads(text[start:end])
    except (ValueError, json.JSONDecodeError):
        return [goal]


def _generate_fix(subtask: str, file_path: str, current_code: str) -> str:
    """Claude generates the fixed version of a file for one subtask."""
    return _call_claude(
        system="You are a code refactoring agent. Return ONLY valid Python. No markdown fences, no explanation.",
        prompt=f"""SUBTASK: {subtask}

FILE: {file_path}

CURRENT CODE:
{current_code}

Return the COMPLETE updated file content that accomplishes the subtask.
Preserve all existing functionality. Only change what the subtask requires.""",
        max_tokens=4000,
    )


def _identify_files(subtask: str, all_files: list[dict]) -> list[str]:
    """Claude picks which files a subtask needs to modify."""
    file_list = "\n".join(f["path"] for f in all_files)
    text = _call_claude(
        system="Return ONLY a JSON array of file paths. No markdown, no explanation.",
        prompt=f"""Which files need to change for this subtask?

SUBTASK: {subtask}

AVAILABLE FILES:
{file_list}

Return a JSON array of 1-3 file paths. Example: ["src/main.py"]""",
    )
    try:
        start = text.index("[")
        end = text.rindex("]") + 1
        return json.loads(text[start:end])
    except (ValueError, json.JSONDecodeError):
        return [all_files[0]["path"]] if all_files else []


# ═══════════════════════════════════════════════════════════════════════
# ORCHESTRATOR: the main execution loop
# ═══════════════════════════════════════════════════════════════════════

def _execute_pipeline(goal: str, project_path: str) -> dict:
    """
    Full autonomous pipeline:
      decompose → (execute → verify → commit/rollback) × N
    """
    global _state
    _state = TaskState(goal, project_path)

    # Scan the project
    all_files = _scan_project(project_path)
    if not all_files:
        return {"error": "No Python files found in project"}

    # Build context from top files
    context = ""
    for f in all_files[:5]:
        try:
            code = _read_file(f["path"])
            context += f"\n--- {f['path']} ({f['lines']} lines) ---\n{code[:800]}\n"
        except:
            pass

    # Step 1: Decompose
    _state.subtasks = _decompose(goal, context)

    # Step 2: Execute each subtask
    for i, subtask in enumerate(_state.subtasks):
        _state.current_idx = i
        result = {"subtask": subtask, "index": i}

        # Identify target files
        target_files = _identify_files(subtask, all_files)
        result["target_files"] = target_files

        # Execute: generate fix for each target file
        executed = False
        for fpath in target_files:
            try:
                current = _read_file(fpath)
                fixed = _generate_fix(subtask, fpath, current)

                # Strip markdown fences if Claude included them
                if fixed.startswith("```"):
                    lines = fixed.split("\n")
                    fixed = "\n".join(lines[1:-1]) if lines[-1].strip() == "```" else "\n".join(lines[1:])

                _write_file(fpath, fixed)
                executed = True
            except Exception as e:
                result["error"] = str(e)

        if not executed:
            result["status"] = "skipped"
            _state.results.append(result)
            continue

        # Verify: run tests
        passed, test_output = _run_tests()
        result["tests_passed"] = passed
        result["test_output"] = test_output[:500]

        if passed:
            _git_commit(subtask)
            result["status"] = "committed"
        else:
            # Rollback this subtask's changes
            rolled = _rollback_files()
            result["status"] = "rolled_back"
            result["rolled_back_files"] = rolled

        _state.results.append(result)

    # Summary
    committed = [r for r in _state.results if r.get("status") == "committed"]
    rolled = [r for r in _state.results if r.get("status") == "rolled_back"]

    return {
        "goal": goal,
        "project": project_path,
        "total_subtasks": len(_state.subtasks),
        "committed": len(committed),
        "rolled_back": len(rolled),
        "skipped": len(_state.results) - len(committed) - len(rolled),
        "commits": _state.git_commits,
        "details": _state.results,
        "started_at": _state.started_at,
        "finished_at": datetime.now().isoformat(),
    }


# ═══════════════════════════════════════════════════════════════════════
# MCP TOOLS: what clients (Claude Desktop, Cursor) can call
# ═══════════════════════════════════════════════════════════════════════

@mcp.tool()
def scan_project(project_path: str) -> str:
    """Scan a project and list all Python files with line counts.

    Args:
        project_path: Absolute path to the project folder.
    """
    files = _scan_project(project_path)
    return json.dumps(files, indent=2)


@mcp.tool()
def plan_task(goal: str, project_path: str) -> str:
    """Decompose a high-level goal into concrete subtasks WITHOUT executing them.
    Use this to preview what the agent would do before running execute_task.

    Args:
        goal: What you want done (e.g. "Add error handling to all API calls").
        project_path: Absolute path to the project folder.
    """
    all_files = _scan_project(project_path)
    context = ""
    for f in all_files[:5]:
        try:
            context += f"\n--- {f['path']} ---\n{_read_file(f['path'])[:800]}\n"
        except:
            pass

    subtasks = _decompose(goal, context)
    return json.dumps({"goal": goal, "subtasks": subtasks}, indent=2)


@mcp.tool()
def execute_task(goal: str, project_path: str) -> str:
    """Execute the full autonomous pipeline: decompose the goal, execute each
    subtask, verify with tests, commit successes, rollback failures.

    Args:
        goal: High-level goal (e.g. "Refactor to use async/await").
        project_path: Absolute path to the project folder.
    """
    result = _execute_pipeline(goal, project_path)
    return json.dumps(result, indent=2)


@mcp.tool()
def check_status() -> str:
    """Check the current status of a running task."""
    if not _state:
        return json.dumps({"status": "idle", "message": "No active task"})
    return json.dumps({
        "goal": _state.goal,
        "progress": f"{_state.current_idx + 1}/{len(_state.subtasks)}",
        "commits_so_far": _state.git_commits,
        "started_at": _state.started_at,
    }, indent=2)


@mcp.tool()
def rollback_all() -> str:
    """Emergency rollback: restore all files to their state before the task started."""
    rolled = _rollback_files()
    if rolled:
        _run("git checkout -- .")
        return json.dumps({"rolled_back": rolled})
    return json.dumps({"message": "Nothing to roll back"})


# ═══════════════════════════════════════════════════════════════════════
# PIECE 3: RUN
# ═══════════════════════════════════════════════════════════════════════

if __name__ == "__main__":
    mcp.run()
