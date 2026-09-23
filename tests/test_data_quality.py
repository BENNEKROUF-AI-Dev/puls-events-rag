"""Contrôles de qualité sur les VRAIES données produites par scripts/fetch_data.py.

Ces tests sont ignorés (skip) tant que data/processed/events.jsonl n'existe pas.
Ils vérifient qu'on a bien récupéré les données attendues : bonne zone,
bonne période, pas de doublons, pas de champs vides, texte propre.
"""
import json

import pandas as pd
import pytest

from rag import config
from rag.preprocess import load_events

pytestmark = pytest.mark.skipif(
    not config.PROCESSED_EVENTS_PATH.exists(),
    reason="Lancez d'abord : python scripts/fetch_data.py",
)

MIN_EVENTS = 100  # en dessous, les recommandations seraient trop pauvres


@pytest.fixture(scope="module")
def events():
    return load_events(config.PROCESSED_EVENTS_PATH)


@pytest.fixture(scope="module")
def report():
    return json.loads(config.CLEANING_REPORT_PATH.read_text(encoding="utf-8"))


def test_volume_suffisant(events):
    assert len(events) >= MIN_EVENTS


def test_nombre_coherent_avec_le_rapport(events, report):
    assert len(events) == report["nombre_final"]


def test_un_seul_departement(events, report):
    assert set(events["department"]) == {report["departement"]}


def test_fenetre_temporelle(events, report):
    begin = pd.to_datetime(events["date_begin"], utc=True)
    end = pd.to_datetime(events["date_end"], utc=True)
    assert (end >= pd.Timestamp(report["fenetre"]["debut"], tz="Europe/Paris")).all()
    assert (begin <= pd.Timestamp(report["fenetre"]["fin"], tz="Europe/Paris")).all()


def test_identifiants_uniques(events):
    assert events["uid"].is_unique


def test_titres_et_descriptions_presents(events):
    assert (events["title"].str.strip() != "").all()
    assert ((events["description"].fillna("") != "") | (events["long_description"].fillna("") != "")).all()


def test_aucun_evenement_annule_ou_en_ligne(events):
    assert not events["status"].isin(config.EXCLUDED_STATUSES).any()
    assert not events["attendance_mode"].isin(config.EXCLUDED_ATTENDANCE_MODES).any()


def test_texte_sans_html(events):
    text = events["title"] + " " + events["description"].fillna("") + " " + events["long_description"].fillna("")
    assert not text.str.contains(r"<[a-zA-Z/][^>]*>", regex=True).any()


def test_liens_openagenda(events):
    assert events["url"].str.startswith("https://openagenda.com/").all()


def test_des_evenements_a_venir(report):
    assert report["anomalies"]["evenements_a_venir"] > 0
