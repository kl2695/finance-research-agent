"""Anthropic API client with prompt caching and model routing."""

from __future__ import annotations

import json
import logging
import time
from typing import Any, Callable

import anthropic
from dotenv import load_dotenv

load_dotenv()

log = logging.getLogger(__name__)

MODEL_SONNET = "claude-sonnet-4-20250514"
MODEL_HAIKU = "claude-haiku-4-5-20251001"

_client: anthropic.Anthropic | None = None


def get_client() -> anthropic.Anthropic:
    global _client
    if _client is None:
        _client = anthropic.Anthropic(max_retries=10)
    return _client


_last_call_time: float = 0
CALL_DELAY_SECONDS = 1.0


def call_claude(
    *,
    system: str,
    messages: list[dict],
    tools: list[dict] | None = None,
    max_tokens: int = 4096,
    model: str | None = None,
) -> anthropic.types.Message:
    """Single Anthropic API call with prompt caching."""
    global _last_call_time
    elapsed = time.time() - _last_call_time
    if elapsed < CALL_DELAY_SECONDS:
        time.sleep(CALL_DELAY_SECONDS - elapsed)

    system_blocks = [
        {"type": "text", "text": system, "cache_control": {"type": "ephemeral"}}
    ]

    kwargs: dict[str, Any] = {
        "model": model or MODEL_SONNET,
        "max_tokens": max_tokens,
        "system": system_blocks,
        "messages": messages,
    }
    if tools:
        kwargs["tools"] = tools

    result = get_client().messages.create(**kwargs)
    _last_call_time = time.time()
    return result


def call_with_tools(
    *,
    system: str,
    user_message: str,
    tools: list[dict],
    tool_executor: Callable[[str, dict], str],
    max_turns: int = 10,
    max_time: float = 120.0,
) -> tuple[str, list[dict]]:
    """Multi-turn tool-use loop. Returns (final_text, tool_log)."""
    messages: list[dict] = [{"role": "user", "content": user_message}]
    tool_log: list[dict] = []
    start_time = time.time()

    for turn in range(max_turns):
        # Wall-clock timeout guard (P84)
        if time.time() - start_time > max_time:
            log.warning(f"ReAct loop timed out after {max_time}s — returning current results")
            break

        response = call_claude(system=system, messages=messages, tools=tools)

        assistant_content: list[dict] = []
        tool_results: list[dict] = []
        has_local_tool_calls = False

        for block in response.content:
            if block.type == "text":
                assistant_content.append({"type": "text", "text": block.text})
            elif block.type == "tool_use":
                assistant_content.append({
                    "type": "tool_use", "id": block.id,
                    "name": block.name, "input": block.input,
                })
                has_local_tool_calls = True
                try:
                    result = tool_executor(block.name, block.input)
                except Exception as e:
                    result = f"Error: {e}"
                truncated = result[:4000] if len(result) > 4000 else result
                tool_log.append({"tool": block.name, "input": block.input, "output": truncated})
                tool_results.append({
                    "type": "tool_result", "tool_use_id": block.id, "content": truncated,
                })
            else:
                dumped = block.model_dump()
                assistant_content.append(dumped)
                if block.type == "web_search_tool_result":
                    content = getattr(block, "content", [])
                    if isinstance(content, list):
                        for r in content:
                            if hasattr(r, "url") and hasattr(r, "title"):
                                tool_log.append({
                                    "tool": "web_search_result",
                                    "url": r.url, "title": r.title,
                                    "input": {}, "output": f"{r.title}: {r.url}",
                                })

        messages.append({"role": "assistant", "content": assistant_content})

        if response.stop_reason == "tool_use" and has_local_tool_calls:
            messages.append({"role": "user", "content": tool_results})
        elif response.stop_reason == "end_turn" or not has_local_tool_calls:
            final_text = "".join(
                block.text for block in response.content if block.type == "text"
            )
            return final_text, tool_log
        else:
            messages.append({"role": "user", "content": tool_results})

    return "Max turns reached.", tool_log


def parse_json_response(text: str) -> dict:
    """Parse JSON from Claude's response, stripping markdown fences."""
    text = text.strip()
    if text.startswith("```"):
        text = text.split("\n", 1)[1]
        text = text.rsplit("```", 1)[0]
    return json.loads(text)
