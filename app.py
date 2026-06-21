"""
Tradutor de Documentos com Preservacao de Formatacao
=====================================================

Aplicativo Streamlit que traduz documentos PDF e Word (.docx), mantendo a
formatacao original (tabelas, imagens, fontes, cabecalhos, rodapes,
numeracao de pagina) tanto quanto tecnicamente possivel.

Para executar:
    pip install -r requirements.txt
    streamlit run app.py
"""

import os
import tempfile
import traceback
from datetime import datetime

import streamlit as st

from utils.detector import detect_language
from utils.languages import LANGUAGES, LANGUAGES_NEED_CUSTOM_FONT, code_to_display, get_code
from utils.translator import TranslateUnavailableError, get_translate_function
from utils.validators import InvalidFileError, validate_docx, validate_pdf
from utils.docx_handler import (
    count_translatable_segments,
    extract_sample_text as extract_sample_text_docx,
    translate_docx_bytes,
)
from utils.pdf_handler import (
    count_pages,
    extract_sample_text as extract_sample_text_pdf,
    translate_pdf_bytes,
)

st.set_page_config(
    page_title="Tradutor de Documentos",
    page_icon="🌐",
    layout="centered",
)

# --------------------------------------------------------------------------
# Estado da sessao
# --------------------------------------------------------------------------
DEFAULT_STATE = {
    "file_id": None,
    "file_bytes": None,
    "file_name": None,
    "file_ext": None,
    "detected_code": None,
    "detected_prob": 0.0,
    "translated_bytes": None,
    "translated_name": None,
    "warnings": [],
    "failed_count": 0,
    "dedup_savings": 0,
    "is_translating": False,
}
for key, value in DEFAULT_STATE.items():
    if key not in st.session_state:
        st.session_state[key] = value


def reset_translation_outputs():
    st.session_state["translated_bytes"] = None
    st.session_state["translated_name"] = None
    st.session_state["warnings"] = []
    st.session_state["failed_count"] = 0
    st.session_state["dedup_savings"] = 0


# --------------------------------------------------------------------------
# Barra lateral: provedor de traducao e opcoes avancadas
# --------------------------------------------------------------------------
st.sidebar.header("⚙️ Configuracoes de traducao")

provider_label = st.sidebar.radio(
    "Provedor de traducao",
    options=["Google Translate (gratuito, sem chave)", "Azure Translator (requer chave de API)"],
    index=0,
    help=(
        "O modo gratuito usa o Google Translate de forma nao oficial e nao "
        "requer nenhuma configuracao, mas pode ser mais lento ou sofrer "
        "limitacao de taxa em documentos muito grandes. O Azure Translator "
        "e uma API paga e oficial, recomendada para uso intensivo."
    ),
)
provider = "azure" if provider_label.startswith("Azure") else "google_free"

azure_key, azure_region = None, None
if provider == "azure":
    azure_key = st.sidebar.text_input("Chave de API do Azure Translator", type="password")
    azure_region = st.sidebar.text_input("Regiao do recurso (ex.: brazilsouth, eastus)")
    st.sidebar.caption(
        "Crie um recurso 'Translator' no portal do Azure para obter a chave e a regiao."
    )

max_workers = st.sidebar.slider(
    "Paralelismo (requisicoes simultaneas)",
    min_value=1, max_value=16, value=8,
    help=(
        "Quantas traducoes sao enviadas ao mesmo tempo. Valores mais altos "
        "aceleram bastante documentos grandes, mas no modo gratuito do "
        "Google um valor muito alto pode disparar bloqueio temporario por "
        "excesso de requisicoes. 8 costuma ser um bom equilibrio."
    ),
)

st.sidebar.markdown("---")
st.sidebar.subheader("Fonte personalizada para PDF (opcional)")
st.sidebar.caption(
    "Necessaria apenas para traduzir PDFs para idiomas com alfabetos nao "
    "latinos (russo, arabe, chines, japones, coreano, grego, hebraico, "
    "tailandes etc.). Envie um arquivo .ttf com boa cobertura Unicode, "
    "como Noto Sans ou DejaVu Sans."
)
custom_font_file = st.sidebar.file_uploader("Fonte (.ttf)", type=["ttf"], key="font_uploader")

# --------------------------------------------------------------------------
# Cabecalho
# --------------------------------------------------------------------------
st.title("🌐 Tradutor de Documentos")
st.write(
    "Envie um documento em **PDF** ou **Word (.docx)**, escolha o idioma de "
    "destino e receba o documento traduzido preservando a formatacao "
    "original: tabelas, imagens, fontes, cabecalhos, rodapes e numeracao "
    "de pagina."
)

# --------------------------------------------------------------------------
# 1) Upload do arquivo
# --------------------------------------------------------------------------
st.header("1. Envie o documento")
uploaded_file = st.file_uploader(
    "Arquivo PDF ou Word (.docx)", type=["pdf", "docx"], key="main_uploader"
)

if uploaded_file is None:
    st.session_state["file_id"] = None
    st.info("Aguardando o envio de um arquivo.")
    st.stop()

file_id = f"{uploaded_file.name}-{uploaded_file.size}"
is_new_file = file_id != st.session_state["file_id"]

if is_new_file:
    reset_translation_outputs()
    file_bytes = uploaded_file.getvalue()
    ext = os.path.splitext(uploaded_file.name)[1].lower()

    if ext not in (".pdf", ".docx"):
        st.error("Formato nao suportado. Envie um arquivo .pdf ou .docx.")
        st.stop()

    # Validacao do arquivo
    try:
        if ext == ".pdf":
            n_units = validate_pdf(file_bytes)
            unit_label = f"{n_units} pagina(s)"
        else:
            n_units = validate_docx(file_bytes)
            unit_label = f"{n_units} paragrafo(s)"
    except InvalidFileError as exc:
        st.error(f"❌ Arquivo invalido: {exc}")
        st.stop()
    except Exception as exc:
        st.error(f"❌ Erro inesperado ao validar o arquivo: {exc}")
        st.stop()

    # Deteccao de idioma
    try:
        if ext == ".pdf":
            sample_text = extract_sample_text_pdf(file_bytes)
        else:
            sample_text = extract_sample_text_docx(file_bytes)
        detected_code, detected_prob = detect_language(sample_text)
    except Exception:
        detected_code, detected_prob = None, 0.0

    st.session_state.update({
        "file_id": file_id,
        "file_bytes": file_bytes,
        "file_name": uploaded_file.name,
        "file_ext": ext,
        "detected_code": detected_code,
        "detected_prob": detected_prob,
    })
    st.success(f"✅ Arquivo carregado: **{uploaded_file.name}** ({unit_label})")

# --------------------------------------------------------------------------
# 2) Idioma detectado
# --------------------------------------------------------------------------
st.header("2. Idioma de origem")

detected_code = st.session_state["detected_code"]
detected_prob = st.session_state["detected_prob"]

if detected_code:
    detected_display = code_to_display(detected_code)
    st.write(
        f"Idioma detectado automaticamente: **{detected_display}** "
        f"(confianca aproximada: {detected_prob * 100:.0f}%)"
    )
else:
    st.warning(
        "Nao foi possivel detectar o idioma automaticamente com confianca "
        "(documento muito curto ou com pouco texto). Selecione o idioma de "
        "origem manualmente abaixo, ou deixe em deteccao automatica para "
        "que o proprio provedor de traducao tente identificar durante a "
        "traducao."
    )

source_options = ["Deteccao automatica"] + list(LANGUAGES.keys())
default_source_index = 0
if detected_code:
    detected_display = code_to_display(detected_code)
    if detected_display in LANGUAGES:
        default_source_index = source_options.index(detected_display)

source_choice = st.selectbox(
    "Confirme ou ajuste o idioma de origem",
    options=source_options,
    index=default_source_index,
)

# --------------------------------------------------------------------------
# 3) Idioma de destino
# --------------------------------------------------------------------------
st.header("3. Idioma de destino")

target_options = list(LANGUAGES.keys())
default_target = "Ingles" if source_choice != "Ingles" else "Portugues"
target_index = target_options.index(default_target) if default_target in target_options else 0

target_choice = st.selectbox(
    "Para qual idioma deseja traduzir?",
    options=target_options,
    index=target_index,
)

# Aviso de fonte para PDFs com idiomas de alfabeto nao latino
if (
    st.session_state["file_ext"] == ".pdf"
    and target_choice in LANGUAGES_NEED_CUSTOM_FONT
    and custom_font_file is None
):
    st.info(
        f"ℹ️ O idioma **{target_choice}** usa um alfabeto que normalmente nao "
        "e coberto pela fonte padrao usada na geracao do PDF. Para um "
        "resultado correto, envie uma fonte .ttf com suporte a esse "
        "alfabeto no painel lateral (ex.: Noto Sans). Sem isso, alguns "
        "caracteres podem nao aparecer corretamente no PDF traduzido."
    )

# --------------------------------------------------------------------------
# 4) Iniciar traducao
# --------------------------------------------------------------------------
st.header("4. Traduzir")

translate_clicked = st.button(
    "🔁 Traduzir documento", type="primary", disabled=st.session_state["is_translating"]
)

if translate_clicked:
    if provider == "azure" and (not azure_key or not azure_region):
        st.error(
            "Para usar o Azure Translator, informe a chave de API e a "
            "regiao na barra lateral."
        )
        st.stop()

    if source_choice == "Deteccao automatica":
        source_code_for_translation = None
    else:
        source_code_for_translation = get_code(source_choice, provider)
    target_code_for_translation = get_code(target_choice, provider)

    if source_code_for_translation == target_code_for_translation:
        st.warning(
            "O idioma de origem e o idioma de destino parecem ser o mesmo. "
            "Verifique a selecao antes de continuar."
        )

    reset_translation_outputs()
    st.session_state["is_translating"] = True

    progress_bar = st.progress(0, text="Preparando traducao...")
    status_placeholder = st.empty()

    font_path = None
    try:
        try:
            translate_fn = get_translate_function(
                provider=provider,
                source_lang=source_code_for_translation,
                target_lang=target_code_for_translation,
                api_key=azure_key,
                region=azure_region,
                max_workers=max_workers,
            )
        except TranslateUnavailableError as exc:
            st.error(f"❌ {exc}")
            st.session_state["is_translating"] = False
            st.stop()

        file_bytes = st.session_state["file_bytes"]
        ext = st.session_state["file_ext"]

        if ext == ".pdf" and custom_font_file is not None:
            tmp_font = tempfile.NamedTemporaryFile(delete=False, suffix=".ttf")
            tmp_font.write(custom_font_file.getvalue())
            tmp_font.close()
            font_path = tmp_font.name

        if ext == ".docx":
            total_segments = count_translatable_segments(file_bytes)
            status_placeholder.write(f"Traduzindo {total_segments} trecho(s) de texto...")

            def docx_progress(done, total):
                frac = min(done / total, 1.0) if total else 1.0
                progress_bar.progress(frac, text=f"Traduzindo trecho {done} de {total}...")

            result_bytes = translate_docx_bytes(file_bytes, translate_fn, progress_callback=docx_progress)
            base_name = os.path.splitext(st.session_state["file_name"])[0]
            out_name = f"{base_name}_traduzido_{target_code_for_translation}.docx"
            doc_warnings = []

        else:  # .pdf
            total_pages = count_pages(file_bytes)
            status_placeholder.write(f"Traduzindo {total_pages} pagina(s)...")

            def pdf_progress(page, total):
                frac = min(page / total, 1.0) if total else 1.0
                progress_bar.progress(frac, text=f"Traduzindo pagina {page} de {total}...")

            result_bytes, doc_warnings = translate_pdf_bytes(
                file_bytes, translate_fn, font_file_path=font_path, progress_callback=pdf_progress
            )
            base_name = os.path.splitext(st.session_state["file_name"])[0]
            out_name = f"{base_name}_traduzido_{target_code_for_translation}.pdf"

        progress_bar.progress(1.0, text="Traducao concluida.")
        status_placeholder.empty()

        st.session_state["translated_bytes"] = result_bytes
        st.session_state["translated_name"] = out_name
        st.session_state["warnings"] = doc_warnings
        st.session_state["failed_count"] = getattr(translate_fn, "failed_count", 0)
        st.session_state["dedup_savings"] = getattr(translate_fn, "dedup_savings", 0)

    except Exception as exc:
        progress_bar.empty()
        status_placeholder.empty()
        st.error(f"❌ Ocorreu um erro durante a traducao: {exc}")
        with st.expander("Detalhes tecnicos do erro"):
            st.code(traceback.format_exc())
    finally:
        st.session_state["is_translating"] = False
        if font_path and os.path.exists(font_path):
            try:
                os.unlink(font_path)
            except OSError:
                pass

# --------------------------------------------------------------------------
# 5) Resultado e download
# --------------------------------------------------------------------------
if st.session_state["translated_bytes"]:
    st.header("5. Resultado")
    st.success("✅ Documento traduzido com sucesso!")

    if st.session_state.get("dedup_savings"):
        st.caption(
            f"⚡ {st.session_state['dedup_savings']} trecho(s) repetido(s) foram "
            "reaproveitados de traducoes ja feitas, economizando tempo."
        )

    if st.session_state["failed_count"]:
        st.warning(
            f"⚠️ {st.session_state['failed_count']} trecho(s) de texto nao "
            "puderam ser traduzidos (problema de conexao ou limite do "
            "provedor) e foram mantidos no idioma original no documento "
            "final."
        )

    for w in st.session_state["warnings"]:
        st.warning(f"⚠️ {w}")

    mime = (
        "application/pdf"
        if st.session_state["translated_name"].endswith(".pdf")
        else "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
    )

    st.download_button(
        label=f"⬇️ Baixar {st.session_state['translated_name']}",
        data=st.session_state["translated_bytes"],
        file_name=st.session_state["translated_name"],
        mime=mime,
        type="primary",
    )

# --------------------------------------------------------------------------
# Limitacoes conhecidas
# --------------------------------------------------------------------------
with st.expander("ℹ️ Limitacoes conhecidas"):
    st.markdown(
        """
**Word (.docx)**
- O texto e traduzido preservando a formatacao em nivel de fonte, tamanho,
  negrito, italico, cor e realce, pois cada trecho de texto e substituido
  no proprio local onde estava, sem recriar o paragrafo.
- Tabelas (inclusive aninhadas), cabecalhos e rodapes de todas as secoes
  sao traduzidos; imagens, graficos e a estrutura de tabelas nao sao
  alterados.
- Caixas de texto flutuantes, WordArt e objetos incorporados (SmartArt,
  planilhas OLE) **nao** sao traduzidos, pois nao sao acessiveis pela
  biblioteca usada.
- Quando uma frase e dividida entre trechos com formatacoes diferentes
  (ex.: uma palavra em negrito no meio do texto), a traducao e feita parte
  por parte para preservar a formatacao exata; isso pode raramente afetar
  a fluidez gramatical nesses pontos especificos.
- Numeracao automatica de pagina (campos do Word) nao e tocada.

**PDF**
- Como o PDF nao tem um modelo de texto "fluido", a traducao e feita
  desenhando o novo texto sobre a posicao exata do texto original. Isso
  preserva perfeitamente a posicao de imagens e elementos vetoriais
  (incluindo linhas de tabelas), mas e uma aproximacao visual do
  documento, nao uma reconstrucao identica.
- A fonte padrao usada para o texto traduzido cobre bem alfabetos latinos.
  Para idiomas como russo, arabe, chines, japones, coreano, grego, hebraico
  ou tailandes, envie uma fonte .ttf com cobertura Unicode na barra
  lateral para um resultado correto.
- Se o texto traduzido for bem mais longo que o original, a fonte e
  reduzida automaticamente para tentar caber no mesmo espaco; em casos
  extremos o texto pode ser cortado (um aviso e exibido quando isso
  acontece).
- Texto dentro de imagens (capturas de tela, graficos rasterizados) nao e
  traduzido.

**Geral**
- O modo gratuito de traducao (Google Translate nao oficial) pode
  apresentar lentidao ou falhas pontuais em documentos muito grandes,
  devido a limites de taxa do servico. Os trechos que falharem permanecem
  no idioma original no resultado final, e a quantidade e informada apos a
  traducao. Para volumes grandes ou uso recorrente, recomenda-se o Azure
  Translator.
        """
    )

st.caption(
    f"Sessao iniciada {datetime.now().strftime('%Y-%m-%d')} · "
    "Os arquivos enviados sao processados apenas durante esta sessao e nao "
    "sao armazenados permanentemente."
)
