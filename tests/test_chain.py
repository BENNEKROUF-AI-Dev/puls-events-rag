"""Tests de la chaîne RAG, sans appel réseau.

Le LLM est remplacé par un modèle factice qui renvoie une réponse fixe et
mémorise le prompt reçu : on peut ainsi vérifier ce qui lui est réellement
envoyé, ce qui est le point critique d'un système RAG.
"""
from datetime import date

import pandas as pd
import pytest
from langchain_core.language_models.fake_chat_models import FakeListChatModel

from rag.chain import NO_RESULT_MESSAGE, RAGService, build_prompt, format_context
from rag.embeddings import describe, get_embeddings
from rag.indexer import build_index

REF_DATE = date(2026, 9, 19)


class RecordingLLM(FakeListChatModel):
    """LLM factice : réponse fixe, et garde la trace des messages reçus."""

    def _generate(self, messages, stop=None, run_manager=None, **kwargs):
        self.__dict__.setdefault("received", []).append(messages)
        return super()._generate(messages, stop=stop, run_manager=run_manager, **kwargs)


@pytest.fixture
def llm():
    return RecordingLLM(responses=["Voici trois idées de sortie."])


@pytest.fixture
def service(events, embeddings, llm, tmp_path):
    build_index(events, embeddings, describe("test"), index_dir=tmp_path)
    return RAGService(provider="test", llm=llm, index_dir=tmp_path, only_upcoming=False)


class TestFormatContext:
    def test_fiches_numerotees_avec_liens(self, service):
        documents = [doc for doc, _ in service.retrieve("concert", k=2)]
        context = format_context(documents)
        assert "--- Fiche 1 ---" in context
        assert "Lien : https://openagenda.com/" in context

    def test_prompt_contient_les_consignes_anti_invention(self):
        messages = build_prompt().format_messages(today="19/09/2026", question="q", context="c")
        system = messages[0].content
        assert "UNIQUEMENT à partir des fiches" in system
        assert "N'invente jamais" in system
        assert "19/09/2026" in system


class TestAsk:
    def test_reponse_et_sources(self, service):
        result = service.ask("un concert de jazz", reference_date=REF_DATE)
        assert result["answer"] == "Voici trois idées de sortie."
        assert result["sources"], "au moins une source doit être citée"
        for source in result["sources"]:
            assert source["titre"] and source["lien"].startswith("https://openagenda.com/")
            assert 0.0 <= source["score"] <= 1.01
        assert result["duree_secondes"] >= 0

    def test_le_llm_recoit_bien_les_fiches(self, service, llm):
        """Le cœur du RAG : le contexte envoyé au modèle contient les vrais événements."""
        result = service.ask("un concert de jazz", reference_date=REF_DATE)
        prompt = llm.received[0][-1].content
        assert "Fiche 1" in prompt
        assert result["sources"][0]["titre"] in prompt
        assert "un concert de jazz" in prompt

    def test_question_vide_refusee(self, service):
        for question in ("", "   ", None):
            with pytest.raises(ValueError, match="vide"):
                service.ask(question)

    def test_nombre_de_sources_limite(self, service):
        assert len(service.ask("concert", k=2, reference_date=REF_DATE)["sources"]) <= 2


class TestSansResultat:
    def test_message_clair_et_aucun_appel_au_llm(self, events, embeddings, llm, tmp_path):
        """Si tous les événements sont passés, on ne dérange pas le LLM pour rien."""
        build_index(events, embeddings, describe("test"), index_dir=tmp_path)
        service = RAGService(provider="test", llm=llm, index_dir=tmp_path, only_upcoming=True)
        result = service.ask("un concert de jazz", reference_date=date(2030, 1, 1))
        assert result["answer"] == NO_RESULT_MESSAGE
        assert result["sources"] == []
        assert not hasattr(llm, "received")


class TestEvenementsAVenir:
    def test_les_evenements_termines_sont_ecartes(self, events, embeddings, llm, tmp_path):
        build_index(events, embeddings, describe("test"), index_dir=tmp_path)
        service = RAGService(provider="test", llm=llm, index_dir=tmp_path, only_upcoming=True)
        # Au 1er avril 2026, l'exposition « 1002 » (terminée le 30 mars) est passée.
        sources = service.ask("exposition Jules Verne", reference_date=date(2026, 4, 1))["sources"]
        assert "1002" not in {source["uid"] for source in sources}

    def test_sans_filtre_les_evenements_passes_restent(self, service):
        sources = service.ask("exposition Jules Verne", reference_date=date(2026, 4, 1))["sources"]
        assert "1002" in {source["uid"] for source in sources}


class TestHealth:
    def test_etat_du_service(self, service):
        health = service.health()
        assert health["index_disponible"] is True
        assert health["nombre_documents"] > 0
        assert health["modele_llm"]

    def test_llm_sans_cle(self, monkeypatch, service):
        monkeypatch.delenv("MISTRAL_API_KEY", raising=False)
        service._llm = None
        with pytest.raises(RuntimeError, match="MISTRAL_API_KEY"):
            _ = service.llm
