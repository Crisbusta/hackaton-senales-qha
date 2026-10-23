"""Límites para PDFs no confiables."""

import io
from pathlib import Path

import pytest
from pypdf import PdfWriter

from app.modules.agent.interface import FieldStatus
from app.modules.statements import StatementError, parse_statement
from app.modules.statements.parser import MAX_PDF_BYTES

FIXTURE = Path(__file__).parent / "fixtures" / "2026-10_estado_de_cuenta_ficticio.pdf"


def _blank_pdf(pages: int) -> bytes:
    writer = PdfWriter()
    for _ in range(pages):
        writer.add_blank_page(width=200, height=200)
    buffer = io.BytesIO()
    writer.write(buffer)
    return buffer.getvalue()


@pytest.mark.parametrize(
    "data",
    [
        b"",
        b"hola",
        b"PK\x03\x04 esto es un zip",
        b"texto",
        b"\x89PNG\r\n\x1a\n" + b"0" * 64,
    ],
)
def test_rejects_non_pdf(data):
    with pytest.raises(StatementError):
        parse_statement(data)


def test_rejects_truncated_pdf():
    with pytest.raises(StatementError):
        parse_statement(b"%PDF-1.7\n" + b"basura" * 100)


def test_rejects_oversized_before_parsing():
    data = FIXTURE.read_bytes()
    padded = data + b"\n%" + b"0" * (MAX_PDF_BYTES - len(data))
    with pytest.raises(StatementError, match="pdf_demasiado_grande"):
        parse_statement(padded)


def test_rejects_too_many_pages():
    with pytest.raises(StatementError, match="pdf_demasiadas_paginas"):
        parse_statement(_blank_pdf(11))


def test_pdf_without_text_is_not_an_error():
    statement = parse_statement(_blank_pdf(1))
    assert statement.warnings == ["pdf_sin_texto"]
    assert all(f.status is FieldStatus.ABSENT for f in statement.fields.values())


def test_does_not_write_to_disk(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    parse_statement(FIXTURE.read_bytes())
    assert list(tmp_path.iterdir()) == []
