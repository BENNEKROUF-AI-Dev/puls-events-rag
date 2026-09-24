# Puls-Events : assistant RAG de recommandation d'événements culturels

POC d'un chatbot qui répond aux questions des utilisateurs sur les événements culturels
en combinant une recherche vectorielle (**Faiss**) et la génération de réponses par un LLM
(**Mistral**), orchestrées avec **LangChain** et exposées via une API **FastAPI**.

> Statut : étapes 1 (environnement) et 2 (données) terminées. Les sections marquées *(à venir)* seront complétées au fil du projet.

**Périmètre du POC :** événements culturels du département de la **Loire-Atlantique** (Nantes, Saint-Nazaire…),
terminés depuis moins d'un an ou à venir. Source : jeu public OpenAgenda (API Opendatasoft).

## Objectifs

- Récupérer les événements OpenAgenda d'une zone géographique, sur une fenêtre d'un an.
- Les nettoyer, les découper en chunks et les indexer dans une base vectorielle Faiss.
- Générer des réponses augmentées avec un LLM Mistral à partir des événements retrouvés.
- Exposer le système via une API REST (`/ask`, `/rebuild`) conteneurisée avec Docker.
- Évaluer la qualité des réponses sur un jeu de test annoté (Ragas).

## Structure du projet

```
puls-events-rag/
├── api/                    # API FastAPI (à venir)
├── src/rag/                # logique métier, importée par les scripts et l'API
│   ├── config.py           # tous les paramètres (zone, fenêtre, filtres, chemins)
│   ├── fetch_events.py     # récupération OpenAgenda (API ou export CSV)
│   ├── preprocess.py       # nettoyage et structuration des événements
│   ├── chunking.py         # événements -> documents à vectoriser
│   ├── embeddings.py       # choix du modèle d'embeddings (local / mistral / test)
│   ├── indexer.py          # construction, sauvegarde et interrogation de l'index Faiss
│   └── chain.py            # le chatbot : recherche + prompt + génération Mistral
├── scripts/
│   ├── check_env.py        # vérification de l'installation
│   ├── fetch_data.py       # récupère + nettoie + sauvegarde les événements
│   ├── build_index.py      # construit la base vectorielle Faiss
│   └── ask.py              # pose une question au chatbot depuis le terminal
├── tests/
│   ├── fixtures/           # petits jeux de données de test (15 événements fictifs)
│   ├── test_preprocess.py  # règles de nettoyage
│   ├── test_fetch_events.py# récupération (API simulée, CSV)
│   ├── test_chunking.py    # découpage et métadonnées
│   ├── test_indexer.py     # index Faiss et recherche
│   ├── test_chain.py       # chaîne RAG (LLM simulé)
│   └── test_data_quality.py# contrôles sur les vraies données produites
├── data/                   # non versionné, reconstruit par les scripts
│   ├── raw/                # données brutes (réponse API ou export CSV)
│   ├── processed/          # events.jsonl + cleaning_report.json
│   └── index/              # index Faiss + index_info.json
├── docs/
│   └── journal.md          # décisions et anomalies (matière du rapport)
├── .env.example            # modèle de configuration
├── pytest.ini
├── requirements.txt        # dépendances aux versions figées
└── README.md
```

## Installation

**Prérequis :** Python 3.11 ou 3.12 (pandas 3 exige Python ≥ 3.11), git, une clé API
Mistral ([console.mistral.ai](https://console.mistral.ai)).

```bash
# 1. Cloner le dépôt
git clone <url-du-depot> && cd puls-events-rag

# 2. Créer et activer l'environnement virtuel (non versionné)
python -m venv venv
source venv/bin/activate          # Windows : venv\Scripts\activate

# 3. Installer les dépendances
pip install --upgrade pip
pip install -r requirements.txt

# 4. Configurer les secrets
cp .env.example .env              # Windows : copy .env.example .env
# puis éditer .env et renseigner MISTRAL_API_KEY

# 5. Vérifier l'installation
python scripts/check_env.py            # tests hors ligne
python scripts/check_env.py --online   # + appel réel à Mistral
```

Tous les tests doivent afficher `[OK]`.

## Choix d'environnement et compatibilités

Les versions sont figées dans `requirements.txt` car l'écosystème LLM évolue très vite.
Deux contraintes ont été identifiées en testant l'installation :

| Contrainte | Raison |
|---|---|
| `langchain-community==0.3.31` (et non 0.4) | `ragas` 0.4.3 importe un module retiré de `langchain-community` 0.4 |
| `mistralai==1.12.4` (et non 2.x) | `instructor`, dépendance de `ragas`, ne fonctionne pas avec le SDK `mistralai` 2.x |

Autres choix :

- **`faiss-cpu`** plutôt que `faiss-gpu`, pour la portabilité (machines sans GPU, conteneur Docker).
- **Imports à jour** : les imports suggérés dans l'énoncé sont obsolètes. On utilise :
  ```python
  import faiss
  from langchain_community.vectorstores import FAISS
  from langchain_mistralai import MistralAIEmbeddings, ChatMistralAI
  from mistralai import Mistral
  ```
- **Secrets** : la clé API est lue depuis `.env` (via `python-dotenv`), fichier exclu par `.gitignore`.

## Utilisation

### 1. Récupérer et nettoyer les données

```bash
python scripts/fetch_data.py                  # via l'API OpenAgenda (Internet requis)
python scripts/fetch_data.py --source csv     # via l'export CSV placé dans data/raw/
```

Options utiles : `--department Gironde`, `--reference-date 2026-09-19` (date « aujourd'hui » fixe,
pour obtenir exactement le même résultat d'une exécution à l'autre).

Le script affiche ce que chaque règle de nettoyage a retiré et écrit :

- `data/processed/events.jsonl` : un événement propre par ligne ;
- `data/processed/cleaning_report.json` : le rapport chiffré du nettoyage.

La source par défaut est l'API : rien à télécharger. Le mode `--source csv` sert de secours hors ligne ;
il faut alors télécharger l'export sur le [portail OpenAgenda d'Opendatasoft](https://public.opendatasoft.com/explore/dataset/evenements-publics-openagenda/)
(onglet Export, format CSV) et l'enregistrer sous `data/raw/evenements-publics-openagenda.csv`.
Attention : cet export peut n'être qu'un échantillon (4 296 événements contre 15 925 via l'API).

Résultat au 23/09/2026 via l'API : **12 730 événements** en Loire-Atlantique, dont 2 207 à venir.
Le détail des filtres et des anomalies corrigées est dans [`docs/journal.md`](docs/journal.md).

### 2. Construire la base vectorielle

```bash
python scripts/build_index.py                    # embeddings locaux (défaut)
python scripts/build_index.py --provider mistral # via l'API mistral-embed
python scripts/build_index.py --provider test --limit 200   # essai instantané, sans modèle
```

Le premier lancement en mode local télécharge le modèle (~220 Mo), puis tout se passe
hors ligne. Comptez quelques minutes pour l'ensemble des événements.

Le script écrit `data/index/` (index Faiss, documents et `index_info.json`), affiche la
fiche technique de l'index et joue trois questions de contrôle pour vérifier que la
recherche renvoie des résultats cohérents.

Trois fournisseurs d'embeddings sont disponibles :

| Fournisseur | Modèle | Dimensions | Coût | Usage |
|---|---|---:|---|---|
| `local` (défaut) | paraphrase-multilingual-MiniLM-L12-v2 (ONNX) | 384 | gratuit | construction et démo, sans limite de débit |
| `mistral` | mistral-embed | 1024 | payant | comparaison, cohérence avec la stack imposée |
| `test` | hachage déterministe | 128 | gratuit | tests automatiques, vérification hors ligne |

### 3. Interroger le chatbot

```bash
python scripts/ask.py "quels concerts de jazz à Nantes ?"
python scripts/ask.py --retrieval-only "expositions pour enfants"   # sans appeler le LLM
python scripts/ask.py                                               # mode interactif
```

Par défaut, seuls les **événements à venir** sont proposés (2 207 sur 12 730) :
recommander une sortie déjà passée n'aurait aucun sens. `--all-dates` lève ce filtre.

La logique est dans la classe `RAGService` (`src/rag/chain.py`), qui expose
`ask()`, `retrieve()` et `health()`. L'API de l'étape suivante ne fera que l'importer.

### 4. API, Docker *(à venir)*

## Tests

```bash
pytest            # tous les tests
pytest -v tests/test_preprocess.py
```

- `test_preprocess.py`, `test_fetch_events.py`, `test_chunking.py` et `test_indexer.py`
  tournent sans Internet, sans clé API et sans modèle : l'API OpenAgenda est simulée et
  les embeddings sont déterministes.
- `test_data_quality.py` vérifie les vraies données produites par `fetch_data.py`
  (zone, période, doublons, champs vides, HTML) ; il est ignoré tant qu'elles n'existent pas.

## Évaluation *(à venir)*

## Dépannage

| Symptôme | Solution |
|---|---|
| `No module named 'mistralai.async_client'` | Mauvaise version de `mistralai` : `pip install -r requirements.txt` dans un venv propre |
| `No module named 'langchain_community.chat_models.vertexai'` | `langchain-community` trop récent : même solution |
| `MISTRAL_API_KEY absente` | Créer `.env` à partir de `.env.example` |
| `DeprecationWarning: langchain-community is being sunset` | Avertissement sans effet sur le POC |
| `Échec de l'appel à l'API OpenAgenda` | Vérifier la connexion, ou utiliser `--source csv` |
| `test_data_quality.py` affiche `skipped` | Normal tant que `python scripts/fetch_data.py` n'a pas été lancé |
