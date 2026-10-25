"""Microsoft Foundry gateway: the only module that knows the Agent Framework SDK (spec 12.3).

Uses the app-owned agent path: ``agent_framework.Agent`` over ``agent_framework.foundry.FoundryChatClient``
(packages ``agent-framework-foundry`` 1.15.0 / ``agent-framework-core`` 1.23.0, the optional ``foundry`` extra;
exact pins in ``infra/constraints.txt``).
Structured output is requested with ``response_format`` set to the project's own Pydantic choice models.

Settings come from the environment (``.env`` is read by the app at start-up): FOUNDRY_PROJECT_ENDPOINT,
FOUNDRY_MODEL, AUTH_MODE (azure_cli for local work, managed_identity on Azure) and FOUNDRY_REASONING_EFFORT
(``minimal`` by default: the agents only choose, and the stage timeouts are short. Measured on gpt-5-mini,
2026-10-24: minimal 4.6-7.7 s per Analyst -> Producer run with 8/8 validated, one after a repair; low 7-15 s with
the Producer stage near its 8 s timeout. Use ``none`` or ``low`` for models without ``minimal``; empty sends no
reasoning option, for models that reject it). Nothing here invents an endpoint,
a model name or a successful call; ``configured`` only means the settings are present.
"""

from __future__ import annotations

import os
from dataclasses import dataclass

from .ask import ASK_INSTRUCTIONS, AskChoice
from .llm_plans import ANALYST_INSTRUCTIONS, PRODUCER_INSTRUCTIONS, AnalystChoice, ProducerChoice
from .recap import RECAP_INSTRUCTIONS, RecapChoice


@dataclass(frozen=True)
class ModelReply:
    value: object  # the parsed choice model
    usage: dict  # token counts as reported by the service (empty if none were reported)


def load_env_file(path) -> None:
    """KEY=VALUE lines from .env. The real environment wins; values are never logged or returned."""
    from pathlib import Path
    path = Path(path)
    if os.environ.get("PITCHIQ_DOTENV", "1") == "0" or not path.is_file():
        return
    for line in path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key, value = key.strip(), value.strip().strip('"').strip("'")
        if key and key not in os.environ:
            os.environ[key] = value


def foundry_settings() -> dict:
    return {"endpoint": os.environ.get("FOUNDRY_PROJECT_ENDPOINT", "").strip(),
            "model": os.environ.get("FOUNDRY_MODEL", "").strip(),
            "auth_mode": os.environ.get("AUTH_MODE", "azure_cli").strip() or "azure_cli",
            "reasoning_effort": os.environ.get("FOUNDRY_REASONING_EFFORT", "minimal").strip(),
            "llm_mode": os.environ.get("LLM_MODE", "offline").strip() or "offline"}


def sdk_available() -> bool:
    try:
        import agent_framework  # noqa: F401
        import agent_framework.foundry  # noqa: F401
        import azure.identity.aio  # noqa: F401
    except ImportError:
        return False
    return True


class FoundryGateway:
    """Four app-owned agents on one Foundry chat client: Analyst and Producer (choose a plan, no tools), Ask
    (answers a viewer's question) and Recap (chooses the moments that tell the replay so far), both with read-only
    tools passed per run. Create on the event loop that uses it."""

    def __init__(self, endpoint: str, model: str, auth_mode: str = "azure_cli", reasoning_effort: str = "minimal"):
        from agent_framework import Agent
        from agent_framework.foundry import FoundryChatClient
        from azure.identity.aio import AzureCliCredential, ManagedIdentityCredential

        self.model = model
        self._extra = {"reasoning": {"effort": reasoning_effort}} if reasoning_effort else {}
        self._credential = ManagedIdentityCredential() if auth_mode == "managed_identity" else AzureCliCredential()
        client = FoundryChatClient(project_endpoint=endpoint, model=model, credential=self._credential)
        self._analyst = Agent(client, ANALYST_INSTRUCTIONS, name="pitchiq-analyst")
        self._producer = Agent(client, PRODUCER_INSTRUCTIONS, name="pitchiq-producer")
        self._ask = Agent(client, ASK_INSTRUCTIONS, name="pitchiq-ask")
        self._recap = Agent(client, RECAP_INSTRUCTIONS, name="pitchiq-recap")
        fic = getattr(client, "function_invocation_configuration", None)
        if isinstance(fic, dict):  # model round trips per run; the app's tool budgets are AskLimits / RecapLimits
            fic["max_iterations"] = 6

    @classmethod
    def from_env(cls) -> "FoundryGateway":
        s = foundry_settings()
        if not (s["endpoint"] and s["model"]):
            raise RuntimeError("FOUNDRY_PROJECT_ENDPOINT and FOUNDRY_MODEL are not set")
        return cls(s["endpoint"], s["model"], s["auth_mode"], s["reasoning_effort"])

    async def _run(self, agent, prompt: str, schema, tools=None) -> ModelReply:
        # no sampling options: some deployed models (reasoning models) reject them; the schema is the constraint
        resp = await agent.run(prompt, tools=tools, options={"response_format": schema, **self._extra})
        usage = dict(resp.usage_details or {}) if getattr(resp, "usage_details", None) else {}
        return ModelReply(resp.value, {k: v for k, v in usage.items() if isinstance(v, (int, float))})

    async def analyst(self, prompt: str) -> ModelReply:
        return await self._run(self._analyst, prompt, AnalystChoice)

    async def producer(self, prompt: str) -> ModelReply:
        return await self._run(self._producer, prompt, ProducerChoice)

    async def ask(self, prompt: str, tools: list) -> ModelReply:
        return await self._run(self._ask, prompt, AskChoice, tools)

    async def recap(self, prompt: str, tools: list) -> ModelReply:
        return await self._run(self._recap, prompt, RecapChoice, tools)

    async def close(self) -> None:
        close = getattr(self._credential, "close", None)
        if close is not None:
            await close()
