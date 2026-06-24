# Task Agent MCP

An autonomous task decomposition and execution agent exposed via the [Model Context Protocol (MCP)](https://modelcontextprotocol.io). Give it a high-level engineering goal — it plans the work, executes each step, verifies nothing broke, and commits the results.

## What It Does

```
You: "Add error handling to all API calls in my RAG pipeline"

Agent:
  1. Scans your codebase
  2. Decomposes into subtasks:
     - "Add try/except to embeddings.py fetch call"
     - "Add retry logic to vector search in retriever.py"
     - "Add timeout handling to API client"
     - "Write tests for error handling paths"
  3. Executes each subtask (generates and applies code changes)
  4. Runs your test suite after each change
  5. Commits if tests pass, rolls back if they don't
  6. Reports: 3/4 subtasks committed, 1 rolled back
```

## Architecture

```
┌──────────────────────────────────────────────────────┐
│                    MCP Client                        │
│           (Claude Desktop / Cursor / VS Code)        │
└──────────────────┬───────────────────────────────────┘
                   │ JSON-RPC over stdio
┌──────────────────▼───────────────────────────────────┐
│                Task Agent MCP Server                 │
│                                                      │
│  ┌─────────┐  ┌───────────┐  ┌──────────┐           │
│  │  scan    │  │  plan     │  │ execute  │           │
│  │ project  │  │  task     │  │  task    │           │
│  └─────────┘  └───────────┘  └────┬─────┘           │
│                                    │                 │
│               ┌────────────────────▼──────────────┐  │
│               │     Execution Pipeline            │  │
│               │                                   │  │
│               │  decompose → identify_files →     │  │
│               │  generate_fix → write → test →    │  │
│               │  commit OR rollback               │  │
│               └────────────────────┬──────────────┘  │
│                                    │                 │
│         ┌──────────────────────────┼──────────┐      │
│         ▼                          ▼          ▼      │
│   ┌──────────┐              ┌──────────┐ ┌────────┐ │
│   │ Anthropic│              │   Git    │ │ pytest │  │
│   │   API    │              │          │ │        │  │
│   └──────────┘              └──────────┘ └────────┘  │
└──────────────────────────────────────────────────────┘
```

## MCP Tools

| Tool | Description |
|------|-------------|
| `scan_project` | List all Python files in a project with line counts |
| `plan_task` | Decompose a goal into subtasks without executing (preview mode) |
| `execute_task` | Full pipeline: decompose → execute → verify → commit/rollback |
| `check_status` | Check progress of a running task |
| `rollback_all` | Emergency rollback to pre-task state |

## Setup

### Prerequisites
- Python 3.10+
- An [Anthropic API key](https://console.anthropic.com/)
- Git (for commit/rollback features)

### Install

```bash
git clone https://github.com/yerramsettianish0427/task-agent-mcp.git
cd task-agent-mcp
pip install -r requirements.txt
export ANTHROPIC_API_KEY="your-key-here"
```

### Connect to Claude Desktop

Add to your `claude_desktop_config.json`:

```json
{
  "mcpServers": {
    "task-agent": {
      "command": "python",
      "args": ["/absolute/path/to/task_agent_mcp.py"],
      "env": {
        "ANTHROPIC_API_KEY": "your-key-here"
      }
    }
  }
}
```

Config location:
- **macOS**: `~/Library/Application Support/Claude/claude_desktop_config.json`
- **Windows**: `%APPDATA%\Claude\claude_desktop_config.json`
- **Windows MSIX**: `%LOCALAPPDATA%\Packages\Claude_pzs8sxrjxfjjc\LocalCache\Roaming\Claude\claude_desktop_config.json`

### Connect to Cursor

Settings → MCP → Add Server → stdio → command: `python /absolute/path/to/task_agent_mcp.py`

## Usage Examples

**Preview a plan:**
> "Plan how to add type hints to my project at /Users/me/my-project"

**Execute autonomously:**
> "Execute task: add comprehensive error handling to /Users/me/rag-system"

**Check progress:**
> "What's the status of the current task?"

**Emergency stop:**
> "Roll back all changes"

## How It Works

1. **Scan** — Discovers all Python files, builds a file map with line counts
2. **Decompose** — Claude analyzes your codebase context and breaks the goal into 4-8 ordered subtasks
3. **Identify** — For each subtask, Claude picks which files need to change
4. **Generate** — Claude reads the current code and produces the complete updated file
5. **Write** — Agent writes the new code, snapshotting the original for rollback
6. **Verify** — Runs `pytest` to check nothing broke
7. **Commit or Rollback** — If tests pass, commits with `[TaskAgent] subtask description`. If tests fail, restores the original file.

## Key Design Decisions

- **Per-subtask rollback**: Each subtask is atomic. A failure in subtask 4 doesn't undo subtasks 1-3.
- **File snapshots over git stash**: Snapshots are more reliable for partial rollbacks across multiple files.
- **Claude as planner AND executor**: The same model decomposes tasks and generates code, ensuring consistency between plan and execution.
- **Stdio transport**: Runs as a subprocess of the client. No ports, no URLs, no deployment — just a pipe.

## Tech Stack

- **MCP SDK** (FastMCP) — Protocol compliance, tool discovery, JSON-RPC
- **Anthropic API** — Task decomposition, code generation, file identification
- **subprocess** — Git operations, test execution
- **pathlib** — Cross-platform file I/O with rollback support

## License

MIT
