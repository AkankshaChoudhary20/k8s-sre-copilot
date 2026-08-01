"""Agent tool definitions, backed by real kubectl calls via KubectlClient.

Shared between rca/fix/chat so the tool surface (and its wording, which the
model reads) stays consistent across commands.
"""

import json
from typing import Callable

from agent import Tool
from k8s import KubectlClient


def build_read_only_tools(client: KubectlClient) -> dict[str, Tool]:
    def list_pods() -> str:
        pods = client.list_pods()
        if not pods:
            return "(no pods found)"
        lines = []
        for p in pods:
            name = p.get("metadata", {}).get("name", "?")
            phase = p.get("status", {}).get("phase", "Unknown")
            statuses = p.get("status", {}).get("containerStatuses") or []
            ready = all(cs.get("ready") for cs in statuses) if statuses else False
            restarts = sum(cs.get("restartCount", 0) for cs in statuses)
            lines.append(f"{name}  phase={phase}  ready={ready}  restarts={restarts}")
        return "\n".join(lines)

    def get_pod_status(pod: str) -> str:
        return json.dumps(client.get_pod_json(pod).get("status", {}), default=str)

    def get_logs(pod: str, container: str = "", tail: int = 100, previous: bool = False) -> str:
        logs = client.get_logs(pod, container or None, int(tail), bool(previous))
        return logs or "(no logs — container may not have started, or logs are empty)"

    def get_events(pod: str) -> str:
        events = client.get_events(pod)
        return events or "(no events found for this pod)"

    def resolve_deployment_name(pod: str) -> str:
        name = client.get_owner_deployment(pod)
        return name or "(pod is not owned by a Deployment via a ReplicaSet)"

    return {
        "list_pods": Tool(
            name="list_pods",
            description="List every pod in the namespace with phase, readiness, and restart count",
            fn=list_pods,
            params={},
        ),
        "get_pod_status": Tool(
            name="get_pod_status",
            description="Get the pod's current status JSON: phase, container states, restart counts",
            fn=get_pod_status,
            params={"pod": "pod name"},
        ),
        "describe_pod": Tool(
            name="describe_pod",
            description="Full `kubectl describe pod` output: spec, status, volumes, and recent events",
            fn=client.describe_pod,
            params={"pod": "pod name"},
        ),
        "get_logs": Tool(
            name="get_logs",
            description="Recent container logs for the pod",
            fn=get_logs,
            params={
                "pod": "pod name",
                "container": "optional container name",
                "tail": "optional line count, default 100",
                "previous": "optional bool — true to read logs from the last crashed instance",
            },
        ),
        "get_events": Tool(
            name="get_events",
            description="Recent Kubernetes events involving this pod",
            fn=get_events,
            params={"pod": "pod name"},
        ),
        "resolve_deployment_name": Tool(
            name="resolve_deployment_name",
            description="Resolve a pod's owning Deployment name (needed before scaling/restarting it)",
            fn=resolve_deployment_name,
            params={"pod": "pod name"},
        ),
    }


def build_mutating_tools(client: KubectlClient, confirm: Callable[[str], bool]) -> dict[str, Tool]:
    """Tools that change cluster state. Each call is gated by `confirm`,
    which prompts the operator before anything actually runs."""

    def rollout_restart(deployment: str) -> str:
        desc = f"kubectl rollout restart deployment/{deployment}"
        if not confirm(desc):
            return "Declined by operator — no change made."
        return client.rollout_restart(deployment)

    def scale(deployment: str, replicas: int) -> str:
        desc = f"kubectl scale deployment/{deployment} --replicas={replicas}"
        if not confirm(desc):
            return "Declined by operator — no change made."
        return client.scale(deployment, int(replicas))

    def delete_pod(pod: str) -> str:
        desc = f"kubectl delete pod {pod} (forces reschedule)"
        if not confirm(desc):
            return "Declined by operator — no change made."
        return client.delete_pod(pod)

    def set_container_image(deployment: str, container: str, image: str) -> str:
        desc = f"set deployment/{deployment} container '{container}' image to '{image}'"
        if not confirm(desc):
            return "Declined by operator — no change made."
        return client.set_container_image(deployment, container, image)

    def set_container_memory_limit(deployment: str, container: str, memory: str) -> str:
        desc = f"set deployment/{deployment} container '{container}' memory limit to '{memory}'"
        if not confirm(desc):
            return "Declined by operator — no change made."
        return client.set_container_memory_limit(deployment, container, memory)

    def set_readiness_probe_path(deployment: str, container: str, path: str) -> str:
        desc = f"set deployment/{deployment} container '{container}' readiness probe path to '{path}'"
        if not confirm(desc):
            return "Declined by operator — no change made."
        return client.set_readiness_probe_path(deployment, container, path)

    return {
        "rollout_restart": Tool(
            name="rollout_restart",
            description="Restart all pods of a Deployment (use resolve_deployment_name first)",
            fn=rollout_restart,
            params={"deployment": "deployment name"},
        ),
        "scale": Tool(
            name="scale",
            description="Change a Deployment's replica count",
            fn=scale,
            params={"deployment": "deployment name", "replicas": "target replica count"},
        ),
        "delete_pod": Tool(
            name="delete_pod",
            description="Delete a single pod to force it to be rescheduled",
            fn=delete_pod,
            params={"pod": "pod name"},
        ),
        "set_container_image": Tool(
            name="set_container_image",
            description="Permanent fix for a bad/missing image tag — patches the Deployment's container image",
            fn=set_container_image,
            params={"deployment": "deployment name", "container": "container name", "image": "new image, e.g. nginx:1.27-alpine"},
        ),
        "set_container_memory_limit": Tool(
            name="set_container_memory_limit",
            description="Permanent fix for OOMKilled — raises the Deployment container's memory limit",
            fn=set_container_memory_limit,
            params={"deployment": "deployment name", "container": "container name", "memory": "new limit, e.g. 256Mi"},
        ),
        "set_readiness_probe_path": Tool(
            name="set_readiness_probe_path",
            description="Permanent fix for a wrong readiness-probe path — patches the Deployment's httpGet probe path",
            fn=set_readiness_probe_path,
            params={"deployment": "deployment name", "container": "container name", "path": "correct HTTP path, e.g. /"},
        ),
    }
