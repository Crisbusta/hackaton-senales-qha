from app.core.config import Settings
from app.modules.agent.factory import build_agent
from app.modules.agent.mock import MockAgent


def test_mock_is_the_default_agent() -> None:
    assert isinstance(build_agent(Settings(_env_file=None)), MockAgent)
