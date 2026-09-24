"""Fixtures partagées par les tests."""
import json
from datetime import date
from pathlib import Path

import pytest

from rag.embeddings import get_embeddings
from rag.fetch_events import load_from_csv
from rag.preprocess import clean_events

FIXTURES = Path(__file__).parent / "fixtures"
# Date de référence FIXE : les tests donnent le même résultat quel que soit le jour.
REF_DATE = date(2026, 9, 19)


@pytest.fixture
def raw_sample():
    """15 événements fictifs, chacun conçu pour déclencher une règle de nettoyage."""
    return load_from_csv(FIXTURES / "sample_events.csv")


@pytest.fixture
def cleaned_sample(raw_sample):
    return clean_events(raw_sample, department="Loire-Atlantique", ref_date=REF_DATE)


@pytest.fixture
def api_records():
    return json.loads((FIXTURES / "sample_api_records.json").read_text(encoding="utf-8"))


@pytest.fixture
def embeddings():
    """Embeddings déterministes : aucun modèle, aucun réseau (voir rag.embeddings)."""
    return get_embeddings("test")


@pytest.fixture
def events(cleaned_sample):
    """Les événements propres du jeu de test (4 événements)."""
    return cleaned_sample[0]


@pytest.fixture
def long_event():
    """Un événement dont la description dépasse la taille d'un chunk."""
    return {
        "uid": "9001",
        "title": "Festival des arts de la rue",
        "description": "Trois jours de spectacles gratuits.",
        "long_description": ("Compagnies de cirque, fanfares et théâtre de rue investissent la ville. " * 40),
        "conditions": "Gratuit",
        "keywords": ["festival", "cirque"],
        "date_begin": "2026-10-02T10:00:00+02:00",
        "date_end": "2026-10-04T23:00:00+02:00",
        "location_name": "Centre-ville",
        "address": "Place du Commerce",
        "postal_code": "44000",
        "city": "Nantes",
        "url": "https://openagenda.com/test/events/9001",
        "status": "Programmé",
    }
