"""Pose une question au chatbot depuis le terminal (test manuel du RAG).

Exemples :
    python scripts/ask.py "quels concerts de jazz à Nantes ?"
    python scripts/ask.py --retrieval-only "expositions pour enfants"
    python scripts/ask.py            # mode interactif
"""
import argparse
import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from rag import config  # noqa: E402
from rag.chain import RAGService  # noqa: E402
from rag.indexer import format_results  # noqa: E402


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("question", nargs="*", help="La question à poser")
    parser.add_argument("--k", type=int, default=config.TOP_K)
    parser.add_argument("--retrieval-only", action="store_true",
                        help="Affiche seulement les événements retrouvés, sans appeler le LLM")
    parser.add_argument("--all-dates", action="store_true",
                        help="Ne pas limiter aux événements à venir")
    return parser.parse_args()


def answer(service: RAGService, question: str, args: argparse.Namespace) -> None:
    if args.retrieval_only:
        print(format_results(service.retrieve(question, k=args.k)))
        return
    result = service.ask(question, k=args.k)
    print(f"\n{result['answer']}\n")
    print(f"-- {len(result['sources'])} source(s), {result['duree_secondes']} s --")
    for source in result["sources"]:
        print(f"   [{source['score']}] {source['titre']} — {source['ville']}, {source['date']}")


def main() -> None:
    logging.basicConfig(level=logging.WARNING, format="%(levelname)s | %(message)s")
    args = parse_args()
    service = RAGService(only_upcoming=not args.all_dates, top_k=args.k)

    if args.question:
        answer(service, " ".join(args.question), args)
        return

    print("Posez vos questions (Ctrl+C pour quitter).")
    while True:
        try:
            question = input("\n> ").strip()
        except (KeyboardInterrupt, EOFError):
            print("\nAu revoir.")
            return
        if question:
            answer(service, question, args)


if __name__ == "__main__":
    main()
