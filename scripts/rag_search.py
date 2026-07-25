"""Wrapper de busqueda sobre el RAG (Proyecto 1). Retrieval-only.

Vive en el repo del AGENTE pero se ejecuta con el .venv del RAG:
    <RAG>/.venv/bin/python scripts/rag_search.py --rag-root <RAG> --query "..."

Importa el codigo VIVO del RAG (src.query.retrieve); no copia nada y no
modifica nada de ese repo. Nunca llama a generate(): devuelve fragmentos
literales de los filings, no prosa generada.

Contrato: stdout = SOLO un JSON. Todo lo demas (banners, avisos, errores)
va a stderr. Si algo truena, el proceso sale con codigo != 0 y el error
legible queda en stderr; la tool del agente lo convierte en mensaje para
el modelo.
"""

import argparse
import contextlib
import json
import sys
from pathlib import Path


def main() -> None:
    parser = argparse.ArgumentParser(description="Busqueda retrieval-only sobre el RAG")
    parser.add_argument("--rag-root", required=True, help="raiz del repo del RAG")
    parser.add_argument("--query", required=True, help="consulta en texto libre")
    parser.add_argument("--top", type=int, default=3, help="fragmentos a devolver")
    parser.add_argument(
        "--no-rerank",
        action="store_true",
        help="apaga el reranker de Cohere EN MEMORIA (el repo del RAG no se toca)",
    )
    args = parser.parse_args()

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

    # Cualquier print interno del RAG (p.ej. avisos de 429 de Cohere) se
    # desvia a stderr para que stdout quede como JSON puro.
    with contextlib.redirect_stdout(sys.stderr):
        vectors, chunks = store.load()
        client = config.get_openai_client()
        retrieval = query.retrieve(args.query, client, vectors, chunks)

    top = retrieval["top"][: args.top]
    payload = {
        "reranker": "off" if args.no_rerank else "on",
        "mode": retrieval["mode"],
        "query": args.query,
        "chunks": [
            {
                "doc_id": chunks[i]["doc_id"],
                "company": chunks[i]["company"],
                "fiscal_year": chunks[i]["fiscal_year"],
                "chunk_no": chunks[i]["chunk_no"],
                "text": chunks[i]["text"],
            }
            for i in top
        ],
    }
    print(json.dumps(payload, ensure_ascii=False))


if __name__ == "__main__":
    main()
