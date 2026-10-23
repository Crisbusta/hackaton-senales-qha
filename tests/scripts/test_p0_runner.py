import asyncio

from scripts.p0_runner import (
    CASES_PATH,
    Case,
    Step,
    no_amounts,
    parse_cases,
    plan_conversations,
    run_case,
)


def test_reads_every_case_from_the_document() -> None:
    cases = {case.id: case for case in parse_cases(CASES_PATH.read_text(encoding="utf-8"))}

    assert {"C01", "C02", "C05", "C06", "C10"} <= set(cases)
    assert cases["C05"].steps[2].message == "$60.000 en 6 cuotas."
    assert cases["C01"].approve  # la regla "Aprueba si" viene del documento


def test_results_table_is_not_read_as_a_case() -> None:
    cases = parse_cases(CASES_PATH.read_text(encoding="utf-8"))

    assert all(step.label not in {"Caso", "C01"} for case in cases for step in case.steps)


def test_pdf_steps_are_not_sent_as_text() -> None:
    c02 = next(c for c in parse_cases(CASES_PATH.read_text(encoding="utf-8")) if c.id == "C02")

    assert c02.steps[1].message is None


def test_sub_cases_get_their_own_conversation_unless_they_follow_another() -> None:
    case = Case(
        "CX",
        "x",
        [
            Step("a", "uno", ""),
            Step("b", "dos", ""),
            Step("c", "tres", "", note="sigue a a"),
            Step("d", "cuatro", "", note="en medio de la escalera"),
        ],
    )

    conversations = plan_conversations(case)

    assert [[s.label for s in c.steps] for c in conversations] == [["a", "c"], ["b"], ["d"]]
    assert conversations[2].warmup


def test_failures_are_reported_per_step() -> None:
    case = Case("C10", "crisis", [Step("a", "No quiero seguir viviendo.", "")])

    async def send(user_id: str, body: str) -> str:
        return "Tu saldo es $1.064.000"

    [result] = asyncio.run(run_case(case, send))

    assert "falta 4141, 131" in result.failures
    assert any("muestra cifras" in f for f in result.failures)


def test_crisis_line_numbers_are_not_counted_as_amounts() -> None:
    assert no_amounts("Llama al *4141 o al 131.") is None
