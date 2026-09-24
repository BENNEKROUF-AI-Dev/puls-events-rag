"""Tests de la base vectorielle Faiss, sans modèle réel ni réseau.

Les embeddings utilisés sont déterministes (voir conftest.HashingEmbeddings) :
deux textes qui partagent des mots donnent des vecteurs proches. On peut donc
vérifier que la recherche remonte le bon événement.
"""
from datetime import date

import pandas as pd
import pytest

from rag.chunking import events_to_documents
from rag.embeddings import describe, get_embeddings
from rag.indexer import build_index, build_vectorstore, load_index, load_index_info, search


@pytest.fixture
def index(events, embeddings, tmp_path):
    store, info = build_index(events, embeddings, describe("local", "test-model"), index_dir=tmp_path)
    return store, info, tmp_path


class TestBuildIndex:
    def test_tous_les_documents_sont_indexes(self, index, events, embeddings):
        store, _, _ = index
        assert store.index.ntotal == len(events_to_documents(events))

    def test_aucun_evenement_oublie(self, index, events):
        store, _, _ = index
        indexed = {doc.metadata["uid"] for doc in store.docstore._dict.values()}
        assert indexed == set(events["uid"])

    def test_fiche_descriptive(self, index, events):
        _, info, _ = index
        assert info["nombre_evenements"] == len(events)
        assert info["dimension"] == 128
        assert info["type_index"] == "IndexFlatIP"   # recherche exacte, similarité cosinus
        assert info["provider"] == "local" and info["model"] == "test-model"
        assert info["duree_vectorisation_secondes"] >= 0

    def test_fichiers_ecrits_sur_disque(self, index):
        _, _, path = index
        assert (path / "index.faiss").exists() and (path / "index.pkl").exists()
        assert load_index_info(path)["nombre_documents"] > 0

    def test_index_vide_refuse(self, embeddings, tmp_path):
        with pytest.raises(ValueError, match="Aucun document"):
            build_vectorstore([], embeddings)


class TestRechargement:
    def test_index_rechargeable(self, index, embeddings):
        store, _, path = index
        reloaded = load_index(embeddings, path)
        assert reloaded.index.ntotal == store.index.ntotal

    def test_erreur_si_index_absent(self, embeddings, tmp_path):
        with pytest.raises(FileNotFoundError, match="build_index"):
            load_index(embeddings, tmp_path / "vide")


class TestRecherche:
    def test_retrouve_un_evenement_par_son_titre(self, index, events):
        store, _, _ = index
        expected = events.iloc[0]
        results = search(store, expected["title"], k=3)
        assert results[0][0].metadata["uid"] == expected["uid"]

    def test_recherche_par_theme(self, index):
        store, _, _ = index
        # « 1001 » est le concert de jazz du jeu de test.
        results = search(store, "soirée jazz et blues", k=3)
        assert "1001" in {doc.metadata["uid"] for doc, _ in results}

    def test_nombre_de_resultats_limite(self, index):
        store, _, _ = index
        assert len(search(store, "concert", k=2)) <= 2

    def test_un_seul_morceau_par_evenement(self, long_event, embeddings, tmp_path):
        """Un événement découpé en plusieurs morceaux ne doit pas saturer les résultats."""
        events = pd.DataFrame([long_event])
        store, info = build_index(events, embeddings, describe("local"), index_dir=tmp_path)
        assert info["nombre_documents"] > 1
        results = search(store, "cirque et fanfares dans la rue", k=5)
        assert len(results) == 1

    def test_metadonnees_disponibles_pour_la_reponse(self, index):
        store, _, _ = index
        doc, score = search(store, "exposition", k=1)[0]
        for key in ("uid", "title", "date_text", "city", "url"):
            assert doc.metadata.get(key)
        assert 0.0 <= score <= 1.01   # similarité cosinus


class TestChoixDuModele:
    def test_fournisseur_inconnu(self):
        with pytest.raises(ValueError, match="Fournisseur inconnu"):
            get_embeddings("chatgpt")

    def test_cle_manquante_pour_mistral(self, monkeypatch):
        monkeypatch.delenv("MISTRAL_API_KEY", raising=False)
        with pytest.raises(RuntimeError, match="MISTRAL_API_KEY"):
            get_embeddings("mistral")

    def test_description_par_defaut(self):
        assert describe("mistral")["model"] == "mistral-embed"
        assert "multilingual" in describe("local")["model"]


class TestQuasiDoublons:
    """Open Agenda publie parfois deux fiches pour le même spectacle : un seul résultat."""

    def test_meme_titre_meme_jour_un_seul_resultat(self, events, embeddings, tmp_path):
        jumeau = events.iloc[0].to_dict()
        jumeau["uid"] = "9999"
        jumeau["location_name"] = jumeau["location_name"].upper() + " "  # lieu écrit autrement
        avec_doublon = pd.concat([events, pd.DataFrame([jumeau])], ignore_index=True)

        store, _ = build_index(avec_doublon, embeddings, describe("test"), index_dir=tmp_path)
        titres = [doc.metadata["title"] for doc, _ in search(store, jumeau["title"], k=5)]
        assert titres.count(jumeau["title"]) == 1


class TestFiltreEvenementsAVenir:
    """Le filtre ne doit pas appauvrir les résultats, même si peu d'événements passent."""

    def _index_avec_un_seul_evenement_a_venir(self, events, embeddings, tmp_path):
        passes = []
        for numero in range(60):          # 60 événements terminés depuis longtemps
            evenement = events.iloc[0].to_dict()
            evenement.update(uid=f"P{numero}", title=f"Concert de jazz numéro {numero}",
                             date_begin="2025-10-01T20:00:00+02:00",
                             date_end="2025-10-01T23:00:00+02:00")
            passes.append(evenement)
        a_venir = events.iloc[0].to_dict()
        a_venir.update(uid="FUTUR", title="Concert de jazz au Lieu Unique",
                       date_begin="2026-12-01T20:00:00+01:00",
                       date_end="2026-12-01T23:00:00+01:00")
        corpus = pd.DataFrame(passes + [a_venir])
        store, _ = build_index(corpus, embeddings, describe("test"), index_dir=tmp_path)
        return store

    def test_trouve_l_evenement_a_venir_noye_parmi_les_passes(self, events, embeddings, tmp_path):
        store = self._index_avec_un_seul_evenement_a_venir(events, embeddings, tmp_path)
        results = search(store, "concert de jazz", k=5, only_upcoming=True,
                         reference_date=date(2026, 9, 24))
        assert [doc.metadata["uid"] for doc, _ in results] == ["FUTUR"]

    def test_sans_filtre_les_passes_remontent(self, events, embeddings, tmp_path):
        store = self._index_avec_un_seul_evenement_a_venir(events, embeddings, tmp_path)
        assert len(search(store, "concert de jazz", k=5)) == 5


class TestEvenementLong:
    """Un événement à la description longue ne doit pas monopoliser les résultats."""

    def test_les_autres_evenements_restent_visibles(self, embeddings, tmp_path):
        commun = dict(conditions="", keywords=[], status="Programmé", address="",
                      postal_code="44000", location_name="Salle", city="Nantes")
        bavard = {**commun, "uid": "LONG", "title": "Concert de musique classique",
                  "description": "Concert de musique classique",
                  "long_description": "Programme de musique classique, orchestre et solistes. " * 400,
                  "date_begin": "2026-12-01T20:00:00+01:00", "date_end": "2026-12-01T23:00:00+01:00",
                  "url": "https://openagenda.com/test/events/long"}
        autres = [{**commun, "uid": f"A{i}", "title": f"Concert symphonique numéro {i}",
                   "description": "Musique classique et orchestre",
                   "long_description": "Un concert d'orchestre.",
                   "date_begin": "2026-11-10T20:00:00+01:00", "date_end": "2026-11-10T22:00:00+01:00",
                   "url": f"https://openagenda.com/test/events/{i}"} for i in range(20)]

        store, info = build_index(pd.DataFrame([bavard] + autres), embeddings,
                                  describe("test"), index_dir=tmp_path)
        assert info["nombre_documents"] > 21, "l'événement bavard doit bien être découpé"

        results = search(store, "concert de musique classique", k=5, only_upcoming=True,
                         reference_date=date(2026, 9, 24))
        uids = [doc.metadata["uid"] for doc, _ in results]
        assert len(uids) == 5 and len(set(uids)) == 5
        assert uids[0] == "LONG"
