"""
services/laws.py

Acceso de bajo nivel al repositorio legalize-es (legislación española en
Markdown, versionada con Git: https://github.com/legalize-dev/legalize-es).

Responsabilidades de este módulo:
    - Localizar el repo clonado en local.
    - Indexar y buscar texto en los archivos .md (búsqueda simple).
    - Leer el contenido de un archivo (versión actual o en un commit concreto).
    - Obtener el historial de commits (global o de una ruta concreta).
    - Calcular diffs entre dos versiones de un mismo archivo.

Todo lo que interactúa con Git usa GitPython. Si el repo no está clonado,
las funciones devuelven listas/resultados vacíos y la UI muestra un aviso,
en vez de lanzar excepciones que rompan la app.
"""

from __future__ import annotations

import difflib
import math
import re
from dataclasses import dataclass, field
from datetime import datetime
from functools import lru_cache
from pathlib import Path

try:
    from git import Repo
    from git.exc import InvalidGitRepositoryError, NoSuchPathError
except ImportError:  # GitPython no instalado todavía
    Repo = None
    InvalidGitRepositoryError = NoSuchPathError = Exception

# Carpeta donde se espera el repo clonado, junto al proyecto.
# Ajusta esta constante (o exporta LEGALIZE_REPO_PATH en tu entorno) si lo
# clonas en otra ubicación.
REPO_DIR_NAME = "legalize-es"
PROJECT_ROOT = Path(__file__).resolve().parent.parent
REPO_PATH = PROJECT_ROOT / REPO_DIR_NAME

# Palabras demasiado frecuentes en textos legales españoles como para servir
# de criterio de búsqueda (aparecen en casi cualquier norma). Se ignoran al
# tokenizar la consulta para que la búsqueda no se diluya en ruido.
STOPWORDS = {
    "que", "dice", "sobre", "para", "por", "los", "las", "del", "con",
    "una", "uno", "articulo", "artículo", "ley", "leyes", "real", "decreto",
    "boe", "general", "estado", "españa", "español", "española", "como",
    "esta", "este", "sus", "les", "sin", "sea", "sean", "sido", "sera",
    "será", "sido", "cual", "cuales", "cuando", "donde", "cada", "entre",
}

# Los títulos de las normas en legalize-es usan el nombre completo, no el
# acrónimo habitual (p. ej. "Impuesto sobre la Renta de las Personas
# Físicas", nunca "IRPF"). Sin esto, preguntar por "IRPF" no encontraría la
# propia ley del IRPF. Cada acrónimo se mapea a su frase canónica completa:
# las palabras sueltas se añaden como términos de búsqueda normales, y si la
# frase entera aparece tal cual en el título de una norma, esa norma recibe
# un bonus fuerte (es una señal casi inequívoca de que es la ley "raíz",
# no una norma que solo toca el tema de pasada).
ACRONYM_PHRASES: dict[str, str] = {
    "irpf": "impuesto sobre la renta de las personas físicas",
    "irnr": "impuesto sobre la renta de no residentes",
    "is": "impuesto sobre sociedades",
    "iva": "impuesto sobre el valor añadido",
    "itp": "transmisiones patrimoniales",
    "ajd": "actos jurídicos documentados",
    "isd": "impuesto sobre sucesiones y donaciones",
    "iae": "impuesto sobre actividades económicas",
    "ibi": "impuesto sobre bienes inmuebles",
    "lgt": "ley general tributaria",
    "et": "estatuto de los trabajadores",
    "lpl": "procedimiento laboral",
    "lrjs": "jurisdicción social",
    "lsc": "sociedades de capital",
    "lec": "enjuiciamiento civil",
    "lecrim": "enjuiciamiento criminal",
    "cc": "código civil",
    "cp": "código penal",
    "lopd": "protección de datos",
}


@dataclass
class LawFragment:
    """Un fragmento de un archivo Markdown que coincide con una búsqueda."""

    file_path: Path       # ruta absoluta en disco
    relative_path: str    # ruta relativa dentro del repo (para citar/enlazar)
    title: str             # título deducido del archivo (primer # del md)
    snippet: str            # fragmento de texto con el contexto del match
    full_text: str          # contenido completo del archivo (para el LLM)
    score: float = 0         # relevancia (título pesa más que densidad en cuerpo), para ordenar


@dataclass
class CommitInfo:
    """Metadatos de un commit del repo, usados por el Monitor."""

    hexsha: str
    short_sha: str
    author: str
    date: datetime
    message: str
    files_changed: list[str] = field(default_factory=list)


def repo_available() -> bool:
    """Comprueba que el repo legalize-es está clonado y accesible."""
    return REPO_PATH.exists() and (REPO_PATH / ".git").exists()


@lru_cache(maxsize=1)
def _get_repo():
    """Devuelve el objeto Repo (GitPython), cacheado tras la primera carga."""
    if Repo is None or not repo_available():
        return None
    try:
        return Repo(REPO_PATH)
    except (InvalidGitRepositoryError, NoSuchPathError):
        return None


def _extract_title(markdown_text: str, fallback: str) -> str:
    """Extrae el primer encabezado '# ...' de un Markdown como título."""
    match = re.search(r"^#\s+(.+)$", markdown_text, flags=re.MULTILINE)
    return match.group(1).strip() if match else fallback


def list_markdown_files() -> list[Path]:
    """Lista todos los .md del repo (excluyendo la carpeta .git)."""
    if not repo_available():
        return []
    return sorted(p for p in REPO_PATH.rglob("*.md") if ".git" not in p.parts)


@dataclass
class _IndexedDocument:
    """Documento precargado en memoria para acelerar búsquedas repetidas."""

    file_path: Path
    relative_path: str
    title: str
    text: str
    lower_text: str
    rank: str  # rango normativo del front-matter YAML: "ley", "orden", "resolucion"...


# Cada archivo de legalize-es trae un front-matter YAML con `rank: "ley"`,
# `"orden"`, `"resolucion"`, etc. Es una señal mucho más fiable que el texto
# para distinguir la norma primaria (la "ley raíz") de normas que solo la
# implementan o la mencionan (una orden que aprueba un modelo de
# declaración, una resolución administrativa...). Se usa como desempate
# cuando el título ya matchea de forma similar.
_RANK_PATTERN = re.compile(r'^rank:\s*"?([\w_]+)"?', flags=re.MULTILINE)

RANK_BOOST: dict[str, float] = {
    "ley": 80,
    "ley_organica": 80,
    "codigo": 80,
    "real_decreto_legislativo": 60,
    "real_decreto_ley": 40,
    "real_decreto": 20,
    "decreto": 20,
    "acuerdo_internacional": 10,
    # "orden", "resolucion", "circular", "instruccion", etc. no llevan
    # boost: suelen ser normas de desarrollo/administrativas, no la norma
    # sustantiva que alguien busca al preguntar por "la ley del IRPF".
}


def _extract_rank(text: str) -> str:
    match = _RANK_PATTERN.search(text[:2000])  # el front-matter va al principio
    return match.group(1) if match else ""


def _read_one_document(file_path: Path) -> _IndexedDocument | None:
    try:
        text = file_path.read_text(encoding="utf-8")
    except (UnicodeDecodeError, OSError):
        return None
    return _IndexedDocument(
        file_path=file_path,
        relative_path=str(file_path.relative_to(REPO_PATH)).replace("\\", "/"),
        title=_extract_title(text, fallback=file_path.stem),
        text=text,
        lower_text=text.lower(),
        rank=_extract_rank(text),
    )


@lru_cache(maxsize=1)
def _load_document_index() -> tuple[_IndexedDocument, ...]:
    """
    Lee una única vez todos los .md del repo y los guarda en memoria.

    El repo tiene más de 12.000 archivos: leerlos del disco en cada
    búsqueda (uno por consulta) tarda demasiado para una app interactiva.
    Cachear el índice hace que la primera búsqueda de la sesión tarde
    bastante menos y las siguientes sean instantáneas. Si el repo se
    actualiza (git pull) mientras la app está abierta, hay que reiniciar
    Streamlit para que se vuelva a indexar.

    La lectura es I/O-bound (miles de aperturas de archivo pequeñas), así
    que se paraleliza con un ThreadPoolExecutor: en Windows, cada apertura
    de archivo tiene una latencia fija apreciable, y lanzar las lecturas
    concurrentemente reduce el tiempo total de forma drástica frente a
    leerlas una a una.
    """
    from concurrent.futures import ThreadPoolExecutor

    file_paths = list_markdown_files()
    documents: list[_IndexedDocument] = []
    with ThreadPoolExecutor(max_workers=32) as executor:
        for doc in executor.map(_read_one_document, file_paths):
            if doc is not None:
                documents.append(doc)
    return tuple(documents)


def search_laws(query: str, max_results: int = 5) -> list[LawFragment]:
    """
    Búsqueda simple de texto libre sobre los Markdown del repo.

    Estrategia (suficiente para una primera versión y fácil de explicar en
    clase): tokeniza la consulta en palabras, cuenta apariciones de cada
    palabra (case-insensitive) en cada archivo, y se queda con los archivos
    con más coincidencias. Para el fragmento mostrado, busca la primera
    línea donde aparece alguno de los términos y añade contexto alrededor.

    Los términos demasiado genéricos (STOPWORDS) se ignoran. Los números
    (p. ej. "32" en "artículo 32") sí se conservan como término, porque
    son justo lo que identifica un artículo concreto dentro de una norma
    larga. La puntuación combina tres señales:
        1. Frase canónica de un acrónimo (p. ej. "impuesto sobre la renta
           de las personas físicas" para "IRPF") encontrada tal cual en el
           título: bonus muy alto, es casi inequívoco de que esa es la ley
           buscada, no una norma que solo la menciona de pasada.
        2. Coincidencias de términos sueltos en el título: pesan mucho.
        3. Coincidencias en el cuerpo: cuentan, pero amortiguadas con
           logaritmo para que una norma larga y realmente relevante (como
           una ley completa con cientos de artículos) no quede por debajo
           de una norma corta que solo repite el término un par de veces.

    Usa el índice en memoria de `_load_document_index()` en vez de releer
    los archivos del disco en cada llamada. Si el proyecto crece más allá
    de esto, este es el punto natural para sustituir por una librería de
    indexación real (p. ej. whoosh o un índice vectorial).
    """
    if not query.strip():
        return []

    all_terms = [t.lower() for t in re.findall(r"\w+", query) if len(t) > 2 or t.isdigit()]
    terms = [t for t in all_terms if t not in STOPWORDS] or all_terms
    if not terms:
        return []

    canonical_phrases: list[str] = []
    for term in list(terms):
        phrase = ACRONYM_PHRASES.get(term)
        if not phrase:
            continue
        canonical_phrases.append(phrase)
        for word in phrase.split():
            if len(word) > 2 and word not in terms:
                terms.append(word)

    TITLE_WEIGHT = 60
    PHRASE_BONUS = 500
    ARTICLE_MATCH_BONUS = 400

    # Si la pregunta pide un artículo concreto ("¿qué dice el artículo 32...
    # "), que ese artículo exista literalmente en el documento es una señal
    # de relevancia muy fuerte (además de permitir citar el texto exacto en
    # vez de un fragmento genérico, ver _build_snippet).
    article_number = _extract_article_number(query)

    results: list[LawFragment] = []
    for doc in _load_document_index():
        lower_title = doc.title.lower()
        phrase_bonus = PHRASE_BONUS * sum(1 for p in canonical_phrases if p in lower_title)
        # Cuenta términos DISTINTOS presentes en el título, no repeticiones:
        # algunos títulos del BOE son frases larguísimas que repiten el
        # mismo término varias veces, y eso no debería pesar más que un
        # título corto que simplemente acierta en los mismos términos.
        title_hits = sum(1 for term in terms if term in lower_title)
        body_hits = sum(doc.lower_text.count(term) for term in terms)
        if title_hits == 0 and body_hits == 0:
            continue  # el boost de rango no debe hacer aparecer normas sin relación

        article_section = (
            _extract_article_section(doc.text, article_number) if article_number else None
        )
        snippet = article_section or _build_snippet(doc.text, terms)
        article_bonus = ARTICLE_MATCH_BONUS if article_section else 0

        score = (
            phrase_bonus
            + title_hits * TITLE_WEIGHT
            + math.log1p(body_hits)
            + RANK_BOOST.get(doc.rank, 0)
            + article_bonus
        )

        results.append(
            LawFragment(
                file_path=doc.file_path,
                relative_path=doc.relative_path,
                title=doc.title,
                snippet=snippet,
                full_text=doc.text,
                score=score,
            )
        )

    results.sort(key=lambda f: f.score, reverse=True)
    return results[:max_results]


# Cada artículo de una norma en legalize-es es un encabezado Markdown propio
# (p. ej. "###### Artículo 32. Reducciones."), seguido de su texto hasta el
# siguiente encabezado. Si la pregunta menciona un número de artículo, hay
# que extraer esa sección completa — no basta con 2 líneas de contexto
# alrededor de la primera coincidencia genérica, que puede caer en
# cualquier sitio del documento (fechas, referencias a otros artículos...).
_ARTICLE_NUMBER_IN_QUERY = re.compile(r"art[íi]culo\s+(\d+)", re.IGNORECASE)
_ARTICLE_HEADING = re.compile(r"^#{1,6}\s*art[íi]culo\s+(\d+)\b", re.IGNORECASE | re.MULTILINE)
_ANY_HEADING = re.compile(r"^#{1,6}\s", re.MULTILINE)
_MAX_ARTICLE_CHARS = 6000


def _extract_article_number(query: str) -> str | None:
    """Detecta un número de artículo mencionado en la pregunta, si lo hay."""
    match = _ARTICLE_NUMBER_IN_QUERY.search(query)
    return match.group(1) if match else None


def _extract_article_section(text: str, article_number: str) -> str | None:
    """
    Busca el encabezado "Artículo {article_number}" en el texto y devuelve
    todo su contenido, hasta el siguiente encabezado de cualquier nivel.
    Devuelve None si ese artículo concreto no aparece en el documento.
    """
    for match in _ARTICLE_HEADING.finditer(text):
        if match.group(1) == article_number:
            start = match.start()
            next_heading = _ANY_HEADING.search(text, match.end())
            end = next_heading.start() if next_heading else len(text)
            section = text[start:end].strip()
            if len(section) > _MAX_ARTICLE_CHARS:
                section = section[:_MAX_ARTICLE_CHARS] + "\n[...texto truncado por longitud...]"
            return section
    return None


def _build_snippet(text: str, terms: list[str], context_lines: int = 6) -> str:
    """
    Fragmento de contexto genérico: unas líneas alrededor de la primera
    coincidencia de algún término. Es el fallback cuando la pregunta no
    menciona (o el documento no contiene) un artículo concreto que se
    pueda extraer entero con `_extract_article_section`.
    """
    lines = text.splitlines()
    for i, line in enumerate(lines):
        lower_line = line.lower()
        if any(term in lower_line for term in terms):
            start = max(0, i - context_lines)
            end = min(len(lines), i + context_lines + 1)
            return "\n".join(lines[start:end]).strip()
    return "\n".join(lines[:5]).strip()


def read_file(relative_path: str) -> str | None:
    """Lee el contenido actual (working tree) de un archivo del repo."""
    file_path = REPO_PATH / relative_path
    if not file_path.exists():
        return None
    return file_path.read_text(encoding="utf-8")


def read_file_at_commit(relative_path: str, commit_sha: str) -> str | None:
    """Lee el contenido de un archivo tal y como estaba en un commit concreto."""
    repo = _get_repo()
    if repo is None:
        return None
    try:
        commit = repo.commit(commit_sha)
        blob = commit.tree / relative_path
        return blob.data_stream.read().decode("utf-8")
    except Exception:
        return None


def github_url(relative_path: str, commit_sha: str | None = None) -> str:
    """Construye la URL pública en GitHub para citar la fuente exacta."""
    ref = commit_sha or "main"
    return f"https://github.com/legalize-dev/legalize-es/blob/{ref}/{relative_path}"


def get_commit_history(
    path_filter: str | None = None, max_commits: int = 20
) -> list[CommitInfo]:
    """
    Devuelve los últimos commits del repo, opcionalmente filtrados por una
    ruta (archivo o carpeta), usados por el Monitor de cambios normativos.
    """
    repo = _get_repo()
    if repo is None:
        return []

    kwargs = {"max_count": max_commits}
    iter_commits = (
        repo.iter_commits(paths=path_filter, **kwargs)
        if path_filter
        else repo.iter_commits(**kwargs)
    )

    commits: list[CommitInfo] = []
    for commit in iter_commits:
        try:
            files_changed = list(commit.stats.files.keys())
        except Exception:
            files_changed = []
        commits.append(
            CommitInfo(
                hexsha=commit.hexsha,
                short_sha=commit.hexsha[:7],
                author=str(commit.author),
                date=datetime.fromtimestamp(commit.committed_date),
                message=commit.message.strip(),
                files_changed=files_changed,
            )
        )
    return commits


def diff_file_between_commits(
    relative_path: str, old_sha: str, new_sha: str
) -> str:
    """
    Devuelve un diff unificado (formato texto, estilo `git diff`) del
    archivo entre dos commits. Útil tanto para mostrarlo en crudo como
    para pasarlo a un LLM y pedirle un resumen en lenguaje claro.
    """
    old_text = read_file_at_commit(relative_path, old_sha) or ""
    new_text = read_file_at_commit(relative_path, new_sha) or ""
    return unified_diff_text(old_text, new_text, relative_path)


def unified_diff_text(old_text: str, new_text: str, label: str) -> str:
    """Genera un diff unificado legible entre dos textos."""
    diff_lines = difflib.unified_diff(
        old_text.splitlines(keepends=True),
        new_text.splitlines(keepends=True),
        fromfile=f"a/{label}",
        tofile=f"b/{label}",
    )
    return "".join(diff_lines)


def list_top_level_folders() -> list[str]:
    """
    Lista las carpetas de primer nivel del repo (p. ej. 'fiscal', 'laboral',
    'autonomico'), usadas para las suscripciones por materia en el Monitor.
    """
    if not repo_available():
        return []
    return sorted(
        p.name
        for p in REPO_PATH.iterdir()
        if p.is_dir() and p.name != ".git"
    )
