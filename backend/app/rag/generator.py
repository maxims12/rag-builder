"""Answer generation: build a grounded prompt from retrieved chunks and call the
configured LLM (CONTRACT.md §4 LLM Provider Protocol).

Providers behind the ``LLMProvider`` interface:
  - ``anthropic``: Anthropic Messages API (default).
  - ``openai``: OpenAI Chat Completions API.
  - ``groq``: Groq's OpenAI-compatible Chat Completions endpoint.
  - ``ollama``: local Ollama server (no API key) via its HTTP API.

SDK clients are lazy-loaded inside each provider so importing this module never
requires a key or a running server. No provider-specific calls leak into routes —
callers use :func:`get_llm_provider` and :class:`Generator`.

Context-window safety (PLAN.md Phase 6 risk): retrieved chunks are packed into
the prompt under a character budget derived from a conservative chars-per-token
estimate, so large chunk sets can't blow past the model's context window.
"""

from __future__ import annotations

import json
import logging
from typing import AsyncIterator, List, Optional, Protocol

import httpx

from app.config_schemas import CredentialsConfig, LLMConfig
from app.errors import APIError
from app.rag.retriever import RetrievedChunk

logger = logging.getLogger("app.rag.generator")

# Conservative chars-per-token estimate for context budgeting.
_CHARS_PER_TOKEN = 4
# Cap context to this many tokens regardless of model to stay well clear of
# window limits while leaving room for the system prompt + completion.
_MAX_CONTEXT_TOKENS = 6000
_OLLAMA_DEFAULT_URL = "http://localhost:11434"
_GROQ_BASE_URL = "https://api.groq.com/openai/v1"


class LLMProvider(Protocol):
    async def generate(
        self,
        prompt: str,
        system_prompt: Optional[str] = None,
        temperature: float = 0.0,
        max_tokens: int = 1024,
    ) -> str: ...

    def generate_stream(
        self,
        prompt: str,
        system_prompt: Optional[str] = None,
        temperature: float = 0.0,
        max_tokens: int = 1024,
    ) -> AsyncIterator[str]: ...


# ── Providers ──────────────────────────────────────────────────────────


class AnthropicLLM:
    """Anthropic Messages API provider."""

    def __init__(self, model: str, api_key: Optional[str]) -> None:
        self._model = model
        self._api_key = api_key
        self._client = None

    def _ensure_client(self):
        if self._client is None:
            if not self._api_key:
                raise APIError(
                    502, "Anthropic API key not configured", "PROVIDER_ERROR"
                )
            from anthropic import AsyncAnthropic

            self._client = AsyncAnthropic(api_key=self._api_key)
        return self._client

    async def generate(
        self,
        prompt: str,
        system_prompt: Optional[str] = None,
        temperature: float = 0.0,
        max_tokens: int = 1024,
    ) -> str:
        client = self._ensure_client()
        try:
            resp = await client.messages.create(
                model=self._model,
                system=system_prompt or "",
                messages=[{"role": "user", "content": prompt}],
                temperature=temperature,
                max_tokens=max_tokens,
            )
        except Exception as exc:
            raise APIError(502, f"Anthropic error: {exc}", "PROVIDER_ERROR") from exc
        return "".join(
            block.text for block in resp.content if getattr(block, "type", "") == "text"
        )

    async def generate_stream(
        self,
        prompt: str,
        system_prompt: Optional[str] = None,
        temperature: float = 0.0,
        max_tokens: int = 1024,
    ) -> AsyncIterator[str]:
        client = self._ensure_client()
        try:
            async with client.messages.stream(
                model=self._model,
                system=system_prompt or "",
                messages=[{"role": "user", "content": prompt}],
                temperature=temperature,
                max_tokens=max_tokens,
            ) as stream:
                async for text in stream.text_stream:
                    yield text
        except APIError:
            raise
        except Exception as exc:
            raise APIError(502, f"Anthropic error: {exc}", "PROVIDER_ERROR") from exc


class OpenAICompatibleLLM:
    """OpenAI Chat Completions provider (also used for Groq via base_url)."""

    def __init__(
        self,
        model: str,
        api_key: Optional[str],
        provider_name: str = "openai",
        base_url: Optional[str] = None,
    ) -> None:
        self._model = model
        self._api_key = api_key
        self._provider_name = provider_name
        self._base_url = base_url
        self._client = None

    def _ensure_client(self):
        if self._client is None:
            if not self._api_key:
                raise APIError(
                    502,
                    f"{self._provider_name} API key not configured",
                    "PROVIDER_ERROR",
                )
            from openai import AsyncOpenAI

            kwargs = {"api_key": self._api_key}
            if self._base_url:
                kwargs["base_url"] = self._base_url
            self._client = AsyncOpenAI(**kwargs)
        return self._client

    def _messages(self, prompt: str, system_prompt: Optional[str]) -> list[dict]:
        messages: list[dict] = []
        if system_prompt:
            messages.append({"role": "system", "content": system_prompt})
        messages.append({"role": "user", "content": prompt})
        return messages

    async def generate(
        self,
        prompt: str,
        system_prompt: Optional[str] = None,
        temperature: float = 0.0,
        max_tokens: int = 1024,
    ) -> str:
        client = self._ensure_client()
        try:
            resp = await client.chat.completions.create(
                model=self._model,
                messages=self._messages(prompt, system_prompt),
                temperature=temperature,
                max_tokens=max_tokens,
            )
        except Exception as exc:
            raise APIError(
                502, f"{self._provider_name} error: {exc}", "PROVIDER_ERROR"
            ) from exc
        return resp.choices[0].message.content or ""

    async def generate_stream(
        self,
        prompt: str,
        system_prompt: Optional[str] = None,
        temperature: float = 0.0,
        max_tokens: int = 1024,
    ) -> AsyncIterator[str]:
        client = self._ensure_client()
        try:
            stream = await client.chat.completions.create(
                model=self._model,
                messages=self._messages(prompt, system_prompt),
                temperature=temperature,
                max_tokens=max_tokens,
                stream=True,
            )
            async for chunk in stream:
                if not chunk.choices:
                    continue
                delta = chunk.choices[0].delta
                if delta and delta.content:
                    yield delta.content
        except APIError:
            raise
        except Exception as exc:
            raise APIError(
                502, f"{self._provider_name} error: {exc}", "PROVIDER_ERROR"
            ) from exc


class OllamaLLM:
    """Local Ollama provider via its HTTP API (no API key required)."""

    def __init__(self, model: str, base_url: Optional[str] = None) -> None:
        self._model = model
        self._base_url = (base_url or _OLLAMA_DEFAULT_URL).rstrip("/")

    async def generate(
        self,
        prompt: str,
        system_prompt: Optional[str] = None,
        temperature: float = 0.0,
        max_tokens: int = 1024,
    ) -> str:
        payload = {
            "model": self._model,
            "prompt": prompt,
            "system": system_prompt or "",
            "stream": False,
            "options": {"temperature": temperature, "num_predict": max_tokens},
        }
        try:
            async with httpx.AsyncClient(timeout=120.0) as client:
                resp = await client.post(f"{self._base_url}/api/generate", json=payload)
                resp.raise_for_status()
                return resp.json().get("response", "")
        except Exception as exc:
            raise APIError(502, f"Ollama error: {exc}", "PROVIDER_ERROR") from exc

    async def generate_stream(
        self,
        prompt: str,
        system_prompt: Optional[str] = None,
        temperature: float = 0.0,
        max_tokens: int = 1024,
    ) -> AsyncIterator[str]:
        payload = {
            "model": self._model,
            "prompt": prompt,
            "system": system_prompt or "",
            "stream": True,
            "options": {"temperature": temperature, "num_predict": max_tokens},
        }
        try:
            async with httpx.AsyncClient(timeout=120.0) as client:
                async with client.stream(
                    "POST", f"{self._base_url}/api/generate", json=payload
                ) as resp:
                    resp.raise_for_status()
                    async for line in resp.aiter_lines():
                        if not line.strip():
                            continue
                        data = json.loads(line)
                        token = data.get("response")
                        if token:
                            yield token
                        if data.get("done"):
                            break
        except APIError:
            raise
        except Exception as exc:
            raise APIError(502, f"Ollama error: {exc}", "PROVIDER_ERROR") from exc


def get_llm_provider(
    config: LLMConfig, credentials: Optional[CredentialsConfig] = None
) -> LLMProvider:
    """Factory: return the configured LLM provider behind the interface."""
    creds = credentials or CredentialsConfig()
    provider = config.llm_provider

    if provider == "anthropic":
        return AnthropicLLM(config.llm_model, creds.anthropic_api_key)
    if provider == "openai":
        return OpenAICompatibleLLM(config.llm_model, creds.openai_api_key, "openai")
    if provider == "groq":
        return OpenAICompatibleLLM(
            config.llm_model, creds.groq_api_key, "groq", base_url=_GROQ_BASE_URL
        )
    if provider == "ollama":
        return OllamaLLM(config.llm_model)

    logger.warning("Unknown LLM provider '%s'; defaulting to anthropic", provider)
    return AnthropicLLM(config.llm_model, creds.anthropic_api_key)


# ── Prompt synthesis ────────────────────────────────────────────────────


def _source_label(chunk: RetrievedChunk) -> str:
    if chunk.source_type == "web":
        return chunk.source_path_or_url
    return chunk.source_path_or_url


def build_context_prompt(query: str, chunks: List[RetrievedChunk]) -> str:
    """Assemble the user prompt: numbered context blocks + the question.

    Context is packed under a token budget so large retrievals can't overflow the
    model context window.
    """
    char_budget = _MAX_CONTEXT_TOKENS * _CHARS_PER_TOKEN
    blocks: List[str] = []
    used = 0
    for i, chunk in enumerate(chunks, start=1):
        label = _source_label(chunk)
        header = f"[{i}] Source ({chunk.source_type}): {label}"
        body = chunk.content or chunk.snippet
        block = f"{header}\n{body}"
        if used + len(block) > char_budget and blocks:
            break
        blocks.append(block)
        used += len(block)

    context = "\n\n".join(blocks) if blocks else "(no relevant context found)"
    return (
        "Use the following retrieved context to answer the question. "
        "Cite the sources you used by their bracketed numbers.\n\n"
        f"=== CONTEXT ===\n{context}\n\n"
        f"=== QUESTION ===\n{query}"
    )


class Generator:
    """Builds a grounded prompt and synthesizes an answer via the LLM provider."""

    def __init__(
        self, config: LLMConfig, credentials: Optional[CredentialsConfig] = None
    ) -> None:
        self.config = config
        self._provider = get_llm_provider(config, credentials)

    async def generate(self, query: str, chunks: List[RetrievedChunk]) -> str:
        prompt = build_context_prompt(query, chunks)
        return await self._provider.generate(
            prompt,
            system_prompt=self.config.system_prompt,
            temperature=self.config.temperature,
            max_tokens=self.config.max_tokens,
        )

    def generate_stream(
        self, query: str, chunks: List[RetrievedChunk]
    ) -> AsyncIterator[str]:
        prompt = build_context_prompt(query, chunks)
        return self._provider.generate_stream(
            prompt,
            system_prompt=self.config.system_prompt,
            temperature=self.config.temperature,
            max_tokens=self.config.max_tokens,
        )
