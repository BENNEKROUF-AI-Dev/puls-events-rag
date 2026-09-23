"""Récupération des événements OpenAgenda.

Deux sources possibles, qui produisent le MÊME format de sortie
(un DataFrame dont les colonnes portent les noms techniques de l'API) :

- l'API Opendatasoft (source par défaut, utilisée par /rebuild) ;
- l'export CSV téléchargé depuis le portail (pratique hors ligne et pour les tests).
"""
from __future__ import annotations

import logging
from datetime import date, timedelta
from pathlib import Path

import pandas as pd
import requests

from rag import config

logger = logging.getLogger(__name__)

# Nom technique de l'API -> libellé de colonne dans l'export CSV.
# On ne garde que les champs utiles au chatbot (pas d'email ni de téléphone
# des contributeurs : données personnelles inutiles pour le POC).
FIELDS = {
    "uid": "Identifiant",
    "canonicalurl": "URL canonique",
    "title_fr": "Titre",
    "description_fr": "Description",
    "longdescription_fr": "Description longue",
    "conditions_fr": "Détail des conditions",
    "keywords_fr": "Mots clés",
    "updatedat": "Dernière mise à jour",
    "daterange_fr": "Résumé horaires",
    "firstdate_begin": "Première date - Début",
    "lastdate_end": "Dernière date - Fin",
    "timings": "Horaires détaillés",
    "location_coordinates": "Coordonnées géographiques",
    "location_name": "Nom du lieu",
    "location_address": "Adresse",
    "location_postalcode": "Code postal",
    "location_city": "Ville",
    "location_department": "Département",
    "location_region": "Région",
    "location_countrycode": "Pays",
    "attendancemode": "Événement physique ou en ligne",
    "status": "État de l'événement",
    "age_min": "Age minimum",
    "age_max": "Age maximum",
    "originagenda_title": "Agenda d'origine (titre)",
}
REQUIRED_FIELDS = ("uid", "title_fr", "firstdate_begin", "lastdate_end", "location_department")


# --- API ----------------------------------------------------------------------
def build_where_clause(department: str, ref_date: date) -> str:
    """Filtre ODSQL appliqué côté serveur : zone + fenêtre temporelle.

    Le filtre est volontairement un peu large ; le nettoyage refait des
    contrôles stricts ensuite (pays, statut, doublons...).
    """
    start = ref_date - timedelta(days=config.WINDOW_DAYS_PAST)
    end = ref_date + timedelta(days=config.WINDOW_DAYS_FUTURE)
    safe_department = department.replace('"', '\\"')
    return (
        f'location_department = "{safe_department}" '
        f"AND lastdate_end >= date'{start.isoformat()}' "
        f"AND firstdate_begin <= date'{end.isoformat()}'"
    )


def fetch_from_api(
    department: str = config.DEPARTMENT,
    ref_date: date | None = None,
    session: requests.Session | None = None,
) -> list[dict]:
    """Télécharge les événements via l'endpoint /exports/json.

    /exports n'est pas paginé (contrairement à /records, limité à 100 résultats
    par page et à 10 000 au total), ce qui simplifie la récupération.
    """
    ref_date = ref_date or config.reference_date()
    session = session or requests.Session()
    params = {
        "where": build_where_clause(department, ref_date),
        "select": ",".join(FIELDS),
        "timezone": "Europe/Paris",
    }
    url = f"{config.OPENDATASOFT_DATASET_URL}/exports/json"
    logger.info("Appel de l'API OpenAgenda : %s", params["where"])
    try:
        response = session.get(url, params=params, timeout=config.HTTP_TIMEOUT_SECONDS)
        response.raise_for_status()
    except requests.RequestException as exc:
        raise RuntimeError(
            f"Échec de l'appel à l'API OpenAgenda ({exc}). Vérifiez votre connexion, "
            "ou utilisez l'export CSV : --source csv"
        ) from exc

    records = response.json()
    if not isinstance(records, list):
        raise RuntimeError(f"Réponse inattendue de l'API : {str(records)[:300]}")
    logger.info("%d événements reçus de l'API", len(records))
    return records


def records_to_dataframe(records: list[dict]) -> pd.DataFrame:
    """Convertit la réponse JSON de l'API en DataFrame aux colonnes attendues."""
    df = pd.DataFrame.from_records(records)
    for field in FIELDS:
        if field not in df.columns:
            df[field] = None
    return df[list(FIELDS)]


# --- CSV ----------------------------------------------------------------------
def load_from_csv(path: Path | str = config.DEFAULT_CSV_PATH, department: str | None = None) -> pd.DataFrame:
    """Charge l'export CSV du portail et renomme les colonnes comme dans l'API.

    Seules les colonnes utiles sont lues, ce qui divise fortement la mémoire
    utilisée sur l'export complet (330 Mo).
    """
    path = Path(path)
    if not path.exists():
        raise FileNotFoundError(
            f"CSV introuvable : {path}. Téléchargez l'export sur le portail Opendatasoft "
            "ou utilisez l'API : --source api"
        )
    header = pd.read_csv(path, sep=";", nrows=0, encoding="utf-8-sig").columns
    labels = [label for label in FIELDS.values() if label in header]
    missing = [api for api, label in FIELDS.items() if label not in header and api in REQUIRED_FIELDS]
    if missing:
        raise ValueError(f"Colonnes obligatoires absentes du CSV : {missing}")

    df = pd.read_csv(path, sep=";", usecols=labels, dtype=str, encoding="utf-8-sig", keep_default_na=True)
    df = df.rename(columns={label: api for api, label in FIELDS.items()})
    for field in FIELDS:
        if field not in df.columns:
            df[field] = None
    df = df[list(FIELDS)]

    if department:
        dep = df["location_department"].fillna("").str.strip().str.casefold()
        df = df[dep == department.casefold()]
    logger.info("%d événements lus depuis %s", len(df), path.name)
    return df.reset_index(drop=True)
