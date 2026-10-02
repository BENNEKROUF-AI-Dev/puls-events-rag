"""Évalue le système RAG sur le jeu de questions de `eval/questions.json`.

Trois usages, dans cet ordre :

    python scripts/evaluate.py --annotate     # 1. annoter à la main ce qui est pertinent
    python scripts/evaluate.py                # 2. noter la RECHERCHE (aucun LLM requis)
    python scripts/evaluate.py --ragas        # 3. noter la GÉNÉRATION (Ragas + Mistral)

Pourquoi deux évaluations séparées ? Parce qu'un RAG peut échouer à deux
endroits très différents, et qu'un score global les mélangerait :

- la **recherche** peut rater l'événement pertinent. C'est mesurable sans aucun
  modèle, à partir d'annotations humaines : précision@k, rappel@k, MRR. C'est
  la partie que l'on peut noter même quand l'API Mistral est indisponible.
- la **génération** peut trahir les fiches fournies (date inventée, événement
  ajouté). C'est ce que mesure Ragas, en faisant juger la réponse par un LLM.

Les annotations vivent dans `eval/annotations.json` : les identifiants des
événements jugés pertinents par un humain, question par question. C'est la
vérité terrain, et elle ne vaut que pour l'index sur lequel elle a été faite —
`--annotate` enregistre donc la fiche de cet index avec elle.

Le calcul des métriques est dans `rag.evaluation`, où il est testé.
"""
from __future__ import annotations

import argparse
import logging
import sys
import warnings
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from rag import config  # noqa: E402
from rag.chain import RAGService  # noqa: E402
from rag.evaluation import (NamedEmbeddings, load_annotations, load_questions,  # noqa: E402
                            retrieval_metrics, save_json, suggest_relevant)
from rag.indexer import load_index_info  # noqa: E402

QUESTIONS_PATH = ROOT / "eval" / "questions.json"
ANNOTATIONS_PATH = ROOT / "eval" / "annotations.json"
RESULTS_PATH = ROOT / "eval" / "resultats.json"


def sources_of(service: RAGService, question: str, k: int) -> list[dict]:
    """Résultats de recherche réduits aux champs utiles à l'évaluation."""
    return [
        {"uid": doc.metadata.get("uid"), "titre": doc.metadata.get("title"),
         "ville": doc.metadata.get("city"), "lieu": doc.metadata.get("location_name"),
         "date": doc.metadata.get("date_text"), "score": round(float(score), 3)}
        for doc, score in service.retrieve(question, k=k)
    ]


# --- 1. Annotation ------------------------------------------------------------
def annotate(service: RAGService, questions: list[dict], k: int) -> None:
    annotations = load_annotations(ANNOTATIONS_PATH)
    deja = annotations.get("questions", {})

    def enregistrer() -> None:
        """Écrit le fichier d'annotations.

        Appelée après CHAQUE question, et pas seulement à la fin : annoter est un
        travail manuel long, et une interruption ne doit jamais le faire perdre.
        """
        save_json(ANNOTATIONS_PATH, {
            "index": load_index_info(),
            "annote_le": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "k": k,
            "questions": deja,
        })

    print(f"Annotation de {len(questions)} questions. Pour chacune, indiquez les numéros "
          "des événements PERTINENTS.")
    print("  ex. « 1 3 4 »  |  « 0 » = aucun  |  Entrée = garder la proposition (*)  |  "
          "« s » = passer  |  « q » = arrêter")
    print("  Chaque réponse est enregistrée aussitôt : vous pouvez interrompre et "
          "reprendre quand vous voulez.\n")

    for question in questions:
        qid = question["id"]
        print("=" * 78)
        print(f"{qid} [{question['categorie']}] {question['question']}")
        if question.get("hors_perimetre"):
            print("  (hors périmètre : la bonne réponse est « 0 », aucun événement pertinent)")

        sources = sources_of(service, question["question"], k)
        if not sources:
            print("  aucun résultat retourné par la recherche")
            deja[qid] = {"question": question["question"], "pertinents": [],
                         "proposes": [], "aucun_resultat": True}
            enregistrer()
            continue

        proposition = suggest_relevant(question, sources)
        for rang, source in enumerate(sources, start=1):
            marque = "*" if source["uid"] in proposition else " "
            print(f" {marque}{rang}. [{source['score']}] {source['titre']} "
                  f"— {source['ville']}, {source['date']}")
        if qid in deja:
            print(f"  (déjà annoté : {len(deja[qid]['pertinents'])} pertinent(s) ; "
                  "une nouvelle saisie remplace l'ancienne)")

        try:
            saisie = input("  pertinents > ").strip().lower()
        except (EOFError, KeyboardInterrupt):
            # Ctrl+C ou Ctrl+Z : on sort proprement. Le travail déjà saisi est
            # sur le disque, question par question.
            print("\n  Interruption. Les annotations déjà saisies sont conservées.")
            saisie = "q"
        if saisie == "q":
            break
        if saisie == "s":
            continue
        if saisie == "":
            pertinents = [s["uid"] for s in sources if s["uid"] in proposition]
        elif saisie == "0":
            pertinents = []
        else:
            rangs = {int(part) for part in saisie.split() if part.isdigit()}
            pertinents = [s["uid"] for rang, s in enumerate(sources, start=1) if rang in rangs]

        deja[qid] = {
            "question": question["question"],
            "pertinents": pertinents,
            "proposes": [s["uid"] for s in sources],
            "titres_pertinents": [s["titre"] for s in sources if s["uid"] in pertinents],
        }
        enregistrer()
        print(f"  -> {len(pertinents)} pertinent(s) sur {len(sources)}, enregistré")

    enregistrer()
    print(f"\nAnnotations enregistrées : eval/annotations.json ({len(deja)} questions sur "
          f"{len(questions)})")


# --- 2. Recherche -------------------------------------------------------------
def evaluate_retrieval(service: RAGService, questions: list[dict], k: int) -> dict:
    annotations = load_annotations(ANNOTATIONS_PATH)
    if not annotations.get("questions"):
        raise SystemExit("Aucune annotation. Lancez d'abord : python scripts/evaluate.py --annotate")

    retrouves = {
        question["id"]: [s["uid"] for s in sources_of(service, question["question"], k)]
        for question in questions if question["id"] in annotations["questions"]
    }
    return retrieval_metrics(questions, annotations, retrouves, k)


# --- 3. Génération (Ragas) ----------------------------------------------------
def evaluate_generation(service: RAGService, questions: list[dict], k: int,
                        limit: int | None) -> dict:
    """Faithfulness, answer relevancy, context precision : jugés par le LLM.

    On garde volontairement l'API historique de Ragas (`ragas.metrics` +
    `LangchainLLMWrapper`) : la nouvelle passe par `instructor`, dont la version
    est déjà contrainte sur ce projet (voir requirements.txt).
    """
    warnings.filterwarnings("ignore", category=DeprecationWarning)
    from ragas import EvaluationDataset, SingleTurnSample, evaluate
    from ragas.embeddings import LangchainEmbeddingsWrapper
    from ragas.llms import LangchainLLMWrapper
    from ragas.metrics import (Faithfulness, LLMContextPrecisionWithoutReference,
                               ResponseRelevancy)

    from rag.chain import format_context, get_llm
    from rag.embeddings import describe, get_embeddings

    a_juger = [q for q in questions if not q.get("hors_perimetre")][:limit]
    echantillons, degrades = [], []

    print(f"Génération des réponses pour {len(a_juger)} questions...")
    for question in a_juger:
        result = service.ask(question["question"], k=k)
        if not result["llm_disponible"]:
            degrades.append(question["id"])
            continue
        contextes = [format_context([doc])
                     for doc, _ in service.retrieve(question["question"], k=k)]
        echantillons.append(SingleTurnSample(user_input=question["question"],
                                             retrieved_contexts=contextes,
                                             response=result["answer"]))
        print(f"  {question['id']} ok")

    if degrades:
        raise SystemExit(
            f"Pas de réponse pour {len(degrades)} question(s) ({', '.join(degrades)}) : "
            "le LLM est indisponible. Ragas ne peut pas juger des réponses inexistantes ; "
            "réessayez quand l'API Mistral répondra.")

    print("Jugement par Ragas (un appel au LLM par métrique et par question)...")
    scores = evaluate(
        dataset=EvaluationDataset(samples=echantillons),
        metrics=[Faithfulness(),
                 # strictness=1 au lieu de 3 par défaut. Cette métrique fait
                 # régénérer plusieurs questions à partir de la réponse, puis
                 # LangChain fusionne les générations — et additionne au passage
                 # les compteurs de jetons renvoyés par Mistral. L'API renvoie
                 # désormais un dictionnaire imbriqué là où LangChain attend un
                 # nombre : la fusion échoue sur « dict += dict » et la métrique
                 # vaut NaN. Avec une seule génération, il n'y a rien à fusionner.
                 # Compromis assumé : l'estimation est plus bruitée qu'avec trois
                 # questions générées. Incompatibilité de bibliothèques, pas du RAG.
                 ResponseRelevancy(strictness=1),
                 LLMContextPrecisionWithoutReference()],
        llm=LangchainLLMWrapper(get_llm()),
        embeddings=LangchainEmbeddingsWrapper(
            NamedEmbeddings(get_embeddings(), describe()["model"])),
    )
    return {"questions_jugees": len(echantillons), "scores": dict(scores._repr_dict)}


# --- Affichage ----------------------------------------------------------------
def print_retrieval(report: dict) -> None:
    print("\n===== Qualité de la RECHERCHE =====")
    print(f"  questions notées            : {report['questions_notees']}")
    for cle in (f"precision@{report['k']}", f"rappel@{report['k']}", "mrr", "taux_de_reussite"):
        print(f"  {cle:<28}: {report[cle]}")
    print(f"  hors périmètre bien vides   : {report['hors_perimetre_correctement_vides']}")

    manques = [ligne for ligne in report["detail"] if ligne["premier_pertinent"] is None]
    if manques:
        print("\n  Questions sans aucun résultat pertinent (à regarder en priorité) :")
        for ligne in manques:
            print(f"    - {ligne['id']} « {ligne['question']} »")


def main() -> None:
    logging.basicConfig(level=logging.WARNING, format="%(levelname)s | %(message)s")
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--annotate", action="store_true", help="Annoter à la main la pertinence")
    parser.add_argument("--ragas", action="store_true", help="Évaluer aussi la génération")
    parser.add_argument("--k", type=int, default=config.TOP_K)
    parser.add_argument("--limit", type=int, default=None,
                        help="Avec --ragas : ne juger que les N premières questions")
    args = parser.parse_args()

    questions = load_questions(QUESTIONS_PATH)
    service = RAGService(top_k=args.k)

    if args.annotate:
        annotate(service, questions, args.k)
        return

    rapport = {"date": datetime.now(timezone.utc).isoformat(timespec="seconds"),
               "index": load_index_info(),
               "recherche": evaluate_retrieval(service, questions, args.k)}
    print_retrieval(rapport["recherche"])

    if args.ragas:
        rapport["generation"] = evaluate_generation(service, questions, args.k, args.limit)
        print("\n===== Qualité de la GÉNÉRATION (Ragas) =====")
        for nom, valeur in rapport["generation"]["scores"].items():
            print(f"  {nom:<28}: {valeur:.3f}")

    save_json(RESULTS_PATH, rapport)
    print("\nRapport complet : eval/resultats.json")


if __name__ == "__main__":
    main()
