"""Deterministic pod-health classifier.

No LLM here on purpose: severity is decided from kubectl-observed facts
(container statuses, waiting/terminated reasons, restart counts, phase,
readiness), so `detect` stays unit-testable against fixture JSON and its
output is something an LLM can later *explain*, not something it invents.
"""

CRITICAL_WAITING_REASONS = {"CrashLoopBackOff"}
CRITICAL_TERMINATED_REASONS = {"OOMKilled", "Error"}
HIGH_WAITING_REASONS = {"ImagePullBackOff", "ErrImagePull", "CreateContainerConfigError"}
HIGH_RESTART_THRESHOLD = 5
MEDIUM_RESTART_THRESHOLD = 1

SEVERITY_ORDER = {"critical": 3, "high": 2, "medium": 1, "low": 0}


def _container_findings(pod_name: str, container_statuses: list[dict]) -> list[dict]:
    findings = []
    for cs in container_statuses or []:
        name = cs.get("name", "?")
        restarts = cs.get("restartCount", 0)
        state = cs.get("state", {})

        waiting = state.get("waiting")
        terminated = state.get("terminated")
        ready = cs.get("ready", False)

        if waiting and waiting.get("reason") in CRITICAL_WAITING_REASONS:
            findings.append({
                "pod": pod_name, "container": name, "severity": "critical",
                "reason": waiting["reason"],
                "detail": waiting.get("message", ""),
            })
        elif terminated and terminated.get("reason") in CRITICAL_TERMINATED_REASONS:
            findings.append({
                "pod": pod_name, "container": name, "severity": "critical",
                "reason": terminated["reason"],
                "detail": f"exitCode={terminated.get('exitCode')} {terminated.get('message', '')}".strip(),
            })
        elif waiting and waiting.get("reason") in HIGH_WAITING_REASONS:
            findings.append({
                "pod": pod_name, "container": name, "severity": "high",
                "reason": waiting["reason"],
                "detail": waiting.get("message", ""),
            })
        elif restarts >= HIGH_RESTART_THRESHOLD:
            findings.append({
                "pod": pod_name, "container": name, "severity": "high",
                "reason": "HighRestartCount",
                "detail": f"restartCount={restarts}",
            })
        elif not ready and restarts >= MEDIUM_RESTART_THRESHOLD:
            findings.append({
                "pod": pod_name, "container": name, "severity": "medium",
                "reason": "NotReadyWithRestarts",
                "detail": f"restartCount={restarts}",
            })
        elif not ready:
            findings.append({
                "pod": pod_name, "container": name, "severity": "medium",
                "reason": "NotReady",
                "detail": "container running but not passing readiness",
            })
    return findings


def classify_pod(pod: dict) -> list[dict]:
    """Return a list of findings (possibly empty) for a single kubectl pod object."""
    metadata = pod.get("metadata", {})
    status = pod.get("status", {})
    pod_name = metadata.get("name", "?")
    phase = status.get("phase", "Unknown")

    findings = []

    if phase == "Pending":
        conditions = status.get("conditions", [])
        unschedulable = any(
            c.get("type") == "PodScheduled" and c.get("status") == "False"
            for c in conditions
        )
        if unschedulable:
            findings.append({
                "pod": pod_name, "container": None, "severity": "high",
                "reason": "Unschedulable",
                "detail": next(
                    (c.get("message", "") for c in conditions if c.get("type") == "PodScheduled"),
                    "",
                ),
            })

    findings.extend(_container_findings(pod_name, status.get("containerStatuses")))
    return findings


def detect(pods: list[dict]) -> list[dict]:
    """Classify every pod, return findings sorted by severity (critical first)."""
    findings = []
    for pod in pods:
        findings.extend(classify_pod(pod))
    findings.sort(key=lambda f: SEVERITY_ORDER[f["severity"]], reverse=True)
    return findings
