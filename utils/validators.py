"""Validacao basica de arquivos enviados pelo usuario."""

import io

import docx
import fitz


class InvalidFileError(Exception):
    pass


def validate_pdf(file_bytes: bytes):
    if not file_bytes.startswith(b"%PDF"):
        raise InvalidFileError(
            "O arquivo nao parece ser um PDF valido (assinatura %PDF ausente)."
        )
    try:
        doc = fitz.open(stream=file_bytes, filetype="pdf")
        n_pages = len(doc)
        doc.close()
    except Exception as exc:
        raise InvalidFileError(f"Nao foi possivel abrir o PDF: {exc}") from exc
    if n_pages == 0:
        raise InvalidFileError("O PDF nao contem paginas.")
    return n_pages


def validate_docx(file_bytes: bytes):
    # arquivos .docx sao na verdade arquivos ZIP (assinatura "PK")
    if not file_bytes.startswith(b"PK"):
        raise InvalidFileError(
            "O arquivo nao parece ser um .docx valido (pode ser um .doc "
            "antigo, que nao e suportado nesta versao -- salve-o como "
            ".docx no Word e tente novamente)."
        )
    try:
        document = docx.Document(io.BytesIO(file_bytes))
        n_paragraphs = len(document.paragraphs)
    except Exception as exc:
        raise InvalidFileError(f"Nao foi possivel abrir o documento Word: {exc}") from exc
    return n_paragraphs
