"""Wrapper de busqueda sobre el RAG (Proyecto 1). Retrieval-only.

Vive en el repo del AGENTE pero se ejecuta con el .venv del RAG. Importa el
codigo VIVO del RAG (src.query.retrieve); no copia nada y no modifica nada
de ese repo. Nunca llama a generate(): devuelve fragmentos literales.

Dos modos:
- Un disparo:  rag_search.py --rag-root <RAG> --query "..." [--top N]
- Worker:      rag_search.py --rag-root <RAG> --serve
  Arranca UNA vez (indice y cliente cargados una sola vez), imprime
  {"ready": true} y atiende consultas por stdin como lineas JSON
  {"query": "...", "top": N}, una respuesta JSON por linea. Cuando el
  proceso padre muere, stdin se cierra y el worker termina solo.

Contrato: stdout = SOLO lineas JSON. Todo lo demas (banner del reranker,
avisos, errores de arranque) va a stderr.
"""

import argparse
import contextlib
import json
import sys
from pathlib import Path


def _build_payload(retrieval, chunks, top, reranker, query):
    ids = retrieval["top"][:top]
    return {
        "reranker": reranker,
        "mode": retrieval["mode"],
        "query": query,
        "chunks": [
            {
                "doc_id": chunks[i]["doc_id"],
                "company": chunks[i]["company"],
                "fiscal_year": chunks[i]["fiscal_year"],
                "chunk_no": chunks[i]["chunk_no"],
                "text": chunks[i]["text"],
            }
            for i in ids
        ],
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Busqueda retrieval-only sobre el RAG")
    parser.add_argument("--rag-root", required=True, help="raiz del repo del RAG")
    parser.add_argument("--query", default=None, help="consulta (modo un disparo)")
    parser.add_argument("--top", type=int, default=3, help="fragmentos a devolver")
    parser.add_argument(
        "--serve",
        action="store_true",
        help="modo worker persistente: consultas JSON por stdin",
    )
    parser.add_argument(
        "--no-rerank",
        action="store_true",
        help="apaga el reranker de Cohere EN MEMORIA (el repo del RAG no se toca)",
    )
    args = parser.parse_args()
    if not args.serve and not args.query:
        parser.error("se requiere --query (o usa --serve)")

    rag_root = Path(args.rag_root).resolve()
    if not (rag_root / "src" / "query.py").exists():
        sys.exit(f"ERROR: {rag_root} no parece la raiz del RAG (falta src/query.py)")
    sys.path.insert(0, str(rag_root))

    from src import config, query, store  # el codigo vivo del RAG

    if args.no_rerank:
        # Parche EN MEMORIA, nunca silencioso: query.py copio el valor con
        # "from .config import RERANKER_ENABLED", asi que se apaga en ambos
        # modulos. El archivo src/config.py del RAG queda intacto.
        config.RERANKER_ENABLED = False
        query.RERANKER_ENABLED = False
        print(
            ">>> RERANKER APAGADO (parche en memoria; config del RAG intacta) <<<",
            file=sys.stderr,
        )
    else:
        print(">>> reranker: ON (config normal del RAG) <<<", file=sys.stderr)
    reranker = "off" if args.no_rerank else "on"

    # Cualquier print interno del RAG (p.ej. avisos de 429 de Cohere) se
    # desvia a stderr para que stdout quede como JSON puro.
    with contextlib.redirect_stdout(sys.stderr):
        vectors, chunks = store.load()
        client = config.get_openai_client()

    if not args.serve:
        with contextlib.redirect_stdout(sys.stderr):
            retrieval = query.retrieve(args.query, client, vectors, chunks)
        payload = _build_payload(retrieval, chunks, args.top, reranker, args.query)
        print(json.dumps(payload, ensure_ascii=False))
        return

    # Modo worker: una linea JSON entra, una linea JSON sale. Los errores de
    # una consulta NO tumban el worker: viajan como {"error": ...} y la tool
    # del agente los convierte en texto legible para el modelo.
    print(json.dumps({"ready": True}), flush=True)
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            request = json.loads(line)
            with contextlib.redirect_stdout(sys.stderr):
                retrieval = query.retrieve(request["query"], client, vectors, chunks)
            payload = _build_payload(
                retrieval, chunks, int(request.get("top", 3)), reranker,
                request["query"],
            )
        except Exception as exc:  # noqa: BLE001 - el error viaja, el worker vive
            payload = {"error": f"{type(exc).__name__}: {exc}"}
        print(json.dumps(payload, ensure_ascii=False), flush=True)


if __name__ == "__main__":
    main()
