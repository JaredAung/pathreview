"""Reproduce issue #47: agent session state is not checkpointed mid-run.

Path A — Agent-level reproduction
---------------------------------
1. Run Orchestrator with several slow mock tools + Redis SessionStore.
2. Kill the process mid-plan (before SessionStore.set at the end of run()).
3. Confirm Redis has no session:{profile_id} key — progress lived only in memory.
4. Re-run in a fresh process and confirm every tool executes again from scratch.

Usage:
    # Redis must be up (docker compose up -d)
    source .venv/bin/activate
    python scripts/repro_agent_state_persistence.py

Why this proves the bug
-----------------------
Orchestrator.run() only calls SessionStore.set() AFTER every tool finishes.
If the process dies mid-loop, completed tool results exist only in RAM
(ContextManager) and are lost. Redis never gets a mid-run checkpoint.
"""

from __future__ import annotations

import multiprocessing as mp
import sys
import time
from pathlib import Path

import redis

# ---------------------------------------------------------------------------
# Make repo imports work when you run: python scripts/this_file.py
# Without this, `from agent...` / `from core...` would fail.
# ---------------------------------------------------------------------------
REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from agent.memory.session_store import SessionStore  # noqa: E402
from agent.orchestrator import Orchestrator  # noqa: E402
from agent.tools.base import BaseTool, ToolResult  # noqa: E402
from core.config import settings  # noqa: E402

# Fake profile id used as the Redis key: session:repro-issue-47
PROFILE_ID = "repro-issue-47"

# Each mock tool sleeps this long → a 5-tool plan takes ~10 seconds total.
TOOL_SLEEP_SECONDS = 2.0

# Kill the child after this many seconds (mid-plan: ~2 tools done, rest not).
# Must be less than (number_of_tools * TOOL_SLEEP_SECONDS) or the run finishes
# before we can interrupt it.
KILL_AFTER_SECONDS = 5.0


# ---------------------------------------------------------------------------
# Slow mock tools
# Real GitHub/README tools are fast or need network. We fake them with sleep
# so we have a realistic "long-running multi-step review" we can interrupt.
# ---------------------------------------------------------------------------
class SlowMockTool(BaseTool):
    """Stand-in for a real agent tool; sleeps to simulate slow work."""

    def __init__(self, name: str, sleep_seconds: float = TOOL_SLEEP_SECONDS):
        self.name = name  # Orchestrator looks tools up by this name
        self.description = f"Slow mock for {name}"
        self.sleep_seconds = sleep_seconds
        self.call_count = 0  # How many times this tool ran in this process

    def execute(self, input_data: dict) -> ToolResult:
        """Called by Orchestrator for each planned step."""
        self.call_count += 1
        print(f"  [{self.name}] starting (call #{self.call_count})...", flush=True)
        time.sleep(self.sleep_seconds)  # Pretend we're analyzing a repo
        print(f"  [{self.name}] done", flush=True)
        # ToolResult.data is what Orchestrator stores in its local `results` dict
        return ToolResult(
            success=True,
            data={"tool": self.name, "input": input_data, "call": self.call_count},
        )


def build_tools() -> dict[str, SlowMockTool]:
    """Names must match what Orchestrator._build_plan() schedules."""
    names = [
        "github_tool",
        "tech_detector",
        "readme_scorer",
        "skill_extractor",
        "market_analyzer",
    ]
    return {name: SlowMockTool(name) for name in names}


def build_profile_data() -> dict:
    """Profile fields that make Orchestrator schedule all 5 tools.

    See agent/orchestrator.py _build_plan():
      - github_username + projects[].github_repo → github_tool
      - files                                      → tech_detector
      - readme_content                             → readme_scorer
      - resume_text                                → skill_extractor
      - (any prior tools)                          → market_analyzer
    """
    return {
        "github_username": "repro-user",
        "projects": [{"github_repo": "repro-repo"}],
        "files": ["main.py", "requirements.txt"],
        "readme_content": "# Repro Repo\n\nDemonstration readme for issue #47.",
        "resume_text": "Software engineer with Python and FastAPI experience.",
        "repo_metadata": {"stars": 0},
    }


# ---------------------------------------------------------------------------
# Redis helpers — SessionStore uses the key pattern session:{id}
# ---------------------------------------------------------------------------
def session_key(profile_id: str = PROFILE_ID) -> str:
    """Same key format as agent/memory/session_store.py."""
    return f"session:{profile_id}"


def redis_client() -> redis.Redis:
    """Connect using REDIS_URL from .env / core.config.settings."""
    return redis.Redis.from_url(settings.redis_url, decode_responses=True)


def clear_session(client: redis.Redis) -> None:
    """Delete leftover key so a previous run doesn't confuse this one."""
    client.delete(session_key())


def session_exists(client: redis.Redis) -> bool:
    """True if Redis currently has a checkpoint for our profile."""
    return bool(client.exists(session_key()))


# ---------------------------------------------------------------------------
# Step 1 helper: run Orchestrator in a SEPARATE process so we can kill it
# without killing this script. That mimics an API server restart mid-review.
# ---------------------------------------------------------------------------
def _child_run_until_killed() -> None:
    """Child process entry point — starts a long Orchestrator.run().

    Parent will terminate this process before run() finishes, so we never
    reach SessionStore.set() at the bottom of Orchestrator.run().
    """
    client = redis_client()
    store = SessionStore(client)  # Redis-backed store (only written at end today)
    tools = build_tools()  # Slow tools (~2s each)
    orch = Orchestrator(tools=tools, session_store=store, tool_timeout=60.0)

    print("\n=== Interrupted mid-run (child process) ===", flush=True)
    orch.run(PROFILE_ID, build_profile_data())

    # If you see this warning, the kill window was too long / tools too fast.
    print("  WARNING: run finished before kill — increase KILL_AFTER_SECONDS", flush=True)


def run_interrupted() -> None:
    """Spawn child → wait partway through the plan → kill child."""
    print("\n=== Step 1: Start multi-tool run, kill mid-plan ===", flush=True)

    # Start Orchestrator in another OS process (not just a thread).
    # Killing a process destroys its in-memory ContextManager / results.
    proc = mp.Process(target=_child_run_until_killed, name="orchestrator-repro")
    proc.start()

    # Let ~2 tools finish, then interrupt before the full plan + Redis write.
    time.sleep(KILL_AFTER_SECONDS)

    if not proc.is_alive():
        # Plan finished too early — we didn't actually interrupt mid-run.
        print("  FAIL: child exited before kill window — tools may be too fast", flush=True)
        proc.join()
        sys.exit(1)

    print(f"  killing child pid={proc.pid} after {KILL_AFTER_SECONDS}s...", flush=True)
    proc.terminate()  # Ask child to stop (like Ctrl+C / server restart)
    proc.join(timeout=5)
    if proc.is_alive():
        proc.kill()  # Force-kill if terminate didn't work
        proc.join(timeout=2)


def main() -> int:
    print("Issue #47 reproduction — agent state not persisted across restarts")
    print(f"Redis: {settings.redis_url}")
    print(f"Session key: {session_key()}")

    # --- Preconditions ---
    client = redis_client()
    try:
        client.ping()
    except redis.ConnectionError as exc:
        print(f"\nFAIL: cannot reach Redis ({exc})")
        print("Start infra first: docker compose up -d")
        return 1

    clear_session(client)
    print("Cleared any existing repro session key.")

    # ===================================================================
    # STEP 1 — Interrupt mid-run (bug trigger)
    # ===================================================================
    run_interrupted()

    # ===================================================================
    # STEP 2 — Prove Redis has no checkpoint after the kill
    # Expected with the bug: False (SessionStore.set never ran)
    # ===================================================================
    mid_run_persisted = session_exists(client)
    print("\n=== Step 2: Inspect Redis after kill ===", flush=True)
    print(f"  {session_key()} exists? {mid_run_persisted}", flush=True)

    if mid_run_persisted:
        # If a fix checkpoints after every tool, you might see True here.
        print("  UNEXPECTED: session was written mid-run (bug may already be fixed)")
        raw = client.get(session_key())
        print(f"  value={raw}")
        return 1

    print(
        "  OBSERVED: no Redis checkpoint — mid-run state lived only in "
        "ContextManager / process memory.",
        flush=True,
    )

    # ===================================================================
    # STEP 3 — Simulate "server came back": new Orchestrator, same profile
    # Expected: every tool runs again (no resume from Redis).
    # Use fast sleeps so this step finishes quickly.
    # ===================================================================
    print("\n=== Step 3: Fresh process re-runs all tools (no resume) ===", flush=True)
    print("  (using fast mocks so we can finish the full plan)", flush=True)
    store = SessionStore(client)
    tools = {name: SlowMockTool(name, sleep_seconds=0.05) for name in build_tools()}
    orch = Orchestrator(tools=tools, session_store=store, tool_timeout=60.0)
    result = orch.run(PROFILE_ID, build_profile_data())
    executed = list(result["tool_results"].keys())
    print(f"  tools executed again from scratch: {executed}", flush=True)

    # ===================================================================
    # STEP 4 — Contrast: a COMPLETED run does write Redis
    # Shows SessionStore works — it's just called too late (only at the end).
    # ===================================================================
    after_complete = session_exists(client)
    print("\n=== Step 4: Contrast — completed run DOES write Redis ===", flush=True)
    print(f"  {session_key()} exists after full run? {after_complete}", flush=True)
    if after_complete:
        raw = client.get(session_key()) or ""
        print(f"  value={raw[:120]}...", flush=True)

    print("\n=== Verdict ===")
    print(
        "BUG REPRODUCED: killing Orchestrator mid-plan leaves no session "
        "checkpoint in Redis; a restart must re-execute every tool."
    )
    print(
        "Root cause: SessionStore.set() only runs after the full tool loop "
        "in agent/orchestrator.py."
    )

    # Clean up so we don't leave test data in Redis
    clear_session(client)
    return 0


if __name__ == "__main__":
    # "spawn" = start a fresh Python interpreter for the child (needed on macOS).
    mp.set_start_method("spawn", force=True)
    raise SystemExit(main())
