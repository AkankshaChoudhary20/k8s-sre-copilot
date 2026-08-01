from detect import detect
from fix import run_fix
from k8s import KubectlClient
from llm import DEFAULT_MODEL, OllamaError
from rca import run_rca
from sop import run_sop


def run_scan(namespace: str, context: str | None, model: str = DEFAULT_MODEL) -> None:
    """detect -> rca -> fix(dry-run only, never --apply) -> sop, per unhealthy pod."""
    client = KubectlClient(namespace=namespace, context=context)
    pods = client.list_pods()
    findings = detect(pods)

    # detect() can return multiple findings per pod (e.g. two containers);
    # scan investigates each pod once, using its highest-severity finding.
    unhealthy: dict[str, dict] = {}
    for f in findings:
        unhealthy.setdefault(f["pod"], f)

    print(f"detect: {len(unhealthy)} unhealthy pod(s) out of {len(pods)} scanned\n")

    summary = []
    for pod, finding in unhealthy.items():
        reason = finding["reason"]
        print(f"=== {pod} ({finding['severity']}: {reason}) ===")

        print("-- rca --")
        try:
            rca_text = run_rca(pod, namespace, context, model, verbose=False)
        except OllamaError as e:
            rca_text = f"[rca failed: {e}]"
        print(rca_text)

        print("\n-- fix (dry-run) --")
        try:
            fix_text = run_fix(pod, namespace, context, model, apply=False, verbose=False)
        except OllamaError as e:
            fix_text = f"[fix failed: {e}]"
        print(fix_text)

        print("\n-- sop --")
        sop_path = None
        try:
            sop_path = run_sop(f"{reason} on {pod}", model, context=rca_text)
            print(f"runbook written to {sop_path}")
        except OllamaError as e:
            print(f"[sop failed: {e}]")

        summary.append({"pod": pod, "severity": finding["severity"], "reason": reason, "sop": sop_path})
        print()

    print("=== scan summary ===")
    for s in summary:
        print(f"[{s['severity'].upper()}] {s['pod']} — {s['reason']} — runbook: {s['sop'] or 'n/a'}")
