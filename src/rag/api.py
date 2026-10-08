"""API REST du chatbot Puls-Events (étape 5).

Trois points d'entrée :
    GET  /health          état du service et fiche de l'index (200, ou 503 sans index)
    POST /ask             une question -> une réponse + ses sources
    POST /rebuild         reconstruit l'index (protégé par un jeton)

Documentation interactive générée automatiquement : http://127.0.0.1:8000/docs

Deux choix de conception à retenir :

1. **Aucune règle métier ici.** Ce fichier ne fait que traduire du HTTP vers
   `RAGService` et retour. Toute la logique du RAG vit dans `rag.chain`, ce qui
   permet de la tester sans serveur et de la réutiliser ailleurs (script,
   notebook, tâche planifiée).

2. **Le service est chargé une seule fois, au démarrage.** Charger l'index à
   chaque requête coûterait plusieurs secondes et autant de mémoire ; ici les
   15 000 vecteurs sont en RAM et une question se traite en quelques
   millisecondes, hors appel au LLM.
"""
from __future__ import annotations

import logging
import secrets
import threading
from contextlib import asynccontextmanager
from datetime import date, datetime, timezone

from fastapi import (BackgroundTasks, Depends, FastAPI, Header, HTTPException, Response,
                     status)
from fastapi.responses import RedirectResponse
from pydantic import BaseModel, Field

from rag import config
from rag.chain import RAGService
from rag.pipeline import refresh_index

logger = logging.getLogger(__name__)

DESCRIPTION = """
Assistant culturel de **Puls-Events** : il répond aux questions sur les
événements du département en s'appuyant uniquement sur les fiches Open Agenda
retrouvées dans une base vectorielle Faiss (architecture RAG).

Si le modèle de rédaction est indisponible (quota d'API atteint, réseau coupé),
`/ask` répond quand même : `llm_disponible` passe à `false` et la réponse
contient la liste brute des événements trouvés. La recherche, elle, ne dépend
d'aucun service extérieur.
"""


# --- Schémas d'entrée et de sortie -------------------------------------------
class Question(BaseModel):
    question: str = Field(
        min_length=3, max_length=500,
        description="La question de l'utilisateur, en français.",
        examples=["Quels concerts de musique classique à venir ?"],
    )
    k: int | None = Field(
        default=None, ge=1, le=20,
        description="Nombre d'événements à retrouver. Par défaut : TOP_K.",
    )
    toutes_dates: bool = Field(
        default=False,
        description="Inclure les événements déjà terminés (utile pour les tests).",
    )
    date_de_reference: date | None = Field(
        default=None,
        description="Remplace la date du jour, pour obtenir un résultat reproductible.",
    )


class Source(BaseModel):
    uid: str | None = None
    titre: str | None = None
    date: str | None = None
    ville: str | None = None
    lieu: str | None = None
    lien: str | None = None
    score: float = Field(description="Similarité cosinus entre 0 et 1.")


class Reponse(BaseModel):
    question: str
    reponse: str
    sources: list[Source]
    llm_disponible: bool = Field(
        description="false = réponse non rédigée par le modèle (mode dégradé)."
    )
    duree_secondes: float


class Etat(BaseModel):
    statut: str
    index_disponible: bool
    modele_llm: str
    top_k: int
    uniquement_a_venir: bool
    reconstruction: dict
    index: dict


class DemandeReconstruction(BaseModel):
    source: str = Field(default="api", pattern="^(api|csv)$")
    departement: str | None = None
    limite: int | None = Field(default=None, ge=1, description="Essai rapide sur N événements.")


class AccuseReconstruction(BaseModel):
    message: str
    reconstruction: dict


# --- État du service ----------------------------------------------------------
service: RAGService | None = None

# Suivi de la reconstruction : elle tourne en tâche de fond et peut durer
# plusieurs minutes. Le client lance /rebuild, puis interroge /health.
rebuild_state: dict = {"en_cours": False, "dernier_resultat": None, "derniere_erreur": None,
                       "demarre_le": None, "termine_le": None}
rebuild_lock = threading.Lock()


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Prépare le service au démarrage et l'oublie à l'arrêt.

    L'index n'est volontairement pas chargé ici : le chargement est paresseux.
    Le démarrage reste donc immédiat (mesuré à 1,6 s, le temps des imports), et
    un conteneur lancé avant que l'index existe répond quand même sur /health —
    avec un 503 et la fiche d'avancement de la reconstruction, pas un plantage.
    """
    global service
    service = RAGService()
    logger.info("Service RAG prêt (index chargé à la première question)")
    yield
    service = None


app = FastAPI(title=config.API_TITLE, version=config.API_VERSION,
              description=DESCRIPTION, lifespan=lifespan)


def get_service() -> RAGService:
    """Dépendance FastAPI : le service RAG partagé.

    Passer par une dépendance plutôt que par une variable globale permet aux
    tests de fournir un service factice (`app.dependency_overrides`), donc de
    tester l'API sans index réel, sans modèle et sans réseau.
    """
    if service is None:                       # ne devrait pas arriver hors tests
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, "Service non initialisé.")
    return service


# --- Points d'entrée ----------------------------------------------------------
@app.get("/", include_in_schema=False)
def racine():
    """Redirige vers la documentation interactive."""
    return RedirectResponse("/docs")


@app.get("/health", response_model=Etat, summary="État du service",
         responses={503: {"model": Etat,
                          "description": "Index indisponible : /ask ne peut pas répondre."}})
def health(response: Response, rag: RAGService = Depends(get_service)):
    """Vérifie que l'index est en place et renvoie sa fiche descriptive.

    Utilisé comme sonde d'aptitude par Docker (étape 6). Le code HTTP reflète la
    capacité réelle à servir une question :

    - **200** l'index est là, `/ask` peut répondre ;
    - **503** l'index manque ; `/ask` renverrait 503 lui aussi.

    Sans ce 503, Docker marquerait « healthy » un conteneur incapable de
    répondre à la moindre question : la sonde doit échouer quand le service ne
    peut pas rendre son service.

    Le corps est renvoyé **dans les deux cas**, et c'est volontaire : quand
    l'index manque, c'est précisément là qu'on a besoin de lire l'avancement de
    la reconstruction (`reconstruction`). Un `HTTPException` aurait remplacé la
    fiche par un simple message d'erreur.

    Cette sonde ne contacte jamais Mistral : elle ne lit qu'un fichier local et
    la configuration. Le modèle de rédaction peut être en panne sans que le
    conteneur soit déclaré malade — c'est le rôle du mode dégradé de `/ask`.
    """
    info = rag.health()
    index_disponible = bool(info.pop("index_disponible", False))
    if not index_disponible:
        response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE
    return Etat(
        statut="ok" if index_disponible else "index absent",
        index_disponible=index_disponible,
        modele_llm=info.pop("modele_llm", ""),
        top_k=info.pop("top_k", config.TOP_K),
        uniquement_a_venir=info.pop("uniquement_a_venir", True),
        reconstruction=dict(rebuild_state),
        index=info,
    )


@app.post("/ask", response_model=Reponse, summary="Poser une question")
def ask(demande: Question, rag: RAGService = Depends(get_service)):
    """Recherche les événements pertinents, puis fait rédiger la réponse.

    Réponses possibles :
    - **200** réponse rédigée, ou réponse dégradée si le LLM est indisponible ;
    - **422** question absente, trop courte ou trop longue (validation Pydantic) ;
    - **503** aucun index : lancez `scripts/build_index.py` ou `POST /rebuild`.
    """
    try:
        result = rag.ask(demande.question, k=demande.k,
                         reference_date=demande.date_de_reference,
                         only_upcoming=not demande.toutes_dates)
    except FileNotFoundError as exc:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, str(exc)) from exc

    return Reponse(
        question=result["question"],
        reponse=result["answer"],
        sources=[Source(**source) for source in result["sources"]],
        llm_disponible=result["llm_disponible"],
        duree_secondes=result["duree_secondes"],
    )


def _run_rebuild(demande: DemandeReconstruction, rag: RAGService) -> None:
    """Exécutée en tâche de fond. Ne lève jamais : l'erreur est stockée."""
    try:
        report = refresh_index(source=demande.source, department=demande.departement,
                               limit=demande.limite)
        rag.reload()
        rebuild_state.update(dernier_resultat=report, derniere_erreur=None)
        logger.info("Reconstruction terminée : %d événements indexés",
                    report["evenements_conserves"])
    except Exception as exc:  # noqa: BLE001
        rebuild_state.update(derniere_erreur=str(exc))
        logger.exception("Reconstruction échouée")
    finally:
        rebuild_state.update(en_cours=False,
                             termine_le=datetime.now(timezone.utc).isoformat(timespec="seconds"))


@app.post("/rebuild", response_model=AccuseReconstruction,
          status_code=status.HTTP_202_ACCEPTED, summary="Reconstruire l'index")
def rebuild(demande: DemandeReconstruction, background: BackgroundTasks,
            x_rebuild_token: str = Header(default="", alias="X-Rebuild-Token"),
            rag: RAGService = Depends(get_service)):
    """Retélécharge les événements, les nettoie et reconstruit l'index Faiss.

    L'opération dure plusieurs minutes : la réponse est immédiate (**202**) et
    l'avancement se suit dans `/health`. L'ancien index continue de répondre
    pendant toute la reconstruction, et reste en place si elle échoue.

    Protégé par l'en-tête `X-Rebuild-Token`, comparé au jeton `REBUILD_TOKEN`
    du fichier `.env`. Sans jeton configuré, l'endpoint est désactivé.
    """
    if not config.REBUILD_TOKEN:
        raise HTTPException(
            status.HTTP_403_FORBIDDEN,
            "Reconstruction désactivée : définissez REBUILD_TOKEN dans .env pour l'activer.")
    # compare_digest : comparaison à durée constante, elle ne révèle pas le
    # nombre de caractères corrects par le temps de réponse.
    if not secrets.compare_digest(x_rebuild_token, config.REBUILD_TOKEN):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Jeton de reconstruction invalide.")

    with rebuild_lock:
        if rebuild_state["en_cours"]:
            raise HTTPException(status.HTTP_409_CONFLICT,
                                "Une reconstruction est déjà en cours.")
        rebuild_state.update(en_cours=True, derniere_erreur=None, termine_le=None,
                             demarre_le=datetime.now(timezone.utc).isoformat(timespec="seconds"))

    background.add_task(_run_rebuild, demande, rag)
    return AccuseReconstruction(
        message="Reconstruction lancée. Suivez l'avancement sur /health.",
        reconstruction=dict(rebuild_state),
    )
