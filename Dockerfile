# Image de l'API Puls-Events.
#
# Construction en deux étapes. La première installe les dépendances et télécharge
# le modèle d'embeddings ; la seconde ne récupère que le résultat. Les outils de
# compilation, eux, restent dans la première et n'alourdissent pas l'image livrée.
#
#   docker build -t puls-events .
#   docker run -p 8000:8000 --env-file .env -v "${PWD}/data:/app/data" puls-events
#
# L'index n'est PAS dans l'image : il est monté depuis l'hôte. Ce sont des données
# reconstructibles de plusieurs dizaines de mégaoctets, qui changent à chaque
# rafraîchissement — les figer dans l'image obligerait à la rebâtir à chaque fois.
# Le modèle d'embeddings, lui, est embarqué : il ne change jamais, et sans lui le
# conteneur aurait besoin du réseau à son premier démarrage.

# ---------------------------------------------------------------- étape 1 : build
FROM python:3.12-slim AS build

ENV PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

# build-essential : faiss-cpu et quelques dépendances compilent des extensions C.
RUN apt-get update \
 && apt-get install -y --no-install-recommends build-essential \
 && rm -rf /var/lib/apt/lists/*

RUN python -m venv /opt/venv
ENV PATH="/opt/venv/bin:$PATH"

# Les dépendances d'abord, le code ensuite : tant que requirements.txt ne bouge
# pas, Docker réutilise cette couche et la reconstruction prend quelques secondes.
COPY requirements.txt .
RUN pip install -r requirements.txt

# Le modèle d'embeddings, téléchargé une fois ici plutôt qu'à chaque démarrage.
# C'est la seule étape qui demande un accès à huggingface.co.
ENV EMBEDDING_CACHE_DIR=/opt/models
RUN python -c "\
from fastembed import TextEmbedding; \
TextEmbedding(model_name='sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2', \
              cache_dir='/opt/models')" \
 && du -sh /opt/models

# -------------------------------------------------------------- étape 2 : runtime
FROM python:3.12-slim AS runtime

LABEL org.opencontainers.image.title="Puls-Events RAG" \
      org.opencontainers.image.description="Assistant culturel : recherche vectorielle Faiss et rédaction Mistral" \
      org.opencontainers.image.source="https://github.com/BENNEKROUF-AI-Dev/puls-events-rag"

COPY --from=build /opt/venv /opt/venv
COPY --from=build /opt/models /opt/models

ENV PATH="/opt/venv/bin:$PATH" \
    PYTHONPATH=/app/src \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    EMBEDDING_CACHE_DIR=/opt/models \
    HF_HUB_OFFLINE=1

WORKDIR /app
COPY src/ ./src/
COPY scripts/ ./scripts/
COPY eval/ ./eval/
COPY tests/ ./tests/
COPY pytest.ini ./

# Utilisateur sans privilèges : un conteneur n'a aucune raison de tourner en root.
RUN useradd --create-home --uid 1000 puls \
 && mkdir -p /app/data \
 && chown -R puls:puls /app
USER puls

EXPOSE 8000

# Sonde d'aptitude : Docker interroge /health et marque le conteneur « unhealthy »
# s'il ne répond plus 200. /health renvoie 503 quand l'index manque, donc un
# conteneur sans index est signalé malade au lieu d'être annoncé prêt à tort.
#
# 15 s de grâce au démarrage : l'index est chargé paresseusement, pas au boot, et
# /health répond en 1,6 s (mesuré) — le temps des imports. 15 s laissent dix fois
# la marge nécessaire sans retarder le diagnostic.
#
# La sonde ne dépend pas de Mistral : /health ne lit qu'un fichier local. Un quota
# d'API épuisé ne doit pas rendre le conteneur « unhealthy » — /ask passe alors en
# mode dégradé et continue de répondre.
HEALTHCHECK --interval=30s --timeout=5s --start-period=15s --retries=3 \
  CMD python -c "import httpx,sys; sys.exit(0 if httpx.get('http://127.0.0.1:8000/health', timeout=4).status_code == 200 else 1)"

# 0.0.0.0 et non 127.0.0.1 : à l'intérieur du conteneur, 127.0.0.1 ne désigne que
# le conteneur lui-même, et aucune requête venant de l'hôte n'arriverait jamais.
CMD ["uvicorn", "rag.api:app", "--host", "0.0.0.0", "--port", "8000"]
