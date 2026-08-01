import argparse
import sys

from k8s import KubectlClient, KubectlError
from chat import run_chat
from detect import detect
from fix import run_fix
from llm import DEFAULT_MODEL, OllamaError
from rca import run_rca
from scan import run_scan
from sop import run_sop

SEVERITY_LABEL = {"critical": "CRITICAL", "high": "HIGH", "medium": "MEDIUM"}


def cmd_detect(args) -> int:
    client = KubectlClient(namespace=args.namespace, context=args.context)
    try:
        pods = client.list_pods()
    except KubectlError as e:
        print(f"error: {e}", file=sys.stderr)
        return 1

    findings = detect(pods)

    print(f"Scanned {len(pods)} pod(s) in namespace '{args.namespace}'\n")
    if not findings:
        print("No problems detected.")
        return 0

    for f in findings:
        target = f["pod"] if not f["container"] else f"{f['pod']}/{f['container']}"
        label = SEVERITY_LABEL[f["severity"]]
        print(f"[{label}] {target} — {f['reason']}")
        if f["detail"]:
            print(f"    {f['detail']}")

    critical = sum(1 for f in findings if f["severity"] == "critical")
    high = sum(1 for f in findings if f["severity"] == "high")
    medium = sum(1 for f in findings if f["severity"] == "medium")
    print(f"\n{len(findings)} finding(s): {critical} critical, {high} high, {medium} medium")
    return 0


def cmd_rca(args) -> int:
    try:
        result = run_rca(args.pod, args.namespace, args.context, args.model)
    except OllamaError as e:
        print(f"error: {e}", file=sys.stderr)
        return 1
    print(f"\n=== Root cause analysis: {args.pod} ===\n{result}")
    return 0


def cmd_fix(args) -> int:
    try:
        result = run_fix(args.pod, args.namespace, args.context, args.model, apply=args.apply)
    except OllamaError as e:
        print(f"error: {e}", file=sys.stderr)
        return 1
    print(f"\n=== Fix plan: {args.pod} ===\n{result}")
    return 0


def cmd_sop(args) -> int:
    try:
        path = run_sop(args.topic, args.model)
    except OllamaError as e:
        print(f"error: {e}", file=sys.stderr)
        return 1
    print(f"Runbook written to {path}")
    return 0


def cmd_scan(args) -> int:
    run_scan(args.namespace, args.context, args.model)
    return 0


def cmd_chat(args) -> int:
    run_chat(args.namespace, args.context, args.model)
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="k8s-sre-copilot")
    parser.add_argument("-n", "--namespace", default="default")
    parser.add_argument("--context", default=None, help="kubectl context to use")
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("detect", help="scan cluster, classify anomalies by severity")

    rca_parser = sub.add_parser("rca", help="investigate a pod, write a grounded root-cause analysis")
    rca_parser.add_argument("pod", help="pod name")
    rca_parser.add_argument("--model", default=DEFAULT_MODEL)

    fix_parser = sub.add_parser("fix", help="produce (and optionally apply) a fix for a pod")
    fix_parser.add_argument("pod", help="pod name")
    fix_parser.add_argument("--model", default=DEFAULT_MODEL)
    fix_parser.add_argument("--apply", action="store_true",
                             help="allow the agent to execute mutating actions (each confirmed interactively)")

    sop_parser = sub.add_parser("sop", help="write a production runbook to docs/SOP-*.md")
    sop_parser.add_argument("topic", help="runbook topic, e.g. 'CrashLoopBackOff on payment-service'")
    sop_parser.add_argument("--model", default=DEFAULT_MODEL)

    scan_parser = sub.add_parser("scan", help="run detect+rca+fix(dry-run)+sop for every unhealthy pod")
    scan_parser.add_argument("--model", default=DEFAULT_MODEL)

    chat_parser = sub.add_parser("chat", help="interactive REPL with read-only kubectl tools")
    chat_parser.add_argument("--model", default=DEFAULT_MODEL)

    return parser


def main():
    parser = build_parser()
    args = parser.parse_args()

    if args.command == "detect":
        sys.exit(cmd_detect(args))
    elif args.command == "rca":
        sys.exit(cmd_rca(args))
    elif args.command == "fix":
        sys.exit(cmd_fix(args))
    elif args.command == "sop":
        sys.exit(cmd_sop(args))
    elif args.command == "scan":
        sys.exit(cmd_scan(args))
    elif args.command == "chat":
        sys.exit(cmd_chat(args))


if __name__ == "__main__":
    main()
