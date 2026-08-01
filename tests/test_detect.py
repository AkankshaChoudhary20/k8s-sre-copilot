import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from detect import classify_pod, detect  # noqa: E402


def _pod(name, phase="Running", conditions=None, container_statuses=None):
    return {
        "metadata": {"name": name},
        "status": {
            "phase": phase,
            "conditions": conditions or [],
            "containerStatuses": container_statuses or [],
        },
    }


def _cs(name="app", ready=True, restarts=0, waiting=None, terminated=None):
    state = {}
    if waiting:
        state["waiting"] = waiting
    if terminated:
        state["terminated"] = terminated
    return {"name": name, "ready": ready, "restartCount": restarts, "state": state}


def test_healthy_pod_has_no_findings():
    pod = _pod("web-1", container_statuses=[_cs(ready=True, restarts=0)])
    assert classify_pod(pod) == []


def test_crashloopbackoff_is_critical():
    pod = _pod("crash-1", container_statuses=[
        _cs(ready=False, restarts=8, waiting={"reason": "CrashLoopBackOff", "message": "back-off restarting"}),
    ])
    findings = classify_pod(pod)
    assert len(findings) == 1
    assert findings[0]["severity"] == "critical"
    assert findings[0]["reason"] == "CrashLoopBackOff"


def test_oomkilled_is_critical():
    pod = _pod("oom-1", container_statuses=[
        _cs(ready=False, restarts=3, terminated={"reason": "OOMKilled", "exitCode": 137}),
    ])
    findings = classify_pod(pod)
    assert findings[0]["severity"] == "critical"
    assert findings[0]["reason"] == "OOMKilled"


def test_imagepullbackoff_is_high():
    pod = _pod("badimg-1", container_statuses=[
        _cs(ready=False, restarts=0, waiting={"reason": "ImagePullBackOff", "message": "not found"}),
    ])
    findings = classify_pod(pod)
    assert findings[0]["severity"] == "high"


def test_unschedulable_pending_is_high():
    pod = _pod("pending-1", phase="Pending", conditions=[
        {"type": "PodScheduled", "status": "False", "message": "0/1 nodes available: insufficient cpu"},
    ])
    findings = classify_pod(pod)
    assert len(findings) == 1
    assert findings[0]["severity"] == "high"
    assert findings[0]["reason"] == "Unschedulable"


def test_not_ready_no_restarts_is_medium():
    pod = _pod("probe-1", container_statuses=[_cs(ready=False, restarts=0)])
    findings = classify_pod(pod)
    assert findings[0]["severity"] == "medium"
    assert findings[0]["reason"] == "NotReady"


def test_high_restart_count_without_crashloop_is_high():
    pod = _pod("flaky-1", container_statuses=[_cs(ready=True, restarts=6)])
    findings = classify_pod(pod)
    assert findings[0]["severity"] == "high"
    assert findings[0]["reason"] == "HighRestartCount"


def test_detect_sorts_critical_first():
    pods = [
        _pod("probe-1", container_statuses=[_cs(ready=False, restarts=0)]),
        _pod("crash-1", container_statuses=[
            _cs(ready=False, restarts=8, waiting={"reason": "CrashLoopBackOff"}),
        ]),
        _pod("healthy-1", container_statuses=[_cs(ready=True, restarts=0)]),
    ]
    findings = detect(pods)
    assert [f["pod"] for f in findings] == ["crash-1", "probe-1"]
    assert findings[0]["severity"] == "critical"
