"""Construit la base vectorielle Faiss à partir des événements nettoyés.

Exemples :
    python scripts/build_index.py                      # embeddings locaux (défaut)
    python scripts/build_index.py --provider mistral   # via l'API Mistral
    python scripts/build_index.py --limit 200          # essai rapide sur 200 événements

Prérequis : avoir lancé scripts/fetch_data.py.
Sorties   : data/index/ (index.faiss, index.pkl, index_info.json)
"""
import argparse
import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from rag import config  # noqa: E402
from rag.embeddings import PROVIDERS, describe, get_embeddings  # noqa: E402
from rag.indexer import build_index, format_results, search  # noqa: E402
from rag.preprocess import load_events  # noqa: E402

# Questions jouées automatiquement en fin de construction : un index qui répond
# n'importe quoi à ces trois questions a un problème.
CONTROL_QUESTIONS = [
    "un concert de musique classique",
    "une exposition à voir en famille",
    "atelier pour apprendre à cuisiner",
]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--provider", choices=PROVIDERS, default=config.EMBEDDING_PROVIDER)
    parser.add_argument("--model", default=None, help="Nom du modèle d'embeddings (facultatif)")
    parser.add_argument("--index-dir", type=Path, default=config.INDEX_DIR)
    parser.add_argument("--limit", type=int, default=None, help="N'indexer que les N premiers événements")
    parser.add_argument("--batch-size", type=int, default=256)
    return parser.parse_args()


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s | %(message)s")
    args = parse_args()

    events = load_events(config.PROCESSED_EVENTS_PATH)
    if args.limit:
        events = events.head(args.limit)
    print(f"{len(events)} événements à indexer")

    embedding_info = describe(args.provider, args.model)
    embeddings = get_embeddings(args.provider, args.model)
    store, info = build_index(events, embeddings, embedding_info,
                              index_dir=args.index_dir, batch_size=args.batch_size)

    print("\n--- Index construit ---")
    for key, value in info.items():
        print(f"  {key.replace('_', ' ')} : {value}")

    print("\n--- Questions de contrôle ---")
    for question in CONTROL_QUESTIONS:
        print(f"\n> {question}")
        print(format_results(search(store, question, k=3)))

    print(f"\nIndex enregistré dans {args.index_dir}")


if __name__ == "__main__":
    main()
