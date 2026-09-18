# Consultor IA — Legislación Española

Herramienta local (sin servidor, todo en tu portátil) que integra tres
funcionalidades sobre el repositorio [`legalize-dev/legalize-es`](https://github.com/legalize-dev/legalize-es)
(legislación española en Markdown, versionada con Git): un asistente
jurídico/fiscal con citas reales, un monitor de cambios normativos, y un
generador de guías y comparativas entre versiones de una norma.

> Proyecto educativo (pensado como base de curso para abogados y
> fiscalistas). Las respuestas de IA son orientativas y no sustituyen el
> asesoramiento de un profesional colegiado.

---

## Funcionalidades

### 1. Asistente jurídico/fiscal
Preguntas en lenguaje natural (p. ej. *"¿Qué dice el artículo 32 de la
Ley del IRPF sobre reducciones?"*). El asistente busca en los ~12.300
archivos Markdown del repo, construye un prompt con los fragmentos
relevantes (el artículo exacto cuando la pregunta lo menciona) y pide al
LLM una respuesta citando `[Fuente N]`. La UI muestra la respuesta junto
con cada cita: ley, ruta del archivo y enlace directo a GitHub.

### 2. Monitor de cambios normativos
Suscríbete a carpetas del repo (por comunidad autónoma o materia) y
consulta un feed con los últimos commits como "alertas normativas". Para
cualquier archivo modificado en un commit, genera el diff y un resumen en
lenguaje claro de qué cambió.

### 3. Guías y comparativas
Busca una norma y genera, en ambos casos como documento HTML descargable:
- una **guía**: resumen de novedades/puntos clave generado por IA, más un
  enlace al texto íntegro en GitHub (no vuelca la ley completa — para una
  norma de cientos de artículos eso sería casi tan largo como la propia
  ley y poco útil como guía);
- una **comparativa** entre dos commits (versiones) de la misma norma:
  solo el **diff** (líneas añadidas/eliminadas, resaltadas) entre ambas
  versiones, más un resumen de los cambios clave generado por IA — no el
  texto íntegro de las dos versiones una debajo de otra.

---

## Arquitectura

```
app.py                  Punto de entrada Streamlit (UI + navegación, sin lógica de negocio)
services/
  laws.py                Acceso al repo: búsqueda, lectura de versiones, historial, diffs
  assistant.py            Lógica del asistente (recupera contexto, arma el prompt, cita fuentes)
  monitor.py               Feed de cambios + explicación de diffs con IA
  guides.py                 Generación de guías HTML y comparativas de versiones
utils/
  llm.py                    Único punto de integración con el LLM (Gemini / Anthropic / OpenAI)
legalize-es/                Repo clonado con la legislación (no versionado en este proyecto)
```

La UI nunca llama directamente ni al repo Git ni al LLM: todo pasa por
`services/` y `utils/llm.py`, así que cambiar de proveedor de IA o de
estrategia de búsqueda se hace en un solo sitio.

---

## Instalación

### 1. Clonar el repositorio de legislación

Debe quedar como una carpeta `legalize-es` junto a `app.py`:

```bash
git clone https://github.com/legalize-dev/legalize-es.git
```

### 2. Entorno virtual y dependencias

```bash
python -m venv venv
venv\Scripts\activate        # Windows
# source venv/bin/activate   # macOS/Linux

pip install -r requirements.txt
```

### 3. Configurar la API key del LLM (opcional)

Copia la plantilla y rellena tu clave:

```bash
copy .env.example .env        # Windows
# cp .env.example .env        # macOS/Linux
```

```env
GEMINI_API_KEY=tu_clave_aqui
```

El proveedor se **autodetecta** según qué clave esté rellena en `.env`
(orden: Gemini → Anthropic → OpenAI). Si no rellenas ninguna, la app
funciona en **modo simulado**: la búsqueda y las citas son reales, pero
el texto de la respuesta es un aviso de ejemplo en vez de una llamada al
LLM — útil para probar toda la interfaz sin coste ni conexión.

El archivo `.env` nunca se sube al repositorio (está en `.gitignore`);
`.env.example` sí, como plantilla para quien clone el proyecto.

### 4. Ejecutar

```bash
streamlit run app.py
```

La primera pregunta de la sesión tarda más (~45-70s: indexa en memoria
los ~12.300 archivos del repo, con lectura en paralelo). Las siguientes
son mucho más rápidas (segundos).

---

## Notas técnicas

- **Búsqueda**: motor léxico propio (sin dependencias externas de
  indexación) con stopwords legales, expansión de acrónimos fiscales
  comunes (IRPF, IVA, IS, ET...), boost por rango normativo (`ley` >
  `orden`/`resolución`, leído del front-matter de cada archivo) y
  extracción del artículo completo cuando la pregunta lo menciona. Es
  intencionadamente simple; si el proyecto crece, el punto natural de
  mejora es migrar a una librería de indexación real (whoosh) o a
  embeddings.
- **Coste por pregunta**: en torno a 1.000-4.000 tokens de entrada según
  cuántos artículos completos se incluyan, y salida limitada a 1.024
  tokens — trivial para cualquier modelo actual.
- **Reintentos**: `utils/llm.py` reintenta automáticamente con backoff
  ante errores transitorios del proveedor (503/429/sobrecarga); un error
  de configuración (API key inválida) no se reintenta.
- **PDF**: las guías/comparativas se generan y descargan en HTML
  (imprimible a PDF desde el navegador). Exportación automática a PDF
  queda anotada como mejora futura en `services/guides.py`.

## Limitaciones conocidas

Con un corpus tan heterogéneo (nacional + 17 comunidades autónomas,
siglos de BOE), una búsqueda por texto —incluso con las mejoras de
relevancia aplicadas— a veces prioriza una norma relacionada por encima
de la ley "raíz" exacta. Está documentado en el docstring de
`services/laws.py::search_laws` junto con el camino de mejora recomendado.

## Stack

Python 3.10+ · Streamlit · GitPython · google-genai (Gemini) · python-dotenv · python-markdown
