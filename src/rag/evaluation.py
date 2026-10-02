"""Calcul des métriques d'évaluation de la recherche.

Ces fonctions sont volontairement pures : elles reçoivent des listes
d'identifiants et renvoient des nombres, sans toucher à l'index ni au réseau.
C'est ce qui permet de les tester avec des cas écrits à la main — et donc de
faire confiance aux chiffres du rapport final.

Trois métriques, et ce qu'elles disent :

- **précision@k** : parmi les k événements proposés, quelle proportion est
  pertinente ? Mesure le bruit envoyé au LLM.
- **rappel@k** : parmi les événements pertinents connus, quelle proportion a
  été retrouvée ? Mesure ce qu'on a manqué. Attention : le rappel est calculé
  sur les événements *annotés*, tous retrouvés parmi les k premiers résultats.
  C'est donc un rappel optimiste — la limite est assumée et documentée.
- **MRR** (rang réciproque moyen) : à quelle position apparaît le premier
  résultat pertinent ? 1,0 = toujours en tête ; 0,5 = en moyenne en deuxième
  position. C'est la métrique la plus proche du ressenti d'un utilisateur, qui
  lit les premières propositions et rarement les suivantes.
"""
from __future__ import annotations

import json
import statistics
from pathlib import Path

from langchain_core.embeddings import Embeddings


class NamedEmbeddings(Embeddings):
    """Adaptateur qui expose le NOM du modèle d'embeddings sous forme de chaîne.

    Ragas journalise l'usage des embeddings et lit l'attribut `model` en
    attendant du texte. `FastEmbedEmbeddings` y range l'objet du modèle chargé,
    et la validation échoue — rendant la métrique `answer_relevancy`
    inexploitable. Désactiver la télémétrie de Ragas ne suffit pas : l'objet de
    suivi est construit, et donc validé, avant même qu'on vérifie s'il faut
    l'envoyer.

    Cet adaptateur ne fait que déléguer les appels, en exposant un nom lisible.
    """

    def __init__(self, embeddings: Embeddings, model: str = "inconnu"):
        self.embeddings = embeddings
        self.model = model

    def embed_query(self, text: str) -> list[float]:
        return self.embeddings.embed_query(text)

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        return self.embeddings.embed_documents(texts)


# --- Fichiers -----------------------------------------------------------------
def load_questions(path: Path | str) -> list[dict]:
    return json.loads(Path(path).read_text(encoding="utf-8"))["questions"]


def load_annotations(path: Path | str) -> dict:
    path = Path(path)
    if not path.exists():
        return {"index": {}, "annote_le": None, "questions": {}}
    return json.loads(path.read_text(encoding="utf-8"))


def save_json(path: Path | str, data: dict) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


# --- Aide à l'annotation ------------------------------------------------------
def suggest_relevant(question: dict, sources: list[dict]) -> set[str]:
    """Pré-sélection automatique des résultats probablement pertinents.

    Simple recherche de mots-clés dans le titre et le lieu : c'est une aide à
    la saisie, pas une vérité terrain. La décision reste humaine, sinon on
    évaluerait la recherche sémantique avec... une recherche par mots-clés.
    """
    attendu = question.get("attendu", {})
    mots = [mot.casefold() for mot in attendu.get("mots_cles", [])]
    ville = (attendu.get("ville") or "").casefold()

    retenus = set()
    for source in sources:
        texte = f"{source.get('titre', '')} {source.get('lieu', '')}".casefold()
        if ville and ville in (source.get("ville") or "").casefold():
            retenus.add(source["uid"])
        elif mots and any(mot in texte for mot in mots):
            retenus.add(source["uid"])
    return retenus


# --- Métriques ----------------------------------------------------------------
def _moyenne(valeurs: list[float]) -> float:
    return round(statistics.fmean(valeurs), 3) if valeurs else 0.0


def retrieval_metrics(questions: list[dict], annotations: dict,
                      retrouves_par_question: dict[str, list[str]], k: int) -> dict:
    """Note la recherche question par question, puis en moyenne.

    `retrouves_par_question` : identifiants d'événements renvoyés par la
    recherche, dans l'ordre des scores. `annotations` : la vérité terrain.

    Les questions hors périmètre sont comptées à part. Elles n'ont pas de bonne
    réponse à trouver : bien y répondre, c'est ne rien proposer. Les mêler aux
    autres gonflerait artificiellement les moyennes.
    """
    annotees = annotations.get("questions", {})
    lignes, precisions, rappels, rangs_inverses = [], [], [], []
    hors_ok = hors_total = 0

    for question in questions:
        qid = question["id"]
        if qid not in annotees:
            continue
        pertinents = set(annotees[qid].get("pertinents", []))
        retrouves = retrouves_par_question.get(qid, [])
        touches = [uid in pertinents for uid in retrouves]

        if question.get("hors_perimetre"):
            hors_total += 1
            hors_ok += int(not pertinents)
            continue

        precision = sum(touches) / len(retrouves) if retrouves else 0.0
        rappel = sum(touches) / len(pertinents) if pertinents else 0.0
        premier = next((rang for rang, ok in enumerate(touches, start=1) if ok), None)

        precisions.append(precision)
        rappels.append(rappel)
        rangs_inverses.append(1 / premier if premier else 0.0)
        lignes.append({"id": qid, "question": question.get("question", ""),
                       "precision": round(precision, 3), "rappel": round(rappel, 3),
                       "premier_pertinent": premier})

    return {
        "k": k,
        "questions_notees": len(precisions),
        f"precision@{k}": _moyenne(precisions),
        f"rappel@{k}": _moyenne(rappels),
        "mrr": _moyenne(rangs_inverses),
        "taux_de_reussite": _moyenne([1.0 if rang else 0.0 for rang in rangs_inverses]),
        "hors_perimetre_correctement_vides": f"{hors_ok}/{hors_total}",
        "detail": lignes,
    }
