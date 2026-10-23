import asyncio
import json
from pathlib import Path

import pytest

from app.core.config import Settings
from app.modules.banking.provider import FIXTURES_DIR, MockBankProvider, build_bank_provider
from app.modules.banking.schemas import BankSnapshot


def snapshot(scenario: str | None = "patricia") -> BankSnapshot | None:
    return asyncio.run(MockBankProvider(scenario).get_snapshot("+56912345678"))


def test_patricia_scenario_loads_every_product() -> None:
    data = snapshot()

    assert data is not None
    assert data.customer.first_name == "Patricia"
    assert len(data.accounts) == 1
    assert len(data.debit_cards) == 1
    assert len(data.credit_cards) == 1
    assert len(data.consumer_loans) == 1
    assert data.transactions


def test_without_scenario_nobody_has_a_bank() -> None:
    assert snapshot(None) is None


def test_unknown_scenario_fails_at_startup() -> None:
    with pytest.raises(ValueError, match="Unknown mock bank scenario"):
        MockBankProvider("no-existe")


def test_snapshots_are_independent_copies() -> None:
    provider = MockBankProvider("patricia")
    first = asyncio.run(provider.get_snapshot("u1"))
    assert first is not None
    first.accounts[0].available_balance = 0

    second = asyncio.run(provider.get_snapshot("u1"))

    assert second is not None and second.accounts[0].available_balance == 87430


def test_factory_uses_configured_scenario() -> None:
    provider = build_bank_provider(Settings(_env_file=None, mock_bank_scenario=""))

    assert asyncio.run(provider.get_snapshot("u1")) is None


@pytest.mark.parametrize("path", sorted(FIXTURES_DIR.glob("*.json")), ids=lambda p: p.stem)
def test_every_fixture_is_valid(path: Path) -> None:
    BankSnapshot.model_validate(json.loads(path.read_text(encoding="utf-8")))


def test_without_consent_the_bank_returns_nothing(tmp_path: Path) -> None:
    data = json.loads((FIXTURES_DIR / "patricia.json").read_text(encoding="utf-8"))
    data["consent"]["granted"] = False
    (tmp_path / "revocado.json").write_text(json.dumps(data), encoding="utf-8")

    provider = MockBankProvider("revocado", fixtures_dir=tmp_path)

    assert asyncio.run(provider.get_snapshot("u1")) is None
