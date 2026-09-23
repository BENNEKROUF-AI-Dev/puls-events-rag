"""Tests du nettoyage : fonctions élémentaires puis pipeline complet."""
import pandas as pd
import pytest

from conftest import REF_DATE
from rag.preprocess import (
    OUTPUT_COLUMNS,
    city_key,
    clean_text,
    harmonize_cities,
    parse_coordinates,
    parse_keywords,
    parse_label,
)


# --- Fonctions élémentaires ---------------------------------------------------
class TestCleanText:
    def test_supprime_les_balises_et_garde_les_paragraphes(self):
        assert clean_text("<p>Bonjour</p><p><strong>à tous</strong></p>") == "Bonjour\nà tous"

    def test_decode_les_entites_html(self):
        assert clean_text("Jazz &amp; blues&nbsp;!") == "Jazz & blues !"

    def test_supprime_les_paragraphes_vides_et_espaces_multiples(self):
        assert clean_text("<p>A</p><p></p><p>   B    C </p>") == "A\nB C"

    @pytest.mark.parametrize("value", [None, float("nan"), pd.NA, "", "   "])
    def test_valeurs_manquantes_donnent_une_chaine_vide(self, value):
        assert clean_text(value) == ""


class TestParseLabel:
    def test_label_multilingue_en_texte_json(self):
        assert parse_label('{"id": 1, "label": {"fr": "Programmé", "en": "Scheduled"}}') == "Programmé"

    def test_label_simple(self):
        assert parse_label({"id": 1, "label": "Sur place"}) == "Sur place"

    def test_label_sans_francais_prend_la_premiere_langue(self):
        assert parse_label({"id": 1, "label": {"en": "Online"}}) == "Online"

    def test_valeur_vide(self):
        assert parse_label(None) == ""


class TestParseKeywords:
    def test_texte_separe_par_des_virgules(self):
        assert parse_keywords("musique, concert ,") == ["musique", "concert"]

    def test_liste_json_et_doublons(self):
        assert parse_keywords(["Jazz", "jazz", "Blues"]) == ["Jazz", "Blues"]

    def test_vide(self):
        assert parse_keywords(None) == []


class TestParseCoordinates:
    @pytest.mark.parametrize("value", ["47.2184, -1.5536", {"lat": 47.2184, "lon": -1.5536}, [47.2184, -1.5536]])
    def test_formats_acceptes(self, value):
        assert parse_coordinates(value) == (47.2184, -1.5536)

    @pytest.mark.parametrize("value", [None, "", "pas de coordonnées", {"lat": 1}])
    def test_formats_invalides(self, value):
        assert parse_coordinates(value) == (None, None)


class TestVilles:
    def test_cle_insensible_casse_accents_tirets(self):
        assert city_key("SAINT HERBLAIN") == city_key("Saint-Herblain") == city_key("saint-hérblain")

    def test_harmonisation_et_completion_par_code_postal(self):
        cities = pd.Series(["Nantes", "Nantes", "NANTES", "", "Rezé"])
        postal = pd.Series(["44000", "44000", "44000", "44000", "44400"])
        result, rewritten, completed = harmonize_cities(cities, postal)
        assert result.tolist() == ["Nantes", "Nantes", "Nantes", "Nantes", "Rezé"]
        assert (rewritten, completed) == (1, 1)


# --- Pipeline complet sur le jeu de test ------------------------------------
class TestCleanEvents:
    def test_ne_garde_que_les_evenements_valides(self, cleaned_sample):
        events, _ = cleaned_sample
        assert sorted(events["uid"]) == ["1001", "1002", "1012", "1014"]

    def test_colonnes_de_sortie(self, cleaned_sample):
        events, _ = cleaned_sample
        assert list(events.columns) == OUTPUT_COLUMNS

    def test_identifiants_uniques(self, cleaned_sample):
        events, _ = cleaned_sample
        assert events["uid"].is_unique

    def test_toutes_les_dates_dans_la_fenetre(self, cleaned_sample):
        events, report = cleaned_sample
        begin = pd.to_datetime(events["date_begin"], utc=True)
        end = pd.to_datetime(events["date_end"], utc=True)
        assert (end >= pd.Timestamp(report["fenetre"]["debut"], tz="Europe/Paris")).all()
        assert (begin <= pd.Timestamp(report["fenetre"]["fin"], tz="Europe/Paris")).all()
        assert (end >= begin).all()

    def test_un_seul_departement(self, cleaned_sample):
        events, _ = cleaned_sample
        assert set(events["department"]) == {"Loire-Atlantique"}

    def test_champs_obligatoires_remplis(self, cleaned_sample):
        events, _ = cleaned_sample
        for column in ("uid", "title", "date_begin", "date_end", "city", "url"):
            assert (events[column].astype(str).str.strip() != "").all(), column
        assert ((events["description"] != "") | (events["long_description"] != "")).all()

    def test_plus_aucune_balise_html(self, cleaned_sample):
        events, _ = cleaned_sample
        text = events["title"] + events["description"] + events["long_description"]
        assert not text.str.contains(r"<[a-zA-Z/]|&[a-z]+;", regex=True).any()

    def test_html_nettoye_sur_un_exemple(self, cleaned_sample):
        events, _ = cleaned_sample
        jazz = events.set_index("uid").loc["1001"]
        assert jazz["long_description"] == "Soirée\njazz\n& blues.\nEntrée libre."

    def test_fin_apres_minuit_corrigee(self, cleaned_sample):
        events, report = cleaned_sample
        electro = events.set_index("uid").loc["1012"]
        assert electro["date_end"] == "2026-10-25T02:00:00+02:00"
        assert report["anomalies"]["fins_apres_minuit_corrigees"] == 1

    def test_ville_harmonisee_et_completee(self, cleaned_sample):
        events, _ = cleaned_sample
        cities = events.set_index("uid")["city"]
        assert cities["1012"] == "Nantes"  # « NANTES » réécrit
        assert cities["1014"] == "Nantes"  # vide, complété via le code postal 44000

    def test_mots_cles_et_ages(self, cleaned_sample):
        events, _ = cleaned_sample
        balade = events.set_index("uid").loc["1014"]
        assert balade["keywords"] == ["conte", "balade"]
        assert (balade["age_min"], balade["age_max"]) == (6, 12)

    def test_rapport_coherent(self, cleaned_sample, raw_sample):
        events, report = cleaned_sample
        steps = report["etapes"]
        assert steps[0]["restants"] == len(raw_sample)
        assert steps[-1]["restants"] == report["nombre_final"] == len(events)
        assert sum(step["retires"] for step in steps) == len(raw_sample) - len(events)

    @pytest.mark.parametrize("etape, retires", [
        ("Hors zone (département / pays)", 2),
        ("Identifiant en double", 1),
        ("Terminé depuis plus d'un an", 1),
        ("Date de début aberrante (> 1 an)", 1),
        ("Événement annulé", 1),
        ("Événement uniquement en ligne", 1),
        ("Agenda non culturel", 1),
        ("Titre manquant", 1),
        ("Aucune description", 1),
        ("Doublon (même titre, lieu et date)", 1),
    ])
    def test_chaque_regle_retire_le_bon_evenement(self, cleaned_sample, etape, retires):
        _, report = cleaned_sample
        assert {s["etape"]: s["retires"] for s in report["etapes"]}[etape] == retires

    def test_date_de_reference_prise_en_compte(self, raw_sample):
        from rag.preprocess import clean_events
        from datetime import date
        events, _ = clean_events(raw_sample, department="Loire-Atlantique", ref_date=date(2027, 9, 1))
        # Un an plus tard, l'exposition de janvier 2026 n'est plus dans la fenêtre.
        assert "1002" not in set(events["uid"])
        assert REF_DATE < date(2027, 9, 1)
