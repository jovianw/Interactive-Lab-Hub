"""Sends a conversation to Claude and returns the reply text.

Needs an API key:  export ANTHROPIC_API_KEY=...
"""

import anthropic

DEFAULT_MODEL = "claude-opus-5"

client = anthropic.Anthropic()


def ask(system: str, messages: list[dict], model: str = DEFAULT_MODEL,
        max_tokens: int = 1024, effort: str | None = None) -> str:
    """effort ("low" to "max") trades thinking for speed; Haiku doesn't accept it."""
    extra = {"output_config": {"effort": effort}} if effort else {}
    response = client.messages.create(
        model=model,
        max_tokens=max_tokens,
        system=system,
        messages=messages,
        **extra,
    )
    return "".join(block.text for block in response.content if block.type == "text")
