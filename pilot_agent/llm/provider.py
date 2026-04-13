"""
LLM provider abstraction.

litellm est derrière ce Protocol — on peut le swaper (ADK, Anthropic SDK direct,
Pydantic AI...) sans toucher au reste du code.

Fallback text-tool-call parser :
  Certains modèles Ollama (gemma, phi, etc.) ne supportent pas le function calling
  structuré — ils retournent les tool calls en JSON dans le contenu texte.
  Le parser de fallback détecte et parse ces formats non-standards.
"""
from __future__ import annotations

import json
import re
import uuid
import logging
from typing import Any, Protocol, runtime_checkable

import litellm
from litellm import acompletion

# Suppress litellm's built-in prints and info banners
litellm.suppress_debug_info = True
litellm.set_verbose = False
logging.getLogger("litellm").setLevel(logging.CRITICAL)
logging.getLogger("LiteLLM").setLevel(logging.CRITICAL)

logger = logging.getLogger(__name__)


# ── Types ─────────────────────────────────────────────────────────────────────

class Message(dict):
    """Dict-based message compatible avec l'API OpenAI/litellm."""


class ToolCall:
    def __init__(self, id: str, name: str, arguments: dict[str, Any]):
        self.id = id
        self.name = name
        self.arguments = arguments

    def __repr__(self) -> str:
        return f"ToolCall({self.name}, {self.arguments})"


class LLMResponse:
    def __init__(
        self,
        content: str | None,
        tool_calls: list[ToolCall],
        stop_reason: str,
    ):
        self.content = content
        self.tool_calls = tool_calls
        self.stop_reason = stop_reason

    @property
    def is_final(self) -> bool:
        return not self.tool_calls


# ── Protocol — swappable ──────────────────────────────────────────────────────

@runtime_checkable
class LLMProvider(Protocol):
    """
    Interface unique pour tous les LLMs.
    Implémenter cette interface = remplacer litellm sans rien casser.
    """

    async def complete(
        self,
        messages: list[Message],
        tools: list[dict],
    ) -> LLMResponse: ...

    @property
    def model_id(self) -> str: ...


# ── LiteLLM implementation ─────────────────────────────────────────────────────

class LiteLLMProvider:
    """
    Provider basé sur litellm ≥ 1.83.0.

    Supporte : Claude, GPT-4, Gemini, Ollama, Mistral, Groq, …
    Le modèle est passé en argument — format litellm : "anthropic/claude-3-5-sonnet-20241022"

    Fallback automatique pour les modèles Ollama qui ne supportent pas le function
    calling structuré (gemma, phi, etc.) : parse les tool calls depuis le texte.
    """

    def __init__(self, model: str, **kwargs: Any):
        self._model = model
        self._kwargs = kwargs  # temperature, max_tokens, etc.

    @property
    def model_id(self) -> str:
        return self._model

    async def complete(
        self,
        messages: list[Message],
        tools: list[dict],
    ) -> LLMResponse:
        params: dict[str, Any] = {
            "model": self._model,
            "messages": messages,
            **self._kwargs,
        }
        if tools:
            params["tools"] = tools
            params["tool_choice"] = "auto"

        try:
            resp = await acompletion(**params)
        except litellm.AuthenticationError as e:
            provider = self._model.split("/")[0] if "/" in self._model else self._model
            raise RuntimeError(
                f"Authentication failed for {provider}. "
                f"Check that your API key is set and valid."
            ) from e
        except litellm.RateLimitError as e:
            raise RuntimeError("Rate limit reached. Try again in a moment.") from e
        except litellm.BadRequestError as e:
            msg = str(e).lower()
            if "authentication" in msg or "invalid" in msg and "api key" in msg:
                provider = self._model.split("/")[0] if "/" in self._model else self._model
                raise RuntimeError(
                    f"Authentication failed for {provider}. "
                    f"Check that your API key is set and valid."
                ) from e
            raise RuntimeError(f"Bad request: {e}") from e
        choice = resp.choices[0]
        msg = choice.message

        # ── Chemin nominal : function calling structuré ────────────────────
        tool_calls: list[ToolCall] = []
        if msg.tool_calls:
            for tc in msg.tool_calls:
                tool_calls.append(
                    ToolCall(
                        id=tc.id,
                        name=tc.function.name,
                        arguments=json.loads(tc.function.arguments or "{}"),
                    )
                )
            return LLMResponse(
                content=msg.content,
                tool_calls=tool_calls,
                stop_reason=choice.finish_reason or "stop",
            )

        # ── Fallback : le modèle a écrit les tool calls en texte ──────────
        # (gemma, phi, certains modèles Ollama)
        if msg.content and tools:
            parsed = _parse_text_tool_calls(msg.content, {
                t["function"]["name"] for t in tools
            })
            if parsed:
                logger.debug(
                    "Fallback text-tool-call parser activé pour %s : %d appels détectés",
                    self._model, len(parsed),
                )
                return LLMResponse(
                    content=None,   # consommé par le parser
                    tool_calls=parsed,
                    stop_reason="tool_calls",
                )

        return LLMResponse(
            content=msg.content,
            tool_calls=[],
            stop_reason=choice.finish_reason or "stop",
        )


def make_provider(model: str, **kwargs: Any) -> LLMProvider:
    """
    Factory.  Exemples :
        make_provider("anthropic/claude-3-5-sonnet-20241022")
        make_provider("openai/gpt-4o")
        make_provider("ollama/llama3.2", api_base="http://localhost:11434")
        make_provider("gemini/gemini-1.5-pro")
    """
    return LiteLLMProvider(model, **kwargs)


# ── Fallback text-tool-call parser ────────────────────────────────────────────

def _parse_text_tool_calls(
    content: str,
    known_tools: set[str],
) -> list[ToolCall] | None:
    """
    Parse les tool calls écrits en texte par les modèles sans function calling natif.

    Formats supportés :
      1. Gemma  : {"tool_calls": [{"function": "name", "args": {...}}]}
      2. OpenAI textuel : {"tool_calls": [{"function": {"name": "...", "arguments": {...}}}]}
      3. function_call  : {"function_call": {"name": "...", "arguments": {...}}}
      4. Action/Input (ReAct) : Action: tool_name\nAction Input: {...}

    Retourne None si aucun tool call trouvé ou si le nom n'est pas dans known_tools.
    """
    text = content.strip()

    # ── Extrait le bloc JSON (brut ou dans ```json ... ```) ────────────────
    json_block = _extract_json_block(text)
    if json_block:
        calls = _parse_json_tool_calls(json_block, known_tools)
        if calls:
            return calls

    # ── Format ReAct : Action: xxx / Action Input: {...} ──────────────────
    calls = _parse_react_format(text, known_tools)
    if calls:
        return calls

    return None


def _extract_json_block(text: str) -> dict | None:
    """Extrait le premier objet JSON valide du texte."""
    # Cherche d'abord un bloc ```json ... ```
    m = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", text, re.DOTALL)
    if m:
        try:
            return json.loads(m.group(1))
        except json.JSONDecodeError:
            pass

    # Cherche le premier { ... } dans le texte
    m = re.search(r"\{.*\}", text, re.DOTALL)
    if m:
        try:
            return json.loads(m.group())
        except json.JSONDecodeError:
            pass

    return None


def _parse_json_tool_calls(data: dict, known_tools: set[str]) -> list[ToolCall] | None:
    """Parse les formats JSON connus."""
    calls: list[ToolCall] = []

    # Format 1 — tool_calls array
    if "tool_calls" in data:
        for i, tc in enumerate(data.get("tool_calls", [])):
            if not isinstance(tc, dict):
                continue
            func = tc.get("function", "")

            if isinstance(func, str):
                # {"function": "name", "args": {...}}  — format Gemma
                name = func
                args = tc.get("args", tc.get("arguments", tc.get("parameters", {})))
            elif isinstance(func, dict):
                # {"function": {"name": "...", "arguments": {...}}}
                name = func.get("name", "")
                args = func.get("arguments", func.get("args", {}))
                if isinstance(args, str):
                    try:
                        args = json.loads(args)
                    except json.JSONDecodeError:
                        args = {}
            else:
                continue

            if name and (not known_tools or name in known_tools):
                calls.append(ToolCall(
                    id=f"fallback_{uuid.uuid4().hex[:8]}",
                    name=name,
                    arguments=args if isinstance(args, dict) else {},
                ))

    # Format 2 — function_call unique
    elif "function_call" in data:
        fc = data["function_call"]
        name = fc.get("name", "")
        args = fc.get("arguments", fc.get("args", {}))
        if isinstance(args, str):
            try:
                args = json.loads(args)
            except json.JSONDecodeError:
                args = {}
        if name and (not known_tools or name in known_tools):
            calls.append(ToolCall(
                id=f"fallback_{uuid.uuid4().hex[:8]}",
                name=name,
                arguments=args if isinstance(args, dict) else {},
            ))

    return calls or None


def _parse_react_format(text: str, known_tools: set[str]) -> list[ToolCall] | None:
    """Parse le format ReAct : 'Action: tool_name\\nAction Input: {...}'."""
    m = re.search(r"Action\s*:\s*(\S+).*?Action\s+Input\s*:\s*(\{.*?\})", text, re.DOTALL | re.IGNORECASE)
    if not m:
        return None
    name = m.group(1).strip()
    if known_tools and name not in known_tools:
        return None
    try:
        args = json.loads(m.group(2))
    except json.JSONDecodeError:
        args = {}
    return [ToolCall(id=f"fallback_{uuid.uuid4().hex[:8]}", name=name, arguments=args)]
