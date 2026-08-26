try:
    from langchain_core.prompts.chat import SystemMessagePromptTemplate
except Exception as exc:  # pragma: no cover
    raise ImportError(
        "SystemMessagePromptTemplate is unavailable. Install langchain-core."
    ) from exc

__all__ = ["SystemMessagePromptTemplate"]
