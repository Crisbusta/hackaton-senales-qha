"""Fuente de datos bancarios.

`BankDataProvider` es la interfaz. `MockBankProvider` lee escenarios ficticios
desde `fixtures/`; un proveedor regulado de Open Finance implementaría la
misma interfaz sin cambios en el resto del backend.

Consentimiento: el usuario lo otorga en el portal de su banco, fuera del
chat. Igual que una API de Open Finance, el proveedor no entrega datos sin
un consentimiento vigente.
"""

import json
from pathlib import Path
from typing import Protocol

from app.core.config import Settings
from app.modules.banking.schemas import BankSnapshot

FIXTURES_DIR = Path(__file__).parent / "fixtures"


class BankDataProvider(Protocol):
    async def get_snapshot(self, user_id: str) -> BankSnapshot | None:
        """Datos bancarios del usuario, o None sin banco vinculado o sin consentimiento."""
        ...


class MockBankProvider:
    """Devuelve el mismo escenario ficticio para cualquier usuario.

    Pensado para la demo: cualquier teléfono del equipo ve los datos de
    Patricia. Sin escenario configurado, nadie tiene banco vinculado.
    """

    def __init__(self, scenario: str | None, fixtures_dir: Path = FIXTURES_DIR) -> None:
        self._snapshot = self._load(fixtures_dir, scenario) if scenario else None

    @staticmethod
    def _load(fixtures_dir: Path, scenario: str) -> BankSnapshot:
        path = fixtures_dir / f"{scenario}.json"
        if not path.is_file():
            raise ValueError(f"Unknown mock bank scenario: {scenario!r}")
        return BankSnapshot.model_validate(json.loads(path.read_text(encoding="utf-8")))

    async def get_snapshot(self, user_id: str) -> BankSnapshot | None:
        if self._snapshot is None or not self._snapshot.consent.granted:
            return None
        return self._snapshot.model_copy(deep=True)


def build_bank_provider(settings: Settings) -> BankDataProvider:
    return MockBankProvider(settings.mock_bank_scenario or None)
