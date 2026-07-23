# CLAUDE.md

Agente multi-step con tool use, retries y recovery. Python 3.12, gestionado con uv.
Codigo en `src/agent/`, tests en `tests/`.

## Reglas del proyecto

### Entorno
- Todo corre en WSL. Nunca PowerShell. Correr `uv` desde Windows destruye el `.venv`.

### Git
- Commits solo a nombre de Iker Pindter. Jamas firmas, atribuciones ni co-autorias de Claude.
- Jamas emojis en los commits.
- Mensajes de commit en imperativo y en ingles.
- Nunca force push.
- Antes de cada push, verificacion de seguridad:
  - `git check-ignore -v .env .venv data` sobre los archivos sensibles.
  - `grep` sobre los archivos trackeados buscando claves.
- No commitear hasta que las pruebas pasen.

### Forma de trabajo
- Plan corto primero. Esperar OK antes de ejecutar.
- Rebanadas: primero que jale feo pero end to end, luego los lujos. Una variable a la vez.
- Cero scope creep. Solo lo que se pidio.

### API y costos
- Modelos baratos por default: `gpt-5.4-nano`.
- Avisar antes de cualquier cosa que gaste mas que centavos de API.

### Exactitud
- Nunca inventar nombres de metodos ni de configuracion.
- Para librerias o APIs, verificar en la documentacion oficial antes de escribir codigo.
