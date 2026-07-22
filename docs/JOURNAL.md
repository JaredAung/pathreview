## Week 7 — Issue selection

**Issue link:** https://github.com/ascherj/pathreview/issues/47

**Issue title:** Agent state isn't persisted across API restarts, causing in-progress reviews to be lost

**Tier:** [ ] Tier 1  [ ] Tier 2  [x] Tier 3

**Problem summary:**
Multi-repo reviews that take a long time (5+ repositories) can lose all progress if the API server restarts mid-run. Session state for an in-flight review lives only in process memory and is not persisted to Redis until the job finishes. After a restart, that memory is gone, so the review cannot continue and users have to start over. A successful fix would checkpoint agent session state to Redis during the run so reviews survive restarts and can continue from where they left off.

**Branch name:** `fix/agent-state-persistence`

**Setup confirmation:** [x] App runs locally at localhost:5173

**Cohort ledger:** [x] Issue added to cohort ledger
