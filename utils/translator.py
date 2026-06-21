"""
Camada de traducao.

Fornece uma funcao de traducao em lote, isolando a aplicacao do provedor
escolhido. Dois provedores sao suportados nesta versao:

  * "google_free": usa o pacote `deep-translator` (GoogleTranslator), que
    nao exige chave de API. E o modo padrao, pronto para uso imediato, mas
    nao e uma API oficial do Google e pode sofrer limitacoes de taxa em
    volumes grandes.

  * "azure": usa a API oficial do Azure AI Translator (REST), que exige uma
    chave de API e a regiao do recurso. Mais robusta e indicada para uso
    em producao / documentos grandes.

A funcao publica `get_translate_function` retorna uma funcao
`f(lista_de_textos) -> lista_de_textos_traduzidos` que preserva a ordem e o
tamanho da lista de entrada (inclusive strings vazias), alem de expor um
contador `failed_count` com a quantidade de segmentos que nao puderam ser
traduzidos (nesses casos o texto original e mantido, para nao perder
conteudo).

Duas otimizacoes de desempenho sao aplicadas antes de chamar o provedor,
de forma transparente para quem usa `translate_fn`:

  1. Deduplicacao: textos identicos (comuns em documentos com tabelas
     repetitivas, cabecalhos/rodapes, codigos, rotulos) sao traduzidos uma
     unica vez e o resultado e reaproveitado em todas as ocorrencias.
  2. Paralelizacao: no modo Google gratuito, que faz uma requisicao HTTP
     por trecho de texto (nao ha API de lote no servico nao oficial), as
     requisicoes sao disparadas em paralelo usando uma pool de threads, ja
     que e um trabalho dominado por espera de rede (I/O), nao por CPU.
"""

import re
import time
import requests
from concurrent.futures import ThreadPoolExecutor
from deep_translator import GoogleTranslator

GOOGLE_FREE_CHAR_LIMIT = 4500
GOOGLE_FREE_DEFAULT_WORKERS = 8
AZURE_BATCH_SIZE = 90
AZURE_CHAR_LIMIT_PER_ITEM = 9000
AZURE_ENDPOINT = "https://api.cognitive.microsofttranslator.com"


def _chunk_text(text: str, max_len: int):
    """Quebra um texto longo em pedacos menores, preferencialmente em
    limites de frase, respeitando o limite de caracteres do provedor."""
    if len(text) <= max_len:
        return [text]

    sentences = re.split(r"(?<=[.!?])\s+", text)
    chunks, current = [], ""
    for sentence in sentences:
        candidate = f"{current} {sentence}".strip() if current else sentence
        if len(candidate) <= max_len:
            current = candidate
        else:
            if current:
                chunks.append(current)
            # frase isolada ainda maior que o limite: corta bruto
            if len(sentence) > max_len:
                for i in range(0, len(sentence), max_len):
                    chunks.append(sentence[i:i + max_len])
                current = ""
            else:
                current = sentence
    if current:
        chunks.append(current)
    return chunks or [text[:max_len]]


def _translate_one_google(text, source_lang, target_lang, retries=3, base_delay=0.2):
    """Traduz um unico texto via Google Translate gratuito (deep-translator).

    Cria sua propria instancia de GoogleTranslator a cada chamada para ser
    seguro em contexto de threads concorrentes.
    """
    src = source_lang if source_lang else "auto"
    pieces = _chunk_text(text, GOOGLE_FREE_CHAR_LIMIT)
    translated_pieces = []
    any_failed = False

    for piece in pieces:
        success = False
        for attempt in range(retries):
            try:
                translator = GoogleTranslator(source=src, target=target_lang)
                translated = translator.translate(piece)
                translated_pieces.append(translated if translated else piece)
                success = True
                break
            except Exception:
                time.sleep(base_delay * (attempt + 1))
        if not success:
            translated_pieces.append(piece)
            any_failed = True

    return " ".join(translated_pieces), any_failed


def _translate_google_free(texts, source_lang, target_lang, max_workers=GOOGLE_FREE_DEFAULT_WORKERS):
    """Traduz uma lista de textos em paralelo (varias requisicoes HTTP
    simultaneas), pois o servico gratuito nao oferece traducao em lote."""
    results = [None] * len(texts)
    failed = 0

    if not texts:
        return results, failed

    def worker(item):
        idx, text = item
        translated, did_fail = _translate_one_google(text, source_lang, target_lang)
        return idx, translated, did_fail

    workers = max(1, min(max_workers, len(texts)))
    with ThreadPoolExecutor(max_workers=workers) as executor:
        for idx, translated, did_fail in executor.map(worker, enumerate(texts)):
            results[idx] = translated
            if did_fail:
                failed += 1

    return results, failed


def _translate_azure(texts, source_lang, target_lang, api_key, region,
                      endpoint=AZURE_ENDPOINT, retries=3, base_delay=0.5):
    results = [""] * len(texts)
    failed = 0

    translatable_idx = list(range(len(texts)))

    headers = {
        "Ocp-Apim-Subscription-Key": api_key,
        "Ocp-Apim-Subscription-Region": region,
        "Content-Type": "application/json",
    }
    params = {"api-version": "3.0", "to": target_lang}
    if source_lang and source_lang != "auto":
        params["from"] = source_lang

    batches = [
        translatable_idx[start:start + AZURE_BATCH_SIZE]
        for start in range(0, len(translatable_idx), AZURE_BATCH_SIZE)
    ]

    def send_batch(batch_idx):
        body = [{"Text": texts[i][:AZURE_CHAR_LIMIT_PER_ITEM]} for i in batch_idx]
        for attempt in range(retries):
            try:
                resp = requests.post(
                    f"{endpoint}/translate", params=params, headers=headers,
                    json=body, timeout=30,
                )
                resp.raise_for_status()
                data = resp.json()
                return batch_idx, [item["translations"][0]["text"] for item in data], 0
            except Exception:
                time.sleep(base_delay * (attempt + 1))
        return batch_idx, [texts[i] for i in batch_idx], len(batch_idx)

    # Azure ja aceita lotes de ate ~90 itens por requisicao; ainda assim
    # paralelizamos os lotes entre si quando ha muitos (documentos grandes).
    with ThreadPoolExecutor(max_workers=min(6, max(1, len(batches)))) as executor:
        for batch_idx, translated_batch, batch_failed in executor.map(send_batch, batches):
            for i, t in zip(batch_idx, translated_batch):
                results[i] = t
            failed += batch_failed

    return results, failed


class TranslateUnavailableError(Exception):
    """Erro de configuracao do provedor (ex.: chave de API ausente)."""


def get_translate_function(provider: str, source_lang: str, target_lang: str,
                            api_key: str = None, region: str = None,
                            max_workers: int = GOOGLE_FREE_DEFAULT_WORKERS):
    """
    Retorna uma funcao `f(textos: list[str]) -> list[str]` que traduz em lote.

    A funcao acumula em `f.failed_count` o numero de segmentos que falharam
    (mantidos no idioma original) ao longo de todas as chamadas, e em
    `f.dedup_savings` o total de chamadas de traducao evitadas gracas a
    textos repetidos (uteis para diagnostico de desempenho).

    Antes de chamar o provedor, a lista de entrada e deduplicada: cada
    texto distinto e traduzido uma unica vez, mesmo que apareca varias
    vezes na lista (comum em tabelas e rotulos repetidos).
    """
    if provider == "azure" and (not api_key or not region):
        raise TranslateUnavailableError(
            "Para usar o Azure Translator e necessario informar a chave de "
            "API e a regiao do recurso na barra lateral."
        )

    def translate_fn(texts):
        if not texts:
            return []

        # Deduplicacao: mantem so a primeira ocorrencia de cada texto nao
        # vazio para enviar ao provedor; strings vazias/whitespace passam
        # direto, sem chamada de API.
        unique_texts = []
        seen = set()
        for t in texts:
            if t and t.strip() and t not in seen:
                seen.add(t)
                unique_texts.append(t)

        if provider == "azure":
            translated_unique, failed = _translate_azure(
                unique_texts, source_lang, target_lang, api_key, region
            )
        else:
            translated_unique, failed = _translate_google_free(
                unique_texts, source_lang, target_lang, max_workers=max_workers
            )

        mapping = dict(zip(unique_texts, translated_unique))

        results = [mapping.get(t, t) if (t and t.strip()) else t for t in texts]

        translate_fn.failed_count += failed
        translate_fn.call_count += 1
        translate_fn.dedup_savings += (len(texts) - len(unique_texts))
        return results

    translate_fn.failed_count = 0
    translate_fn.call_count = 0
    translate_fn.dedup_savings = 0
    return translate_fn
