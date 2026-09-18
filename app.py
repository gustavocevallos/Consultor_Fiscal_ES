"""
app.py — Consultor IA: asistente jurídico/fiscal sobre legislación española

=============================================================================
 CÓMO PONER EN MARCHA ESTE PROYECTO (todo en local, sin servidor)
=============================================================================

1. Clonar el repositorio de legislación junto a este proyecto (mismo nivel
   que app.py), de forma que quede como "./legalize-es":

       git clone https://github.com/legalize-dev/legalize-es.git

   Si prefieres clonarlo en otra ruta, edita la constante REPO_PATH en
   services/laws.py.

2. Crear un entorno virtual e instalar dependencias:

       python -m venv venv
       venv\\Scripts\\activate        (Windows)
       pip install -r requirements.txt

3. (Opcional) Configurar el LLM real:
       - Copia .env.example como .env (este último no se sube a git).
       - Pega tu API key, por ejemplo: GEMINI_API_KEY=tu_clave_aqui
       - El proveedor se autodetecta según la key rellena (ver utils/llm.py
         para forzar uno concreto con LLM_PROVIDER, o añadir Anthropic/OpenAI).
   Si no se configura ninguna key, la app funciona en modo simulado (sin
   llamadas a ningún LLM), lo que permite probar toda la interfaz sin coste
   ni conexión.

4. Ejecutar la app:

       streamlit run app.py

=============================================================================
Este archivo solo se encarga de la UI (Streamlit) y de la navegación entre
las 3 secciones. Toda la lógica de negocio vive en los módulos de
services/ y utils/, para mantener la interfaz separada del backend.
"""

from __future__ import annotations

import streamlit as st

from services import guides, monitor
from services.assistant import answer_question
from services.laws import repo_available, search_laws

# =============================================================================
# Configuración general de la página
# =============================================================================
st.set_page_config(
    page_title="Consultor IA - Legislación Española",
    page_icon=None,
    layout="wide",
)

# Estilos mínimos para un aspecto más sobrio/profesional: tipografía de
# cabeceras, separación de secciones y una franja superior discreta.
st.markdown(
    """
    <style>
        h1 { font-weight: 600; letter-spacing: -0.01em; }
        [data-testid="stSidebar"] h1 {
            font-size: 1.15rem;
            font-weight: 700;
            text-transform: uppercase;
            letter-spacing: 0.04em;
        }
        [data-testid="stExpander"] summary { font-weight: 500; }
        .app-eyebrow {
            text-transform: uppercase;
            letter-spacing: 0.08em;
            font-size: 0.75rem;
            color: var(--text-color, #6b7280);
            opacity: 0.7;
            margin-bottom: -0.6rem;
        }
    </style>
    """,
    unsafe_allow_html=True,
)


# =============================================================================
# SECCIÓN 1: Asistente jurídico/fiscal con citas reales
# =============================================================================
def render_assistant_page() -> None:
    st.markdown('<p class="app-eyebrow">Sección 1</p>', unsafe_allow_html=True)
    st.title("Asistente jurídico/fiscal")
    st.caption(
        "Pregunta en lenguaje natural. El asistente busca en la legislación "
        "vigente (repositorio legalize-es) y responde citando la ley y el "
        "artículo exactos."
    )

    question = st.text_area(
        "Tu pregunta",
        placeholder="Ej: ¿Qué dice el artículo 39 del IRPF sobre rendimientos del trabajo?",
        height=100,
    )
    st.caption(
        "La primera pregunta de la sesión tarda más (indexa ~12.000 normas "
        "en memoria); las siguientes son mucho más rápidas."
    )

    if st.button("Preguntar", type="primary", disabled=not question.strip()):
        with st.spinner("Buscando en la legislación y consultando al modelo..."):
            result = answer_question(question)

        st.subheader("Respuesta")
        st.markdown(result.answer)

        if result.citations:
            st.subheader("Citas y fuentes")
            for i, citation in enumerate(result.citations, start=1):
                with st.expander(f"[Fuente {i}] {citation.title}"):
                    st.markdown(f"**Archivo:** `{citation.relative_path}`")
                    st.markdown(f"**Enlace GitHub:** {citation.url}")
                    st.code(citation.snippet, language="markdown")


# =============================================================================
# SECCIÓN 2: Monitor de cambios normativos
# =============================================================================
def render_monitor_page() -> None:
    st.markdown('<p class="app-eyebrow">Sección 2</p>', unsafe_allow_html=True)
    st.title("Monitor de cambios normativos")
    st.caption(
        "Suscríbete a carpetas del repositorio (materias) para ver un feed "
        "de los últimos cambios, con resumen y diff generados por IA."
    )

    folders = monitor.available_subscriptions()
    subscribed = st.multiselect(
        "Suscripciones (deja vacío para ver todo el historial)",
        options=folders,
        help="Ej: 'fiscal', 'laboral', 'autonomico/andalucia'",
    )

    max_commits = st.slider("Número de cambios a mostrar", 5, 30, 15)

    if st.button("Actualizar feed", type="primary"):
        st.session_state["monitor_feed"] = monitor.get_change_feed(
            subscribed_folders=subscribed or None, max_commits=max_commits
        )

    feed = st.session_state.get("monitor_feed")
    if feed is None:
        st.info("Pulsa 'Actualizar feed' para cargar los últimos cambios normativos.")
        return

    if not feed:
        st.warning("No se han encontrado commits (¿repo clonado y con historial?).")
        return

    st.subheader(f"Últimos {len(feed)} cambios")
    for alert in feed:
        commit = alert.commit
        header = f"{commit.date:%d/%m/%Y %H:%M} · {commit.message.splitlines()[0]}"
        with st.expander(header):
            st.markdown(f"**Autor:** {commit.author}  ")
            st.markdown(f"**Commit:** [`{commit.short_sha}`]({alert.github_commit_url})")
            st.markdown(f"**Archivos modificados:** {len(alert.files_changed)}")

            if not alert.files_changed:
                continue

            selected_file = st.selectbox(
                "Ver diff explicado de un archivo",
                options=alert.files_changed,
                key=f"file_select_{commit.hexsha}",
            )
            if st.button("Explicar cambio", key=f"explain_{commit.hexsha}"):
                with st.spinner("Generando diff y resumen con IA..."):
                    diff_text, summary = monitor.explain_commit_diff(
                        selected_file, commit.hexsha
                    )
                st.markdown("**Resumen del cambio (IA):**")
                st.markdown(summary)
                st.markdown("**Diff:**")
                st.code(diff_text or "(sin cambios detectados)", language="diff")


# =============================================================================
# SECCIÓN 3: Guías y comparativas antes/después de reformas
# =============================================================================
def render_guides_page() -> None:
    st.markdown('<p class="app-eyebrow">Sección 3</p>', unsafe_allow_html=True)
    st.title("Guías y comparativas")
    st.caption(
        "Busca una ley, genera una guía consolidada con novedades, o "
        "compara dos versiones para ver una comparativa antes/después."
    )

    query = st.text_input(
        "Buscar ley (título, palabras clave, artículo...)",
        placeholder="Ej: Estatuto de los Trabajadores",
    )

    if not query.strip():
        st.info("Escribe un término de búsqueda para localizar una norma.")
        return

    results = search_laws(query, max_results=10)
    if not results:
        st.warning("No se han encontrado normas con esos términos.")
        return

    options = {f"{r.title} ({r.relative_path})": r.relative_path for r in results}
    selected_label = st.selectbox("Resultados", options=list(options.keys()))
    selected_path = options[selected_label]

    tab_guide, tab_compare = st.tabs(["Generar guía actualizada", "Comparar versiones"])

    with tab_guide:
        st.write(
            "Genera un documento con el texto consolidado de la norma y un "
            "resumen de novedades/puntos clave, listo para descargar."
        )
        if st.button("Generar guía", type="primary", key="btn_generate_guide"):
            with st.spinner("Generando guía con IA..."):
                guide = guides.generate_guide(selected_path)
            st.session_state["last_guide"] = guide

        guide = st.session_state.get("last_guide")
        if guide is not None:
            st.download_button(
                "Descargar guía (HTML)",
                data=guide.html_content,
                file_name=f"guia_{guide.title[:40]}.html",
                mime="text/html",
            )
            with st.expander("Vista previa de la guía"):
                st.components.v1.html(guide.html_content, height=600, scrolling=True)

    with tab_compare:
        st.write(
            "Elige dos commits (versiones) de la norma para ver qué artículos "
            "cambiaron y un resumen de los cambios clave."
        )
        commits = guides.list_commits_for_file(selected_path, max_commits=30)
        if len(commits) < 2:
            st.warning(
                "No hay suficiente historial de commits para este archivo "
                "como para comparar versiones."
            )
        else:
            commit_labels = {
                f"{c.date:%d/%m/%Y} · {c.short_sha} · {c.message.splitlines()[0]}": c.hexsha
                for c in commits
            }
            labels = list(commit_labels.keys())
            col1, col2 = st.columns(2)
            with col1:
                old_label = st.selectbox("Versión antigua", labels, index=len(labels) - 1)
            with col2:
                new_label = st.selectbox("Versión nueva", labels, index=0)

            if st.button("Comparar versiones", type="primary", key="btn_compare"):
                old_sha = commit_labels[old_label]
                new_sha = commit_labels[new_label]
                with st.spinner("Generando comparativa con IA..."):
                    comparison = guides.compare_versions(selected_path, old_sha, new_sha)
                st.session_state["last_comparison"] = comparison

            comparison = st.session_state.get("last_comparison")
            if comparison is not None:
                st.download_button(
                    "Descargar comparativa (HTML)",
                    data=comparison.html_content,
                    file_name=f"comparativa_{comparison.title[:40]}.html",
                    mime="text/html",
                )
                with st.expander("Vista previa de la comparativa"):
                    st.components.v1.html(comparison.html_content, height=600, scrolling=True)


# =============================================================================
# Navegación lateral y arranque de la app
# =============================================================================
st.sidebar.title("Consultor IA")
st.sidebar.caption("Legislación española · fuente: legalize-es")

SECTIONS = {
    "Asistente jurídico/fiscal": render_assistant_page,
    "Monitor de cambios normativos": render_monitor_page,
    "Guías y comparativas": render_guides_page,
}

selected_section = st.sidebar.radio("Navegación", list(SECTIONS.keys()))

st.sidebar.divider()
if repo_available():
    st.sidebar.success("Repositorio legalize-es detectado")
else:
    st.sidebar.error(
        "No se encuentra la carpeta 'legalize-es' junto al proyecto.\n\n"
        "Clónala con:\n`git clone https://github.com/legalize-dev/legalize-es.git`"
    )

st.sidebar.divider()
st.sidebar.caption(
    "Proyecto educativo. Las respuestas de IA son orientativas y no "
    "sustituyen el asesoramiento jurídico profesional."
)

# Renderiza la sección elegida en el sidebar.
SECTIONS[selected_section]()
