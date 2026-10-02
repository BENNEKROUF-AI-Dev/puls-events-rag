"""Chaîne complète de rafraîchissement : téléchargement -> nettoyage -> index.

Ce module existe pour que l'API (étape 5) puisse reconstruire l'index sans
dupliquer la logique des scripts. Les scripts restent le chemin manuel, avec
leurs rapports détaillés ; l'API appelle `refresh_index()`.

Point important : l'index n'est **pas** écrasé sur place. Il est construit dans
un dossier voisin, puis mis en place par un simple renommage. Un rafraîchissement
qui échoue en cours de route laisse donc l'ancien index intact, et aucune requête
ne peut tomber sur un index à moitié écrit.
"""
from __future__ import annotations

import logging
import shutil
import time
from datetime import date
from pathlib import Path

from rag import config
from rag.embeddings import describe, get_embeddings
from rag.fetch_events import fetch_from_api, load_from_csv, records_to_dataframe
from rag.indexer import build_index
from rag.preprocess import clean_events, save_events

logger = logging.getLogger(__name__)


def load_raw(source: str = "api", department: str | None = None,
             ref_date: date | None = None, csv_path: Path | str | None = None):
    """Récupère les événements bruts, depuis l'API ou depuis l'export CSV."""
    department = department or config.DEPARTMENT
    if source == "api":
        return records_to_dataframe(fetch_from_api(department=department, ref_date=ref_date))
    if source == "csv":
        return load_from_csv(csv_path or config.DEFAULT_CSV_PATH, department=department)
    raise ValueError(f"Source inconnue : {source!r}. Valeurs possibles : 'api', 'csv'.")


def swap_index(built_dir: Path, target_dir: Path) -> None:
    """Met en place l'index fraîchement construit à la place de l'ancien.

    Deux renommages plutôt qu'une copie : l'opération dure quelques
    millisecondes, même pour un index de plusieurs dizaines de mégaoctets.
    """
    target_dir = Path(target_dir)
    previous = target_dir.with_name(target_dir.name + ".old")
    shutil.rmtree(previous, ignore_errors=True)

    if target_dir.exists():
        target_dir.rename(previous)
    try:
        built_dir.rename(target_dir)
    except OSError:                      # la mise en place a échoué : on remet l'ancien
        if previous.exists() and not target_dir.exists():
            previous.rename(target_dir)
        raise
    shutil.rmtree(previous, ignore_errors=True)


def refresh_index(
    source: str = "api",
    department: str | None = None,
    reference_date: date | None = None,
    provider: str | None = None,
    embedding_model: str | None = None,
    index_dir: Path | str | None = None,
    limit: int | None = None,
    csv_path: Path | str | None = None,
    processed_path: Path | str | None = None,
) -> dict:
    """Rafraîchit tout : données brutes, nettoyage, index. Renvoie un rapport.

    `limit` n'existe que pour les essais rapides : il tronque la liste des
    événements avant vectorisation.
    """
    started = time.perf_counter()
    department = department or config.DEPARTMENT
    ref_date = reference_date or config.reference_date()
    index_dir = Path(index_dir or config.INDEX_DIR)

    logger.info("Rafraîchissement : source=%s département=%s", source, department)
    raw = load_raw(source, department=department, ref_date=ref_date, csv_path=csv_path)

    events, cleaning_report = clean_events(raw, department=department, ref_date=ref_date)
    if events.empty:
        raise RuntimeError(
            "Le nettoyage n'a laissé aucun événement : rafraîchissement abandonné, "
            "l'index précédent est conservé."
        )
    processed_path = Path(processed_path or config.PROCESSED_EVENTS_PATH)
    processed_path.parent.mkdir(parents=True, exist_ok=True)
    save_events(events, processed_path)

    if limit:
        events = events.head(limit)

    # Construction à côté, puis mise en place : l'ancien index reste interrogeable
    # pendant toute la vectorisation (plusieurs minutes sur 12 000 événements).
    build_dir = index_dir.with_name(index_dir.name + ".new")
    shutil.rmtree(build_dir, ignore_errors=True)
    _, index_info = build_index(events, get_embeddings(provider, embedding_model),
                                describe(provider, embedding_model), index_dir=build_dir)
    swap_index(build_dir, index_dir)

    return {
        "source": source,
        "departement": department,
        "date_de_reference": ref_date.isoformat(),
        "evenements_bruts": int(len(raw)),
        "evenements_conserves": int(len(events)),
        "nettoyage": cleaning_report,
        "index": index_info,
        "duree_secondes": round(time.perf_counter() - started, 1),
    }
