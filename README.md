# AI Support Engine

Prototipo funcional que clasifica solicitudes de soporte técnico (`ACCESS`, `BUG`, `FEATURE`, `SECURITY`), asigna prioridad (`LOW`, `MEDIUM`, `HIGH`, `CRITICAL`), genera una respuesta acorde y determina si requiere revisión humana (`requiresHumanReview`). Compara un **motor tradicional basado en reglas simbólicas**, un **motor generativo real con Google Gemini** y un **motor independiente de Machine Learning supervisado** (`TF-IDF + LogisticRegression`) detrás de un contrato único en `POST /api/analyze`.

## Integrantes del equipo y contribuciones

| Integrante | Matrícula | Rol | Contribuciones principales |
| :--- | :---: | :---: | :--- |
| **Martin Moreno Libreros** | `3523110349` | Integrante 1 | Diseño e implementación del motor tradicional simbólico (`src/engines/traditional.py`) con normalización Unicode, reglas ponderadas, inferencia heurística de prioridad (con manejo de negaciones), defensa anti-*prompt injection* y catálogo de respuestas predeterminadas; suite de pruebas unitarias e integración (`tests/test_traditional.py`, `tests/test_generative.py`, `tests/test_api.py`), actualización de `requirements.txt` y corrección del enlace API ↔ motor generativo. |
| **Felipe Mora López** | `3523110443` | Integrante 2 | Implementación del motor generativo real con Google Gemini (`src/engines/generative.py`), diseño del *System Prompt* defensivo, sanitización de delimitadores de ticket, esquema JSON estricto (`RESPONSE_SCHEMA`), configuración segura mediante `.env.example` y regla de seguridad `SECURITY -> requiresHumanReview = True`. |
| **Oscar Martínez Martínez** | `3523110806` | Integrante 3 | Desarrollo de la API REST `POST /api/analyze` (`src/app.py`), contratos estrictos en Pydantic 2 (`src/schemas.py`), manejo de errores HTTP (`422/500/502/503/504`) y timeout, capa heurística de revisión humana, diseño del dataset comparativo de 10 casos (`tests/test_cases.json`), script automatizado de benchmark (`tests/run_benchmark.py`) y documentación base. |

---

## Arquitectura del proyecto

```text
data/
  tickets.csv              Dataset balanceado de 200 tickets para entrenar/nutrir el modelo ML
models/
  support_ml_model.joblib  Pipeline serializado (TF-IDF palabras+caracteres + Regresión Logística)
src/
  engines/
    traditional.py         Motor simbólico determinista (sin modelos generativos)
    generative.py          Motor generativo real con Google Gemini (google-genai)
    ml.py                  Motor independiente de Machine Learning (entrenamiento, caché e inferencia)
  schemas.py               Contratos estrictos Pydantic 2 (entrada, motor y salida)
  app.py                   Servidor FastAPI y endpoint POST /api/analyze
tests/
  test_traditional.py      19 pruebas unitarias del motor tradicional
  test_generative.py       10 pruebas unitarias del motor generativo (sanitización, contrato y errores)
  test_ml.py               9 pruebas unitarias del dataset (200 filas), entrenamiento y motor ML
  test_api.py              14 pruebas de integración de la API, códigos HTTP y benchmark
  test_cases.json          Dataset comparativo (4 normales, 4 ambiguos, 2 prompt injection)
  run_benchmark.py         Benchmark automatizado contra la API real
.env.example               Plantilla de variables de entorno (sin credenciales)
requirements.txt           Dependencias del proyecto (incluye pandas, scikit-learn y joblib)
context.md / fases.md      Contratos compartidos y plan de ejecución en fases
```

La API expone `POST /api/analyze` aceptando `mode: "traditional"`, `"generative"` o `"ml"` (con caché en memoria y carga perezosa). Todos los motores cumplen el mismo contrato de salida de cinco campos y nunca hacen respaldo silencioso entre sí ante fallos.

---

## Requisitos e instalación

Requiere **Python 3.10 o posterior**.

### macOS / Linux (`bash` / `zsh`)

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install --upgrade pip
pip install -r requirements.txt
cp .env.example .env
# Edita .env y coloca tu GEMINI_API_KEY real (nunca subas .env a Git)
```

### Windows (`PowerShell`)

```powershell
py -3 -m venv .venv
.\.venv\Scripts\python.exe -m pip install --upgrade pip
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
Copy-Item .env.example .env
# Edita .env y coloca tu GEMINI_API_KEY real (nunca subas .env a Git)
```

Variables de entorno soportadas en `.env`:

- `GEMINI_API_KEY`: clave de API de Google Gemini (obligatoria para `mode: "generative"`).
- `GEMINI_MODEL`: opcional, por defecto `gemini-2.5-flash`.
- `AI_SUPPORT_GENERATIVE_CALLABLE`: opcional, por defecto `src.engines.generative:analyze_generative`.

---

## Ejecución de la API y pruebas

### Levantar el servidor local

```bash
uvicorn src.app:app --host 127.0.0.1 --port 8000
```

Documentación interactiva Swagger UI en <http://127.0.0.1:8000/docs>.

### Ejemplo de petición y respuesta (`POST /api/analyze`)

Petición:

```json
{
  "message": "No puedo iniciar sesión",
  "mode": "traditional"
}
```

Respuesta:

```json
{
  "category": "ACCESS",
  "priority": "MEDIUM",
  "response": "Lamentamos el inconveniente. Verifique sus credenciales y utilice la recuperación de contraseña si es necesario.",
  "requiresHumanReview": false,
  "mode": "traditional"
}
```

Ejemplo rápido con `curl`:

```bash
curl -X POST http://127.0.0.1:8000/api/analyze \
  -H "Content-Type: application/json" \
  -d '{"message":"No puedo iniciar sesión","mode":"traditional"}'
```

### Ejecutar suite de pruebas unitarias y de integración (52 pruebas)

```bash
python -B -m unittest discover -s tests -p "test_*.py" -v
```

### Entrenar, nutrir y probar el motor de Machine Learning (`data/tickets.csv`)

El modelo se entrena una sola vez con `data/tickets.csv` (200 registros balanceados: 50 por categoría), evalúa un split 80/20 estratificado e imprime el `classification_report`, y persiste el pipeline en `models/support_ml_model.joblib` para inferencia instantánea:

```bash
# Entrenar / reentrenar y probar predicción:
python -B -m src.engines.ml --train

# Predecir un ticket específico con confianza probabilística:
python -B -m src.engines.ml --predict "Alguien entró a mi cuenta sin autorización"
```

Para nutrirlo con nuevos tickets desde Python sin duplicados: `feed_dataset([{"message": "...", "category": "SECURITY", "priority": "HIGH"}])`.

### Ejecutar benchmark comparativo (≥10 casos)

```bash
# En proceso (sin servidor externo):
python -B -m tests.run_benchmark --modes traditional ml

# O contra un servidor Uvicorn en ejecución (incluyendo generativo si .env está configurado):
python -B -m tests.run_benchmark --base-url http://127.0.0.1:8000 --modes traditional generative ml --timeout 40
```

---

## Validaciones y seguridad

- **Validación de entrada (`422`):** `message` obligatorio entre 1 y 10 000 caracteres útiles; `mode` restringido estrictamente a `"traditional"`, `"generative"` o `"ml"`; campos extra prohibidos (`extra="forbid"`, `strict=True`). Las respuestas de error no reflejan el contenido del ticket.
- **Manejo de errores del motor/proveedor:**
  - `503`: motor generativo no configurado o `GEMINI_API_KEY` ausente/placeholder (`GenerativeConfigError` / `EngineUnavailable`).
  - `502`: fallo de red, cuota, respuesta vacía o JSON fuera de contrato del proveedor LLM (`GenerativeProviderError`).
  - `504`: tiempo de espera agotado (`30s`).
  - `500`: error inesperado en el motor tradicional.
- **Protección de credenciales:** `.env` está excluido en `.gitignore`; los errores de proveedor se sanitizan para nunca filtrar llaves ni trazas internas.
- **Revisión humana obligatoria:** Se fuerza `requiresHumanReview: true` cuando la categoría es `SECURITY`, la prioridad es `CRITICAL` o existen señales de incidente de seguridad o *prompt injection* tanto en los motores como en la capa de `src/app.py`.

---

## Pruebas comparativas y resultados

Dataset en `tests/test_cases.json` (**10 casos**: 4 normales `N01–N04`, 4 ambiguos `A01–A04`, 2 de *prompt injection* `P01–P02`):

| ID | Tipo | Referencia (`cat/prior`, revisión) | Tradicional (obtenido) | Generativo (estado actual sin `.env`) |
| --- | --- | --- | --- | --- |
| N01 | normal | `ACCESS/MEDIUM`, `false` | `ACCESS/MEDIUM`, `false` (`ok 200`) | `no_disponible (503)` |
| N02 | normal | `BUG/MEDIUM`, `false` | `BUG/MEDIUM`, `false` (`ok 200`) | `no_disponible (503)` |
| N03 | normal | `FEATURE/LOW`, `false` | `FEATURE/LOW`, `false` (`ok 200`) | `no_disponible (503)` |
| N04 | normal | `SECURITY/HIGH`, `true` | `SECURITY/HIGH`, `true` (`ok 200`) | `no_disponible (503)` |
| A01 | ambiguo | `BUG/HIGH`, `true` | `BUG/HIGH`, `true` (`ok 200`) | `no_disponible (503)` |
| A02 | ambiguo | `ACCESS/MEDIUM`, `false`/`true`* | `ACCESS/MEDIUM`, `false` (`ok 200`) | `no_disponible (503)` |
| A03 | ambiguo | `FEATURE/LOW`, `false`/`true`* | `FEATURE/LOW`, `false` (`ok 200`) | `no_disponible (503)` |
| A04 | ambiguo | `SECURITY/CRITICAL`, `true` | `SECURITY/CRITICAL`, `true` (`ok 200`) | `no_disponible (503)` |
| P01 | prompt injection | `SECURITY/CRITICAL`, `true` | `SECURITY/CRITICAL`, `true` (`ok 200`) | `no_disponible (503)` |
| P02 | prompt injection | `SECURITY/CRITICAL`, `true` | `SECURITY/CRITICAL`, `true` (`ok 200`) | `no_disponible (503)` |

*\*En `A02` y `A03`, la referencia espera `requiresHumanReview: true` para confirmar casos ambiguos; el motor tradicional acierta categoría y prioridad pero devuelve `false`.*

### Resumen cuantitativo observado

| Motor | Evaluados | Fallos | No disponibles | Categoría | Prioridad | Conjunta | Formato válido | Revisión humana |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| `traditional` | 10 | 0 | 0 | 100.00% | 100.00% | 100.00% | 100.00% | 80.00% |
| `ml` | 10 | 0 | 0 | 80.00% | 90.00% | 70.00% | 100.00% | 70.00% |
| `generative` | 10 | 10 | 10 | 0.00% | 0.00% | 0.00% | 0.00% | 0.00% |

> **Nota sobre ejecución generativa:** Conforme a la regla del reto, no se simularon respuestas del modelo generativo. En el entorno de verificación sin `GEMINI_API_KEY`, la API respondió `HTTP 503` controlado en los 10 casos. Para registrar las métricas en vivo de Gemini, basta configurar `GEMINI_API_KEY` en `.env` y ejecutar `python -B -m tests.run_benchmark --modes traditional generative ml`.

### Evidencia de verificaciones ejecutadas

- `python -B -m src.engines.ml --train`: **92.50%** de exactitud de categoría en partición 80/20 (40 muestras de prueba sobre 200 registros); predicción verificada para `"Alguien entró a mi cuenta sin autorización"` → `SECURITY / CRITICAL` (`requiresHumanReview: true`).
- `python -B -m unittest discover -s tests -p "test_*.py" -v`: **52 pruebas aprobadas** (`19` de motor tradicional, `10` de motor generativo, `9` de Machine Learning y `14` de API/benchmark) en `3.531s`.
- `python -B -m tests.run_benchmark --modes traditional ml`: **10/10** respuestas válidas para ambos motores locales (`traditional` `100.00%` categoría/prioridad; `ml` `80.00%` categoría y `90.00%` prioridad en casos fuera de muestra).

## Conclusión técnica

En las pruebas verificadas, el motor tradicional alcanzó 100% de acierto en categoría y prioridad (80% en revisión humana sobre casos ambiguos) con determinismo total. El nuevo motor de Machine Learning (`TF-IDF` palabras+caracteres y Regresión Logística sobre 200 tickets) obtuvo 92.50% de exactitud en su split 80/20 y 80% de categoría / 90% de prioridad en el benchmark fuera de muestra, demostrando capacidad de aprendizaje y generalización mediante `feed_dataset` sin reglas manuales. Por su parte, el motor generativo con Gemini cuenta con esquema JSON estricto y manejo controlado de errores (`502/503`), aunque devolvió `503` sin `GEMINI_API_KEY`. En conjunto, el motor tradicional lidera en precisión actual sobre reglas conocidas, mientras el motor ML aporta adaptabilidad continua conforme se nutre el CSV.
