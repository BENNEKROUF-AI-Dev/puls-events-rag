"""Démarre l'API REST en local.

    python scripts/serve.py                  # http://127.0.0.1:8000/docs
    python scripts/serve.py --reload         # redémarrage auto pendant le développement
    python scripts/serve.py --port 9000

Ce script existe pour une raison simple : `uvicorn rag.api:app` ne trouverait
pas le paquet, qui vit dans `src/`. On ajoute donc `src/` au chemin d'import
avant de lancer le serveur.
"""
import argparse
import sys
from pathlib import Path

SRC = Path(__file__).resolve().parents[1] / "src"
sys.path.insert(0, str(SRC))


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--host", default="127.0.0.1",
                        help="127.0.0.1 = accessible depuis cette machine seulement")
    parser.add_argument("--port", type=int, default=8000)
    parser.add_argument("--reload", action="store_true",
                        help="Recharge le code à chaque modification (développement)")
    return parser.parse_args()


def main() -> None:
    import uvicorn

    args = parse_args()
    print(f"Documentation interactive : http://{args.host}:{args.port}/docs")
    uvicorn.run("rag.api:app", host=args.host, port=args.port, reload=args.reload,
                reload_dirs=[str(SRC)] if args.reload else None, log_level="info")


if __name__ == "__main__":
    main()
