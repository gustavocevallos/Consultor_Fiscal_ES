"""
services/guides.py

Lógica del "Generador automático de guías y comparativas antes/después de
reformas".

Dos funcionalidades principales:
    - generate_guide(): a partir de una ley (archivo Markdown), genera un
      documento HTML con un resumen de "novedades principales" hecho por
      IA y un enlace al texto íntegro en GitHub (no vuelca la ley entera:
      para una norma de cientos de artículos eso sería casi ilegible).
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
    .diff-block {{ font-family: 'Courier New', monospace; font-size: 0.85rem;
                    white-space: pre-wrap; background: #f8f8f8; border: 1px solid #ddd;
                    border-radius: 4px; padding: 1rem; overflow-x: auto; }}
    .diff-add {{ display: block; background: #e6ffed; color: #22863a; }}
    .diff-del {{ display: block; background: #ffeef0; color: #b31d28; }}
    .diff-hunk {{ display: block; color: #6f42c1; font-weight: bold; }}
    .diff-file {{ display: block; color: #666; }}
    a {{ color: #3b5bdb; }}
</style>
</head>
<body>
{body_html}
</body>
</html>"""


def generate_guide(relative_path: str) -> GuideResult:
    """
    Genera una guía HTML de una ley: un resumen de novedades/puntos clave
    generado por IA, más un enlace al texto íntegro en GitHub. No incluye
    el texto consolidado completo en el documento — para una norma de
    cientos de artículos (como la Ley del IRPF) eso produciría un
    documento casi tan largo como la propia ley, difícil de leer.
    """
    text = read_file(relative_path)
    if text is None:
        raise FileNotFoundError(f"No se encontró el archivo: {relative_path}")

    title = _extract_title(text, fallback=relative_path)

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
    <p>
        Consulta el texto íntegro y actualizado de la norma en
        <a href="{source_url}" target="_blank">GitHub</a>.
    </p>
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


def _diff_to_html(diff_text: str) -> str:
    """
    Convierte un diff unificado en HTML con las líneas añadidas/eliminadas
    resaltadas, en vez de mostrar el texto completo de ambas versiones.
    """
    if not diff_text.strip():
        return "<p><em>No hay diferencias de contenido entre estas dos versiones.</em></p>"

    rendered_lines = []
    for line in diff_text.splitlines():
        escaped = html.escape(line)
        if line.startswith("+++") or line.startswith("---"):
            rendered_lines.append(f'<span class="diff-file">{escaped}</span>')
        elif line.startswith("@@"):
            rendered_lines.append(f'<span class="diff-hunk">{escaped}</span>')
        elif line.startswith("+"):
            rendered_lines.append(f'<span class="diff-add">{escaped}</span>')
        elif line.startswith("-"):
            rendered_lines.append(f'<span class="diff-del">{escaped}</span>')
        else:
            rendered_lines.append(escaped)
    return '<pre class="diff-block">' + "\n".join(rendered_lines) + "</pre>"


def compare_versions(
    relative_path: str, old_sha: str, new_sha: str
) -> GuideResult:
    """
    Genera una comparativa HTML "antes/después" de un archivo entre dos
    commits: solo las líneas que cambiaron (diff resaltado), no el texto
    completo de ambas versiones — para una norma larga, mostrar dos veces
    el texto íntegro sería casi tan largo como la propia ley y dificultaría
    ver qué cambió realmente. Incluye también un resumen de IA.
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
    diff_html = _diff_to_html(diff_text)

    old_url = github_url(relative_path, commit_sha=old_sha)
    new_url = github_url(relative_path, commit_sha=new_sha)

    body = f"""
    <h1>Comparativa: {html.escape(title)}</h1>
    <p class="meta">
        Versión antigua: <a href="{old_url}" target="_blank">commit
        <code>{old_sha[:7]}</code></a> &nbsp;|&nbsp;
        Versión nueva: <a href="{new_url}" target="_blank">commit
        <code>{new_sha[:7]}</code></a>
    </p>
    <div class="novedades">
        <h2>Resumen de cambios clave</h2>
        {summary_html}
    </div>
    <h2>Diferencias</h2>
    {diff_html}
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
