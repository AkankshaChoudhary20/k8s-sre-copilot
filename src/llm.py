import requests

OLLAMA_URL = "http://localhost:11434/api/chat"
DEFAULT_MODEL = "gemma3:4b"
DEFAULT_TIMEOUT = 120


class OllamaError(RuntimeError):
    pass


class OllamaClient:
    """Minimal client for Ollama's /api/chat. No native tool-calling here on
    purpose — `ollama show gemma3:4b` doesn't list `tools` as a capability,
    so the ReAct loop in agent.py drives tool use through plain prompting
    instead of Ollama's `tools` parameter."""

    def __init__(self, model: str = DEFAULT_MODEL, base_url: str = OLLAMA_URL):
        self.model = model
        self.base_url = base_url

    def chat(self, messages: list[dict], temperature: float = 0.2) -> str:
        try:
            resp = requests.post(
                self.base_url,
                json={
                    "model": self.model,
                    "messages": messages,
                    "stream": False,
                    "options": {"temperature": temperature},
                },
                timeout=DEFAULT_TIMEOUT,
            )
        except requests.RequestException as e:
            raise OllamaError(
                f"could not reach Ollama at {self.base_url} — is `ollama serve` running? ({e})"
            ) from e

        if resp.status_code != 200:
            raise OllamaError(f"Ollama returned {resp.status_code}: {resp.text[:500]}")

        data = resp.json()
        message = data.get("message", {})
        content = message.get("content")
        if not content:
            raise OllamaError(f"Ollama response had no message content: {data}")
        return content
