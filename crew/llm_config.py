"""
Shared LLM configuration for CrewAI agents.

CrewAI uses LiteLLM under the hood, which expects Ollama models to be
prefixed with "ollama/". Centralizing this here means every agent file
just imports `chat_llm` instead of re-building this string.
"""

from crewai import LLM

from common.settings import OLLAMA_BASE_URL, OLLAMA_CHAT_MODEL

chat_llm = LLM(
    model=f"ollama/{OLLAMA_CHAT_MODEL}",
    base_url=OLLAMA_BASE_URL,
    temperature=0.2,  # low temperature: we want grounded, consistent routing/phrasing, not creativity
)
