from agent import run_agent
from k8s import KubectlClient
from llm import DEFAULT_MODEL, OllamaClient, OllamaError
from tools import build_read_only_tools

CHAT_INTRO = """You are an SRE assistant with read-only access to a
Kubernetes cluster via kubectl-backed tools. Answer the operator's question
using the tools — don't guess at cluster state or invent pod names you
haven't checked. If the question doesn't name a specific pod, start with
list_pods to see what actually exists.
"""


def run_chat(namespace: str, context: str | None, model: str = DEFAULT_MODEL) -> None:
    client = KubectlClient(namespace=namespace, context=context)
    llm = OllamaClient(model=model)
    tools = build_read_only_tools(client)

    print(f"k8s-sre-copilot chat — namespace '{namespace}', model '{model}'. Type 'exit' to quit.\n")
    while True:
        try:
            question = input("> ").strip()
        except (EOFError, KeyboardInterrupt):
            print()
            break
        if not question:
            continue
        if question.lower() in ("exit", "quit"):
            break

        try:
            answer = run_agent(llm, tools, CHAT_INTRO, question, verbose=False)
        except OllamaError as e:
            answer = f"[error: {e}]"
        print(answer)
        print()
