"""Tests du calcul des métriques d'évaluation.

Des chiffres qu'on met dans un rapport doivent être vérifiables : chaque cas
ci-dessous a un résultat calculé à la main, écrit dans le commentaire.
"""
import pytest

from rag.evaluation import load_questions, retrieval_metrics, save_json, suggest_relevant

QUESTIONS = [
    {"id": "q1", "question": "un concert de jazz", "attendu": {"mots_cles": ["jazz"]}},
    {"id": "q2", "question": "à Nantes", "attendu": {"ville": "Nantes"}},
    {"id": "q3", "question": "un match de foot à Marseille", "hors_perimetre": True, "attendu": {}},
]


def annotations(**pertinents_par_question) -> dict:
    return {"questions": {qid: {"pertinents": list(uids)}
                          for qid, uids in pertinents_par_question.items()}}


class TestSuggestRelevant:
    SOURCES = [
        {"uid": "a", "titre": "Soirée jazz au Pannonica", "ville": "Nantes", "lieu": "Pannonica"},
        {"uid": "b", "titre": "Exposition Jules Verne", "ville": "Nantes", "lieu": "Musée"},
        {"uid": "c", "titre": "Concert de JAZZ manouche", "ville": "Rezé", "lieu": "Salle"},
    ]

    def test_mots_cles_insensibles_a_la_casse(self):
        assert suggest_relevant(QUESTIONS[0], self.SOURCES) == {"a", "c"}

    def test_filtre_par_ville(self):
        assert suggest_relevant(QUESTIONS[1], self.SOURCES) == {"a", "b"}

    def test_sans_critere_aucune_proposition(self):
        assert suggest_relevant({"attendu": {}}, self.SOURCES) == set()


class TestRetrievalMetrics:
    def test_tout_pertinent_en_tete(self):
        """2 pertinents sur 2 résultats -> précision 1, rappel 1, MRR 1."""
        report = retrieval_metrics(
            QUESTIONS[:1], annotations(q1=["a", "b"]), {"q1": ["a", "b"]}, k=2)
        assert report["precision@2"] == 1.0
        assert report["rappel@2"] == 1.0
        assert report["mrr"] == 1.0
        assert report["taux_de_reussite"] == 1.0

    def test_un_seul_pertinent_en_troisieme_position(self):
        """1 pertinent sur 3 résultats, en 3e -> précision 1/3, MRR 1/3."""
        report = retrieval_metrics(
            QUESTIONS[:1], annotations(q1=["c"]), {"q1": ["a", "b", "c"]}, k=3)
        assert report["precision@3"] == round(1 / 3, 3)
        assert report["rappel@3"] == 1.0
        assert report["mrr"] == round(1 / 3, 3)

    def test_aucun_pertinent_retrouve(self):
        report = retrieval_metrics(
            QUESTIONS[:1], annotations(q1=["z"]), {"q1": ["a", "b"]}, k=2)
        assert report["precision@2"] == 0.0
        assert report["rappel@2"] == 0.0
        assert report["mrr"] == 0.0
        assert report["taux_de_reussite"] == 0.0
        assert report["detail"][0]["premier_pertinent"] is None

    def test_moyenne_sur_deux_questions(self):
        """q1 : le pertinent est en 1re position -> précision 1/2, MRR 1.
        q2 : le pertinent est en 2e position  -> précision 1/2, MRR 1/2.
        Moyennes : précision 0,5 et MRR 0,75. Deux recherches de même précision
        mais de qualité ressentie différente : c'est tout l'intérêt du MRR."""
        report = retrieval_metrics(
            QUESTIONS[:2],
            annotations(q1=["a"], q2=["y"]),
            {"q1": ["a", "x"], "q2": ["w", "y"]},
            k=2)
        assert report["questions_notees"] == 2
        assert report["precision@2"] == 0.5
        assert report["mrr"] == 0.75

    def test_les_questions_hors_perimetre_sont_comptees_a_part(self):
        """Une question hors périmètre ne doit pas entrer dans les moyennes."""
        report = retrieval_metrics(
            QUESTIONS, annotations(q1=["a"], q3=[]), {"q1": ["a"], "q3": ["n'importe quoi"]}, k=1)
        assert report["questions_notees"] == 1, "seule q1 est notée"
        assert report["precision@1"] == 1.0
        assert report["hors_perimetre_correctement_vides"] == "1/1"

    def test_hors_perimetre_avec_un_pertinent_annote_compte_comme_rate(self):
        report = retrieval_metrics(QUESTIONS[2:], annotations(q3=["a"]), {"q3": ["a"]}, k=1)
        assert report["hors_perimetre_correctement_vides"] == "0/1"

    def test_les_questions_non_annotees_sont_ignorees(self):
        report = retrieval_metrics(QUESTIONS, annotations(q1=["a"]), {"q1": ["a"]}, k=1)
        assert [ligne["id"] for ligne in report["detail"]] == ["q1"]

    def test_sans_annotation_les_moyennes_valent_zero_sans_planter(self):
        report = retrieval_metrics(QUESTIONS, {"questions": {}}, {}, k=5)
        assert report["questions_notees"] == 0
        assert report["mrr"] == 0.0


class TestFichiers:
    def test_le_jeu_de_questions_du_projet_est_valide(self):
        """Le vrai fichier eval/questions.json doit rester exploitable."""
        from pathlib import Path
        chemin = Path(__file__).resolve().parents[1] / "eval" / "questions.json"
        questions = load_questions(chemin)
        assert len(questions) >= 15, "un jeu d'évaluation crédible compte au moins 15 questions"
        identifiants = [q["id"] for q in questions]
        assert len(set(identifiants)) == len(identifiants), "identifiants dupliqués"
        for question in questions:
            assert question["question"].strip()
            assert question["categorie"]
        assert any(q.get("hors_perimetre") for q in questions), \
            "il faut des cas limites : un système qui ne sait pas refuser n'est pas évalué"

    def test_save_json_cree_les_dossiers_et_garde_les_accents(self, tmp_path):
        cible = tmp_path / "a" / "b" / "resultats.json"
        save_json(cible, {"clé": "événement à Rezé"})
        assert "événement à Rezé" in cible.read_text(encoding="utf-8")
