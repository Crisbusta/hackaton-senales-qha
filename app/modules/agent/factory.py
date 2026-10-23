"""Selección de la implementación del agente según AGENT_IMPL.

El backend solo conoce el protocolo `Agent`. `LlmAgent` se importa de forma
diferida para que el backend y sus tests no dependan del proveedor LLM.
"""

from app.core.config import Settings
from app.modules.agent.interface import Agent
from app.modules.agent.mock import MockAgent


def build_agent(settings: Settings) -> Agent:
    if settings.agent_impl == "llm":
        from app.modules.agent.service import LlmAgent

        return LlmAgent()
    return MockAgent()
