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
│   └── preprocess.py       # nettoyage et structuration des événements
├── scripts/
│   ├── check_env.py        # vérification de l'installation
│   └── fetch_data.py       # récupère + nettoie + sauvegarde les événements
├── tests/
│   ├── fixtures/           # petits jeux de données de test (15 événements fictifs)
│   ├── test_preprocess.py  # règles de nettoyage
│   ├── test_fetch_events.py# récupération (API simulée, CSV)
│   └── test_data_quality.py# contrôles sur les vraies données produites
├── data/                   # non versionné, reconstruit par les scripts
│   ├── raw/                # données brutes (réponse API ou export CSV)
│   └── processed/          # events.jsonl + cleaning_report.json
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

Pour l'export CSV : le télécharger sur le [portail OpenAgenda d'Opendatasoft](https://public.opendatasoft.com/explore/dataset/evenements-publics-openagenda/)
(onglet Export, format CSV) et l'enregistrer sous `data/raw/evenements-publics-openagenda.csv`.

Résultat au 19/09/2026 : **1 061 événements** en Loire-Atlantique, dont 244 à venir.
Le détail des filtres et des anomalies corrigées est dans [`docs/journal.md`](docs/journal.md).

### 2. Construire l'index, lancer l'API, Docker *(à venir)*

## Tests

```bash
pytest            # tous les tests
pytest -v tests/test_preprocess.py
```

- `test_preprocess.py` et `test_fetch_events.py` tournent sans Internet ni clé API
  (l'API OpenAgenda est simulée).
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
