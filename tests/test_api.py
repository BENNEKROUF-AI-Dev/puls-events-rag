"""Tests de l'API REST, sans serveur, sans index réel et sans réseau.

`TestClient` appelle l'application directement, en mémoire : aucun port n'est
ouvert, rien n'est lancé en arrière-plan. Le service RAG est remplacé par un
service construit sur l'index de test (4 événements, embeddings déterministes)
grâce à `app.dependency_overrides` — c'est précisément ce que l'injection de
dépendance de FastAPI permet.
"""
from datetime import date

import pytest
from fastapi.testclient import TestClient
from langchain_core.language_models.fake_chat_models import FakeListChatModel

from rag import api
from rag.api import app, get_service
from rag.chain import NO_RESULT_MESSAGE, RAGService
from rag.embeddings import describe
from rag.indexer import build_index

REF_DATE = date(2026, 9, 19)


@pytest.fixture
def rag_service(events, embeddings, tmp_path):
    build_index(events, embeddings, describe("test"), index_dir=tmp_path)
    return RAGService(provider="test", index_dir=tmp_path, only_upcoming=False,
                      llm=FakeListChatModel(responses=["Voici trois idées de sortie."]))


@pytest.fixture
def client(rag_service):
    app.dependency_overrides[get_service] = lambda: rag_service
    api.rebuild_state.update(en_cours=False, dernier_resultat=None, derniere_erreur=None,
                             demarre_le=None, termine_le=None)
    with TestClient(app) as test_client:
        yield test_client
    app.dependency_overrides.clear()


class TestHealth:
    def test_etat_ok_et_fiche_de_l_index(self, client):
        response = client.get("/health")
        assert response.status_code == 200
        body = response.json()
        assert body["statut"] == "ok"
        assert body["index_disponible"] is True
        assert body["index"]["nombre_documents"] > 0
        assert body["reconstruction"]["en_cours"] is False

    def test_503_quand_l_index_manque(self, tmp_path):
        """La sonde doit échouer quand le service ne peut pas rendre son service.

        Sans ce 503, le HEALTHCHECK du Dockerfile — qui teste `status_code == 200`
        — marquerait « healthy » un conteneur dont /ask répond 503 à toutes les
        questions. C'est l'incohérence relevée par l'audit.
        """
        service = RAGService(provider="test", index_dir=tmp_path / "vide", only_upcoming=False)
        app.dependency_overrides[get_service] = lambda: service
        with TestClient(app) as test_client:
            response = test_client.get("/health")
            assert response.status_code == 503
            body = response.json()
            assert body["statut"] == "index absent"
            assert body["index_disponible"] is False
            # Le corps reste renvoyé avec le 503 : sans index, c'est justement là
            # qu'on a besoin de lire l'avancement de la reconstruction.
            assert "reconstruction" in body
        app.dependency_overrides.clear()

    def test_le_schema_declare_les_deux_codes(self, client):
        """La documentation générée annonce le 503, elle ne le découvre pas."""
        reponses = client.get("/openapi.json").json()["paths"]["/health"]["get"]["responses"]
        assert set(reponses) >= {"200", "503"}

    def test_la_sonde_ne_depend_pas_du_llm(self, client, rag_service):
        """Un LLM en panne ne rend pas le conteneur malade : seul l'index compte."""
        class LLMEnPanne:
            def invoke(self, messages):
                raise RuntimeError("429 Rate limit exceeded")

        rag_service._llm = LLMEnPanne()
        assert client.get("/health").status_code == 200

    def test_la_racine_renvoie_vers_la_documentation(self, client):
        response = client.get("/", follow_redirects=False)
        assert response.status_code in (307, 302)
        assert response.headers["location"] == "/docs"

    def test_le_schema_openapi_est_genere(self, client):
        schema = client.get("/openapi.json").json()
        assert set(schema["paths"]) >= {"/ask", "/health", "/rebuild"}


class TestAsk:
    def test_reponse_avec_sources(self, client):
        response = client.post("/ask", json={"question": "un concert de jazz",
                                             "date_de_reference": "2026-09-19"})
        assert response.status_code == 200
        body = response.json()
        assert body["reponse"] == "Voici trois idées de sortie."
        assert body["llm_disponible"] is True
        assert body["sources"], "au moins une source doit être citée"
        for source in body["sources"]:
            assert source["lien"].startswith("https://openagenda.com/")
            assert 0.0 <= source["score"] <= 1.01
        assert body["duree_secondes"] >= 0

    def test_le_parametre_k_limite_les_sources(self, client):
        body = client.post("/ask", json={"question": "concert", "k": 2,
                                         "date_de_reference": "2026-09-19"}).json()
        assert len(body["sources"]) <= 2

    @pytest.mark.parametrize("charge", [
        {},                                  # question absente
        {"question": ""},                    # question vide
        {"question": "a"},                   # trop courte
        {"question": "x" * 501},             # trop longue
        {"question": "concert", "k": 0},     # k hors bornes
        {"question": "concert", "k": 99},
    ])
    def test_demandes_invalides_refusees(self, client, charge):
        assert client.post("/ask", json=charge).status_code == 422

    def test_aucun_resultat_donne_un_message_clair(self, client):
        """Tous les événements du jeu de test sont passés en 2030."""
        body = client.post("/ask", json={"question": "un concert de jazz",
                                         "date_de_reference": "2030-01-01"}).json()
        assert body["reponse"] == NO_RESULT_MESSAGE
        assert body["sources"] == []

    def test_toutes_dates_rouvre_les_evenements_passes(self, client):
        charge = {"question": "exposition Jules Verne", "date_de_reference": "2026-04-01"}
        avec = client.post("/ask", json={**charge, "toutes_dates": True}).json()
        assert "1002" in {source["uid"] for source in avec["sources"]}


class TestModeDegrade:
    """Mistral indisponible : l'API répond 200, en signalant le mode dégradé."""

    class LLMEnPanne:
        def invoke(self, messages):
            raise RuntimeError('Error response 429 ... {"type":"rate_limited"}')

    def test_200_avec_llm_disponible_faux(self, client, rag_service):
        rag_service._llm = self.LLMEnPanne()
        response = client.post("/ask", json={"question": "un concert de jazz",
                                             "date_de_reference": "2026-09-19"})
        assert response.status_code == 200, "une réponse utile reste un succès HTTP"
        body = response.json()
        assert body["llm_disponible"] is False
        assert "limite de débit" in body["reponse"]
        assert body["sources"], "la recherche ne dépend pas du LLM"


class TestIndexAbsent:
    def test_503_et_message_actionnable(self, tmp_path):
        service = RAGService(provider="test", index_dir=tmp_path / "vide", only_upcoming=False)
        app.dependency_overrides[get_service] = lambda: service
        with TestClient(app) as client:
            etat = client.get("/health")
            assert etat.status_code == 503, "/health et /ask doivent dire la même chose"
            assert etat.json()["index_disponible"] is False
            response = client.post("/ask", json={"question": "un concert"})
            assert response.status_code == 503
            assert "build_index" in response.json()["detail"]
        app.dependency_overrides.clear()


class TestRebuild:
    """La reconstruction elle-même n'est pas rejouée ici (plusieurs minutes de
    calcul et un appel réseau) : on vérifie la protection, le refus des appels
    concurrents, et que la tâche de fond est bien programmée."""

    def test_desactive_sans_jeton_configure(self, client, monkeypatch):
        monkeypatch.setattr(api.config, "REBUILD_TOKEN", "")
        response = client.post("/rebuild", json={})
        assert response.status_code == 403
        assert "REBUILD_TOKEN" in response.json()["detail"]

    def test_jeton_invalide_refuse(self, client, monkeypatch):
        monkeypatch.setattr(api.config, "REBUILD_TOKEN", "le-bon-jeton")
        response = client.post("/rebuild", json={}, headers={"X-Rebuild-Token": "faux"})
        assert response.status_code == 401
        assert api.rebuild_state["en_cours"] is False, "aucune reconstruction ne démarre"

    def test_jeton_valide_lance_la_tache(self, client, monkeypatch):
        monkeypatch.setattr(api.config, "REBUILD_TOKEN", "le-bon-jeton")
        appels = []
        monkeypatch.setattr(api, "refresh_index",
                            lambda **kwargs: appels.append(kwargs) or {"evenements_conserves": 3})

        response = client.post("/rebuild", json={"source": "csv", "limite": 10},
                               headers={"X-Rebuild-Token": "le-bon-jeton"})

        assert response.status_code == 202
        # TestClient exécute les tâches de fond avant de rendre la main.
        assert appels == [{"source": "csv", "department": None, "limit": 10}]
        assert api.rebuild_state["en_cours"] is False
        assert api.rebuild_state["dernier_resultat"] == {"evenements_conserves": 3}
        assert api.rebuild_state["derniere_erreur"] is None

    def test_un_echec_est_rapporte_sans_planter_l_api(self, client, monkeypatch):
        monkeypatch.setattr(api.config, "REBUILD_TOKEN", "le-bon-jeton")

        def echoue(**kwargs):
            raise RuntimeError("API OpenAgenda injoignable")
        monkeypatch.setattr(api, "refresh_index", echoue)

        response = client.post("/rebuild", json={}, headers={"X-Rebuild-Token": "le-bon-jeton"})

        assert response.status_code == 202
        assert api.rebuild_state["en_cours"] is False
        assert "OpenAgenda injoignable" in api.rebuild_state["derniere_erreur"]
        # L'API continue de répondre : l'ancien index est toujours en place.
        assert client.get("/health").json()["index_disponible"] is True

    def test_deux_reconstructions_simultanees_refusees(self, client, monkeypatch):
        monkeypatch.setattr(api.config, "REBUILD_TOKEN", "le-bon-jeton")
        api.rebuild_state.update(en_cours=True)
        response = client.post("/rebuild", json={}, headers={"X-Rebuild-Token": "le-bon-jeton"})
        assert response.status_code == 409
