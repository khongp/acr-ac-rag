"""
LLM Router — Cloud vs Local Model Routing
==========================================
Routes LLM inference between cloud-hosted Gemini (default/active)
and local model providers (Ollama/MedGemma — scaffolded, not yet active).

Cloud mode (default):
  - LLM:        ChatGoogleGenerativeAI (gemini-2.5-flash) via langchain_google_genai
  - Embeddings: CachedGoogleGenerativeAIEmbeddings (gemini-embedding-2) via ingest.py

Local mode (opt-in via DEPLOYMENT_MODE=local):
  - LLM:        ChatOllama via langchain_ollama (falls back to cloud if unavailable)
  - Embeddings: HuggingFaceEmbeddings (all-MiniLM-L6-v2) via langchain_huggingface

Configuration via environment variables:
  DEPLOYMENT_MODE = "cloud" (default) | "local"
  LOCAL_MODEL     = model name for Ollama (default: "llama3.1:8b")
  OLLAMA_HOST     = Ollama server URL  (default: "http://localhost:11434")
"""

import os
import warnings
import logging
from dotenv import load_dotenv
from functools import lru_cache

__all__ = ["get_llm", "get_llm_fast", "get_embeddings"]

logger = logging.getLogger("acr-ac-rag")

load_dotenv()

# ---------------------------------------------------------------------------
# Constants / defaults
# ---------------------------------------------------------------------------
_DEFAULT_LOCAL_MODEL = "llama3.1:8b"
_DEFAULT_OLLAMA_HOST = "http://localhost:11434"
_DEFAULT_PRIMARY_MODEL = "gemini-2.5-flash"
_DEFAULT_FAST_MODEL = "gemini-2.5-flash-lite"


def get_deployment_mode() -> str:
    """Return the active deployment mode: ``'cloud'`` or ``'local'``.

    Reads from the ``DEPLOYMENT_MODE`` environment variable.
    Defaults to ``'cloud'`` when the variable is absent or unrecognised.
    """
    mode = os.getenv("DEPLOYMENT_MODE", "cloud").strip().lower()
    if mode not in ("cloud", "local"):
        warnings.warn(
            f"[LLM Router] Unrecognised DEPLOYMENT_MODE='{mode}'; "
            "falling back to 'cloud'."
        )
        return "cloud"
    return mode


@lru_cache(maxsize=4)
def get_llm(temperature: float = 0.0, tier: str = "primary"):
    """Return a LangChain ``BaseChatModel`` for the active deployment mode.

    Cloud mode  → ``ChatGoogleGenerativeAI(model=LLM_PRIMARY_MODEL or LLM_FAST_MODEL)``
    Local mode  → ``ChatOllama`` configured with LOCAL_MODEL / OLLAMA_HOST.

    Parameters
    ----------
    temperature : float, optional
        Sampling temperature passed to the underlying model (default 0.0).
    tier : str, optional
        Model tier to use: 'primary' (default) or 'fast'.

    Returns
    -------
    BaseChatModel
        A LangChain chat model instance ready for ``.invoke()`` / ``.stream()``.
    """
    mode = get_deployment_mode()

    if mode == "local":
        try:
            from langchain_ollama import ChatOllama  # type: ignore[import-untyped]
        except ImportError:
            warnings.warn(
                "[LLM Router] langchain_ollama is not installed. "
                "Install it with `pip install langchain-ollama` to use local mode. "
                "Falling back to cloud (Gemini)."
            )
            mode = "cloud"  # fall through to cloud block below
        else:
            model_name = os.getenv("LOCAL_MODEL", _DEFAULT_LOCAL_MODEL).strip()
            ollama_host = os.getenv("OLLAMA_HOST", _DEFAULT_OLLAMA_HOST).strip()
            logger.info(f"[LLM Router] Using LOCAL model via Ollama: {model_name} @ {ollama_host}")
            return ChatOllama(
                model=model_name,
                base_url=ollama_host,
                temperature=temperature,
            )

    # Cloud mode (default)
    from langchain_google_genai import ChatGoogleGenerativeAI  # type: ignore[import-untyped]

    if tier == "fast":
        model_name = os.getenv("LLM_FAST_MODEL", _DEFAULT_FAST_MODEL).strip()
    else:
        model_name = os.getenv("LLM_PRIMARY_MODEL", _DEFAULT_PRIMARY_MODEL).strip()

    logger.info(f"[LLM Router] Initializing cloud model: {model_name} (tier: '{tier}', temp: {temperature})")
    return ChatGoogleGenerativeAI(
        model=model_name,
        temperature=temperature,
    )


def get_llm_fast(temperature: float = 0.0):
    """Return the cached fast-tier cloud model (default: gemini-2.5-flash-lite)."""
    return get_llm(temperature=temperature, tier="fast")


@lru_cache()
def get_embeddings():
    """Return a LangChain ``Embeddings`` instance for the active deployment mode.

    Cloud mode  → ``CachedGoogleGenerativeAIEmbeddings`` from ``ingest.py``
                   (uses ``gemini-embedding-2`` with local SQLite caching).
    Local mode  → ``HuggingFaceEmbeddings(model_name='all-MiniLM-L6-v2')``.
                   Falls back to cloud with a warning if
                   ``langchain_huggingface`` is not installed.

    Returns
    -------
    Embeddings
        A LangChain embeddings instance.
    """
    mode = get_deployment_mode()

    if mode == "local":
        try:
            from langchain_huggingface import HuggingFaceEmbeddings  # type: ignore[import-untyped]
        except ImportError:
            warnings.warn(
                "[LLM Router] langchain_huggingface is not installed. "
                "Install it with `pip install langchain-huggingface` to use local embeddings. "
                "Falling back to cloud (Gemini) embeddings."
            )
            mode = "cloud"  # fall through to cloud block below
        else:
            logger.info("[LLM Router] Using LOCAL embeddings: all-MiniLM-L6-v2")
            return HuggingFaceEmbeddings(model_name="all-MiniLM-L6-v2")

    # Cloud mode (default)
    from ingest import CachedGoogleGenerativeAIEmbeddings  # type: ignore[import-untyped]

    logger.info("[LLM Router] Using CLOUD embeddings: CachedGoogleGenerativeAIEmbeddings (gemini-embedding-2)")
    return CachedGoogleGenerativeAIEmbeddings()
