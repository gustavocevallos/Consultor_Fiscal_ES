"""
utils/llm.py

Punto único de integración con el LLM (Gemini, Claude u OpenAI). El resto
de la app nunca llama directamente a la API de un proveedor: siempre pasa
por `ask_llm()`, así que cambiar de proveedor o de modelo se hace en un
solo sitio.

Las credenciales se leen del archivo `.env` en la raíz del proyecto (ver
`.env.example` para la plantilla). Ese archivo está en `.gitignore` y
nunca debe subirse al repositorio.

Selección de proveedor:
    - Si defines LLM_PROVIDER en `.env` ("gemini", "anthropic" u "openai"),
      se usa ese explícitamente.
    - Si no, se autodetecta según qué API key esté presente en `.env`
      (orden de prioridad: Gemini > Anthropic > OpenAI).
    - Si no hay ninguna key configurada, la app funciona en modo
      "simulado": no llama a ningún LLM y devuelve una respuesta de
      ejemplo, para poder probar toda la interfaz sin coste ni conexión.
"""

from __future__ import annotations

import os
import textwrap
import time

from dotenv import load_dotenv

# Carga las variables definidas en .env al entorno del proceso. No falla si
# el archivo no existe (útil antes de que el usuario lo cree).
load_dotenv()

GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY")
ANTHROPIC_API_KEY = os.environ.get("ANTHROPIC_API_KEY")
OPENAI_API_KEY = os.environ.get("OPENAI_API_KEY")

# Modelos por defecto (ajustar según disponibilidad/coste).
GEMINI_MODEL = os.environ.get("GEMINI_MODEL", "gemini-3.6-flash")
ANTHROPIC_MODEL = os.environ.get("ANTHROPIC_MODEL", "claude-sonnet-5")
OPENAI_MODEL = os.environ.get("OPENAI_MODEL", "gpt-4.1")


def _autodetect_provider() -> str:
    explicit = os.environ.get("LLM_PROVIDER", "").strip().lower()
    if explicit:
        return explicit
    if GEMINI_API_KEY:
        return "gemini"
    if ANTHROPIC_API_KEY:
        return "anthropic"
    if OPENAI_API_KEY:
        return "openai"
    return "simulated"


LLM_PROVIDER = _autodetect_provider()

# Fragmentos que suelen aparecer en errores TRANSITORIOS del proveedor
# (sobrecarga puntual, límite de peticiones por minuto) y que por tanto
# merece la pena reintentar, a diferencia de un error de configuración
# (API key inválida, modelo inexistente...) que fallará siempre igual.
_TRANSIENT_ERROR_MARKERS = (
    "503", "429", "unavailable", "resource_exhausted",
    "rate limit", "rate_limit", "too many requests", "overloaded",
)


def _is_transient_error(exc: Exception) -> bool:
    text = str(exc).lower()
    return any(marker in text for marker in _TRANSIENT_ERROR_MARKERS)


def ask_llm(
    prompt: str,
    system: str | None = None,
    max_tokens: int = 1024,
    max_retries: int = 3,
) -> str:
    """
    Envía un prompt al LLM configurado y devuelve el texto de la respuesta.

    Args:
        prompt: el mensaje del usuario (normalmente ya incluye el contexto
            legal recuperado, construido por services/assistant.py u otros).
        system: instrucciones de sistema opcionales (rol, tono, formato).
        max_tokens: límite de tokens de salida.
        max_retries: reintentos ante errores transitorios del proveedor
            (503/429/sobrecarga), con espera creciente (1s, 2s, 4s...)
            entre cada uno. Un error de configuración (API key inválida,
            modelo inexistente) no se reintenta: fallaría igual las veces
            que se repita.

    Returns:
        El texto de la respuesta del modelo.
    """
    last_exc: Exception | None = None
    attempts = 0

    for attempt in range(max_retries + 1):
        attempts = attempt + 1
        try:
            result = _call_provider(prompt=prompt, system=system, max_tokens=max_tokens)
            if not result:
                # Red de seguridad: si algún proveedor devuelve None/"" sin
                # lanzar excepción (en vez de arreglarlo caso por caso en
                # cada _call_*), nunca debe llegar así a la UI.
                raise RuntimeError("El proveedor devolvió una respuesta vacía.")
            return result
        except Exception as exc:  # nunca debe tumbar la app por un fallo de red/API
            last_exc = exc
            is_last_attempt = attempt == max_retries
            if is_last_attempt or not _is_transient_error(exc):
                break
            time.sleep(2**attempt)  # backoff: 1s, 2s, 4s...

    transient_hint = (
        "\n\nEsto parece un problema temporal de disponibilidad del proveedor "
        "(no de tu configuración): espera un minuto y vuelve a intentarlo."
        if _is_transient_error(last_exc)
        else "\n\nRevisa la API key en el archivo .env y la configuración en utils/llm.py."
    )
    return (
        f"No se ha podido contactar con el proveedor de IA ({LLM_PROVIDER}) "
        f"tras {attempts} intento(s). Error: {last_exc}"
        f"{transient_hint}"
    )


def _call_provider(prompt: str, system: str | None, max_tokens: int) -> str:
    if LLM_PROVIDER == "gemini":
        return _call_gemini(prompt, system, max_tokens)
    if LLM_PROVIDER == "anthropic":
        return _call_anthropic(prompt, system, max_tokens)
    if LLM_PROVIDER == "openai":
        return _call_openai(prompt, system, max_tokens)
    return _simulated_response(prompt, system)


def _call_gemini(prompt: str, system: str | None, max_tokens: int) -> str:
    """
    Llamada real a la API de Google Gemini usando el SDK actual `google-genai`
    (Requiere `pip install google-genai`). El paquete antiguo
    `google-generativeai` está descontinuado por Google, así que no se usa.
    """
    if not GEMINI_API_KEY:
        raise RuntimeError("Falta GEMINI_API_KEY en el archivo .env.")

    from google import genai
    from google.genai import types

    client = genai.Client(api_key=GEMINI_API_KEY)
    response = client.models.generate_content(
        model=GEMINI_MODEL,
        contents=prompt,
        config=types.GenerateContentConfig(
            system_instruction=system,
            max_output_tokens=max_tokens,
        ),
    )

    # El SDK google-genai puede devolver response.text == None sin lanzar
    # ninguna excepción (p. ej. si un filtro de seguridad bloquea la
    # respuesta, o si se corta por max_output_tokens antes de generar
    # texto). Sin esta comprobación, ese None se propaga tal cual hasta la
    # UI y se ve literalmente el texto "None" en pantalla.
    if not response.text:
        finish_reason = None
        try:
            finish_reason = response.candidates[0].finish_reason
        except (AttributeError, IndexError, TypeError):
            pass
        raise RuntimeError(
            "Gemini no devolvió texto en la respuesta "
            f"(finish_reason={finish_reason}). Suele deberse a un filtro de "
            "seguridad/contenido del modelo (p. ej. si la pregunta se lee "
            "como petición de asesoramiento personalizado) o a que se "
            "alcanzó el límite de max_output_tokens antes de generar texto. "
            "Prueba a reformular la pregunta de forma más neutra/objetiva."
        )
    return response.text


def _call_anthropic(prompt: str, system: str | None, max_tokens: int) -> str:
    """Llamada real a la API de Anthropic (Claude). Requiere `pip install anthropic`."""
    if not ANTHROPIC_API_KEY:
        raise RuntimeError("Falta ANTHROPIC_API_KEY en el archivo .env.")

    import anthropic

    client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)
    response = client.messages.create(
        model=ANTHROPIC_MODEL,
        max_tokens=max_tokens,
        system=system or "",
        messages=[{"role": "user", "content": prompt}],
    )
    return response.content[0].text


def _call_openai(prompt: str, system: str | None, max_tokens: int) -> str:
    """Llamada real a la API de OpenAI. Requiere `pip install openai`."""
    if not OPENAI_API_KEY:
        raise RuntimeError("Falta OPENAI_API_KEY en el archivo .env.")

    from openai import OpenAI

    client = OpenAI(api_key=OPENAI_API_KEY)
    messages = []
    if system:
        messages.append({"role": "system", "content": system})
    messages.append({"role": "user", "content": prompt})

    response = client.chat.completions.create(
        model=OPENAI_MODEL,
        max_tokens=max_tokens,
        messages=messages,
    )
    return response.choices[0].message.content


def _simulated_response(prompt: str, system: str | None) -> str:
    """
    Respuesta de ejemplo para poder desarrollar y dar clase sin API key.
    No inventa contenido legal: simplemente indica que el LLM está en modo
    simulado y muestra el prompt que se le habría enviado, para poder
    verificar que el contexto recuperado es correcto.
    """
    preview = textwrap.shorten(prompt, width=600, placeholder=" [...]")
    return (
        "**Modo simulado (sin conexión a LLM real).**\n\n"
        "Esta es una respuesta de ejemplo. Para obtener una respuesta real, "
        "añade una API key en el archivo `.env` (ver `.env.example`) — por "
        "ejemplo `GEMINI_API_KEY=...` — y reinicia la app.\n\n"
        "**Prompt que se habría enviado al modelo:**\n\n"
        f"> {preview}"
    )
