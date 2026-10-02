"""Le chatbot : recherche dans Faiss puis rédaction par le LLM Mistral.

Toute la logique métier est ici, dans une classe réutilisable. L'API de
l'étape 5 ne fera que l'importer : aucune règle métier ne doit vivre dans le
code web.
"""
from __future__ import annotations

import logging
import threading
import time
from datetime import date
from pathlib import Path

from langchain_core.documents import Document
from langchain_core.language_models import BaseChatModel
from langchain_core.prompts import ChatPromptTemplate

from rag import config
from rag.embeddings import describe, get_embeddings
from rag.indexer import load_index, load_index_info, search

logger = logging.getLogger(__name__)

SYSTEM_PROMPT = """Tu es l'assistant culturel de Puls-Events. Tu recommandes des événements \
culturels à partir d'une liste de fiches qui te sont fournies.

Règles impératives :
1. Réponds UNIQUEMENT à partir des fiches fournies. N'invente jamais un événement, \
une date, un lieu ou un prix.
2. Si aucune fiche ne correspond à la demande, dis-le simplement et propose, \
si c'est pertinent, ce qui s'en rapproche le plus.
3. Présente chaque événement sous cette forme exacte, sur une seule ligne :
   **Titre** — Lieu, Ville — Date
   Les quatre informations sont obligatoires. Une recommandation de sortie sans lieu \
ne sert à rien. Puis une à deux phrases de description en dessous.
4. RECOPIE les dates, les titres et les lieux EXACTEMENT comme ils figurent dans les fiches. \
Ne reformule pas une date, ne la convertis pas, ne la déduis pas : copie-la mot pour mot.
5. Nous sommes le {today}. Signale si un événement est déjà passé ou se termine bientôt.
6. Réponds en français, sur un ton chaleureux et concis : trois événements au maximum.
7. Commence DIRECTEMENT par les recommandations. N'ouvre jamais par une phrase sur ce qui \
manque, sur ce qui n'est pas disponible, ou sur ce qui ne correspond « pas strictement » \
à la demande. Si les fiches contiennent des événements proches, présente-les comme des \
recommandations, pas comme des pis-aller.
8. Termine par la liste des liens des événements cités."""

USER_PROMPT = """Question de l'utilisateur :
{question}

Fiches d'événements disponibles :
{context}"""

NO_RESULT_MESSAGE = (
    "Je n'ai trouvé aucun événement correspondant à votre demande dans notre base. "
    "Essayez avec d'autres mots-clés, une autre période ou une autre ville."
)

LLM_UNAVAILABLE_MESSAGE = (
    "Le modèle de rédaction est momentanément indisponible ({raison}). "
    "Voici tout de même les événements trouvés pour votre demande :"
)


def describe_llm_error(error: Exception) -> str:
    """Traduit une erreur d'appel au LLM en une raison lisible par un utilisateur."""
    text = str(error)
    if "MISTRAL_API_KEY" in text:
        return "clé d'API non configurée"
    # Attention : Mistral renvoie 429 pour DEUX causes très différentes.
    # « backend_out_of_capacity » (code 3505) = leurs serveurs sont saturés, rien à
    # voir avec le compte ni avec le quota ; la même requête passera quelques
    # secondes plus tard. Les confondre enverrait l'utilisateur chercher un
    # problème de quota inexistant. Ce cas se teste donc AVANT le 429 générique.
    if "capacity" in text.lower():
        return "capacité insuffisante côté Mistral, à réessayer"
    if "429" in text or "rate limit" in text.lower():
        return "limite de débit de l'API atteinte"
    if "401" in text or "unauthorized" in text.lower():
        return "clé d'API refusée"
    if "timeout" in text.lower() or "connect" in text.lower():
        return "service injoignable"
    return "erreur inattendue du service"


def format_events_fallback(results: list[tuple[Document, float]]) -> str:
    """Liste lisible des événements trouvés, quand le LLM ne peut pas rédiger."""
    lines = []
    for doc, _ in results:
        meta = doc.metadata
        lieu = ", ".join(part for part in [meta.get("location_name"), meta.get("city")] if part)
        lines.append(f"- {meta.get('title')} — {lieu}, {meta.get('date_text')}\n  {meta.get('url')}")
    return "\n".join(lines)


def format_context(documents: list[Document]) -> str:
    """Met les fiches retrouvées en forme pour le prompt, numérotées et lisibles."""
    blocks = []
    for number, doc in enumerate(documents, start=1):
        blocks.append(f"--- Fiche {number} ---\n{doc.page_content}\nLien : {doc.metadata.get('url', '')}")
    return "\n\n".join(blocks)


def build_prompt() -> ChatPromptTemplate:
    return ChatPromptTemplate.from_messages([("system", SYSTEM_PROMPT), ("human", USER_PROMPT)])


def get_llm(model: str | None = None, temperature: float = 0.0) -> BaseChatModel:
    """Le modèle de génération : Mistral, via LangChain.

    Température à 0 : la tâche est de restituer fidèlement des fiches, pas d'inventer.
    Toute créativité se paie ici en erreurs de dates.
    """
    from langchain_mistralai import ChatMistralAI

    if not config.mistral_api_key():
        raise RuntimeError("MISTRAL_API_KEY absente : renseignez-la dans .env")
    return ChatMistralAI(model=model or config.LLM_MODEL, temperature=temperature, max_retries=3)


class RAGService:
    """Système RAG complet : index + recherche + génération.

    L'index et le modèle sont chargés une seule fois, au premier appel, puis
    réutilisés : l'API ne doit pas tout recharger à chaque question.
    """

    def __init__(
        self,
        provider: str | None = None,
        embedding_model: str | None = None,
        llm: BaseChatModel | None = None,
        index_dir: Path | str | None = None,
        top_k: int | None = None,
        only_upcoming: bool = True,
    ):
        self.embedding_info = describe(provider, embedding_model)
        self.index_dir = Path(index_dir or config.INDEX_DIR)
        self.top_k = top_k or config.TOP_K
        self.only_upcoming = only_upcoming
        self._llm = llm
        self._store = None
        self._store_lock = threading.Lock()
        self._prompt = build_prompt()

    # --- Chargement paresseux --------------------------------------------------
    @property
    def store(self):
        # Verrou : sous l'API, plusieurs requêtes peuvent arriver pendant le
        # chargement de l'index. Sans lui, chacune chargerait sa propre copie
        # des 15 000 vecteurs en mémoire.
        if self._store is None:
            with self._store_lock:
                if self._store is None:
                    embeddings = get_embeddings(self.embedding_info["provider"],
                                                self.embedding_info["model"])
                    self._store = load_index(embeddings, self.index_dir)
                    logger.info("Index chargé : %d vecteurs", self._store.index.ntotal)
        return self._store

    def reload(self) -> None:
        """Oublie l'index en mémoire : il sera rechargé à la question suivante.

        Appelée après une reconstruction, pour que le service serve le nouvel
        index sans redémarrage.
        """
        with self._store_lock:
            self._store = None
        logger.info("Index déchargé : il sera rechargé à la prochaine question")

    @property
    def llm(self) -> BaseChatModel:
        if self._llm is None:
            self._llm = get_llm()
        return self._llm

    # --- Utilisation -----------------------------------------------------------
    def retrieve(self, question: str, k: int | None = None,
                 reference_date: date | None = None,
                 only_upcoming: bool | None = None) -> list[tuple[Document, float]]:
        """Recherche seule, sans appel au LLM.

        `only_upcoming` est un paramètre d'appel et non un attribut modifié au
        passage : sous l'API, deux requêtes simultanées se marcheraient dessus.
        """
        return search(self.store, question, k=k or self.top_k,
                      only_upcoming=self.only_upcoming if only_upcoming is None else only_upcoming,
                      reference_date=reference_date)

    def ask(self, question: str, k: int | None = None, reference_date: date | None = None,
            only_upcoming: bool | None = None) -> dict:
        """Question -> réponse augmentée + sources citées."""
        question = (question or "").strip()
        if not question:
            raise ValueError("La question est vide.")

        started = time.perf_counter()
        results = self.retrieve(question, k=k, reference_date=reference_date,
                                only_upcoming=only_upcoming)

        if not results:
            return {"question": question, "answer": NO_RESULT_MESSAGE, "sources": [],
                    "llm_disponible": True,
                    "duree_secondes": round(time.perf_counter() - started, 2)}

        documents = [doc for doc, _ in results]
        today = (reference_date or config.reference_date()).strftime("%d/%m/%Y")
        messages = self._prompt.format_messages(
            today=today, question=question, context=format_context(documents))

        # Le mode dégradé est volontaire : si Mistral est indisponible (quota, réseau),
        # la recherche a déjà fait son travail et l'utilisateur reçoit les événements
        # trouvés plutôt qu'une erreur technique.
        llm_disponible = True
        try:
            answer = self.llm.invoke(messages).content
        except Exception as exc:  # noqa: BLE001
            logger.warning("Appel au LLM impossible : %s", exc)
            llm_disponible = False
            answer = (LLM_UNAVAILABLE_MESSAGE.format(raison=describe_llm_error(exc))
                      + "\n\n" + format_events_fallback(results))

        return {
            "question": question,
            "answer": answer,
            "llm_disponible": llm_disponible,
            "sources": [
                {
                    "uid": doc.metadata.get("uid"),
                    "titre": doc.metadata.get("title"),
                    "date": doc.metadata.get("date_text"),
                    "ville": doc.metadata.get("city"),
                    "lieu": doc.metadata.get("location_name"),
                    "lien": doc.metadata.get("url"),
                    "score": round(float(score), 3),
                }
                for doc, score in results
            ],
            "duree_secondes": round(time.perf_counter() - started, 2),
        }

    def health(self) -> dict:
        """État du service : utilisé par l'endpoint /health de l'API."""
        info = load_index_info(self.index_dir)
        return {
            "index_disponible": (self.index_dir / "index.faiss").exists(),
            "modele_llm": config.LLM_MODEL,
            "top_k": self.top_k,
            "uniquement_a_venir": self.only_upcoming,
            **info,
        }
