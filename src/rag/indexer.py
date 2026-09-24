"""Construction et chargement de la base vectorielle Faiss.

L'index choisi est `IndexFlatIP` sur des vecteurs normalisés, ce qui revient à
une similarité cosinus. La recherche est alors *exacte* (aucune approximation)
et reste instantanée à cette échelle : quelques millisecondes pour ~15 000
vecteurs. Des index approchés (IVF, HNSW) ne deviendraient utiles qu'à partir
de centaines de milliers de vecteurs, au prix d'un rappel imparfait.
"""
from __future__ import annotations

import json
import logging
import time
import warnings
from datetime import date, datetime, timezone
from pathlib import Path

import pandas as pd
from langchain_community.vectorstores import FAISS
from langchain_community.vectorstores.utils import DistanceStrategy
from langchain_core.documents import Document
from langchain_core.embeddings import Embeddings

from rag import config
from rag.chunking import events_to_documents
from rag.preprocess import normalize_title_key

logger = logging.getLogger(__name__)

# LangChain avertit que « normalize_L2 » ne s'applique pas à MAX_INNER_PRODUCT.
# L'avertissement est trompeur : le code normalise bien les vecteurs des documents
# ET des requêtes (faiss.normalize_L2 dans __add et similarity_search_with_score).
# C'est exactement ce que l'on veut : produit scalaire sur vecteurs normalisés = cosinus.
warnings.filterwarnings("ignore", message="Normalizing L2 is not applicable")


def build_vectorstore(
    documents: list[Document],
    embeddings: Embeddings,
    batch_size: int = 256,
    progress: bool = True,
) -> FAISS:
    """Vectorise les documents par lots et construit l'index Faiss.

    Le traitement par lots évite de tout envoyer d'un coup à l'API et permet
    d'afficher une progression sur plusieurs milliers de documents.
    """
    if not documents:
        raise ValueError("Aucun document à indexer.")

    store = FAISS.from_documents(
        documents[:batch_size],
        embeddings,
        normalize_L2=True,                                  # -> similarité cosinus
        distance_strategy=DistanceStrategy.MAX_INNER_PRODUCT,
    )
    for start in range(batch_size, len(documents), batch_size):
        store.add_documents(documents[start:start + batch_size])
        if progress:
            done = min(start + batch_size, len(documents))
            logger.info("Vectorisation : %d / %d documents (%.0f %%)",
                        done, len(documents), 100 * done / len(documents))
    return store


def build_index(
    events: pd.DataFrame,
    embeddings: Embeddings,
    embedding_info: dict,
    index_dir: Path | str = config.INDEX_DIR,
    batch_size: int = 256,
) -> tuple[FAISS, dict]:
    """Événements nettoyés -> index Faiss sauvegardé sur disque + fiche descriptive."""
    documents = events_to_documents(events)
    logger.info("%d événements -> %d documents à vectoriser", len(events), len(documents))

    started = time.perf_counter()
    store = build_vectorstore(documents, embeddings, batch_size=batch_size)
    duration = time.perf_counter() - started

    index_dir = Path(index_dir)
    index_dir.mkdir(parents=True, exist_ok=True)
    store.save_local(str(index_dir))

    info = {
        **embedding_info,
        "dimension": store.index.d,
        "type_index": type(store.index).__name__,
        "nombre_evenements": int(len(events)),
        "nombre_documents": int(store.index.ntotal),
        "documents_par_evenement": round(store.index.ntotal / max(len(events), 1), 2),
        "taille_chunk": config.CHUNK_SIZE,
        "recouvrement_chunk": config.CHUNK_OVERLAP,
        "duree_vectorisation_secondes": round(duration, 1),
        "construit_le": datetime.now(timezone.utc).isoformat(timespec="seconds"),
    }
    (index_dir / "index_info.json").write_text(
        json.dumps(info, ensure_ascii=False, indent=2), encoding="utf-8")
    logger.info("Index sauvegardé dans %s (%d vecteurs, dimension %d)",
                index_dir, store.index.ntotal, store.index.d)
    return store, info


def load_index(embeddings: Embeddings, index_dir: Path | str = config.INDEX_DIR) -> FAISS:
    """Recharge un index existant. Lève une erreur explicite s'il n'existe pas."""
    index_dir = Path(index_dir)
    if not (index_dir / "index.faiss").exists():
        raise FileNotFoundError(
            f"Aucun index dans {index_dir}. Lancez d'abord : python scripts/build_index.py")
    return FAISS.load_local(
        str(index_dir),
        embeddings,
        normalize_L2=True,
        distance_strategy=DistanceStrategy.MAX_INNER_PRODUCT,
        allow_dangerous_deserialization=True,  # fichier produit par nous, pas par un tiers
    )


def load_index_info(index_dir: Path | str = config.INDEX_DIR) -> dict:
    path = Path(index_dir) / "index_info.json"
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}


def search(
    store: FAISS,
    query: str,
    k: int = config.TOP_K,
    dedupe: bool = True,
    only_upcoming: bool = False,
    reference_date: date | None = None,
) -> list[tuple[Document, float]]:
    """Recherche sémantique dans l'index.

    - `dedupe` : ne garde qu'un seul résultat par événement, et fusionne aussi les
      quasi-doublons — deux fiches distinctes d'Open Agenda pour le même spectacle,
      au même endroit le même jour, avec un nom de lieu écrit différemment.
    - `only_upcoming` : écarte les événements déjà terminés. Indispensable pour
      une recommandation de sortie : sur 12 730 événements, 2 207 seulement sont
      encore à venir.
    """
    filter_fn = None
    if only_upcoming:
        today = (reference_date or config.reference_date()).isoformat()
        filter_fn = lambda meta: str(meta.get("date_end", ""))[:10] >= today  # noqa: E731

    results = store.similarity_search_with_score(
        query, k=k * 3 if dedupe else k, filter=filter_fn, fetch_k=max(k * 20, 100))
    if not dedupe:
        return results
    best: dict[tuple[str, str], tuple[Document, float]] = {}
    for doc, score in results:
        key = (normalize_title_key(doc.metadata.get("title", "")),
               str(doc.metadata.get("date_begin", ""))[:10])
        if key not in best:
            best[key] = (doc, score)
    return list(best.values())[:k]


def format_results(results: list[tuple[Document, float]]) -> str:
    """Affichage lisible des résultats de recherche, pour les tests manuels."""
    lines = []
    for rank, (doc, score) in enumerate(results, start=1):
        meta = doc.metadata
        lines.append(f"{rank}. [{score:.3f}] {meta['title']} — {meta['city']}, {meta['date_text']}")
    return "\n".join(lines)
