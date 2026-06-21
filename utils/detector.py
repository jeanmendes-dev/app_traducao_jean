"""Deteccao automatica do idioma de origem do documento."""

from langdetect import detect_langs, DetectorFactory

# Resultados deterministicos entre execucoes
DetectorFactory.seed = 0


def detect_language(text: str):
    """
    Detecta o idioma predominante de um texto.

    Retorna uma tupla (codigo_iso, confianca) onde confianca esta entre 0 e 1.
    Se nao for possivel detectar (texto vazio, curto demais, so numeros/simbolos
    etc.), retorna (None, 0.0).
    """
    if not text:
        return None, 0.0

    sample = text.strip()[:4000]
    if len(sample) < 8:
        return None, 0.0

    try:
        candidates = detect_langs(sample)
        if not candidates:
            return None, 0.0
        top = candidates[0]
        return top.lang, float(top.prob)
    except Exception:
        return None, 0.0
