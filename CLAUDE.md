# Ventas e inventario

## Estructura

- `backend/`: aplicacion Python, organizada en `api`, `datos`, `dominio`, `persistencia` y `servicios`.
- `backend/data/`: bases SQLite locales del sistema y de demostracion.
- `frontend/`: interfaz web HTML, JavaScript y CSS.
- `tests/`: pruebas automatizadas y dependencias de desarrollo.
- `docs/`: documentacion y material del proyecto.

## Decisiones que preservar

- Mantener Python estandar, SQLite y la interfaz web existente; no migrar a FastAPI ni React como parte de una reorganizacion.
- Usar imports de paquete desde `backend` y ejecutar los puntos de entrada como modulos, por ejemplo `python -m backend.api.web_server`.
- No sobrescribir ni borrar las bases SQLite existentes al modificar codigo o ejecutar pruebas.
- Ejecutar `python -m pytest -q`, `node --check frontend/src/app.js` y `git diff --check` al validar cambios relacionados.
