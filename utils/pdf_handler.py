"""
Traducao de documentos .pdf preservando o layout visual.

O PDF nao tem um modelo de "fluxo de texto" como o .docx: cada caractere e
posicionado em coordenadas fixas na pagina. Por isso a estrategia aqui e
diferente da usada para .docx:

  1. Cada pagina e analisada com `page.get_text("dict")`, que devolve blocos
     de texto com a posicao (bounding box), fonte, tamanho e cor de cada
     linha.
  2. Blocos de imagem (graficos, fotos, logotipos) sao ignorados e
     permanecem exatamente como estavam.
  3. O texto de cada linha e traduzido.
  4. A area original do texto e "apagada" com uma anotacao de redacao
     (`add_redact_annot` + `apply_redactions`), que remove apenas o texto
     daquela caixa delimitadora, sem afetar imagens, linhas de tabela ou
     vetores na pagina.
  5. O texto traduzido e desenhado de volta na mesma posicao
     (`insert_textbox`), tentando manter o tamanho de fonte original e
     reduzindo-o automaticamente se o texto traduzido for mais longo e nao
     couber na mesma caixa.

Isso preserva fielmente a posicao de imagens, tabelas (que em PDF sao
apenas texto + linhas vetoriais, sem uma estrutura de "tabela" propria) e o
layout geral, mas o resultado e uma aproximacao visual, e nao uma
reconstrucao perfeita do PDF original -- ver limitacoes abaixo.

Limitacoes conhecidas (reportadas ao usuario na interface):
  - As fontes embutidas no PDF original normalmente nao podem ser reusadas
    para o texto traduzido; usamos por padrao uma fonte Base-14 do
    PyMuPDF (Helvetica), que cobre bem alfabetos latinos. Para idiomas com
    alfabetos nao latinos (russo, arabe, chines, japones, coreano, grego,
    hebraico, tailandes, etc.) e necessario fornecer uma fonte TTF com
    cobertura Unicode (ex.: Noto Sans) atraves do campo de upload de fonte
    na barra lateral -- caso contrario o texto traduzido podera aparecer
    em branco ou com caracteres incorretos.
  - Se o texto traduzido for significativamente mais longo que o original,
    o tamanho da fonte e reduzido automaticamente para tentar caber na
    mesma caixa; em casos extremos o texto pode ainda assim ser cortado.
  - Numeros de pagina e algarismos isolados sao preservados sem traducao
    por heuristica (linhas compostas apenas por digitos/numeros romanos).
  - Texto dentro de imagens (ex.: graficos rasterizados, capturas de tela)
    nao e traduzido, pois nao e texto selecionavel no PDF.
"""

import re

import fitz  # PyMuPDF

_PAGE_NUMBER_RE = re.compile(
    r'^\s*(p[aá]gina|page)?\s*\.?\s*\d+\s*(de|of)?\s*\d*\s*\.?\s*$|^\s*[ivxlcdm]+\s*$',
    re.IGNORECASE,
)


def _looks_like_page_number(text: str) -> bool:
    return bool(_PAGE_NUMBER_RE.match(text.strip()))


def _color_int_to_rgb(color_int: int):
    r = ((color_int >> 16) & 255) / 255.0
    g = ((color_int >> 8) & 255) / 255.0
    b = (color_int & 255) / 255.0
    return (r, g, b)


def _collect_page_items(page):
    """Extrai linhas de texto traduziveis com posicao, fonte e cor."""
    items = []
    page_dict = page.get_text("dict")
    for block in page_dict.get("blocks", []):
        if block.get("type") != 0:  # 0 = bloco de texto, 1 = imagem
            continue
        for line in block.get("lines", []):
            spans = line.get("spans", [])
            if not spans:
                continue
            line_text = "".join(span.get("text", "") for span in spans).strip()
            if not line_text or _looks_like_page_number(line_text):
                continue
            span0 = spans[0]
            items.append({
                "bbox": line["bbox"],
                "text": line_text,
                "size": span0.get("size", 11) or 11,
                "color": _color_int_to_rgb(span0.get("color", 0)),
            })
    return items


def extract_sample_text(file_bytes: bytes, max_pages: int = 2, max_chars: int = 4000) -> str:
    doc = fitz.open(stream=file_bytes, filetype="pdf")
    parts = []
    total_len = 0
    for i in range(min(max_pages, len(doc))):
        text = doc[i].get_text()
        parts.append(text)
        total_len += len(text)
        if total_len >= max_chars:
            break
    doc.close()
    return "\n".join(parts)[:max_chars]


def count_pages(file_bytes: bytes) -> int:
    doc = fitz.open(stream=file_bytes, filetype="pdf")
    n = len(doc)
    doc.close()
    return n


def translate_pdf_bytes(file_bytes: bytes, translate_fn, font_file_path: str = None,
                         progress_callback=None):
    """
    Traduz o texto de um PDF mantendo posicionamento, imagens e elementos
    vetoriais (linhas de tabela, formas) intactos.

    `translate_fn`: funcao que recebe uma lista de strings e devolve a
    lista traduzida na mesma ordem.
    `font_file_path`: caminho opcional para uma fonte .ttf com cobertura
    Unicode, recomendada para idiomas com alfabetos nao latinos.
    `progress_callback(pagina_atual, total_paginas)`.

    Retorna (bytes_do_pdf_traduzido, lista_de_avisos).
    """
    doc = fitz.open(stream=file_bytes, filetype="pdf")
    total_pages = len(doc)
    warnings = []

    fontname = "customfont" if font_file_path else "helv"

    for page_index in range(total_pages):
        page = doc[page_index]
        items = _collect_page_items(page)

        if items:
            texts = [it["text"] for it in items]
            translated_texts = translate_fn(texts)

            # Passo 1: apaga (redage) apenas as caixas de texto originais,
            # preservando imagens e vetores fora dessas caixas.
            for it in items:
                rect = fitz.Rect(it["bbox"])
                page.add_redact_annot(rect, fill=(1, 1, 1), cross_out=False)
            page.apply_redactions()

            # Passo 2: insere o texto traduzido na mesma posicao.
            for it, new_text in zip(items, translated_texts):
                if not new_text or not new_text.strip():
                    continue
                rect = fitz.Rect(it["bbox"])
                # da uma pequena folga vertical/horizontal para evitar corte
                # por diferencas de metrica entre fontes
                rect = fitz.Rect(rect.x0, rect.y0 - 1, rect.x1 + 4, rect.y1 + 3)

                fontsize = it["size"]
                fitted = -1
                kwargs = dict(
                    fontname=fontname,
                    color=it["color"],
                    align=0,
                )
                if font_file_path:
                    kwargs["fontfile"] = font_file_path

                while fontsize > 4:
                    fitted = page.insert_textbox(rect, new_text, fontsize=fontsize, **kwargs)
                    if fitted >= 0:
                        break
                    fontsize -= 0.5

                if fitted < 0:
                    warnings.append(
                        f"Pagina {page_index + 1}: um trecho de texto traduzido era "
                        f"mais longo que o espaco original e pode ter sido cortado."
                    )

        if progress_callback:
            progress_callback(page_index + 1, total_pages)

    output = doc.tobytes(garbage=3, deflate=True)
    doc.close()
    return output, warnings
