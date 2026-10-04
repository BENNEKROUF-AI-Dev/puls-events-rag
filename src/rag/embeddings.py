"""Choix du modèle d'embeddings.

Deux fournisseurs, sélectionnables sans toucher au code (EMBEDDING_PROVIDER) :

- « local »   : modèle multilingue exécuté sur la machine via fastembed (ONNX).
                Gratuit, sans limite de débit, fonctionne hors ligne une fois
                le modèle téléchargé. Choisi par défaut : les 12 730 événements
                dépassent largement les quotas gratuits de l'API Mistral.
- « mistral » : API mistral-embed (1024 dimensions), cohérente avec la stack
                imposée, mais payante et soumise à des limites de débit.
- « test »    : embeddings déterministes calculés par hachage de mots. Aucun
                modèle, aucun réseau : sert aux tests automatiques et à vérifier
                la chaîne complète hors ligne.
"""
from __future__ import annotations

import hashlib
import logging

from langchain_core.embeddings import Embeddings

from rag import config

logger = logging.getLogger(__name__)

PROVIDERS = ("local", "mistral", "test")


class HashingEmbeddings(Embeddings):
    """Embeddings déterministes : chaque mot est projeté par hachage, puis le
    vecteur est normalisé. Deux textes partageant des mots restent proches, ce
    qui suffit à tester la chaîne d'indexation et de recherche sans modèle."""

    dimension = 128

    def _vector(self, text: str) -> list[float]:
        vector = [0.0] * self.dimension
        for word in "".join(c.lower() if c.isalnum() else " " for c in text).split():
            vector[int(hashlib.md5(word.encode()).hexdigest(), 16) % self.dimension] += 1.0
        norm = sum(value * value for value in vector) ** 0.5
        return [value / norm for value in vector] if norm else vector

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        return [self._vector(text) for text in texts]

    def embed_query(self, text: str) -> list[float]:
        return self._vector(text)


def get_embeddings(provider: str | None = None, model: str | None = None) -> Embeddings:
    """Instancie le modèle d'embeddings demandé."""
    provider = (provider or config.EMBEDDING_PROVIDER).lower()

    if provider == "local":
        model = model or config.LOCAL_EMBEDDING_MODEL
        try:
            from langchain_community.embeddings.fastembed import FastEmbedEmbeddings
        except ImportError as exc:  # pragma: no cover
            raise RuntimeError("fastembed manquant : pip install -r requirements.txt") from exc
        logger.info("Embeddings locaux : %s (premier lancement = téléchargement du modèle)", model)
        options = {"model_name": model, "batch_size": config.EMBEDDING_BATCH_SIZE}
        if config.EMBEDDING_CACHE_DIR:
            # Emplacement fixe du modèle. Sans cela fastembed choisit un dossier
            # temporaire, et un conteneur retéléchargerait 220 Mo à chaque démarrage.
            options["cache_dir"] = config.EMBEDDING_CACHE_DIR
        return FastEmbedEmbeddings(**options)

    if provider == "test":
        return HashingEmbeddings()

    if provider == "mistral":
        model = model or config.MISTRAL_EMBEDDING_MODEL
        from langchain_mistralai import MistralAIEmbeddings

        if not config.mistral_api_key():
            raise RuntimeError("MISTRAL_API_KEY absente : renseignez-la dans .env")
        logger.info("Embeddings via l'API Mistral : %s", model)
        return MistralAIEmbeddings(model=model, max_retries=5)

    raise ValueError(f"Fournisseur inconnu : {provider!r}. Valeurs possibles : {PROVIDERS}")


def describe(provider: str | None = None, model: str | None = None) -> dict:
    """Informations tracées dans les métadonnées de l'index et le rapport."""
    provider = (provider or config.EMBEDDING_PROVIDER).lower()
    defaults = {"local": config.LOCAL_EMBEDDING_MODEL,
                "mistral": config.MISTRAL_EMBEDDING_MODEL,
                "test": "hachage-deterministe"}
    return {"provider": provider, "model": model or defaults.get(provider, "?")}
