from agent import run_agent
from k8s import KubectlClient
from llm import DEFAULT_MODEL, OllamaClient
from tools import build_read_only_tools

RCA_INTRO = """You are an SRE agent investigating a broken Kubernetes pod.

get_pod_status only tells you *that* something is wrong (a waiting/terminated
reason), never *why*. Before concluding anything, also call get_logs and/or
get_events to see the actual failure — do not stop after the status check
alone. Once you have enough evidence, give a final answer with three parts:
1. Symptom — what's observably wrong
2. Root cause — grounded in specific evidence you gathered; quote the
   evidence (a log line, an event message, a status reason)
3. Confidence — high/medium/low, and what would raise it if not high
"""


def run_rca(pod: str, namespace: str, context: str | None, model: str = DEFAULT_MODEL,
            verbose: bool = True) -> str:
    client = KubectlClient(namespace=namespace, context=context)
    llm = OllamaClient(model=model)
    tools = build_read_only_tools(client)
    task = f"Investigate pod '{pod}' in namespace '{namespace}' and write a root-cause analysis."
    return run_agent(llm, tools, RCA_INTRO, task, verbose=verbose,
                      require_any_before_final={"get_logs", "get_events"})
