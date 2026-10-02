# Puls-Events : assistant RAG pour les événements culturels

Projet 7 du parcours Data Scientist. POC d'un chatbot qui répond à des questions sur les
événements culturels de Loire-Atlantique, à partir des données Open Agenda.

Recherche vectorielle avec Faiss, génération avec Mistral, le tout orchestré par LangChain.

Avancement : étapes 1 à 5 faites (environnement, données, index, chaîne RAG, API REST et
évaluation). Reste Docker et la démo.

## Installation

Il faut Python 3.11 ou 3.12 (pandas 3 ne s'installe pas en dessous), git, et une clé API
Mistral créée sur console.mistral.ai.

```bash
git clone https://github.com/BENNEKROUF-AI-Dev/puls-events-rag.git
cd puls-events-rag

python -m venv venv
source venv/bin/activate          # Windows : .\venv\Scripts\Activate.ps1

pip install -r requirements.txt

cp .env.example .env              # Windows : Copy-Item .env.example .env
# puis mettre sa clé dans MISTRAL_API_KEY

python scripts/check_env.py --online
```

Le dernier script vérifie que tout est en place : versions, Faiss, LangChain, clé API.
Tout doit afficher `[OK]`. Testé sur Windows 11 avec Python 3.12.10.

## Utilisation

**1. Récupérer les données**

```bash
python scripts/fetch_data.py
```

Interroge l'API Open Agenda et écrit `data/processed/events.jsonl` plus un rapport de
nettoyage chiffré. Au 24/09/2026 : 12 730 événements en Loire-Atlantique, dont 2 207 à venir.

Options : `--department Gironde` pour changer de zone, `--reference-date 2026-09-24` pour
figer la date du jour, `--source csv` pour travailler hors ligne depuis un export téléchargé
dans `data/raw/`.

**2. Construire l'index**

```bash
python scripts/build_index.py
```

Découpe les événements en documents, les vectorise et construit l'index Faiss dans
`data/index/`. Le premier lancement télécharge le modèle d'embeddings (220 Mo), ensuite tout
tourne hors ligne. Comptez une vingtaine de minutes pour les 14 815 documents.

Le script finit par poser trois questions de contrôle : si les résultats sont absurdes, c'est
que quelque chose ne va pas dans l'index.

Pour un essai rapide sans rien télécharger : `--provider test --limit 200`.

**3. Poser des questions**

```bash
python scripts/ask.py "quels concerts de jazz à Nantes ?"
python scripts/ask.py --retrieval-only "expositions pour enfants"   # sans appeler Mistral
python scripts/ask.py                                               # mode interactif
```

Par défaut seuls les événements à venir sont proposés. `--all-dates` enlève ce filtre.

**4. Lancer l'API**

```bash
python scripts/serve.py
```

Puis http://127.0.0.1:8000/docs pour la documentation interactive, générée par FastAPI
depuis le code — on peut y poser une question sans écrire de client.

| Appel | Ce qu'il fait |
|---|---|
| `GET /health` | état du service, fiche de l'index, avancement d'une reconstruction |
| `POST /ask` | une question → une réponse rédigée + les sources citées |
| `POST /rebuild` | retélécharge, renettoie et reconstruit l'index (jeton `X-Rebuild-Token`) |

`/rebuild` répond tout de suite (202) et travaille en arrière-plan : le nouvel index est
construit à côté puis mis en place par un renommage. L'ancien continue donc de répondre
pendant toute la reconstruction, et reste en place si elle échoue. Sans `REBUILD_TOKEN`
dans `.env`, l'endpoint est désactivé.

Pour vérifier un serveur qui tourne, depuis un autre terminal :

```bash
python scripts/api_test.py
```

**5. Évaluer**

```bash
python scripts/evaluate.py --annotate     # annoter à la main ce qui est pertinent
python scripts/evaluate.py                # noter la recherche : précision@k, rappel@k, MRR
python scripts/evaluate.py --ragas        # noter la rédaction : Ragas + Mistral
```

Deux évaluations séparées, parce qu'un RAG peut se tromper à deux endroits : la recherche
peut rater le bon événement, ou la rédaction peut trahir les fiches. La première se mesure
sans aucun modèle, ce qui permet de la noter même quand le quota Mistral est épuisé.

## Tests

```bash
pytest
```

146 tests, dont la plupart tournent sans Internet, sans clé API et sans modèle : l'API Open
Agenda est simulée, les embeddings sont remplacés par un calcul déterministe et le LLM par un
faux modèle. Seul `test_data_quality.py` a besoin des vraies données produites par
`fetch_data.py` ; il est ignoré tant qu'elles n'existent pas.

Les tests de l'API appellent l'application en mémoire, sans ouvrir de port : `TestClient` de
FastAPI, avec un index factice de quatre événements. `scripts/api_test.py` fait l'inverse —
un vrai serveur HTTP sur le vrai index — et sert de vérification de bout en bout.

## Structure

```
src/rag/          config, collecte, nettoyage, découpage, embeddings, index,
                  chaîne RAG, pipeline de rafraîchissement, API, métriques
scripts/          check_env, fetch_data, build_index, ask, serve, api_test, evaluate
tests/            tests unitaires + jeu de test fictif dans fixtures/
eval/             les 20 questions d'évaluation et les annotations humaines
data/             données et index, non versionnés (tout se reconstruit)
docs/journal.md   mes choix, les anomalies trouvées et les corrections
docs/commandes.md toutes les commandes, avec ce qu'elles font
```

Aucune règle métier ne vit dans `api.py` : il traduit du HTTP vers `chain.py` et retour.
Même principe pour les scripts, qui ne sont que des interfaces en ligne de commande
au-dessus du paquet. C'est ce qui rend tout testable sans serveur.

## Quelques choix à expliquer

**Pourquoi la Loire-Atlantique.** Le Nord avait plus d'événements, mais 42 % venaient d'un
agenda France Travail (ateliers de recherche d'emploi). Une fois ce bruit retiré, la
Loire-Atlantique en garde le plus, et sur un territoire plus compact.

**Pourquoi des versions figées.** J'ai perdu du temps là-dessus : Ragas ne marche pas avec
`langchain-community` 0.4 ni avec le SDK `mistralai` 2.x. D'où `requirements.txt` avec des
versions exactes. Les imports donnés dans l'énoncé n'existent plus non plus.

**Pourquoi des embeddings locaux par défaut.** Mon compte Mistral gratuit renvoie des erreurs
429 dès le premier appel, et il y avait 14 815 documents à vectoriser. Le modèle local est
gratuit, sans limite, et marche hors ligne pour la démo. `mistral-embed` reste activable avec
`EMBEDDING_PROVIDER=mistral`, pour comparer les deux. La génération des réponses, elle, passe
bien par Mistral.

**Pourquoi un index plat.** `IndexFlatIP` sur vecteurs normalisés, donc similarité cosinus. À
15 000 vecteurs la recherche exhaustive prend quelques millisecondes, aucune raison d'utiliser
un index approché type IVF ou HNSW qui rate parfois des résultats. Ce serait utile à partir de
centaines de milliers de vecteurs.

**Pourquoi le MRR en plus de la précision.** Précision et rappel ne disent pas *où* le bon
résultat apparaît. Deux recherches de précision identique n'ont pas la même valeur selon que
l'événement pertinent sort en première ou en cinquième position, puisqu'on lit les premières
propositions et rarement les suivantes.

Le détail est dans `docs/journal.md`.

## Problèmes courants

| Message | Que faire |
|---|---|
| `No module named 'mistralai.async_client'` | mauvaise version, réinstaller depuis `requirements.txt` dans un venv propre |
| `MISTRAL_API_KEY absente` | créer `.env` à partir de `.env.example` |
| `429 Rate limit exceeded` | quota Mistral atteint ; le système bascule en mode dégradé et liste quand même les événements trouvés |
| `Échec de l'appel à l'API OpenAgenda` | vérifier la connexion, ou passer par `--source csv` |
| `test_data_quality.py` en `skipped` | normal tant que `fetch_data.py` n'a pas été lancé |

## À faire

- [x] API REST FastAPI (`/ask`, `/rebuild`, `/health`) + Swagger
- [x] Jeu de test annoté et évaluation avec Ragas
- [ ] Dockerfile et démo locale
- [ ] Rapport technique et slides de soutenance
