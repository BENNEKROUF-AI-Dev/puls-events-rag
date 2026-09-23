"""Nettoyage et structuration des événements OpenAgenda.

Entrée  : DataFrame brut (noms de colonnes de l'API, voir fetch_events.FIELDS).
Sortie  : DataFrame propre, une ligne par événement, prêt à être découpé en
          chunks et vectorisé (étape 3), + un rapport chiffré de chaque étape.
"""
from __future__ import annotations

import html
import json
import re
import unicodedata
import warnings
from datetime import date, datetime, timedelta, timezone

import pandas as pd
from bs4 import BeautifulSoup, MarkupResemblesLocatorWarning

from rag import config

warnings.filterwarnings("ignore", category=MarkupResemblesLocatorWarning)

OUTPUT_COLUMNS = [
    "uid", "title", "description", "long_description", "conditions", "keywords",
    "date_begin", "date_end", "date_text", "n_occurrences",
    "location_name", "address", "postal_code", "city", "department", "region",
    "latitude", "longitude", "url", "status", "attendance_mode",
    "age_min", "age_max", "origin_agenda", "updated_at",
]

_SPACES = re.compile(r"[ \t  ]+")
_INVISIBLE = re.compile(r"[​‌‍﻿]")


# --- Fonctions élémentaires (testées une à une) ------------------------------
def is_missing(value) -> bool:
    """Vrai pour None, NaN, NA et chaînes vides."""
    if value is None:
        return True
    if isinstance(value, (list, dict)):
        return len(value) == 0
    try:
        if pd.isna(value):
            return True
    except (TypeError, ValueError):
        pass
    return isinstance(value, str) and not value.strip()


def clean_text(value) -> str:
    """Supprime le HTML, décode les entités (&amp; -> &) et normalise les espaces.

    Les paragraphes sont conservés sous forme de retours à la ligne, les lignes
    vides sont supprimées.
    """
    if is_missing(value):
        return ""
    text = str(value)
    if "<" in text and ">" in text:
        text = BeautifulSoup(text, "html.parser").get_text(separator="\n")
    text = html.unescape(text)
    text = _INVISIBLE.sub("", text)
    lines = (_SPACES.sub(" ", line).strip() for line in text.splitlines())
    return "\n".join(line for line in lines if line)


def parse_json(value):
    """Décode une valeur JSON stockée en texte (cas du CSV) ; sinon la renvoie telle quelle."""
    if isinstance(value, str) and value.strip()[:1] in "[{":
        try:
            return json.loads(value)
        except json.JSONDecodeError:
            return value
    return value


def parse_label(value, lang: str = "fr") -> str:
    """Extrait le libellé français d'un champ multilingue.

    Formats rencontrés : {"id": 1, "label": {"fr": "Programmé", "en": ...}},
    {"id": 1, "label": "Sur place"}, ou directement un texte.
    """
    value = parse_json(value)
    if is_missing(value):
        return ""
    if isinstance(value, dict):
        label = value.get("label", "")
        if isinstance(label, dict):
            label = label.get(lang) or next(iter(label.values()), "")
        return str(label).strip()
    return str(value).strip()


def parse_keywords(value) -> list[str]:
    """Mots-clés en liste, sans doublons ni vides (liste JSON, liste Python ou « a,b,c »)."""
    value = parse_json(value)
    if is_missing(value):
        return []
    items = value if isinstance(value, list) else str(value).split(",")
    keywords: list[str] = []
    for item in items:
        word = clean_text(item)
        if word and word.casefold() not in {k.casefold() for k in keywords}:
            keywords.append(word)
    return keywords


def parse_coordinates(value) -> tuple[float | None, float | None]:
    """(latitude, longitude) depuis « 47.21, -1.55 », {"lat":..,"lon":..} ou [lat, lon]."""
    value = parse_json(value)
    try:
        if isinstance(value, dict):
            return float(value["lat"]), float(value["lon"])
        if isinstance(value, (list, tuple)) and len(value) == 2:
            return float(value[0]), float(value[1])
        if isinstance(value, str) and "," in value:
            lat, lon = value.split(",", 1)
            return float(lat), float(lon)
    except (KeyError, TypeError, ValueError):
        pass
    return None, None


def count_occurrences(value) -> int:
    """Nombre de créneaux horaires d'un événement (0 si inconnu)."""
    value = parse_json(value)
    return len(value) if isinstance(value, list) else 0


def normalize_city(value) -> str:
    city = clean_text(value)
    return config.CITY_ALIASES.get(city, city)


def city_key(value) -> str:
    """Clé insensible à la casse, aux accents et aux tirets : « SAINT-HERBLAIN » == « Saint Herblain »."""
    text = unicodedata.normalize("NFKD", str(value)).encode("ascii", "ignore").decode()
    return re.sub(r"[^a-z0-9]", "", text.casefold())


def harmonize_cities(cities: pd.Series, postal_codes: pd.Series) -> tuple[pd.Series, int, int]:
    """Unifie l'orthographe des villes et complète les villes manquantes.

    - Variantes d'une même ville (« NANTES », « Nantes ») -> l'orthographe la plus fréquente.
    - Ville vide -> ville la plus fréquente pour le même code postal.
    Renvoie (villes, nombre de villes réécrites, nombre de villes complétées).
    """
    cities = cities.map(normalize_city)
    keys = cities.map(city_key)
    filled = cities[cities != ""]
    canonical = filled.groupby(keys[cities != ""]).agg(lambda s: s.value_counts().index[0])
    harmonized = keys.map(canonical).fillna(cities)
    rewritten = int(((cities != "") & (harmonized != cities)).sum())

    by_postal = harmonized[harmonized != ""].groupby(postal_codes[harmonized != ""]).agg(
        lambda s: s.value_counts().index[0])
    missing = harmonized == ""
    harmonized = harmonized.where(~missing, postal_codes.map(by_postal).fillna(""))
    completed = int((missing & (harmonized != "")).sum())
    return harmonized, rewritten, completed


def normalize_title_key(value) -> str:
    """Clé de comparaison pour repérer les doublons de titre (casse et espaces ignorés)."""
    return _SPACES.sub(" ", clean_text(value)).casefold()


# --- Pipeline -----------------------------------------------------------------
class _Tracker:
    """Applique les filtres successifs en comptant ce que chacun retire."""

    def __init__(self, df: pd.DataFrame):
        self.df = df
        self.steps = [{"etape": "Données brutes", "retires": 0, "restants": len(df)}]

    def keep(self, mask: pd.Series, label: str) -> None:
        mask = mask.fillna(False).astype(bool)
        removed = int((~mask).sum())
        self.df = self.df[mask]
        self.steps.append({"etape": label, "retires": removed, "restants": len(self.df)})


def clean_events(
    raw: pd.DataFrame,
    department: str = config.DEPARTMENT,
    ref_date: date | None = None,
) -> tuple[pd.DataFrame, dict]:
    """Nettoie les événements bruts et renvoie (événements propres, rapport)."""
    ref_date = ref_date or config.reference_date()
    ref = pd.Timestamp(ref_date, tz="Europe/Paris")
    window_start = ref - timedelta(days=config.WINDOW_DAYS_PAST)
    window_end = ref + timedelta(days=config.WINDOW_DAYS_FUTURE)

    df = raw.copy().reset_index(drop=True)
    t = _Tracker(df)

    # 1. Zone géographique : bon département. Le code pays est souvent vide ou
    #    mal saisi (« fr », « 33 ») : on n'exclut que les vrais codes étrangers (« CH »...).
    dep = t.df["location_department"].map(clean_text).str.casefold()
    country = t.df["location_countrycode"].map(clean_text).str.upper()
    foreign = country.str.fullmatch(r"[A-Z]{2}") & (country != config.COUNTRY_CODE)
    t.keep((dep == department.casefold()) & ~foreign, "Hors zone (département / pays)")

    # 2. Identifiants en double.
    t.keep(~t.df["uid"].astype(str).duplicated(), "Identifiant en double")

    # 3. Dates. Fin avant le début de moins de 24 h = soirée finissant après minuit
    #    saisie sans changer de jour (ex. 22h -> 02h) : on corrige au lieu de supprimer.
    begin = pd.to_datetime(t.df["firstdate_begin"], utc=True, errors="coerce")
    end = pd.to_datetime(t.df["lastdate_end"], utc=True, errors="coerce")
    after_midnight = (end < begin) & (begin - end < pd.Timedelta(days=1))
    end = end.where(~after_midnight, end + pd.Timedelta(days=1))
    t.df = t.df.assign(_begin=begin, _end=end, _end_fixed=after_midnight)
    t.keep(t.df["_begin"].notna() & t.df["_end"].notna() & (t.df["_end"] >= t.df["_begin"]),
           "Dates absentes ou incohérentes")

    # 4. Fenêtre temporelle : terminé depuis moins d'un an, commence dans moins d'un an.
    t.keep(t.df["_end"] >= window_start, "Terminé depuis plus d'un an")
    t.keep(t.df["_begin"] <= window_end, "Date de début aberrante (> 1 an)")

    # Anomalies mesurées sur les événements de la fenêtre (avant les autres filtres).
    long_desc = t.df["longdescription_fr"].fillna("").astype(str)
    country_raw = t.df["location_countrycode"].fillna("").astype(str).str.strip()
    anomalies = {
        "descriptions_longues_avec_html": int(long_desc.str.contains(r"<[a-zA-Z/]", regex=True).sum()),
        "entites_html_a_decoder": int(long_desc.str.contains(r"&[a-zA-Z#0-9]+;", regex=True).sum()),
        "codes_pays_vides_ou_mal_saisis": int((country_raw != config.COUNTRY_CODE).sum()),
        "fins_apres_minuit_corrigees": int(t.df["_end_fixed"].sum()),
    }

    # 5. Statut et mode de participation.
    status = t.df["status"].map(parse_label)
    t.df = t.df.assign(_status=status)
    t.keep(~t.df["_status"].isin(config.EXCLUDED_STATUSES), "Événement annulé")
    mode = t.df["attendancemode"].map(parse_label)
    t.df = t.df.assign(_mode=mode)
    t.keep(~t.df["_mode"].isin(config.EXCLUDED_ATTENDANCE_MODES), "Événement uniquement en ligne")

    # 6. Agendas non culturels (ateliers emploi, challenges vélo...).
    pattern = "|".join(re.escape(p) for p in config.EXCLUDED_AGENDA_PATTERNS)
    agenda = t.df["originagenda_title"].fillna("").astype(str)
    t.keep(~agenda.str.contains(pattern, case=False, regex=True), "Agenda non culturel")

    # 7. Nettoyage du texte, puis rejet des fiches vides.
    t.df = t.df.assign(
        title=t.df["title_fr"].map(clean_text),
        description=t.df["description_fr"].map(clean_text),
        long_description=t.df["longdescription_fr"].map(clean_text),
    )
    t.keep(t.df["title"] != "", "Titre manquant")
    t.keep((t.df["description"] != "") | (t.df["long_description"] != ""), "Aucune description")

    # 8. Doublons de contenu : même titre, même lieu, même date de début.
    key = (t.df["title"].map(normalize_title_key) + "|"
           + t.df["location_name"].map(normalize_title_key) + "|"
           + t.df["_begin"].astype(str))
    t.keep(~key.duplicated(), "Doublon (même titre, lieu et date)")

    postal_codes = t.df["location_postalcode"].map(clean_text)
    cities, rewritten, completed = harmonize_cities(t.df["location_city"], postal_codes)
    t.df = t.df.assign(_city=cities, _postal=postal_codes)
    clean = _build_output(t.df)

    anomalies.update({
        "villes_reecrites": rewritten,
        "villes_manquantes_completees": completed,
        "evenements_sans_mots_cles": int((clean["keywords"].map(len) == 0).sum()),
        "evenements_sans_coordonnees": int(clean["latitude"].isna().sum()),
        "descriptions_longues_plus_de_2000_caracteres": int((clean["long_description"].str.len() > 2000).sum()),
        "evenements_a_venir": int((pd.to_datetime(clean["date_end"], utc=True) >= ref).sum()),
    })
    report = {
        "departement": department,
        "date_de_reference": ref_date.isoformat(),
        "fenetre": {"debut": window_start.date().isoformat(), "fin": window_end.date().isoformat()},
        "etapes": t.steps,
        "anomalies": anomalies,  # mesurées dans la fenêtre temporelle
        "nombre_final": len(clean),
        "genere_le": datetime.now(timezone.utc).isoformat(timespec="seconds"),
    }
    return clean, report


def _to_paris_iso(series: pd.Series) -> pd.Series:
    return series.dt.tz_convert("Europe/Paris").map(lambda ts: ts.isoformat())


def _build_output(df: pd.DataFrame) -> pd.DataFrame:
    coords = df["location_coordinates"].map(parse_coordinates)
    out = pd.DataFrame({
        "uid": df["uid"].astype(str).str.strip(),
        "title": df["title"],
        "description": df["description"],
        "long_description": df["long_description"],
        "conditions": df["conditions_fr"].map(clean_text),
        "keywords": df["keywords_fr"].map(parse_keywords),
        "date_begin": _to_paris_iso(df["_begin"]),
        "date_end": _to_paris_iso(df["_end"]),
        "date_text": df["daterange_fr"].map(clean_text),
        "n_occurrences": df["timings"].map(count_occurrences),
        "location_name": df["location_name"].map(clean_text),
        "address": df["location_address"].map(clean_text),
        "postal_code": df["_postal"],
        "city": df["_city"],
        "department": df["location_department"].map(clean_text),
        "region": df["location_region"].map(clean_text),
        "latitude": coords.map(lambda c: c[0]),
        "longitude": coords.map(lambda c: c[1]),
        "url": df["canonicalurl"].map(clean_text),
        "status": df["_status"],
        "attendance_mode": df["_mode"],
        "age_min": pd.to_numeric(df["age_min"], errors="coerce").astype("Int64"),
        "age_max": pd.to_numeric(df["age_max"], errors="coerce").astype("Int64"),
        "origin_agenda": df["originagenda_title"].map(clean_text),
        "updated_at": df["updatedat"].map(clean_text),
    })
    return out.sort_values("date_begin").reset_index(drop=True)[OUTPUT_COLUMNS]


# --- Lecture / écriture -------------------------------------------------------
def save_events(df: pd.DataFrame, path) -> None:
    """Sauvegarde en JSON Lines : lisible, et garde les listes (mots-clés)."""
    df.to_json(path, orient="records", lines=True, force_ascii=False)


def load_events(path) -> pd.DataFrame:
    return pd.read_json(path, orient="records", lines=True, dtype={"uid": str, "postal_code": str})
