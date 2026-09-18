"""
services/assistant.py

Lógica del "Asistente jurídico/fiscal con citas reales".

Flujo:
    1. El usuario escribe una pregunta en lenguaje natural.
    2. Buscamos en el repo legalize-es los fragmentos de ley más relevantes
       (services.laws.search_laws).
    3. Construimos un prompt que incluye esos fragmentos como contexto y
       pedimos al LLM que responda citando exactamente esas fuentes.
    4. Devolvemos la respuesta junto con la lista de citas (ley, ruta,
       enlace a GitHub) para que la UI las muestre de forma separada.
"""

from __future__ import annotations

from dataclasses import dataclass

from services.laws import LawFragment, github_url, search_laws
from utils.llm import ask_llm

SYSTEM_PROMPT = (
    "Eres un asistente jurídico y fiscal especializado en legislación "
    "española. Respondes SIEMPRE basándote únicamente en los fragmentos de "
    "ley proporcionados como contexto. Si el contexto no contiene "
    "información suficiente para responder con seguridad, dilo "
    "explícitamente en vez de inventar contenido legal. Usa un lenguaje "
    "claro, dirigido a abogados y asesores fiscales, y cita la ley y el "
    "artículo concreto cuando afirmes algo."
)


@dataclass
class Citation:
    """Una cita mostrada junto a la respuesta del asistente."""

    title: str
    relative_path: str
    url: str
    snippet: str


@dataclass
class AssistantAnswer:
    """Resultado completo de una pregunta al asistente."""

    question: str
    answer: str
    citations: list[Citation]


def _build_context(fragments: list[LawFragment]) -> str:
    """Concatena los fragmentos recuperados en un bloque de contexto para el LLM."""
    blocks = []
    for i, fragment in enumerate(fragments, start=1):
        blocks.append(
            f"[Fuente {i}] {fragment.title} ({fragment.relative_path})\n"
            f"{fragment.snippet}"
        )
    return "\n\n---\n\n".join(blocks)


def _build_prompt(question: str, fragments: list[LawFragment]) -> str:
    context = _build_context(fragments)
    return (
        "Contexto legal recuperado (usa solo esto para responder, citando "
        "el número de fuente entre corchetes, p. ej. [Fuente 1]):\n\n"
        f"{context}\n\n"
        "---\n\n"
        f"Pregunta del usuario:\n{question}\n\n"
        "Responde en español, de forma clara y precisa, citando las fuentes "
        "relevantes entre corchetes."
    )


def answer_question(question: str, max_sources: int = 5) -> AssistantAnswer:
    """
    Punto de entrada principal del asistente: recibe una pregunta y
    devuelve una respuesta con citas.
    """
    fragments = search_laws(question, max_results=max_sources)

    if not fragments:
        return AssistantAnswer(
            question=question,
            answer=(
                "No se han encontrado artículos relacionados en el repositorio "
                "legalize-es. Prueba a reformular la pregunta con términos más "
                "concretos (nombre de la ley, número de artículo, impuesto, etc.), "
                "o comprueba que el repo está clonado en local."
            ),
            citations=[],
        )

    prompt = _build_prompt(question, fragments)
    answer_text = ask_llm(prompt=prompt, system=SYSTEM_PROMPT)

    citations = [
        Citation(
            title=fragment.title,
            relative_path=fragment.relative_path,
            url=github_url(fragment.relative_path),
            snippet=fragment.snippet,
        )
        for fragment in fragments
    ]

    return AssistantAnswer(question=question, answer=answer_text, citations=citations)
