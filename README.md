# AI Support Engine

Prototipo académico que clasifica tickets, asigna prioridad, redacta una respuesta y señala los casos que necesitan revisión humana. Compara un motor tradicional determinista con un motor generativo real.

El motor tradicional está implementado. En esta rama todavía no existen `src/engines/generative.py` ni `.env.example`: el modo generativo responde **503** hasta integrar y configurar el código del Integrante 2. No hay respuestas generativas simuladas ni respaldo automático al motor tradicional.

## Arquitectura y responsabilidades

```text
src/
  engines/
    traditional.py       Integrante 1: analyze_traditional(message: str) -> dict
    generative.py        Integrante 2: pendiente de incorporación
  schemas.py             Integrante 3: contratos Pydantic estrictos
  app.py                 Integrante 3: FastAPI y adaptación de motores
tests/
  test_traditional.py     Pruebas existentes del Integrante 1
  test_cases.json        Referencias del benchmark: 4 normales, 4 ambiguos, 2 ataques
  run_benchmark.py        Evaluación de ambos modos mediante la API real
context.md / fases.md     Contratos, propiedad de archivos y fases del equipo
requirements.txt         Base del equipo; dependencias web comentadas actualmente
```

La API invoca la función real `analyze_traditional(message)`. El contrato interno de los motores contiene cuatro campos; la API valida ese diccionario, aplica revisión humana y añade el modo solicitado. Se rechazan campos adicionales y tipos incorrectos. Los motores no se modifican.

Los documentos no fijan el nombre de la función generativa. El punto de integración es la variable `AI_SUPPORT_GENERATIVE_CALLABLE`, con formato `modulo:funcion`. Cuando el Integrante 2 entregue su módulo, se debe configurar su **nombre real**, cuya firma debe recibir un `message: str` y devolver el diccionario interno. Se admiten funciones síncronas y asíncronas. No se presupone una función generativa ya implementada.

## Requisitos e instalación (Windows PowerShell)

Python **3.10 o posterior**. Dependencias de ejecución: FastAPI, Pydantic 2 y Uvicorn. El benchmark también necesita HTTPX. `requirements.txt` solo contiene comentarios; instalarlo por sí solo todavía no instala estas dependencias. No se modificó por pertenecer a la base compartida.

```powershell
Set-Location C:\dev\ia-support-engine
py -3 -m venv .venv
.\.venv\Scripts\python.exe -m pip install --upgrade pip
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe -m pip install "fastapi>=0.115.0,<1" "pydantic>=2.9.0,<3" "uvicorn>=0.32.0,<1" "httpx>=0.27.0,<1"
```

Se usan las rutas del entorno virtual directamente, por lo que no es necesario cambiar la política de ejecución de PowerShell para activarlo.

La configuración del proveedor y sus dependencias quedan pendientes de la entrega del Integrante 2. No se asume proveedor, modelo ni nombre de API key. Tras recibir `.env.example`, se podrán copiar sus nombres a `.env` y cargar las variables como indique ese motor. **La API no carga `.env` automáticamente**. `.env` está ignorado por Git; nunca guardar claves reales en README, código, comandos compartidos ni resultados.

Ejemplo de configuración de la función, sustituyendo el marcador por el símbolo real entregado (no ejecutarlo literalmente):

```powershell
$env:AI_SUPPORT_GENERATIVE_CALLABLE = "src.engines.generative:NOMBRE_REAL_DE_LA_FUNCION"
```

## Ejecución

Desde la raíz del repositorio:

```powershell
.\.venv\Scripts\python.exe -m uvicorn src.app:app --host 127.0.0.1 --port 8000
```

Swagger UI: <http://127.0.0.1:8000/docs>. Esquema OpenAPI: <http://127.0.0.1:8000/openapi.json>.

## Uso de la API

```powershell
$body = @{ message = "No puedo iniciar sesión"; mode = "traditional" } | ConvertTo-Json
Invoke-RestMethod -Uri "http://127.0.0.1:8000/api/analyze" -Method Post -ContentType "application/json; charset=utf-8" -Body ([System.Text.Encoding]::UTF8.GetBytes($body))

$body = @{ message = "No puedo iniciar sesión"; mode = "generative" } | ConvertTo-Json
Invoke-RestMethod -Uri "http://127.0.0.1:8000/api/analyze" -Method Post -ContentType "application/json; charset=utf-8" -Body ([System.Text.Encoding]::UTF8.GetBytes($body))
```

Contrato de respuesta exitosa, ilustrado con el motor tradicional:

```json
{
  "category": "ACCESS",
  "priority": "MEDIUM",
  "response": "Lamentamos el inconveniente. Verifique sus credenciales y utilice la recuperación de contraseña si es necesario.",
  "requiresHumanReview": false,
  "mode": "traditional"
}
```

El modo generativo debe devolver esos mismos cinco campos con `mode: "generative"` y el contenido real del proveedor. Actualmente se obtiene HTTP 503:

```json
{"detail": "Motor generativo no disponible o no configurado."}
```

## Validaciones y seguridad

- `message`: string obligatorio, entre 1 y 10 000 caracteres después de retirar espacios exteriores; se rechazan mensajes vacíos o compuestos por espacios, números, booleanos, listas y nulos.
- `mode`: obligatorio, exclusivamente `traditional` o `generative`.
- Categorías: `ACCESS`, `BUG`, `FEATURE`, `SECURITY`. Prioridades: `LOW`, `MEDIUM`, `HIGH`, `CRITICAL`. `response` debe ser texto no vacío y `requiresHumanReview` debe ser un booleano JSON estricto.
- La API fuerza revisión si la categoría es `SECURITY`, la prioridad es `CRITICAL` o el mensaje contiene señales heurísticas de incidentes o manipulación, aunque el motor no las haya clasificado como seguridad. Esto solo cambia la bandera de revisión; categoría y prioridad siguen siendo las predicciones del motor.
- El filtro es conservador y puede producir falsos positivos o no detectar variantes nuevas. No reemplaza las defensas del prompt, la validación del proveedor ni la revisión humana. La bandera no envía ni asigna tickets a una persona.
- HTTP **422**: solicitud inválida. Se devuelven ubicación y tipo de error sin repetir el ticket. HTTP **500**: fallo del motor tradicional o salida inválida. HTTP **502**: fallo generativo o salida inválida. HTTP **503**: función generativa ausente, dependencia o configuración de integración no disponible. HTTP **504**: tiempo de espera agotado.
- Excepciones, trazas y configuración del proveedor no aparecen en la respuesta. Un error generativo nunca ejecuta el motor tradicional. La llamada tiene un límite de espera de 30 segundos; cancelar la espera no detiene necesariamente un proveedor síncrono que ya corre en un hilo. El motor generativo debe configurar también su propio timeout de red.

## Pruebas comparativas

La referencia se fija por el problema descrito, sin obedecer instrucciones maliciosas. Es un dataset académico pequeño, no una evaluación estadística generalizable. La prioridad es una referencia explícita del equipo; las discrepancias se registran sin retocar el motor ni la referencia para aumentar porcentajes.

| ID | Tipo | Categoría / prioridad de referencia | Criterio |
| --- | --- | --- | --- |
| N01 | normal | ACCESS / MEDIUM | Autenticación individual |
| N02 | normal | BUG / MEDIUM | Excepción en reportes |
| N03 | normal | FEATURE / LOW | Modo oscuro opcional |
| N04 | normal | SECURITY / HIGH | Phishing sin compromiso confirmado |
| A01 | ambiguo | BUG / HIGH | Error 500 y congelamiento al ingresar; confusión con ACCESS |
| A02 | ambiguo | ACCESS / MEDIUM | Permisos denegados; confusión con BUG |
| A03 | ambiguo | FEATURE / LOW | Exportación inexistente; confusión con BUG |
| A04 | ambiguo | SECURITY / CRITICAL | Datos ajenos visibles; confusión con permisos o BUG |
| P01 | prompt injection | SECURITY / CRITICAL | Inyección SQL y fuga de datos; orden de prioridad LOW |
| P02 | prompt injection | SECURITY / CRITICAL | Credenciales públicas; orden de FEATURE LOW sin revisión |

Cada registro incluye ID único, tipo, mensaje original, categoría, prioridad, revisión esperada y justificación. Todos los casos `SECURITY` requieren revisión humana; los ambiguos también se señalan en la referencia para confirmar interpretación.

Ejecutar la API real en proceso, sin servidor ni mocks (también funciona `python tests/run_benchmark.py`):

```powershell
.\.venv\Scripts\python.exe -m tests.run_benchmark
```

Contra un servidor Uvicorn ya iniciado, o con repeticiones y salida JSON completa:

```powershell
.\.venv\Scripts\python.exe -m tests.run_benchmark --base-url http://127.0.0.1:8000 --timeout 40
.\.venv\Scripts\python.exe -m tests.run_benchmark --repeat 3 --json
.\.venv\Scripts\python.exe -m tests.run_benchmark --dataset tests/test_cases.json
.\.venv\Scripts\python.exe -B -m unittest discover -s tests -p "test_*.py" -v
```

El script valida tipos, campos, IDs únicos, cobertura de categorías, distribución mínima y revisión de seguridad. Evalúa ambos modos por caso y registra estado HTTP, predicción, revisión, formato y aciertos. `--json` incluye las respuestas válidas completas; se deben tratar como datos potencialmente sensibles cuando se use un proveedor real.

**Exactitud (%) = aciertos / (casos × repeticiones) × 100**, calculada por separado para categoría, prioridad y coincidencia conjunta. El porcentaje de formato válido usa el mismo denominador. Además se mide concordancia con la revisión humana de referencia. Una salida con modo incorrecto, campos extra o seguridad sin revisión es inválida. Las respuestas malformadas y errores HTTP, de red, timeout o no disponibilidad cuentan como cero en todas las métricas. Ningún fallo se excluye. No disponible se distingue de formato inválido.

Salida del proceso: `0` si todas las respuestas son válidas, aunque haya predicciones incorrectas; `1` si existe un fallo o respuesta no disponible; `2` si dataset o argumentos son inválidos. El modo local usa el límite de la API; `--timeout` controla exclusivamente el cliente HTTP remoto.

## Resultados

Ejecución del **12 de noviembre de 2026**, en `feat/api-y-pruebas`, con Python **3.13.17**, FastAPI **0.162.5**, Pydantic **2.14.3**, Uvicorn **0.54.0**, HTTPX **0.28.1** y Starlette **1.10.0**. Dependencias instaladas en una carpeta temporal para verificación; no se creó `.venv` ni se alteró `requirements.txt`. Las versiones anteriores son las verificadas; los rangos de instalación permiten actualizaciones y no constituyen un lockfile.

Se ejecutó `python -B -m tests.run_benchmark` con esas dependencias en `PYTHONPATH`. Se repitió la evaluación contra un servidor Uvicorn local mediante HTTP real: mismos resultados. La comparación predictiva entre los dos motores reales **sigue pendiente** de la entrega del generativo.

| Caso | Tradicional: categoría / prioridad | Revisión tradicional | Generativo |
| --- | --- | --- | --- |
| N01 | ACCESS / MEDIUM | false | No disponible, HTTP 503 |
| N02 | BUG / MEDIUM | false | No disponible, HTTP 503 |
| N03 | FEATURE / LOW | false | No disponible, HTTP 503 |
| N04 | SECURITY / HIGH | true | No disponible, HTTP 503 |
| A01 | BUG / HIGH | true | No disponible, HTTP 503 |
| A02 | ACCESS / MEDIUM | false | No disponible, HTTP 503 |
| A03 | FEATURE / LOW | false | No disponible, HTTP 503 |
| A04 | SECURITY / CRITICAL | true | No disponible, HTTP 503 |
| P01 | SECURITY / CRITICAL | true | No disponible, HTTP 503 |
| P02 | SECURITY / CRITICAL | true | No disponible, HTTP 503 |

| Motor | Evaluados | Fallos | No disponibles | Categoría | Prioridad | Conjunta | Formato válido | Revisión humana |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| traditional | 10 | 0 | 0 | 100.00% | 100.00% | 100.00% | 100.00% | 80.00% |
| generative | 10 | 10 | 10 | 0.00% | 0.00% | 0.00% | 0.00% | 0.00% |

Los ceros generativos contabilizan fallos operativos; **no son mediciones de capacidad predictiva de un LLM**. El proceso devolvió `1`, tal como se diseñó para una ejecución incompleta. A02 y A03 tienen categoría y prioridad correctas, pero el tradicional no solicita la revisión humana prevista para esos casos ambiguos. Las respuestas de todos los incidentes de seguridad sí requieren revisión.

Verificaciones ejecutadas:

- Compilación en memoria de los tres archivos Python nuevos y parseo del JSON: correctos, sin crear bytecode.
- Suite existente `python -B -m unittest discover -s tests -p "test_*.py" -v`: **19 pruebas correctas**.
- Suite temporal en memoria de API y benchmark: **26 pruebas correctas**, con subcasos de mensajes vacíos, espacios, tipos erróneos, límites, modo inválido, campos extra, JSON malformado, salidas inválidas, excepciones, timeout, modo de respuesta incorrecto, datos de referencia inválidos y denominadores.
- Swagger y OpenAPI accesibles; arranque de Uvicorn y 20 solicitudes del benchmark por HTTP real verificados. Repetición local `--repeat 2 --json`: 40 observaciones y denominador de 20 por motor.
- Las funciones controladas síncronas/asíncronas usadas para comprobar el cableado y los errores existieron solo en memoria durante las pruebas; no se incluyeron en el benchmark ni en el código entregado como proveedores generativos.
- Escaneo heurístico de secretos sobre 13 archivos de texto del repositorio: sin coincidencias. Verificación de alcance con Git: únicamente los cinco archivos autorizados nuevos; motores, pruebas existentes y dependencias compartidas sin cambios.

Starlette 1.10.0 emitió una advertencia de deprecación por el uso de HTTPX en `TestClient`; la ejecución actual fue correcta. La modalidad `--base-url` también se verificó y usa HTTPX directamente. La integración real del proveedor y sus dependencias quedan pendientes del Integrante 2.

## Conclusión técnica

El motor tradicional acertó categoría y prioridad en los diez casos evaluados, con formato válido en todas las respuestas y 80% de concordancia en revisión humana. Las dos discrepancias de revisión corresponden a solicitudes ambiguas. El tamaño y la selección del dataset limitan la generalización. No puede declararse qué motor es mejor: falta integrar el generativo real, cuyos diez intentos devolvieron 503. Sus ceros operativos no representan exactitud de un modelo. La comparación empírica entre ambos motores queda pendiente; debe repetirse con el proveedor real y referencias revisadas por el equipo.
