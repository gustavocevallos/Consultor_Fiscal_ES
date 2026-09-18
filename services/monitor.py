"""
services/monitor.py

Lógica del "Monitor de cambios normativos con alertas y diffs explicados".

En esta primera versión, el monitor es "reactivo": no hay un proceso en
segundo plano vigilando el repo, sino que cada vez que el usuario abre la
sección se leen los últimos commits de Git (opcionalmente filtrados por
una carpeta o archivo al que esté "suscrito") y se muestran como un feed
de alertas.

Para cada commit, se puede pedir un resumen en lenguaje claro generado por
IA a partir del diff real de los archivos modificados.
"""

from __future__ import annotations

from dataclasses import dataclass

from services.laws import (
    CommitInfo,
    diff_file_between_commits,
    get_commit_history,
    github_url,
    list_top_level_folders,
)
from utils.llm import ask_llm

SYSTEM_PROMPT = (
    "Eres un asistente que explica cambios normativos a abogados y "
    "asesores fiscales. Recibes un diff (formato git) de un artículo o "
    "norma y debes explicar, en 3-5 frases y en lenguaje claro, qué ha "
    "cambiado y qué implicaciones prácticas puede tener. No inventes "
    "cambios que no estén en el diff."
)


@dataclass
class ChangeAlert:
    """Una entrada del feed de cambios normativos."""

    commit: CommitInfo
    files_changed: list[str]
    github_commit_url: str


def available_subscriptions() -> list[str]:
    """
    Lista de posibles "suscripciones" (carpetas de primer nivel del repo,
    p. ej. 'fiscal', 'laboral', 'autonomico'). En esta primera versión no
    se persisten preferencias de usuario entre sesiones: la selección vive
    en el estado de la sesión de Streamlit (ver app.py).
    """
    return list_top_level_folders()


def get_change_feed(
    subscribed_folders: list[str] | None, max_commits: int = 15
) -> list[ChangeAlert]:
    """
    Construye el feed de alertas de cambios normativos.

    Si `subscribed_folders` está vacío o es None, se muestra el historial
    global del repo. Si contiene carpetas, se combina (sin duplicados) el
    historial de cada una, ordenado por fecha descendente.
    """
    if not subscribed_folders:
        commits = get_commit_history(path_filter=None, max_commits=max_commits)
        return [_to_alert(c) for c in commits]

    seen_shas: set[str] = set()
    alerts: list[ChangeAlert] = []
    for folder in subscribed_folders:
        for commit in get_commit_history(path_filter=folder, max_commits=max_commits):
            if commit.hexsha in seen_shas:
                continue
            seen_shas.add(commit.hexsha)
            alerts.append(_to_alert(commit))

    alerts.sort(key=lambda a: a.commit.date, reverse=True)
    return alerts[:max_commits]


def _to_alert(commit: CommitInfo) -> ChangeAlert:
    return ChangeAlert(
        commit=commit,
        files_changed=commit.files_changed,
        github_commit_url=(
            f"https://github.com/legalize-dev/legalize-es/commit/{commit.hexsha}"
        ),
    )


def explain_commit_diff(relative_path: str, commit_sha: str) -> tuple[str, str]:
    """
    Genera el diff de un archivo entre el commit indicado y su commit padre,
    junto con un resumen en lenguaje claro hecho por el LLM.

    Devuelve (diff_texto, resumen_ia).
    """
    from services.laws import _get_repo  # import local para evitar ciclos de uso

    repo = _get_repo()
    if repo is None:
        return "", "Repositorio no disponible."

    try:
        commit = repo.commit(commit_sha)
        parent_sha = commit.parents[0].hexsha if commit.parents else commit_sha
    except Exception:
        return "", "No se ha podido localizar el commit indicado."

    diff_text = diff_file_between_commits(relative_path, parent_sha, commit_sha)
    if not diff_text.strip():
        return diff_text, "No hay cambios detectados en este archivo para este commit."

    prompt = (
        f"Diff del archivo {relative_path}:\n\n```diff\n{diff_text}\n```\n\n"
        "Explica en lenguaje claro qué ha cambiado."
    )
    summary = ask_llm(prompt=prompt, system=SYSTEM_PROMPT)
    return diff_text, summary
