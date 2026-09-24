"""Tests du passage « événement -> documents à vectoriser »."""
import json

import pytest
from langchain_text_splitters import RecursiveCharacterTextSplitter

from rag import config
from rag.chunking import build_event_text, event_to_documents, events_to_documents, format_date_range


@pytest.fixture
def splitter():
    return RecursiveCharacterTextSplitter(chunk_size=config.CHUNK_SIZE, chunk_overlap=config.CHUNK_OVERLAP)


class TestFormatDateRange:
    def test_evenement_sur_une_journee(self):
        assert format_date_range("2026-10-03T20:00:00+02:00", "2026-10-03T23:00:00+02:00") == \
            "le 3 octobre 2026 à 20h00"

    def test_evenement_sur_plusieurs_jours(self):
        assert format_date_range("2026-10-02T10:00:00+02:00", "2026-10-04T23:00:00+02:00") == \
            "du 2 octobre 2026 au 4 octobre 2026"


class TestBuildEventText:
    def test_entete_contient_les_informations_cles(self, events):
        event = events.iloc[0].to_dict()
        header, _ = build_event_text(event)
        assert event["title"] in header
        assert event["city"] in header
        assert header.startswith("Titre :")
        assert "Date :" in header and "Lieu :" in header

    def test_description_courte_non_dupliquee(self):
        event = {
            "uid": "1", "title": "Concert", "city": "Nantes", "location_name": "Salle",
            "address": "", "postal_code": "", "keywords": [], "conditions": "", "status": "Programmé",
            "date_begin": "2026-10-03T20:00:00+02:00", "date_end": "2026-10-03T23:00:00+02:00",
            "description": "Un concert de jazz exceptionnel dans un lieu intimiste",
            "long_description": "Un concert de jazz exceptionnel dans un lieu intimiste, avec un trio parisien.",
        }
        _, description = build_event_text(event)
        assert description.count("Un concert de jazz") == 1


class TestEventToDocuments:
    def test_evenement_court_donne_un_seul_document(self, events, splitter):
        documents = event_to_documents(events.iloc[0].to_dict(), splitter)
        assert len(documents) == 1
        assert documents[0].metadata["n_chunks"] == 1

    def test_evenement_long_est_decoupe(self, long_event, splitter):
        documents = event_to_documents(long_event, splitter)
        assert len(documents) > 1
        assert all(len(doc.page_content) <= config.CHUNK_SIZE * 1.5 for doc in documents)

    def test_chaque_morceau_garde_son_contexte(self, long_event, splitter):
        """Un morceau isolé doit rester compréhensible : titre, date et lieu répétés."""
        for doc in event_to_documents(long_event, splitter):
            assert long_event["title"] in doc.page_content
            assert "Date :" in doc.page_content
            assert "Nantes" in doc.page_content

    def test_metadonnees_completes_et_serialisables(self, long_event, splitter):
        documents = event_to_documents(long_event, splitter)
        for index, doc in enumerate(documents):
            meta = doc.metadata
            assert meta["uid"] == "9001"
            assert meta["title"] == long_event["title"]
            assert meta["city"] == "Nantes"
            assert meta["url"].startswith("https://openagenda.com/")
            assert meta["chunk_index"] == index
            assert meta["n_chunks"] == len(documents)
            json.dumps(meta)  # aucune valeur exotique dans les métadonnées


class TestEventsToDocuments:
    def test_tous_les_evenements_sont_convertis(self, events):
        documents = events_to_documents(events)
        assert {doc.metadata["uid"] for doc in documents} == set(events["uid"])
        assert len(documents) >= len(events)
