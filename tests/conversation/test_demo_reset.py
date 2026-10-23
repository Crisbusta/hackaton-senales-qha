import asyncio

from app.core.config import Settings
from app.modules.agent.interface import AgentRequest, AgentResponse, ConversationMode
from app.modules.conversation.repository import InMemoryConversationRepository
from app.modules.conversation.service import DEMO_RESET_REPLY, ConversationService
from app.modules.whatsapp.schemas import IncomingMessage

ADMIN = "+56911111111"
OTHER = "+56922222222"


class SpyAgent:
    def __init__(self) -> None:
        self.requests: list[AgentRequest] = []

    async def process(self, request: AgentRequest) -> AgentResponse:
        self.requests.append(request)
        return AgentResponse(message="ok")


def setup():
    agent = SpyAgent()
    repo = InMemoryConversationRepository()
    service = ConversationService(
        agent=agent, repository=repo, agent_timeout_s=5, demo_admins=frozenset({ADMIN})
    )
    return agent, repo, service


def send(service, user: str, body: str, sid: str):
    incoming = IncomingMessage(message_sid=sid, from_number=f"whatsapp:{user}", body=body)
    return asyncio.run(service.handle(incoming))


def seed(repo, user: str) -> None:
    state = asyncio.run(repo.get(user))
    state.mode = ConversationMode.BALANCE_OVERVIEW
    state.turn_count = 5
    asyncio.run(repo.save(state))


def test_admin_reset_clears_the_conversation_without_calling_the_agent() -> None:
    agent, repo, service = setup()
    seed(repo, ADMIN)

    reply = send(service, ADMIN, " /Reiniciar ", "SM1")

    state = asyncio.run(repo.get(ADMIN))
    assert reply == DEMO_RESET_REPLY
    assert state.mode is ConversationMode.GREETING
    assert state.turn_count == 0
    assert agent.requests == []


def test_reset_only_affects_the_admin_conversation() -> None:
    _, repo, service = setup()
    seed(repo, ADMIN)
    seed(repo, OTHER)

    send(service, ADMIN, "/reiniciar", "SM1")

    assert asyncio.run(repo.get(OTHER)).turn_count == 5


def test_for_other_numbers_the_command_is_just_a_message() -> None:
    agent, repo, service = setup()
    seed(repo, OTHER)

    reply = send(service, OTHER, "/reiniciar", "SM1")

    assert reply == "ok"
    assert len(agent.requests) == 1
    assert asyncio.run(repo.get(OTHER)).turn_count == 6


def test_after_a_reset_duplicates_are_still_ignored() -> None:
    _, _, service = setup()
    send(service, ADMIN, "Hola", "SM1")
    send(service, ADMIN, "/reiniciar", "SM2")

    assert send(service, ADMIN, "Hola", "SM1") is None


def test_admin_numbers_are_parsed_from_settings() -> None:
    settings = Settings(_env_file=None, demo_admin_numbers=" +56911111111, +56933333333 ,")

    assert settings.demo_admins == frozenset({"+56911111111", "+56933333333"})
    assert Settings(_env_file=None).demo_admins == frozenset()
