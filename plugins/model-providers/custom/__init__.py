"""Custom / Ollama (local) provider profile: any endpoint registered as
provider="custom" (Ollama, vLLM, llama.cpp, GLM-5.2 on ARK, …)."""

from typing import Any
from urllib.parse import urlparse

from agent.reasoning_effort import OPENAI_COMPAT_WIRE_EFFORTS, clamp_effort
from providers import register_provider
from providers.base import ProviderProfile


def _looks_like_ollama_endpoint(base_url: str | None) -> bool:
    """True only for explicit Ollama signatures (port 11434 or an ``ollama`` host label).
    ``think`` is Ollama-native; strict hosts (Mistral, Groq) 422 on it, and
    arbitrary localhost may be llama.cpp / vLLM / LM Studio."""
    raw = (base_url or "").strip()
    if not raw:
        return False
    parsed = urlparse(raw if "://" in raw else f"//{raw}")
    try:  # urlparse raises ValueError on malformed ports ("host:99999"); treat as not-Ollama.
        if parsed.port == 11434:
            return True
    except ValueError:
        return False
    host = (parsed.hostname or "").lower().rstrip(".")
    return bool(host) and (host == "ollama.com" or host.endswith(".ollama.com") or "ollama" in host.split("."))


class CustomProfile(ProviderProfile):
    """Custom/Ollama local provider — think=false and num_ctx support."""

    def _declared_efforts(self, ctx: dict) -> tuple[str, ...] | None:
        """The reasoning ladder the target route declares on its own ``/v1/models``
        (api.kotoba.cloud publishes ``reasoningEfforts`` and 400s levels outside it,
        ADR 2609160940), or None when the route declares nothing / is unknown.
        Cache-only: the picker's discovery fetch seeds the mirror, so this never
        blocks the request path on HTTP."""
        try:
            from hermes_cli.models_reasoning_caps import custom_route_model_reasoning_capabilities
            caps = custom_route_model_reasoning_capabilities(
                ctx.get("base_url"), ctx.get("model"), allow_fetch=False)
        except Exception:
            return None
        if caps and caps.get("authoritative"):
            levels = tuple(str(e) for e in (caps.get("supported_efforts") or ()))
            return levels or None
        return None

    def supported_reasoning_efforts(self, model: str | None) -> tuple[str, ...] | None:
        """Declared ladder for this profile's route (tri-state contract on ProviderProfile).

        ``self.base_url``/``model`` alone identify the route for a custom endpoint; the
        request path gets the authoritative per-call view via :meth:`_declared_efforts`.
        """
        return self._declared_efforts({"base_url": self.base_url, "model": model})

    def build_api_kwargs_extras(
        self, *, reasoning_config: dict | None = None, ollama_num_ctx: int | None = None, **ctx: Any
    ) -> tuple[dict[str, Any], dict[str, Any]]:
        extra_body: dict[str, Any] = {}
        top_level: dict[str, Any] = {}
        if ollama_num_ctx:
            extra_body["options"] = {"num_ctx": ollama_num_ctx}
        # disabled -> top-level reasoning_effort="none" (Ollama's /v1 ignores
        # extra_body.think) plus think=False only on Ollama URLs; enabled+effort ->
        # top-level reasoning_effort clamped to the OpenAI-compat wire (GLM/ARK,
        # vLLM and SGLang all top out at "max"; "ultra" verbatim 400s); enabled
        # without effort -> omit so the server default applies. Never emit
        # think=True (Ollama-only flag).
        # A route that publishes its own ladder (reasoningEfforts on /v1/models)
        # REJECTS levels outside it — clamp against the declaration, nearest
        # weaker, so a stale configured xhigh degrades honestly instead of
        # breaking the session on a 400.
        declared = self._declared_efforts(ctx)
        if reasoning_config and isinstance(reasoning_config, dict):
            effort = (reasoning_config.get("effort") or "").strip().lower()
            if effort == "none" or reasoning_config.get("enabled", True) is False:
                # See #14820.
                if declared is not None and "none" not in declared:
                    pass  # thinking-off on an OFFLESS ladder: the route rejects none — omit so its floor applies
                else:
                    top_level["reasoning_effort"] = "none"
                if _looks_like_ollama_endpoint(ctx.get("base_url")):
                    extra_body["think"] = False
            elif effort:
                top_level["reasoning_effort"] = clamp_effort(effort, declared or OPENAI_COMPAT_WIRE_EFFORTS)
        return extra_body, top_level

    def fetch_models(
        self, *, api_key: str | None = None, base_url: str | None = None, timeout: float = 8.0
    ) -> list[str] | None:
        """base_url is user-configured; fetch only if set."""
        if not (base_url or self.base_url):
            return None
        return super().fetch_models(api_key=api_key, base_url=base_url, timeout=timeout)


custom = CustomProfile(
    name="custom", aliases=("ollama", "local", "vllm", "llamacpp", "llama.cpp", "llama-cpp"),
    env_vars=(),  # No fixed key — custom endpoint
    base_url="",  # User-configured
    # An arbitrary client ceiling can exceed a local server's actual output limit.
    # The endpoint owns its generation default.
)

register_provider(custom)
