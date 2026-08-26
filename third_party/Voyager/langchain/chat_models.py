try:
    # Legacy third-party Voyager modules import ChatOpenAI from langchain.chat_models.
    # Newer langchain exposes OpenAI chat models from langchain_openai.
    from langchain_openai import ChatOpenAI
except Exception:  # pragma: no cover - fallback for environments with missing integration package
    try:
        from langchain_community.chat_models import ChatOpenAI  # type: ignore
    except Exception as exc:  # pragma: no cover
        raise ImportError(
            "ChatOpenAI is unavailable. Install langchain-openai or langchain-community."
        ) from exc

__all__ = ["ChatOpenAI"]
