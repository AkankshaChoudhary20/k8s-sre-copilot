from agent import run_agent
from k8s import KubectlClient
from llm import DEFAULT_MODEL, OllamaClient
from tools import build_mutating_tools, build_read_only_tools

FIX_INTRO_BASE = """You are an SRE agent fixing a broken Kubernetes pod.

Use the read-only tools to gather real evidence first. Your final answer
must have four clearly labeled sections:
1. Mitigation — an immediate, low-risk action to restore service now
2. Permanent fix — the underlying change needed (e.g. a manifest edit),
   and why the mitigation alone isn't enough
3. Validation — how to confirm the fix actually worked
4. Rollback — how to undo it if it makes things worse
"""

FIX_INTRO_APPLY = FIX_INTRO_BASE + """
You also have mutating tools — each one prompts the operator for
confirmation before it runs, so it's safe to try:
- rollout_restart / scale / delete_pod: restart-class mitigations. These do
  NOT fix a bad image tag, an undersized resource limit, or a wrong probe
  path — a crashing container will just keep crashing after a restart.
- set_container_image / set_container_memory_limit / set_readiness_probe_path
  (use resolve_deployment_name first to get the target): each patches one
  specific field on the Deployment's pod spec — this is how you apply the
  permanent fix, not just the mitigation.

IMPORTANT: writing "I will restart the pod" in a final_answer does NOT
restart it. To actually take an action you must emit it as its own action
step — {"action": "rollout_restart", "args": {...}} — in a turn by itself,
BEFORE your final_answer turn. If you haven't emitted at least one mutating
action this way, nothing has been applied yet, no matter what your answer
says.
"""


def _confirm(description: str) -> bool:
    answer = input(f"Apply this action? {description} [y/N] ").strip().lower()
    return answer == "y"


def run_fix(pod: str, namespace: str, context: str | None, model: str = DEFAULT_MODEL,
            apply: bool = False, verbose: bool = True) -> str:
    client = KubectlClient(namespace=namespace, context=context)
    llm = OllamaClient(model=model)
    tools = build_read_only_tools(client)

    intro = FIX_INTRO_BASE
    if apply:
        tools.update(build_mutating_tools(client, _confirm))
        intro = FIX_INTRO_APPLY

    task = f"Investigate pod '{pod}' in namespace '{namespace}' and produce/apply a fix."
    # fix needs more room than rca: diagnose, resolve the deployment name,
    # apply, then validate is routinely 5+ steps even when nothing goes wrong.
    return run_agent(llm, tools, intro, task, verbose=verbose, max_iterations=10,
                      require_any_before_final={"get_logs", "get_events"})
