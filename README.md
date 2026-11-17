# task-agent-mcp

An MCP server that takes a coding goal for a Python project, asks Claude to
split it into subtasks, has Claude rewrite the relevant files for each one,
and runs pytest after every subtask. If the tests pass it commits, otherwise
it puts the files back.

## Install and run

```bash
git clone https://github.com/yerramsettianish0427/task-agent-mcp.git
cd task-agent-mcp
pip install -r requirements.txt
export ANTHROPIC_API_KEY=...
python task_agent_mcp.py
```

It talks MCP over stdio, so normally a client starts it. For Claude Desktop,
add this to `claude_desktop_config.json`:

```json
{
  "mcpServers": {
    "task-agent": {
      "command": "python",
      "args": ["/absolute/path/to/task_agent_mcp.py"],
      "env": { "ANTHROPIC_API_KEY": "..." }
    }
  }
}
```

The tools are `scan_project`, `plan_task` (subtasks only, no edits),
`execute_task`, `check_status` and `rollback_all`.

## Example

Listing the tools from an MCP client and calling `check_status` before any
task has run:

```
tools: ['scan_project', 'plan_task', 'execute_task', 'check_status', 'rollback_all']
check_status: {"status": "idle", "message": "No active task"}
```

`execute_task` returns JSON with each subtask, the files it touched, whether
the tests passed, and whether it was committed, rolled back or skipped.

## Limits

- The target project needs a git repo and a pytest suite. If pytest fails or
  finds no tests, every subtask gets rolled back.
- Planning only sees the first 800 characters of the five largest files, and
  Claude returns whole files rather than diffs.
- Python files only, one task at a time, and `execute_task` blocks until it
  finishes, so `check_status` is mostly useful afterwards.
- `rollback_all` discards uncommitted changes. It doesn't undo commits.
- The model is hard-coded (`claude-sonnet-5-5`).
