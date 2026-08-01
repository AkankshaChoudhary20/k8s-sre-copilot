# k8s SRE Copilot

A local command-line tool that turns a local Kubernetes cluster (Kind) +
Ollama into an on-call SRE assistant. Point it at your cluster and it
detects problems, explains them, proposes fixes, and writes reusable
runbooks — all offline, using a local LLM.

## Why this project

Sibling to [`log-anomaly-detector`](../log-anomaly-detector) — that project's
"Next steps" called for wiring detection into a real cluster and using
tool-calling. This is that: same evaluation-first habit (deterministic,
testable detection before any LLM is involved), applied to a live Kind
cluster instead of synthetic data.

## The commands

| Command | What it does | Uses LLM? | Status |
|---|---|---|---|
| `detect` | Scans cluster, classifies anomalies by severity | No | **Built** |
| `rca <pod>` | Investigates a resource, writes grounded root-cause analysis | Yes (agent w/ tools) | **Built** |
| `fix <pod>` | Produces mitigation + permanent fix + validation + rollback | Yes (agent w/ tools) | **Built** |
| `sop "<topic>"` | Writes a production runbook to `docs/SOP-*.md` | Yes | **Built** |
| `scan` | Runs all of the above in sequence for every unhealthy pod | Yes | **Built** |
| `chat` | Interactive REPL with kubectl tools | Yes | **Built** |

## Design

- `src/k8s.py` — `KubectlClient`, a thin `subprocess` wrapper around
  `kubectl` (no k8s Python client library, no separate kubeconfig handling —
  whatever context `kubectl` already points at is what gets operated on).
  Read-only methods (`list_pods`, `describe_pod`, `get_logs`, `get_events`)
  for `detect`/`rca`; mutating methods (`rollout_restart`, `scale`, `patch`,
  `delete_pod`) reserved for `fix`, gated behind `--apply` + confirmation.
- `src/detect.py` — deterministic, rule-based severity classifier over
  kubectl JSON. **No LLM decides severity.** Container waiting/terminated
  reasons, restart counts, and readiness are mapped to critical/high/medium
  by fixed rules, so the classifier is unit-testable without a live cluster
  or a model, and its output is something an LLM can *explain* later, not
  something it invents.
  - critical: `CrashLoopBackOff`, `OOMKilled`
  - high: `ImagePullBackOff`/`ErrImagePull`, unschedulable `Pending`, restart
    count ≥ 5
  - medium: not-Ready (with or without restarts)
- `src/cli.py` — argparse entrypoint, `-n/--namespace` and `--context`
  flags, dispatches to subcommands.
- `manifests/` — four intentionally broken Deployments used to seed a local
  cluster with real, reproducible failures for demoing/testing `detect`
  (and later `rca`/`fix`): `crashloop-app`, `oom-app`,
  `imagepullbackoff-app`, `bad-probe-app`.
- `scripts/setup_cluster.sh` — creates the Kind cluster (via `bin/kind`,
  downloaded directly — no Homebrew required) and applies `manifests/`.
- `src/llm.py` — `OllamaClient`, a minimal wrapper around Ollama's
  `/api/chat`. No native tool-calling (see Assumptions).
- `src/agent.py` — a hand-rolled ReAct loop: the model is prompted to reply
  with a single JSON object, either `{"action": ..., "args": ...}` to call a
  tool or `{"final_answer": ...}` to finish; the driver executes the tool
  against real cluster state and feeds the result back as the next turn's
  observation. Two reliability guardrails were added after live testing
  exposed real gaps (see Results):
  - `require_any_before_final` rejects a `final_answer` that arrives before
    the model has called at least one evidence-gathering tool, forcing
    another iteration instead of accepting an unsupported conclusion.
  - a raw-text snippet is logged whenever a response fails to parse as
    JSON, since silently retrying hid what the model was actually doing.
- `src/tools.py` — the tool catalog exposed to the agent, backed by
  `KubectlClient`. Read-only (`get_pod_status`, `describe_pod`, `get_logs`,
  `get_events`, `resolve_deployment_name`) is available to `rca` and to
  `fix` always; mutating tools (`rollout_restart`, `scale`, `delete_pod`,
  `set_container_image`, `set_container_memory_limit`,
  `set_readiness_probe_path`) are only added to `fix`'s toolset when
  `--apply` is passed, and each one prompts for interactive confirmation
  before it runs.
- `src/rca.py` — read-only agent loop producing a grounded root-cause
  analysis (symptom / root cause / confidence).
- `src/fix.py` — same loop plus (optionally) mutating tools; **dry-run by
  default**, prints a mitigation/permanent-fix/validation/rollback plan as
  text. Only with `--apply` can it actually execute changes, and even then
  every mutating call requires a `y` at an interactive prompt.
- `src/sop.py` — single-shot (no agent loop, no tools) completion writing a
  runbook to `docs/SOP-<slug>.md`.
- `src/scan.py` — orchestrates `detect` → `rca` → `fix` (dry-run only,
  never `--apply`) → `sop` for every unhealthy pod, prints a summary table.
- `src/chat.py` — REPL reusing `agent.py` with the same read-only toolset
  as `rca`, one fresh agent run per question (no cross-question memory —
  simpler and more predictable than threading a growing conversation
  through a model this small).

## Assumptions

- `kubectl` is on `PATH` and already points at a valid context (or pass
  `--context`); this tool does not manage kubeconfig.
- Local-only: designed against a Kind cluster, but `detect` will work
  against any cluster your `kubectl` can reach — it only reads pod status.
- `detect` classifies each pod's *current* waiting/terminated state — e.g.
  `oom-app` is flagged `CrashLoopBackOff` once it's repeatedly restarting,
  even though the underlying `lastState.terminated.reason` is `OOMKilled`.
  That's correct: root-causing *why* a pod is crash-looping is `rca`'s job
  (agent + logs/events tools), not `detect`'s.
- `ollama serve` is running locally (default: `http://localhost:11434`) and
  `gemma3:4b` is pulled. `ollama show gemma3:4b` does not list `tools` as a
  capability, so `rca`/`fix` do not use Ollama's native function-calling —
  see `src/agent.py`'s hand-rolled ReAct loop instead.

## Results (this run)

Live run against a Kind cluster (`sre-copilot`) seeded with the four
`manifests/` failure modes:

```
Scanned 4 pod(s) in namespace 'default'

[CRITICAL] crashloop-app-9c56b96d4-zwqqh/app — CrashLoopBackOff
[CRITICAL] oom-app-6d5df965b6-s76vh/app — CrashLoopBackOff
[HIGH] imagepullbackoff-app-6d859d47b5-vwjkg/app — ErrImagePull
[MEDIUM] bad-probe-app-867bfb5f8d-s4qzv/app — NotReady

4 finding(s): 2 critical, 1 high, 1 medium
```

All four seeded failures were classified correctly. Two manifest bugs were
found and fixed during this live test, worth noting since they're easy
mistakes to repeat:
- `polinux/stress`'s `--vm-bytes` wants a decimal suffix (`200M`), not a
  binary one (`200Mi`) — with `Mi` it fails fast with an argument-parsing
  error instead of ever touching memory, which looks like an OOM demo but
  isn't one.
- A hardened `securityContext` (`capabilities: drop: ["ALL"]`) on
  `bad-probe-app` broke nginx's own entrypoint (it needs `CAP_CHOWN` to
  prep `/var/cache/nginx`), which masked the actual bug the manifest was
  meant to demonstrate (a bad readiness probe path). Security hardening on
  a *deliberately broken* demo app can accidentally introduce a second,
  unrelated failure — worth being careful about which layer a "seeded
  failure" manifest is supposed to be broken at.

`tests/test_detect.py` — 8 unit tests, all passing, covering each severity
rule against fixture kubectl JSON (no live cluster needed):

```
8 passed in 0.01s
```

### `rca` / `fix` — live against gemma3:4b

Live-tested against all four seeded failures. Results were genuinely mixed,
and it's worth reporting honestly rather than only showcasing the good run:

- **`rca` was reliable.** Against `crashloop-app`, the model initially
  finalized after a single `get_pod_status` call — technically correct
  severity, but not actually root-caused. Adding the
  `require_any_before_final={"get_logs", "get_events"}` guardrail (rejecting
  a premature final answer rather than just asking nicely in the prompt)
  fixed this: the retried run called `get_logs`, found the container's own
  `simulating a startup crash` message, and cited it directly. Same pattern
  held for `imagepullbackoff-app` (correctly reasoned that ImagePullBackOff
  explains the total absence of logs).
- **`fix --apply` succeeded end-to-end on `bad-probe-app`.** The agent
  called `get_pod_status` → `resolve_deployment_name` → diagnosed the wrong
  probe path from `get_logs` (repeated 404s on `/healthz`) →
  `set_readiness_probe_path(..., path="/")` (confirmed at the `y/N`
  prompt) → the pod went `1/1 Running` and dropped out of `detect`'s
  findings entirely on the next scan. This is a real closed loop: detect →
  diagnose → apply → verify, with no human writing the fix.
- **`fix --apply` was unreliable elsewhere**, and the failure mode is
  specific and worth naming rather than hand-waving: a *generic*
  `patch_deployment(deployment, patch_json)` tool — where `patch_json` is a
  JSON string containing another JSON object — reliably broke gemma3:4b's
  output. It would either narrate an action in the final answer without
  emitting it as a real `{"action": ...}` step (so nothing actually
  happened despite `--apply`), or get stuck in repeated
  unparseable-JSON retries. Splitting that one generic tool into three
  flat-argument tools (`set_container_image`,
  `set_container_memory_limit`, `set_readiness_probe_path`, none of them
  taking nested JSON) is what made the successful `bad-probe-app` run
  possible. The lesson: a 4B local model's tool-calling reliability is
  sensitive to argument *shape*, not just prompt wording — nested
  JSON-in-JSON is a real cliff, flat args are not.
- `fix`'s iteration budget was raised to 10 (from 6, `rca`'s default)
  because a full diagnose → resolve → apply → validate sequence routinely
  needs more turns than an investigation-only task.
- Along the way, two tool-catalog gaps surfaced from live testing and were
  fixed: `chat` had no way to discover pod names at all (no `list_pods`
  tool existed), so a "which pods are unhealthy?" question got answered
  with a hallucinated pod name instead of an honest "let me check." Adding
  `list_pods` fixed it immediately — verified live, it now correctly names
  the actual unhealthy pods and their real restart counts.

### `sop` and `scan`

- `sop` writes genuinely usable runbooks (symptoms / kubectl diagnosis
  commands / mitigation / permanent fix / escalation) — see `docs/SOP-*.md`
  for real generated examples. One recurring cosmetic bug, now fixed: the
  model often wrapped its entire answer in a stray ` ```markdown ` fence
  despite being told not to; `sop.py` strips it before writing the file.
  One un-fixed, low-stakes quirk worth naming: escalation sections invent
  a plausible-sounding named contact ("page Jane Smith") since it has no
  real on-call/org data to draw from — fine for a demo runbook, not
  something to paste into production without editing.
- `scan` ran end-to-end across all 3 remaining unhealthy pods without
  crashing, correctly aggregated a summary, and wrote 3 runbooks — but it's
  the run that exposed `rca`'s real unattended reliability rate, worth
  reporting plainly rather than only citing the earlier hand-picked
  successes: of 3 `rca` calls, 1 was a clean, well-grounded root cause
  (`imagepullbackoff-app`), 1 hit the iteration cap and returned an
  explicit `[agent stopped: ...]` failure (`crashloop-app`), and 1
  degenerated to a literal `"..."` as its entire final answer
  (`oom-app`). `fix` (dry-run) and `sop` succeeded in all 3 cases
  regardless — the pipeline is robust to a sub-step failing (it prints the
  failure and moves on), even though not every sub-step succeeds. A 4B
  local model doing unattended multi-step tool use is not yet something
  to trust unsupervised; `scan`'s value right now is triage-speed and
  runbook generation, not autonomous RCA accuracy.

### `chat`

Verified live with "Which pods are unhealthy right now and why?" — after
adding `list_pods` (see above), it correctly named both `CrashLoopBackOff`
pods with actual restart counts and separately identified the `Pending`
image-pull failure, all grounded in real tool output.

## How to run

```bash
# one-time: create the venv and install deps
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt

# unit tests (no cluster needed)
python3 -m pytest tests/ -v

# live: create a Kind cluster seeded with broken workloads, then detect
# (requires Docker Desktop running)
./scripts/setup_cluster.sh
python3 src/cli.py detect

# investigate and fix a specific pod (requires `ollama serve` running,
# and the model pulled: `ollama pull gemma3:4b`)
POD=$(kubectl get pods -l app=bad-probe-app -o jsonpath='{.items[0].metadata.name}')
python3 src/cli.py rca "$POD"
python3 src/cli.py fix "$POD"            # dry-run: prints a plan, changes nothing
python3 src/cli.py fix "$POD" --apply    # may execute changes, each confirmed interactively

python3 src/cli.py sop "CrashLoopBackOff on a Deployment"   # writes docs/SOP-*.md
python3 src/cli.py scan                                      # detect+rca+fix(dry-run)+sop for every unhealthy pod
python3 src/cli.py chat                                       # interactive REPL, read-only tools
```

Tear down the cluster with `./bin/kind delete cluster --name sre-copilot`.

## Next steps (not built yet)

All six commands from the original plan are built and live-verified. What's
still open, in priority order set by what the live testing actually found:

- **`rca` reliability under `scan`'s unattended, `verbose=False` runs was
  only 1/3 clean** (see Results) — worth a real fix, not just a note. Likely
  angles: a stricter regex/streaming-based extractor instead of "find the
  first `{` and last `}`" (which is what likely mangled multi-brace
  responses), or detecting a degenerate `"..."`/near-empty final_answer and
  forcing one more iteration the same way `require_any_before_final` does
  for premature answers.
- A side-by-side against a model Ollama lists `tools` support for (e.g.
  `qwen2.5` or `llama3.1`) would answer whether native function-calling is
  simply more reliable than this hand-rolled loop, at the cost of a larger
  download — worth knowing before assuming the ReAct loop is the ceiling.
- `sop`'s escalation section invents named contacts with no real org data
  behind them — either drop that subsection or make it clear it's a
  placeholder to fill in, so nobody copies a fictional name into a real
  runbook.
- Everything so far has been exercised by hand; `tests/` only covers
  `detect` (deterministic, cheap to test). The agent loop and prompt
  formatting are the least-tested part of this project precisely because
  they're the least deterministic — worth thinking about what a meaningful
  automated test even looks like for a small, sometimes-unreliable local
  model, rather than skipping tests for that layer entirely.
