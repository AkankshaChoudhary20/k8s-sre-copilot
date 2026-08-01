"""Hand-rolled ReAct loop.

Ollama's native `tools` function-calling isn't reliably usable with
gemma3:4b (`ollama show gemma3:4b` doesn't list `tools` as a capability),
so tool use is driven by plain prompting instead: the model is asked to
reply with a single JSON action or a final answer, we execute the action
against a real tool function, and feed the result back as an observation.
"""

import json
import re
from dataclasses import dataclass, field
from typing import Callable

from llm import OllamaClient, OllamaError

MAX_ITERATIONS = 6
MAX_OBSERVATION_CHARS = 4000


@dataclass
class Tool:
    name: str
    description: str
    fn: Callable[..., str]
    params: dict[str, str] = field(default_factory=dict)  # param name -> description


def _tool_catalog(tools: dict[str, Tool]) -> str:
    lines = []
    for tool in tools.values():
        params = ", ".join(f"{k} ({v})" for k, v in tool.params.items()) or "no arguments"
        lines.append(f"- {tool.name}: {tool.description}. Args: {params}")
    return "\n".join(lines)


def _build_system_prompt(task_intro: str, tools: dict[str, Tool]) -> str:
    return f"""{task_intro}

You investigate by calling tools, one at a time. Available tools:
{_tool_catalog(tools)}

Respond with ONLY a single JSON object — no other text, no markdown fences.
To call a tool: {{"action": "<tool name>", "args": {{...}}}}
When you have enough information to answer: {{"final_answer": "<your answer>"}}

Ground every claim in what the tools actually returned. If the evidence
doesn't support a conclusion, say what's still unknown instead of guessing.
"""


def _extract_json(text: str) -> dict:
    text = text.strip()
    fence_match = re.search(r"```(?:json)?\s*(\{.*\})\s*```", text, re.DOTALL)
    if fence_match:
        text = fence_match.group(1)
    else:
        brace_match = re.search(r"\{.*\}", text, re.DOTALL)
        if brace_match:
            text = brace_match.group(0)
    return json.loads(text)


def _format_final_answer(answer) -> str:
    """The model is asked for a string final_answer, but small models
    sometimes nest structured sections in a dict instead — render either
    shape as readable text rather than a Python repr."""
    if isinstance(answer, str):
        return answer
    if isinstance(answer, dict):
        return "\n\n".join(f"{k}: {v}" for k, v in answer.items())
    return str(answer)


def run_agent(llm: OllamaClient, tools: dict[str, Tool], task_intro: str, task: str,
              max_iterations: int = MAX_ITERATIONS, verbose: bool = True,
              require_any_before_final: set[str] | None = None) -> str:
    """require_any_before_final: at least one of these tool names must have
    been called before a final_answer is accepted. gemma3:4b doesn't
    reliably follow "you must call X" as plain instruction text (verified
    empirically — it happily finalizes after a single status check), so
    this is enforced by rejecting the final_answer and forcing another
    iteration, rather than relying on wording alone."""
    messages = [
        {"role": "system", "content": _build_system_prompt(task_intro, tools)},
        {"role": "user", "content": task},
    ]
    called_tools: set[str] = set()
    require_any_before_final = require_any_before_final or set()

    for i in range(1, max_iterations + 1):
        try:
            reply = llm.chat(messages)
        except OllamaError as e:
            return f"[agent stopped: {e}]"

        messages.append({"role": "assistant", "content": reply})

        try:
            parsed = _extract_json(reply)
        except (json.JSONDecodeError, AttributeError):
            if verbose:
                snippet = reply.replace("\n", " ")[:200]
                print(f"  [iteration {i}] unparseable response, retrying... raw: {snippet!r}")
            messages.append({
                "role": "user",
                "content": (
                    "Your last response was not valid JSON. Respond with ONLY a JSON "
                    'object: {"action": "<tool>", "args": {...}} or {"final_answer": "..."}'
                ),
            })
            continue

        if "final_answer" in parsed:
            missing = require_any_before_final - called_tools
            if require_any_before_final and missing == require_any_before_final:
                if verbose:
                    print(f"  [iteration {i}] rejected early final_answer — "
                          f"must call one of: {', '.join(missing)}")
                messages.append({
                    "role": "user",
                    "content": (
                        "You haven't gathered enough evidence yet. Before answering, "
                        f"call one of: {', '.join(missing)}."
                    ),
                })
                continue
            return _format_final_answer(parsed["final_answer"])

        action = parsed.get("action")
        args = parsed.get("args", {}) or {}

        if action not in tools:
            observation = f"Unknown tool '{action}'. Available tools: {', '.join(tools)}"
            if verbose:
                print(f"  [iteration {i}] unknown tool '{action}'")
        else:
            if verbose:
                print(f"  [iteration {i}] {action}({args})")
            called_tools.add(action)
            try:
                result = tools[action].fn(**args)
            except TypeError as e:
                result = f"error calling {action}: bad arguments ({e})"
            except Exception as e:  # a bad tool call must not crash the loop
                result = f"error calling {action}: {e}"
            observation = str(result)[:MAX_OBSERVATION_CHARS]

        messages.append({"role": "user", "content": f"Observation: {observation}"})

    return (
        "[agent stopped: reached max iterations without a final answer — "
        f"last observation: {messages[-1]['content'][:500]}]"
    )
