"""Model calls (OpenAI Responses API via Portkey) with token and cost tracking."""

from __future__ import annotations

import json
import time
import uuid
from dataclasses import dataclass, field
from typing import Callable

from .config import LOGS_DIR, MAX_TOOL_ROUNDS, cost_usd, get_client, model_name


@dataclass
class CallRecord:
    agent: str
    input_tokens: int
    cached_tokens: int
    output_tokens: int
    reasoning_tokens: int
    latency_s: float
    cost_usd: float


@dataclass
class RunTracker:
    """Collects every model call in one user request and logs it to logs/usage.jsonl."""

    kind: str
    run_id: str = field(default_factory=lambda: uuid.uuid4().hex[:10])
    calls: list[CallRecord] = field(default_factory=list)
    started: float = field(default_factory=time.time)

    def add(self, agent: str, usage, latency: float) -> None:
        inp = getattr(usage, "input_tokens", 0) or 0
        out = getattr(usage, "output_tokens", 0) or 0
        cached = getattr(getattr(usage, "input_tokens_details", None), "cached_tokens", 0) or 0
        reasoning = getattr(getattr(usage, "output_tokens_details", None), "reasoning_tokens", 0) or 0
        rec = CallRecord(agent, inp, cached, out, reasoning, round(latency, 2), cost_usd(inp, cached, out))
        self.calls.append(rec)
        LOGS_DIR.mkdir(exist_ok=True)
        with (LOGS_DIR / "usage.jsonl").open("a", encoding="utf-8") as f:
            f.write(json.dumps({
                "ts": time.strftime("%Y-%m-%d %H:%M:%S"), "run_id": self.run_id, "kind": self.kind,
                "model": model_name(), **rec.__dict__,
            }) + "\n")

    def summary(self) -> dict:
        return {
            "run_id": self.run_id,
            "model": model_name(),
            "calls": [c.__dict__ for c in self.calls],
            "input_tokens": sum(c.input_tokens for c in self.calls),
            "output_tokens": sum(c.output_tokens for c in self.calls),
            "reasoning_tokens": sum(c.reasoning_tokens for c in self.calls),
            "cost_usd": round(sum(c.cost_usd for c in self.calls), 6),
            "wall_time_s": round(time.time() - self.started, 1),
        }


_JSON_REMINDER = {"type": "input_text", "text": "Reply with the JSON object described in your instructions."}


class AgentError(RuntimeError):
    pass


def _parse_json(text: str) -> dict:
    text = (text or "").strip()
    if text.startswith("```"):
        text = text.strip("`").split("\n", 1)[-1]
    start, end = text.find("{"), text.rfind("}")
    if start == -1 or end == -1:
        raise ValueError("no JSON object in reply")
    return json.loads(text[start : end + 1])


def call_json(
    agent: str,
    instructions: str,
    content: list[dict],
    tracker: RunTracker,
    effort: str = "low",
) -> dict:
    """Single model call that must return a JSON object. Retries once on bad JSON."""

    client = get_client()
    # JSON mode requires the word "json" in the input messages, not just instructions.
    messages = [{"role": "user", "content": content + [_JSON_REMINDER]}]
    for attempt in range(2):
        t0 = time.time()
        resp = client.responses.create(
            model=model_name(),
            instructions=instructions,
            input=messages,
            reasoning={"effort": effort},
            text={"format": {"type": "json_object"}},
        )
        tracker.add(agent, resp.usage, time.time() - t0)
        try:
            return _parse_json(resp.output_text)
        except (ValueError, json.JSONDecodeError):
            if attempt == 1:
                raise AgentError(f"{agent} returned malformed JSON twice.")
            messages.append({"role": "user", "content": "Your last reply was not valid JSON. Reply with the JSON object only."})
    raise AgentError(f"{agent} failed")


def call_with_tools(
    agent: str,
    instructions: str,
    content: list[dict],
    tools: list[dict],
    dispatch: Callable[[str, dict], dict],
    tracker: RunTracker,
    effort: str = "low",
) -> tuple[dict, list[dict]]:
    """Tool-calling loop. Returns (final JSON, trace of tool calls)."""

    client = get_client()
    history: list = [{"role": "user", "content": content + [_JSON_REMINDER]}]
    trace: list[dict] = []
    for round_no in range(MAX_TOOL_ROUNDS + 1):
        final_round = round_no == MAX_TOOL_ROUNDS
        t0 = time.time()
        resp = client.responses.create(
            model=model_name(),
            instructions=instructions,
            input=history,
            tools=tools,
            tool_choice="none" if final_round else "auto",
            reasoning={"effort": effort},
            text={"format": {"type": "json_object"}},
        )
        tracker.add(agent, resp.usage, time.time() - t0)
        calls = [o for o in resp.output if o.type == "function_call"]
        if not calls:
            try:
                return _parse_json(resp.output_text), trace
            except (ValueError, json.JSONDecodeError) as exc:
                raise AgentError(f"{agent} returned malformed JSON.") from exc
        history += resp.output
        for call in calls:
            try:
                args = json.loads(call.arguments or "{}")
                result = dispatch(call.name, args)
            except Exception as exc:  # noqa: BLE001 - report tool errors back to the model
                args, result = {}, {"error": str(exc)}
            trace.append({"tool": call.name, "args": args, "result": result})
            history.append({"type": "function_call_output", "call_id": call.call_id, "output": json.dumps(result)})
    raise AgentError(f"{agent} exceeded {MAX_TOOL_ROUNDS} tool rounds.")
