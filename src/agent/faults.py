"""Inyeccion de fallas (fault injection): sabotaje controlado para probar
que el agente aguanta. JAMAS prendida por default; solo se activa con el
flag --fault de la terminal o la variable de entorno AGENT_FAULT.

Formato de configuracion (un solo string):
    "kind[,tool=NOMBRE][,step=N][,mode=once|always]"
Tipos (kind):
    tool_exception  la tool truena con una excepcion
    tool_garbage    la tool devuelve basura ilegible en vez del dato
    api_timeout     la llamada a la API lanza timeout ANTES de tocar la red
                    (no cuesta dinero)
    unknown_tool    se reescribe el nombre de la tool pedida a uno
                    inexistente, simulando que el modelo pidio algo que no hay
"""

from dataclasses import dataclass, field

import httpx
import openai

FAULT_KINDS = {"tool_exception", "tool_garbage", "api_timeout", "unknown_tool"}

# Sin digitos a proposito: el detector de alucinaciones no debe encontrar
# numeros "legitimos" dentro de la basura.
GARBAGE = "###BASURA### respuesta corrupta e ilegible, sin datos utilizables"


@dataclass
class FaultConfig:
    kind: str
    tool: str | None = None  # que tool afecta (solo fallas de tool)
    step: int | None = None  # None = cualquier paso
    mode: str = "always"  # "once" = solo la primera vez


def parse_fault(spec: str) -> FaultConfig:
    """Convierte el string del flag --fault en una FaultConfig, validando."""
    parts = [p.strip() for p in spec.split(",") if p.strip()]
    if not parts or parts[0] not in FAULT_KINDS:
        raise ValueError(
            f"Falla desconocida {spec!r}. Tipos validos: {sorted(FAULT_KINDS)}"
        )
    config = FaultConfig(kind=parts[0])
    for part in parts[1:]:
        key, _, value = part.partition("=")
        if key == "tool":
            config.tool = value
        elif key == "step":
            config.step = int(value)
        elif key == "mode":
            if value not in ("once", "always"):
                raise ValueError(f"mode invalido {value!r}: usa once o always")
            config.mode = value
        else:
            raise ValueError(f"opcion desconocida {key!r} en {spec!r}")
    return config


@dataclass
class FaultInjector:
    """Aplica la falla configurada y registra cada disparo en `fired`."""

    config: FaultConfig
    fired: list = field(default_factory=list)

    def _applies(self, step: int, tool: str | None = None) -> bool:
        c = self.config
        if c.step is not None and step != c.step:
            return False
        if c.tool is not None and tool is not None and tool != c.tool:
            return False
        if c.mode == "once" and self.fired:
            return False
        return True

    def _record(self, step: int, tool: str | None = None) -> None:
        self.fired.append({"kind": self.config.kind, "step": step, "tool": tool})

    def before_api_call(self, step: int) -> None:
        """api_timeout: lanza el timeout antes de tocar la red."""
        if self.config.kind == "api_timeout" and self._applies(step):
            self._record(step)
            raise openai.APITimeoutError(
                request=httpx.Request("POST", "https://api.openai.com/v1/responses")
            )

    def rewrite_tool_name(self, step: int, name: str) -> str:
        """unknown_tool: cambia el nombre pedido por uno inexistente."""
        if self.config.kind == "unknown_tool" and self._applies(step, name):
            self._record(step, name)
            return "tool_fantasma"
        return name

    def before_tool_run(self, step: int, name: str) -> None:
        """tool_exception: la tool truena antes de ejecutarse."""
        if self.config.kind == "tool_exception" and self._applies(step, name):
            self._record(step, name)
            raise RuntimeError("falla inyectada a proposito (fault injection)")

    def corrupt_result(self, step: int, name: str, result: str) -> str:
        """tool_garbage: sustituye el resultado bueno por basura."""
        if self.config.kind == "tool_garbage" and self._applies(step, name):
            self._record(step, name)
            return GARBAGE
        return result
