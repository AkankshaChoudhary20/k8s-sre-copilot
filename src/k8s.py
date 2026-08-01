import json
import subprocess


class KubectlError(RuntimeError):
    pass


class KubectlClient:
    """Thin subprocess wrapper around kubectl. No k8s client library, no
    kubeconfig handling of our own — whatever context `kubectl` is already
    pointed at is what we operate on."""

    def __init__(self, namespace: str = "default", context: str | None = None):
        self.namespace = namespace
        self.context = context

    def _run(self, args: list[str], check: bool = True) -> str:
        cmd = ["kubectl"]
        if self.context:
            cmd += ["--context", self.context]
        cmd += args
        result = subprocess.run(cmd, capture_output=True, text=True)
        if check and result.returncode != 0:
            raise KubectlError(result.stderr.strip() or f"kubectl {' '.join(args)} failed")
        return result.stdout

    # --- read-only ---

    def list_pods(self) -> list[dict]:
        out = self._run(["get", "pods", "-n", self.namespace, "-o", "json"])
        return json.loads(out).get("items", [])

    def get_pod_json(self, pod: str) -> dict:
        out = self._run(["get", "pod", pod, "-n", self.namespace, "-o", "json"])
        return json.loads(out)

    def describe_pod(self, pod: str) -> str:
        return self._run(["describe", "pod", pod, "-n", self.namespace])

    def get_logs(self, pod: str, container: str | None = None, tail: int = 100,
                 previous: bool = False) -> str:
        args = ["logs", pod, "-n", self.namespace, "--tail", str(tail)]
        if container:
            args += ["-c", container]
        if previous:
            args += ["--previous"]
        return self._run(args, check=False)

    def get_events(self, pod: str) -> str:
        return self._run([
            "get", "events", "-n", self.namespace,
            "--field-selector", f"involvedObject.name={pod}",
            "--sort-by=.lastTimestamp",
        ])

    def get_owner_deployment(self, pod: str) -> str | None:
        """Resolve Pod -> ReplicaSet -> Deployment, since mutating ops (scale,
        rollout restart) target the Deployment, not the hash-suffixed pod name."""
        pod_obj = self.get_pod_json(pod)
        owners = pod_obj.get("metadata", {}).get("ownerReferences", [])
        rs_name = next((o["name"] for o in owners if o.get("kind") == "ReplicaSet"), None)
        if not rs_name:
            return None
        rs_json = json.loads(self._run(["get", "replicaset", rs_name, "-n", self.namespace, "-o", "json"]))
        rs_owners = rs_json.get("metadata", {}).get("ownerReferences", [])
        return next((o["name"] for o in rs_owners if o.get("kind") == "Deployment"), None)

    # --- mutating (fix.py only, gated behind --apply + confirmation) ---

    def rollout_restart(self, deployment: str) -> str:
        return self._run(["rollout", "restart", f"deployment/{deployment}", "-n", self.namespace])

    def scale(self, deployment: str, replicas: int) -> str:
        return self._run([
            "scale", f"deployment/{deployment}", f"--replicas={replicas}", "-n", self.namespace,
        ])

    def patch(self, resource: str, name: str, patch_json: dict) -> str:
        return self._run([
            "patch", resource, name, "-n", self.namespace,
            "--type=strategic", "-p", json.dumps(patch_json),
        ])

    # Flat-argument convenience patches. A generic patch(resource, name, dict)
    # tool asks an LLM to emit JSON-inside-JSON as a tool argument, which a
    # small local model (gemma3:4b) reliably fumbles — these give it the
    # three permanent-fix shapes this project's demo failures actually need,
    # each with plain string/number arguments only.

    def set_container_image(self, deployment: str, container: str, image: str) -> str:
        patch = {"spec": {"template": {"spec": {"containers": [{"name": container, "image": image}]}}}}
        return self.patch("deployment", deployment, patch)

    def set_container_memory_limit(self, deployment: str, container: str, memory: str) -> str:
        patch = {"spec": {"template": {"spec": {"containers": [
            {"name": container, "resources": {"limits": {"memory": memory}}},
        ]}}}}
        return self.patch("deployment", deployment, patch)

    def set_readiness_probe_path(self, deployment: str, container: str, path: str) -> str:
        patch = {"spec": {"template": {"spec": {"containers": [
            {"name": container, "readinessProbe": {"httpGet": {"path": path}}},
        ]}}}}
        return self.patch("deployment", deployment, patch)

    def delete_pod(self, pod: str) -> str:
        return self._run(["delete", "pod", pod, "-n", self.namespace])
