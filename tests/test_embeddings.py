"""Tests du choix du fournisseur d'embeddings et de son emplacement de cache.

Aucun modèle n'est téléchargé ici : on vérifie ce que la fabrique **demande**,
en remplaçant la classe réelle par une doublure qui mémorise ses arguments.
C'est suffisant, et surtout c'est exécutable hors ligne.
"""
import sys
import types

import pytest

from rag import config, embeddings
from rag.embeddings import HashingEmbeddings, describe, get_embeddings


class FauxFastEmbed:
    """Retient les arguments reçus, au lieu de charger 220 Mo de modèle."""

    dernier: dict = {}

    def __init__(self, **kwargs):
        FauxFastEmbed.dernier = kwargs


@pytest.fixture
def fastembed_simule(monkeypatch):
    """Remplace langchain_community.embeddings.fastembed par une doublure.

    L'import ayant lieu *dans* la fonction, il faut intercepter le module lui-même.
    """
    faux_module = types.ModuleType("langchain_community.embeddings.fastembed")
    faux_module.FastEmbedEmbeddings = FauxFastEmbed
    monkeypatch.setitem(sys.modules, "langchain_community.embeddings.fastembed", faux_module)
    FauxFastEmbed.dernier = {}
    return FauxFastEmbed


class TestFournisseurs:
    def test_test_renvoie_des_embeddings_deterministes(self):
        assert isinstance(get_embeddings("test"), HashingEmbeddings)

    def test_fournisseur_inconnu_refuse_avec_les_valeurs_possibles(self):
        with pytest.raises(ValueError, match="local"):
            get_embeddings("hasard")

    def test_la_casse_du_fournisseur_est_ignoree(self):
        assert isinstance(get_embeddings("TEST"), HashingEmbeddings)


class TestCacheDuModele:
    """Le conteneur embarque le modèle à un emplacement fixe. Si ce réglage ne
    remontait plus jusqu'à fastembed, l'image retéléchargerait 220 Mo à chaque
    démarrage — sans erreur, juste très lentement."""

    def test_le_dossier_de_cache_est_transmis(self, fastembed_simule, monkeypatch):
        monkeypatch.setattr(config, "EMBEDDING_CACHE_DIR", "/opt/models")
        get_embeddings("local")
        assert fastembed_simule.dernier["cache_dir"] == "/opt/models"

    def test_sans_reglage_fastembed_choisit_lui_meme(self, fastembed_simule, monkeypatch):
        monkeypatch.setattr(config, "EMBEDDING_CACHE_DIR", None)
        get_embeddings("local")
        assert "cache_dir" not in fastembed_simule.dernier, \
            "transmettre None forcerait fastembed à utiliser le dossier courant"

    def test_le_modele_et_la_taille_de_lot_suivent_la_configuration(self, fastembed_simule):
        get_embeddings("local", model="un/modele")
        assert fastembed_simule.dernier["model_name"] == "un/modele"
        assert fastembed_simule.dernier["batch_size"] == config.EMBEDDING_BATCH_SIZE


class TestDescribe:
    def test_la_fiche_nomme_le_fournisseur_et_le_modele(self):
        fiche = describe("local")
        assert fiche["provider"] == "local"
        assert fiche["model"] == config.LOCAL_EMBEDDING_MODEL

    def test_le_fournisseur_de_test_est_identifiable(self):
        assert describe("test")["model"] == "hachage-deterministe"

    def test_un_modele_explicite_est_conserve(self):
        assert describe("mistral", "autre-modele")["model"] == "autre-modele"


def test_le_module_declare_les_trois_fournisseurs():
    assert set(embeddings.PROVIDERS) == {"local", "mistral", "test"}
