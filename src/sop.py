import os
import re

from llm import DEFAULT_MODEL, OllamaClient

DOCS_DIR = "docs"

SOP_SYSTEM_PROMPT = """You write production Kubernetes runbooks (SOPs) for
an on-call SRE audience. Write in Markdown with these sections:
# <Topic>
## Symptoms
## Diagnosis steps
(concrete kubectl commands, not vague advice)
## Mitigation
## Permanent fix
## Escalation
(when to page someone else, and who)

Be specific and actionable. Do not pad with generic advice — every line
should be something an on-call engineer could actually follow at 3am.
Output only the Markdown document, nothing else."""


def _slugify(topic: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", topic.lower()).strip("-")
    return slug or "untitled"


def _strip_markdown_fence(text: str) -> str:
    """Despite being told to output only Markdown, small models often still
    wrap the whole thing in a ```markdown ... ``` fence — strip it if present
    so docs/SOP-*.md is clean Markdown, not a fenced code block of Markdown."""
    text = text.strip()
    match = re.match(r"^```(?:markdown)?\s*\n(.*)\n```$", text, re.DOTALL)
    return match.group(1) if match else text


def run_sop(topic: str, model: str = DEFAULT_MODEL, context: str = "") -> str:
    """Write a runbook for `topic` to docs/SOP-<slug>.md and return the path."""
    llm = OllamaClient(model=model)
    user_content = f"Write a runbook for: {topic}"
    if context:
        user_content += f"\n\nRelevant context gathered from the cluster:\n{context}"

    content = llm.chat([
        {"role": "system", "content": SOP_SYSTEM_PROMPT},
        {"role": "user", "content": user_content},
    ])
    content = _strip_markdown_fence(content)

    os.makedirs(DOCS_DIR, exist_ok=True)
    path = os.path.join(DOCS_DIR, f"SOP-{_slugify(topic)}.md")
    with open(path, "w") as f:
        f.write(content.strip() + "\n")
    return path
