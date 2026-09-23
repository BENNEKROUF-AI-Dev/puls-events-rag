"""Tests de la récupération des données, sans aucun appel réseau réel."""
from datetime import date

import pytest
import requests

from conftest import FIXTURES, REF_DATE
from rag.fetch_events import FIELDS, build_where_clause, fetch_from_api, load_from_csv, records_to_dataframe
from rag.preprocess import clean_events


class FakeResponse:
    def __init__(self, payload, status=200):
        self.payload, self.status_code = payload, status

    def json(self):
        return self.payload

    def raise_for_status(self):
        if self.status_code >= 400:
            raise requests.HTTPError(f"HTTP {self.status_code}")


class FakeSession:
    """Remplace requests.Session : mémorise l'appel et renvoie une réponse préparée."""

    def __init__(self, response):
        self.response, self.calls = response, []

    def get(self, url, params=None, timeout=None):
        self.calls.append({"url": url, "params": params, "timeout": timeout})
        return self.response


def test_filtre_odsql_zone_et_fenetre():
    where = build_where_clause("Loire-Atlantique", date(2026, 9, 19))
    assert 'location_department = "Loire-Atlantique"' in where
    assert "lastdate_end >= date'2025-09-19'" in where
    assert "firstdate_begin <= date'2027-09-19'" in where


def test_appel_api_parametres(api_records):
    session = FakeSession(FakeResponse(api_records))
    records = fetch_from_api("Loire-Atlantique", REF_DATE, session=session)
    call = session.calls[0]
    assert len(records) == 2
    assert call["url"].endswith("/evenements-publics-openagenda/exports/json")
    assert "location_department" in call["params"]["where"]
    assert set(call["params"]["select"].split(",")) == set(FIELDS)
    assert call["timeout"] is not None


def test_erreur_api_message_clair():
    session = FakeSession(FakeResponse({"error": "boom"}, status=503))
    with pytest.raises(RuntimeError, match="--source csv"):
        fetch_from_api("Loire-Atlantique", REF_DATE, session=session)


def test_reponse_api_inattendue():
    session = FakeSession(FakeResponse({"results": []}))
    with pytest.raises(RuntimeError, match="inattendue"):
        fetch_from_api("Loire-Atlantique", REF_DATE, session=session)


def test_format_api_nettoye_comme_le_csv(api_records):
    """L'API renvoie des listes et objets JSON natifs : le résultat doit être identique au CSV."""
    events, _ = clean_events(records_to_dataframe(api_records), "Loire-Atlantique", REF_DATE)
    assert sorted(events["uid"]) == ["1001", "1002"]
    jazz = events.set_index("uid").loc["1001"]
    assert jazz["keywords"] == ["musique", "jazz"]
    assert (jazz["latitude"], jazz["longitude"]) == (47.2184, -1.5536)
    assert jazz["status"] == "Programmé"


def test_csv_colonnes_renommees(raw_sample):
    assert list(raw_sample.columns) == list(FIELDS)


def test_csv_filtre_departement():
    df = load_from_csv(FIXTURES / "sample_events.csv", department="gironde")
    assert df["uid"].tolist() == ["1008"]


def test_csv_absent():
    with pytest.raises(FileNotFoundError):
        load_from_csv(FIXTURES / "inexistant.csv")
