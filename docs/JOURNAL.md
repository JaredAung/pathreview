## Week 7 — Issue selection

**Issue link:** https://github.com/ascherj/pathreview/issues/47

**Issue title:** Agent state isn't persisted across API restarts, causing in-progress reviews to be lost

**Tier:** [ ] Tier 1  [ ] Tier 2  [x] Tier 3

**Problem summary:**
Multi-repo reviews that take a long time (5+ repositories) can lose all progress if the API server restarts mid-run. Session state for an in-flight review lives only in process memory and is not persisted to Redis until the job finishes. After a restart, that memory is gone, so the review cannot continue and users have to start over. A successful fix would checkpoint agent session state to Redis during the run so reviews survive restarts and can continue from where they left off.

**Branch name:** `fix/agent-state-persistence`

**Setup confirmation:** [x] App runs locally at localhost:5173

**Cohort ledger:** [x] Issue added to cohort ledger

## Week 8 — Reproduction & solution planning

**Reproduction commit link:** https://github.com/JaredAung/pathreview/commit/38b192d362a2f6a4e597fddc266ed7a2c1d0b8f7

**Reproduction summary:**
I reproduced the bug with `scripts/repro_agent_state_persistence.py`, which runs `Orchestrator` against Redis with slow mock tools and kills the process mid-plan. After the kill, Redis had no `session:repro-issue-47` key, and a fresh re-run executed every tool again from scratch — confirming mid-run progress lives only in memory and is not checkpointed to `SessionStore` until `run()` finishes.

**PLAN.md link:** [link to PLAN.md in your fork]

**Blockers or open questions:**
[Anything you're still uncertain about going into Week 9, or leave blank]