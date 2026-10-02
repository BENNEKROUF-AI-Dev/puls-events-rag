"""Paramètres centraux du projet.

Tous les réglages modifiables (zone, fenêtre temporelle, filtres) sont ici,
pour ne jamais avoir à chercher une valeur codée en dur dans le code.
Les valeurs peuvent être surchargées par des variables d'environnement (.env).
"""
import os
from datetime import date
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

# --- Chemins ------------------------------------------------------------------
ROOT_DIR = Path(__file__).resolve().parents[2]
DATA_DIR = ROOT_DIR / "data"
RAW_DIR = DATA_DIR / "raw"
PROCESSED_DIR = DATA_DIR / "processed"

# Surchargeable : le conteneur Docker (étape 6) monte l'index à un autre endroit.
INDEX_DIR = Path(os.getenv("INDEX_DIR", DATA_DIR / "index"))

DEFAULT_CSV_PATH = RAW_DIR / "evenements-publics-openagenda.csv"
PROCESSED_EVENTS_PATH = PROCESSED_DIR / "events.jsonl"
CLEANING_REPORT_PATH = PROCESSED_DIR / "cleaning_report.json"
INDEX_INFO_PATH = INDEX_DIR / "index_info.json"

# --- Périmètre du POC ---------------------------------------------------------
# Département choisi : Loire-Atlantique (Nantes, Saint-Nazaire...).
# C'est la zone qui garde le plus d'événements culturels une fois les
# ateliers d'emploi retirés (voir docs/journal.md).
DEPARTMENT = os.getenv("EVENTS_DEPARTMENT", "Loire-Atlantique")
COUNTRY_CODE = "FR"

# Fenêtre temporelle : événements de moins d'un an + événements à venir.
WINDOW_DAYS_PAST = 365    # on garde ce qui s'est terminé il y a moins d'un an
WINDOW_DAYS_FUTURE = 365  # au-delà, on considère la date comme aberrante


def reference_date() -> date:
    """Date « aujourd'hui » utilisée pour la fenêtre temporelle.

    Fixable via REFERENCE_DATE=AAAA-MM-JJ pour rendre un traitement reproductible.
    """
    value = os.getenv("REFERENCE_DATE")
    return date.fromisoformat(value) if value else date.today()


# --- Règles de nettoyage ------------------------------------------------------
# Agendas d'origine qui ne publient pas d'événements culturels.
# « Mes événements France Travail » = ateliers de recherche d'emploi,
# « Geovelo » = challenges de vélo du quotidien.
EXCLUDED_AGENDA_PATTERNS = ("France Travail", "Geovelo")
EXCLUDED_STATUSES = ("Annulé",)          # événements annulés
EXCLUDED_ATTENDANCE_MODES = ("En ligne",)  # le chatbot recommande des sorties

# Variantes de noms de villes que l'harmonisation automatique ne peut pas deviner
# (les différences de casse, d'accents et de tirets sont gérées automatiquement).
CITY_ALIASES = {
    "SAINT AIGNAN DE GRAND LIEU": "Saint-Aignan-Grandlieu",
    "SUCE SUR ERDRE": "Sucé-sur-Erdre",
    "Dunkirk": "Dunkerque",  # rencontré dans le Nord (nom anglais)
}

# --- Découpage en chunks ------------------------------------------------------
# 1 200 caractères ~ 300 mots : assez pour une description complète d'événement,
# assez court pour que la recherche reste précise.
CHUNK_SIZE = 1200
CHUNK_OVERLAP = 150

# --- Embeddings ---------------------------------------------------------------
# « local » (par défaut, gratuit, sans limite de débit) ou « mistral » (API).
EMBEDDING_PROVIDER = os.getenv("EMBEDDING_PROVIDER", "local")
LOCAL_EMBEDDING_MODEL = os.getenv(
    "LOCAL_EMBEDDING_MODEL", "sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2"
)
MISTRAL_EMBEDDING_MODEL = os.getenv("MISTRAL_EMBEDDING_MODEL", "mistral-embed")
EMBEDDING_BATCH_SIZE = int(os.getenv("EMBEDDING_BATCH_SIZE", "64"))

# --- Génération (LLM) ---------------------------------------------------------
# ministral-3b : le plus petit modèle Mistral, et le seul accessible sans frais
# sur le palier gratuit une fois les quotas de mistral-small atteints. Largement
# suffisant pour reformuler des fiches d'événements, puisque tout le contenu lui
# est fourni. mistral-small-latest ou mistral-large-latest pour comparer.
LLM_MODEL = os.getenv("LLM_MODEL", "ministral-3b-latest")

# --- Recherche ----------------------------------------------------------------
TOP_K = int(os.getenv("TOP_K", "5"))

# --- API REST (étape 5) -------------------------------------------------------
API_TITLE = "API Puls-Events"
API_VERSION = "1.0.0"
# La reconstruction de l'index coûte plusieurs minutes de calcul : l'endpoint
# /rebuild est donc protégé par un jeton. Vide = endpoint désactivé, ce qui est
# le comportement voulu par défaut (rien d'exposé par accident).
REBUILD_TOKEN = os.getenv("REBUILD_TOKEN", "")


def mistral_api_key() -> str | None:
    return os.getenv("MISTRAL_API_KEY")


# --- Source OpenAgenda (API Opendatasoft / Huwise, Explore v2.1) -------------
OPENDATASOFT_DATASET_URL = (
    "https://public.opendatasoft.com/api/explore/v2.1/"
    "catalog/datasets/evenements-publics-openagenda"
)
HTTP_TIMEOUT_SECONDS = 120
