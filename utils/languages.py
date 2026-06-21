"""
Lista de idiomas suportados pela aplicacao.

Cada idioma tem um codigo proprio para o Google Translate (nao oficial, via
deep-translator) e para a Azure Translator API, pois os dois servicos usam
convencoes de codigo ligeiramente diferentes (ex.: chines simplificado e
"zh-CN" no Google e "zh-Hans" na Azure).
"""

# nome exibido -> {"google": codigo, "azure": codigo}
LANGUAGES = {
    "Portugues": {"google": "pt", "azure": "pt"},
    "Portugues (Portugal)": {"google": "pt", "azure": "pt-pt"},
    "Ingles": {"google": "en", "azure": "en"},
    "Espanhol": {"google": "es", "azure": "es"},
    "Frances": {"google": "fr", "azure": "fr"},
    "Alemao": {"google": "de", "azure": "de"},
    "Italiano": {"google": "it", "azure": "it"},
    "Holandes": {"google": "nl", "azure": "nl"},
    "Russo": {"google": "ru", "azure": "ru"},
    "Japones": {"google": "ja", "azure": "ja"},
    "Chines (Simplificado)": {"google": "zh-CN", "azure": "zh-Hans"},
    "Chines (Tradicional)": {"google": "zh-TW", "azure": "zh-Hant"},
    "Coreano": {"google": "ko", "azure": "ko"},
    "Arabe": {"google": "ar", "azure": "ar"},
    "Hindi": {"google": "hi", "azure": "hi"},
    "Polones": {"google": "pl", "azure": "pl"},
    "Sueco": {"google": "sv", "azure": "sv"},
    "Turco": {"google": "tr", "azure": "tr"},
    "Grego": {"google": "el", "azure": "el"},
    "Hebraico": {"google": "iw", "azure": "he"},
    "Ucraniano": {"google": "uk", "azure": "uk"},
    "Tcheco": {"google": "cs", "azure": "cs"},
    "Romeno": {"google": "ro", "azure": "ro"},
    "Hungaro": {"google": "hu", "azure": "hu"},
    "Tailandes": {"google": "th", "azure": "th"},
    "Vietnamita": {"google": "vi", "azure": "vi"},
    "Indonesio": {"google": "id", "azure": "id"},
    "Danes": {"google": "da", "azure": "da"},
    "Finlandes": {"google": "fi", "azure": "fi"},
    "Noruegues": {"google": "no", "azure": "nb"},
}

# Idiomas cujo alfabeto normalmente nao e coberto pelas fontes Base-14 do
# PyMuPDF (Helvetica/Times/Courier). Para esses, recomendamos o upload de
# uma fonte TTF com cobertura Unicode (ex.: Noto Sans, Arial Unicode, DejaVu Sans).
LANGUAGES_NEED_CUSTOM_FONT = {
    "Russo", "Japones", "Chines (Simplificado)", "Chines (Tradicional)",
    "Coreano", "Arabe", "Hindi", "Grego", "Hebraico", "Ucraniano", "Tailandes",
}


def get_code(display_name: str, provider: str) -> str:
    """Retorna o codigo de idioma correto para o provedor selecionado."""
    entry = LANGUAGES.get(display_name)
    if not entry:
        raise KeyError(f"Idioma desconhecido: {display_name}")
    return entry["azure"] if provider == "azure" else entry["google"]


# Mapa auxiliar: codigo ISO (2 letras, minusculo) -> nome em portugues,
# usado para mostrar de forma amigavel o idioma detectado automaticamente.
ISO_TO_DISPLAY = {
    "pt": "Portugues",
    "en": "Ingles",
    "es": "Espanhol",
    "fr": "Frances",
    "de": "Alemao",
    "it": "Italiano",
    "nl": "Holandes",
    "ru": "Russo",
    "ja": "Japones",
    "zh-cn": "Chines (Simplificado)",
    "zh-tw": "Chines (Tradicional)",
    "ko": "Coreano",
    "ar": "Arabe",
    "hi": "Hindi",
    "pl": "Polones",
    "sv": "Sueco",
    "tr": "Turco",
    "el": "Grego",
    "he": "Hebraico",
    "uk": "Ucraniano",
    "cs": "Tcheco",
    "ro": "Romeno",
    "hu": "Hungaro",
    "th": "Tailandes",
    "vi": "Vietnamita",
    "id": "Indonesio",
    "da": "Danes",
    "fi": "Finlandes",
    "no": "Noruegues",
}


def code_to_display(code: str) -> str:
    if not code:
        return "Desconhecido"
    return ISO_TO_DISPLAY.get(code.lower(), code.upper())
