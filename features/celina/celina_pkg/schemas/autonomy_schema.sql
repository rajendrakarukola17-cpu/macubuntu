-- Celina v5 Autonomous Core SQLite Schema (`tasks.db`)
-- Always opened with: PRAGMA journal_mode=WAL; PRAGMA busy_timeout=5000; PRAGMA synchronous=NORMAL;

CREATE TABLE IF NOT EXISTS schema_version (
    id INTEGER PRIMARY KEY CHECK (id = 1),
    version TEXT NOT NULL,
    updated_at TEXT DEFAULT (datetime('now'))
);

-- Task graph: persistent DAG of goals, subgoals, control nodes, and atomic actions
CREATE TABLE IF NOT EXISTS tasks (
    id TEXT PRIMARY KEY,                    -- UUID
    parent_id TEXT REFERENCES tasks(id),
    goal TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'pending', -- pending|running|done|failed|blocked|cancelled|observed
    node_type TEXT NOT NULL,                -- 'agent' (LLM-reasoned) | 'control' (flow)
    control_kind TEXT,                      -- 'sequence'|'branch'|'loop'|'retry'|NULL
    control_config TEXT,                    -- JSON for flow nodes
    assigned_gear INTEGER DEFAULT 1,        -- which Celina gear handles this
    priority INTEGER DEFAULT 5,
    order_index INTEGER DEFAULT 0,          -- deterministic sibling ordering for sequence nodes
    created_at TEXT DEFAULT (datetime('now')),
    updated_at TEXT DEFAULT (datetime('now')),
    attempts INTEGER DEFAULT 0,
    max_attempts INTEGER DEFAULT 3,
    last_error TEXT,
    result_summary TEXT,
    evidence_hash TEXT                      -- links to evidence log
);

CREATE INDEX IF NOT EXISTS idx_tasks_status ON tasks(status, priority ASC, order_index ASC, created_at ASC);
CREATE INDEX IF NOT EXISTS idx_tasks_parent ON tasks(parent_id, order_index ASC);

-- Execution evidence: tamper-evident SHA-256 hash chain
CREATE TABLE IF NOT EXISTS evidence (
    seq INTEGER PRIMARY KEY AUTOINCREMENT,
    task_id TEXT REFERENCES tasks(id),
    timestamp TEXT NOT NULL,
    tool_name TEXT,
    arguments_hash TEXT,
    exit_code INTEGER,
    backup_path TEXT,
    why_receipt_hash TEXT,
    prev_hash TEXT NOT NULL,
    entry_hash TEXT NOT NULL               -- SHA-256(task_id|timestamp|tool_name|arguments_hash|exit_code|prev_hash)
);

CREATE INDEX IF NOT EXISTS idx_evidence_task ON evidence(task_id);

-- Learning proposals: guarded overnight skill improvements awaiting human review
CREATE TABLE IF NOT EXISTS proposals (
    id TEXT PRIMARY KEY,
    created_at TEXT DEFAULT (datetime('now')),
    source_task_ids TEXT,                  -- JSON array of failed task IDs
    proposal_type TEXT,                    -- 'new_skill'|'patch_tool'|'new_rule'
    payload TEXT NOT NULL,                 -- JSON proposal
    test_result TEXT,                      -- 'passed'|'failed'|'not_run'
    status TEXT DEFAULT 'pending'          -- pending|approved|rejected
);

CREATE INDEX IF NOT EXISTS idx_proposals_status ON proposals(status, created_at DESC);
