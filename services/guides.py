"""
services/guides.py

Lógica del "Generador automático de guías y comparativas antes/después de
reformas".

Dos funcionalidades principales:
    - generate_guide(): a partir de una ley (archivo Markdown), genera un
      documento HTML con el texto consolidado y un resumen de "novedades
      principales" hecho por IA.
    - compare_versions(): dado un archivo y dos commits (o dos fechas que
      se resuelven al commit más cercano), genera una comparativa
      "antes/después" en HTML, con un resumen de los cambios clave.

Ambas funciones devuelven HTML como string, listo para mostrarse en
Streamlit (st.components.v1.html) o descargarse con st.download_button.
Convertir ese HTML a PDF puede hacerse con herramientas externas (ver
comentario al final del archivo).
"""

from __future__ import annotations

import html
from dataclasses import dataclass
from datetime import datetime

import markdown as md  # python-markdown: convierte el texto legal a HTML

from services.laws import (
    CommitInfo,
    get_commit_history,
    github_url,
    read_file,
    read_file_at_commit,
    unified_diff_text,
)
from utils.llm import ask_llm

SYSTEM_PROMPT_NOVEDADES = (
    "Eres un asistente que redacta guías legales para abogados y asesores "
    "fiscales. A partir del texto de una norma, redacta un resumen breve "
    "(5-8 puntos en una lista) de los aspectos más relevantes o que suelen "
    "generar dudas. No inventes contenido que no esté en el texto."
)

SYSTEM_PROMPT_COMPARATIVA = (
    "Eres un asistente que explica reformas legales a abogados y asesores "
    "fiscales. A partir de un diff entre dos versiones de una norma, "
    "redacta un resumen claro de los cambios clave y sus implicaciones "
    "prácticas. No inventes cambios que no estén en el diff."
)


@dataclass
class GuideResult:
    html_content: str
    title: str


def _wrap_html(title: str, body_html: str) -> str:
    """Envuelve un fragmento de HTML en un documento completo y con estilos
    mínimos, para que sea legible tanto en pantalla como al exportar/imprimir."""
    return f"""<!DOCTYPE html>
<html lang="es">
<head>
<meta charset="utf-8">
<title>{html.escape(title)}</title>
<style>
    body {{ font-family: Georgia, 'Times New Roman', serif; max-width: 860px;
            margin: 2rem auto; padding: 0 1.5rem; line-height: 1.6; color: #222; }}
    h1, h2, h3 {{ font-family: Arial, sans-serif; color: #1a1a2e; }}
    .meta {{ color: #666; font-size: 0.9rem; margin-bottom: 2rem; }}
    .novedades {{ background: #f5f7fb; border-left: 4px solid #3b5bdb;
                   padding: 1rem 1.5rem; border-radius: 4px; margin-bottom: 2rem; }}
    .diff-block {{ font-family: 'Courier New', monospace; white-space: pre-wrap;
                    background: #f8f8f8; border: 1px solid #ddd; border-radius: 4px;
                    padding: 1rem; }}
    .diff-old {{ background: #fff0f0; border: 1px solid #f0c0c0; padding: 1rem;
                  border-radius: 4px; margin-bottom: 1rem; }}
    .diff-new {{ background: #f0fff4; border: 1px solid #b7ebc6; padding: 1rem;
                  border-radius: 4px; }}
    a {{ color: #3b5bdb; }}
</style>
</head>
<body>
{body_html}
</body>
</html>"""


def generate_guide(relative_path: str) -> GuideResult:
    """
    Genera una guía HTML de una ley: texto consolidado (convertido de
    Markdown a HTML) + resumen de novedades principales generado por IA.
    """
    text = read_file(relative_path)
    if text is None:
        raise FileNotFoundError(f"No se encontró el archivo: {relative_path}")

    title = _extract_title(text, fallback=relative_path)
    consolidated_html = md.markdown(text, extensions=["extra", "toc"])

    prompt = (
        f"Texto de la norma ({title}):\n\n{text[:6000]}\n\n"
        "Genera el resumen de novedades/puntos clave solicitado."
    )
    novedades = ask_llm(prompt=prompt, system=SYSTEM_PROMPT_NOVEDADES)
    novedades_html = md.markdown(novedades)

    source_url = github_url(relative_path)
    generated_at = datetime.now().strftime("%d/%m/%Y %H:%M")

    body = f"""
    <h1>{html.escape(title)}</h1>
    <p class="meta">
        Guía generada automáticamente el {generated_at} a partir de
        <a href="{source_url}" target="_blank">{html.escape(relative_path)}</a>
        (repositorio legalize-es).
    </p>
    <div class="novedades">
        <h2>Novedades y puntos clave</h2>
        {novedades_html}
    </div>
    <h2>Texto consolidado</h2>
    {consolidated_html}
    """
    return GuideResult(html_content=_wrap_html(title, body), title=title)


def _extract_title(text: str, fallback: str) -> str:
    for line in text.splitlines():
        if line.strip().startswith("# "):
            return line.strip("# ").strip()
    return fallback


def list_commits_for_file(relative_path: str, max_commits: int = 30) -> list[CommitInfo]:
    """Historial de commits de un archivo concreto, para elegir versiones a comparar."""
    return get_commit_history(path_filter=relative_path, max_commits=max_commits)


def compare_versions(
    relative_path: str, old_sha: str, new_sha: str
) -> GuideResult:
    """
    Genera una comparativa HTML "antes/después" de un archivo entre dos
    commits, con un resumen de los cambios clave hecho por IA.
    """
    old_text = read_file_at_commit(relative_path, old_sha) or "(versión no disponible)"
    new_text = read_file_at_commit(relative_path, new_sha) or "(versión no disponible)"
    title = _extract_title(new_text, fallback=relative_path)

    diff_text = unified_diff_text(old_text, new_text, relative_path)

    prompt = (
        f"Diff entre dos versiones de {title} ({relative_path}):\n\n"
        f"```diff\n{diff_text}\n```\n\n"
        "Redacta el resumen de cambios clave solicitado."
    )
    summary = ask_llm(prompt=prompt, system=SYSTEM_PROMPT_COMPARATIVA)
    summary_html = md.markdown(summary)

    old_html = md.markdown(old_text, extensions=["extra"])
    new_html = md.markdown(new_text, extensions=["extra"])

    body = f"""
    <h1>Comparativa: {html.escape(title)}</h1>
    <p class="meta">
        Versión antigua: commit <code>{old_sha[:7]}</code> &nbsp;|&nbsp;
        Versión nueva: commit <code>{new_sha[:7]}</code>
    </p>
    <div class="novedades">
        <h2>Resumen de cambios clave</h2>
        {summary_html}
    </div>
    <h2>Antes</h2>
    <div class="diff-old">{old_html}</div>
    <h2>Después</h2>
    <div class="diff-new">{new_html}</div>
    """
    return GuideResult(html_content=_wrap_html(f"Comparativa - {title}", body), title=title)


# --- Exportación a PDF -------------------------------------------------
#
# Esta primera versión solo genera HTML (descargable directamente con
# st.download_button, y perfectamente imprimible a PDF desde el navegador
# con Ctrl+P -> "Guardar como PDF").
#
# Para generar PDF de forma automática desde Python, la opción más sencilla
# en Windows sin dependencias del sistema es `pip install xhtml2pdf` y:
#
#     from xhtml2pdf import pisa
#     def html_to_pdf_bytes(html_content: str) -> bytes:
#         import io
#         buffer = io.BytesIO()
#         pisa.CreatePDF(html_content, dest=buffer)
#         return buffer.getvalue()
#
# Se deja como mejora futura para no añadir esa dependencia en la v1.
