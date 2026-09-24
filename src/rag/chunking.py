"""Transformation des événements en documents prêts à vectoriser.

Un événement devient une fiche texte. Les fiches courtes (la majorité) restent
entières ; seules les descriptions longues sont découpées en plusieurs morceaux.
Chaque morceau reporte l'en-tête factuel (titre, date, lieu) : isolé dans l'index,
un morceau doit rester compréhensible.
"""
from __future__ import annotations

from datetime import datetime

import pandas as pd
from langchain_core.documents import Document
from langchain_text_splitters import RecursiveCharacterTextSplitter

from rag import config

MONTHS = ["janvier", "février", "mars", "avril", "mai", "juin", "juillet",
          "août", "septembre", "octobre", "novembre", "décembre"]


def format_date_range(date_begin: str, date_end: str) -> str:
    """« du 3 octobre 2026 au 5 octobre 2026 » ou « le 3 octobre 2026 à 20h00 »."""
    begin, end = datetime.fromisoformat(str(date_begin)), datetime.fromisoformat(str(date_end))
    day = f"{begin.day} {MONTHS[begin.month - 1]} {begin.year}"
    if begin.date() == end.date():
        return f"le {day} à {begin:%Hh%M}"
    return f"du {day} au {end.day} {MONTHS[end.month - 1]} {end.year}"


def build_event_text(event: dict) -> tuple[str, str]:
    """Renvoie (en-tête factuel, description) d'un événement."""
    place = ", ".join(p for p in [event.get("location_name"), event.get("address"),
                                  f"{event.get('postal_code', '')} {event.get('city', '')}".strip()] if p)
    lines = [
        f"Titre : {event['title']}",
        f"Date : {format_date_range(event['date_begin'], event['date_end'])}",
        f"Lieu : {place}",
        f"Ville : {event.get('city', '')}",
    ]
    keywords = event.get("keywords") or []
    if len(keywords):
        lines.append(f"Mots-clés : {', '.join(keywords)}")
    if event.get("conditions"):
        lines.append(f"Conditions : {event['conditions']}")
    if event.get("status") and event["status"] != "Programmé":
        lines.append(f"Statut : {event['status']}")

    description = "\n".join(t for t in [event.get("description"), event.get("long_description")]
                            if t and t.strip())
    # La description courte est souvent le début de la longue : on évite la répétition.
    short, long_ = (event.get("description") or "").strip(), (event.get("long_description") or "").strip()
    if short and long_ and long_.startswith(short[:60]):
        description = long_
    return "\n".join(lines), description


def event_to_documents(event: dict, splitter: RecursiveCharacterTextSplitter) -> list[Document]:
    """Un événement -> un ou plusieurs documents LangChain, métadonnées comprises."""
    header, description = build_event_text(event)
    full = f"{header}\nDescription : {description}".strip()

    if len(full) <= config.CHUNK_SIZE:
        texts = [full]
    else:
        texts = [f"{header}\nDescription (partie {i + 1}) : {part}"
                 for i, part in enumerate(splitter.split_text(description))] or [full]

    metadata = {
        "uid": str(event["uid"]),
        "title": event["title"],
        "date_begin": str(event["date_begin"]),
        "date_end": str(event["date_end"]),
        "date_text": format_date_range(event["date_begin"], event["date_end"]),
        "city": event.get("city", ""),
        "location_name": event.get("location_name", ""),
        "url": event.get("url", ""),
        "keywords": ", ".join(event.get("keywords") or []),
        "status": event.get("status", ""),
    }
    return [Document(page_content=text, metadata={**metadata, "chunk_index": i, "n_chunks": len(texts)})
            for i, text in enumerate(texts)]


def events_to_documents(events: pd.DataFrame) -> list[Document]:
    """Convertit tous les événements nettoyés en documents à indexer."""
    splitter = RecursiveCharacterTextSplitter(
        chunk_size=config.CHUNK_SIZE,
        chunk_overlap=config.CHUNK_OVERLAP,
        separators=["\n\n", "\n", ". ", " ", ""],
    )
    documents: list[Document] = []
    for event in events.to_dict(orient="records"):
        documents.extend(event_to_documents(event, splitter))
    return documents
