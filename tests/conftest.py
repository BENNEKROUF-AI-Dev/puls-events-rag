"""Fixtures partagées par les tests."""
import json
from datetime import date
from pathlib import Path

import pytest

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
