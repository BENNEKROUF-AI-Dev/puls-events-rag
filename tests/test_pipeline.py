"""Tests du rafraîchissement complet : données -> nettoyage -> index.

Aucun appel réseau : la source brute est remplacée par le jeu de test, et les
embeddings sont les embeddings déterministes. Le point réellement vérifié ici
est la sécurité de la mise en place : un rafraîchissement raté ne doit jamais
détruire l'index qui répondait jusque-là.
"""
from datetime import date
from pathlib import Path

import pytest

from rag import pipeline
from rag.embeddings import describe, get_embeddings
from rag.indexer import build_index, load_index

REF_DATE = date(2026, 9, 19)


def faux_index(chemin: Path, marque: str) -> Path:
    """Crée un dossier d'index factice, reconnaissable à son contenu."""
    chemin.mkdir(parents=True, exist_ok=True)
    (chemin / "index.faiss").write_text(marque, encoding="utf-8")
    return chemin


class TestSwapIndex:
    def test_le_nouvel_index_remplace_l_ancien(self, tmp_path):
        cible = faux_index(tmp_path / "index", "ancien")
        neuf = faux_index(tmp_path / "index.new", "nouveau")

        pipeline.swap_index(neuf, cible)

        assert (cible / "index.faiss").read_text(encoding="utf-8") == "nouveau"
        assert not neuf.exists()
        assert not (tmp_path / "index.old").exists(), "aucun résidu ne doit rester"

    def test_fonctionne_sans_index_prealable(self, tmp_path):
        cible = tmp_path / "index"
        pipeline.swap_index(faux_index(tmp_path / "index.new", "premier"), cible)
        assert (cible / "index.faiss").read_text(encoding="utf-8") == "premier"

    def test_l_ancien_index_est_restaure_si_la_mise_en_place_echoue(self, tmp_path, monkeypatch):
        cible = faux_index(tmp_path / "index", "ancien")
        neuf = faux_index(tmp_path / "index.new", "nouveau")

        vrai_rename = Path.rename

        def rename_capricieux(self, target):
            if Path(self).name.endswith(".new"):      # échec au dernier moment
                raise OSError("disque occupé")
            return vrai_rename(self, target)
        monkeypatch.setattr(Path, "rename", rename_capricieux)

        with pytest.raises(OSError):
            pipeline.swap_index(neuf, cible)

        assert (cible / "index.faiss").read_text(encoding="utf-8") == "ancien", \
            "l'index qui répondait doit être remis en place"


class TestRefreshIndex:
    def test_rafraichissement_complet_et_rapport(self, tmp_path, raw_sample, monkeypatch):
        monkeypatch.setattr(pipeline, "load_raw", lambda *a, **k: raw_sample)
        index_dir = tmp_path / "index"

        rapport = pipeline.refresh_index(
            department="Loire-Atlantique", reference_date=REF_DATE, provider="test",
            index_dir=index_dir, processed_path=tmp_path / "events.jsonl")

        assert rapport["evenements_conserves"] > 0
        assert rapport["index"]["nombre_documents"] >= rapport["evenements_conserves"]
        assert rapport["nettoyage"], "le rapport de nettoyage est conservé"
        assert (tmp_path / "events.jsonl").exists()
        # L'index produit est réellement interrogeable.
        store = load_index(get_embeddings("test"), index_dir)
        assert store.index.ntotal == rapport["index"]["nombre_documents"]

    def test_un_nettoyage_vide_conserve_l_ancien_index(self, tmp_path, raw_sample, events,
                                                       embeddings, monkeypatch):
        index_dir = tmp_path / "index"
        build_index(events, embeddings, describe("test"), index_dir=index_dir)
        ancien = (index_dir / "index.faiss").read_bytes()

        # Le mauvais département : le nettoyage écarte tout.
        monkeypatch.setattr(pipeline, "load_raw", lambda *a, **k: raw_sample)
        with pytest.raises(RuntimeError, match="aucun événement"):
            pipeline.refresh_index(department="Cantal", reference_date=REF_DATE,
                                   provider="test", index_dir=index_dir,
                                   processed_path=tmp_path / "events.jsonl")

        assert (index_dir / "index.faiss").read_bytes() == ancien

    def test_source_inconnue_refusee(self):
        with pytest.raises(ValueError, match="Source inconnue"):
            pipeline.load_raw("ftp")
