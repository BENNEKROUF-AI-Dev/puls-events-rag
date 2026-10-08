# Commandes du projet

Toutes les commandes, dans l'ordre où on s'en sert, avec ce que fait chacune.
Écrites pour PowerShell sous Windows.

---

## 1. Avant toute chose

Ouvre le dossier `puls-events-rag` dans l'Explorateur, clique dans la barre d'adresse,
tape `powershell` et valide : la fenêtre s'ouvre déjà au bon endroit.

```powershell
.\venv\Scripts\Activate.ps1
```

Active l'environnement virtuel. Ta ligne doit maintenant commencer par `(venv)` en vert :
c'est le signe que Python utilise les bibliothèques du projet et pas celles du système.
**À refaire à chaque nouvelle fenêtre PowerShell.**

Si PowerShell refuse en parlant de scripts désactivés, lance une seule fois
`Set-ExecutionPolicy -Scope CurrentUser RemoteSigned`, réponds `O`, puis réessaie.

---

## 2. Vérifier que tout est en place

```powershell
python scripts\check_env.py
python scripts\check_env.py --online
```

Six contrôles : version de Python, Faiss, LangChain avec sauvegarde et rechargement d'un
index, imports Mistral, autres bibliothèques, présence de la clé API. Avec `--online`,
un vrai appel à Mistral est ajouté.

Tout doit afficher `[OK]`. C'est la commande à lancer en premier quand quelque chose
ne marche plus : elle dit tout de suite si le problème vient de l'installation.

---

## 3. Récupérer et nettoyer les données

```powershell
python scripts\fetch_data.py
```

Interroge l'API Open Agenda, applique les douze règles de nettoyage et écrit deux fichiers :
`data\processed\events.jsonl` (un événement propre par ligne) et
`data\processed\cleaning_report.json` (ce que chaque règle a retiré).

L'affichage montre le nombre d'événements restants après chaque règle. C'est ce tableau
qu'on commente en soutenance : 15 925 fiches brutes, 12 730 exploitables.

Variantes utiles :

```powershell
python scripts\fetch_data.py --source csv          # hors ligne, depuis un export CSV
python scripts\fetch_data.py --department Gironde  # changer de zone
python scripts\fetch_data.py --reference-date 2026-09-24   # figer la date du jour
```

La dernière sert à obtenir exactement le même résultat d'une exécution à l'autre,
ce qui est indispensable pour comparer deux traitements.

---

## 4. Construire la base vectorielle

```powershell
python scripts\build_index.py
```

Transforme chaque événement en fiche texte, découpe les descriptions longues, calcule les
vecteurs et écrit l'index Faiss dans `data\index\`. Compte une vingtaine de minutes pour
14 815 documents. Le premier lancement télécharge le modèle (220 Mo), ensuite tout est
hors ligne.

À la fin, le script affiche la fiche technique de l'index et joue trois questions de
contrôle. Si les réponses sont absurdes, l'index a un problème.

Variantes :

```powershell
python scripts\build_index.py --provider test --limit 200   # essai en 3 secondes, sans modèle
python scripts\build_index.py --provider mistral            # via l'API mistral-embed
```

Le mode `test` utilise des vecteurs calculés par hachage de mots. Il ne sert pas à produire
un vrai index, mais à vérifier que toute la chaîne fonctionne sans rien télécharger.

---

## 5. Poser des questions

```powershell
python scripts\ask.py --retrieval-only "un concert de musique classique"
```

Montre uniquement les événements retrouvés, avec leur score de similarité. **Aucun appel à
Mistral**, donc gratuit, instantané et sans risque de quota. C'est la commande à utiliser
pour une démonstration.

```powershell
python scripts\ask.py "quels concerts de musique classique à venir ?"
```

La chaîne complète : recherche, puis rédaction de la réponse par Mistral avec les sources
citées. Si le quota est atteint, le système bascule en mode dégradé et liste quand même
les événements trouvés.

```powershell
python scripts\ask.py                      # mode interactif, une question après l'autre
python scripts\ask.py --all-dates "..."    # inclure les événements déjà passés
```

Par défaut, seuls les événements à venir sont proposés : 2 207 sur 12 730.

---

## 6. Lancer l'API REST

```powershell
python scripts\serve.py
```

Démarre le serveur sur `http://127.0.0.1:8000`. Laisse ce terminal ouvert : il affiche
chaque requête reçue. `Ctrl+C` pour arrêter.

Ouvre ensuite **http://127.0.0.1:8000/docs** dans le navigateur : FastAPI y génère une
documentation interactive à partir du code. On peut y poser une question sans écrire une
seule ligne de client, bouton « Try it out ». C'est ce qu'on montre en soutenance.

```powershell
python scripts\serve.py --reload        # recharge le code à chaque modification
python scripts\serve.py --port 9000     # si le port 8000 est déjà pris
```

### Les trois points d'entrée

| Appel | Ce qu'il fait |
|---|---|
| `GET /health` | l'index est-il en place, quel modèle, combien de vecteurs. Répond `200` avec index, `503` sans — c'est ce code que lit la sonde Docker |
| `POST /ask` | une question → une réponse rédigée + les sources citées |
| `POST /rebuild` | retélécharge, renettoie et reconstruit l'index (jeton requis) |

### Les tester depuis un second terminal

```powershell
python scripts\api_test.py
```

Interroge le serveur comme le ferait un vrai client : état, cinq questions, refus des
demandes invalides, protection de `/rebuild`. Affiche un décompte de vérifications
réussies. **À lancer dans un autre terminal**, celui du serveur étant occupé.

```powershell
python scripts\api_test.py --json     # affiche aussi une réponse JSON complète
```

À la main, une question :

```powershell
curl.exe -X POST http://127.0.0.1:8000/ask -H "Content-Type: application/json" -d "{\"question\": \"un concert de jazz\"}"
```

Sous PowerShell, `curl.exe` (avec le `.exe`) plutôt que `curl` : sans l'extension,
PowerShell appelle son propre `Invoke-WebRequest`, dont les options sont différentes.

### Reconstruire l'index par l'API

```powershell
python scripts\api_test.py             # vérifie d'abord que le jeton protège bien
```

La reconstruction demande un jeton, défini par `REBUILD_TOKEN` dans `.env`. Sans ce jeton,
l'endpoint est **désactivé** — c'est voulu : rien d'exposé par accident.

```powershell
curl.exe -X POST http://127.0.0.1:8000/rebuild -H "X-Rebuild-Token: mon-jeton" -H "Content-Type: application/json" -d "{\"source\": \"api\"}"
```

La réponse arrive tout de suite (`202`) : la reconstruction tourne en arrière-plan, et
l'avancement se suit sur `GET /health`, champ `reconstruction`. **L'ancien index continue
de répondre pendant tout ce temps**, et reste en place si la reconstruction échoue.

---

## 7. Évaluer le système

Deux évaluations séparées, parce qu'un RAG peut se tromper à deux endroits : la recherche
peut rater le bon événement, ou la rédaction peut trahir les fiches qu'on lui a fournies.

### Étape 1 — annoter à la main ce qui est pertinent

```powershell
python scripts\evaluate.py --annotate
```

Affiche, pour chacune des 20 questions de `eval/questions.json`, les événements retrouvés,
et demande lesquels sont pertinents. On tape les numéros (`1 3 4`), `0` s'il n'y en a aucun,
Entrée pour accepter la proposition marquée `*`, `s` pour passer, `q` pour arrêter.

Compte une vingtaine de minutes. C'est du travail manuel, et c'est normal : ces annotations
sont la **vérité terrain**, et aucun calcul ne peut la remplacer sans évaluer la recherche
sémantique par une recherche par mots-clés. Le résultat va dans `eval/annotations.json`,
qui est versionné.

### Étape 2 — noter la recherche (aucun appel à Mistral)

```powershell
python scripts\evaluate.py
```

Affiche **précision@5**, **rappel@5**, **MRR** et le taux de réussite, plus la liste des
questions sans aucun résultat pertinent. Gratuit et instantané : c'est la partie mesurable
même quand le quota Mistral est épuisé.

Ce que disent les trois chiffres :

| Métrique | La question à laquelle elle répond |
|---|---|
| précision@5 | parmi les 5 événements proposés, combien sont pertinents ? (le bruit) |
| rappel@5 | parmi les événements pertinents, combien ont été retrouvés ? (les oublis) |
| MRR | à quelle position sort le premier bon résultat ? 1,0 = toujours en tête |

### Étape 3 — noter la rédaction (Ragas, nécessite Mistral)

```powershell
python scripts\evaluate.py --ragas --limit 5
```

Fait juger les réponses par un LLM sur trois critères : **faithfulness** (la réponse
respecte-t-elle les fiches, ou invente-t-elle ?), **answer relevancy** (répond-elle
vraiment à la question ?), **context precision** (les fiches fournies étaient-elles
utiles ?).

C'est *faithfulness* qui aurait détecté automatiquement la date inventée du premier essai
(voir `docs/journal.md`). Attention : chaque question coûte plusieurs appels au LLM, d'où
`--limit` pour commencer petit.

Le rapport complet est écrit dans `eval/resultats.json`.

---

## 8. Lancer les tests

```powershell
pytest
```

162 tests en une dizaine de secondes. La plupart tournent sans Internet, sans clé API et
sans modèle, parce que l'API Open Agenda, les embeddings et le modèle de langage sont
remplacés par des doublures.

```powershell
pytest -v tests\test_preprocess.py   # un seul fichier, en détail
pytest -k "doublon"                  # seulement les tests dont le nom contient "doublon"
```

Si `test_data_quality.py` affiche `skipped`, c'est normal : ces tests attendent les vraies
données produites par `fetch_data.py`.

---

## 9. Enregistrer et publier sur GitHub

```powershell
git status
```

Montre ce qui a changé. Les fichiers en rouge ne sont pas encore enregistrés.
**Vérifie toujours qu'on n'y voit ni `.env` ni `venv/`.**

```powershell
git add .
git commit -m "Message decrivant ce qui a change"
git push
```

`add` sélectionne les fichiers, `commit` les enregistre en local avec un message, `push`
les envoie sur GitHub. Tant que le `push` n'est pas fait, rien n'est en ligne.

```powershell
git log --oneline      # l'historique des commits
```

Les avertissements sur `LF will be replaced by CRLF` sont sans conséquence : c'est Git qui
adapte les fins de ligne entre Windows et Linux.

---

## 10. L'enchaînement pour une démonstration

**Premier terminal :**

```powershell
.\venv\Scripts\Activate.ps1
pytest
python scripts\ask.py --retrieval-only "un concert de musique classique"
type data\processed\cleaning_report.json
python scripts\serve.py
```

**Second terminal** (le premier est occupé par le serveur) :

```powershell
.\venv\Scripts\Activate.ps1
python scripts\api_test.py
python scripts\evaluate.py
```

Puis le navigateur sur **http://127.0.0.1:8000/docs**, et une question depuis
« Try it out ».

Ce qu'on dit pendant que ça tourne :

1. **`pytest`** : « 162 tests, dont la plupart sans Internet ni clé API. »
2. **`ask.py`** : « Le premier résultat est un concert de musique de chambre alors que le
   mot *classique* n'apparaît pas dans la fiche. C'est la recherche par le sens. »
3. **le rapport de nettoyage** : « Voilà ce que le nettoyage a retiré, règle par règle. »
4. **`/docs`** : « La documentation est générée depuis le code : elle ne peut pas être
   périmée. »
5. **`api_test.py`** : « Le même système vu de l'extérieur, en HTTP, sur le vrai index. »
6. **`evaluate.py`** : « Et voilà ce que vaut la recherche, mesuré sur vingt questions
   annotées à la main. »

Rien dans cet enchaînement ne dépend d'Internet — sauf la rédaction par Mistral, qui a son
mode dégradé. Aucune mauvaise surprise en direct.

---

## 11. Lancer l'API dans Docker

Docker Desktop doit être installé et démarré (l'icône baleine dans la barre des tâches).

### Construire l'image

```powershell
docker build -t puls-events .
```

Comptez cinq à dix minutes la première fois : Docker installe les dépendances et
télécharge le modèle d'embeddings **à l'intérieur de l'image**. C'est long une fois, puis
le conteneur démarre sans réseau.

Les reconstructions suivantes sont rapides tant que `requirements.txt` ne change pas :
Docker réutilise la couche des dépendances.

### Démarrer

```powershell
docker run -p 8000:8000 --env-file .env -v "${PWD}/data:/app/data" puls-events
```

Puis **http://127.0.0.1:8000/docs**, exactement comme en local.

Les trois options comptent :

| Option | Ce qu'elle fait |
|---|---|
| `-p 8000:8000` | relie le port 8000 de ta machine à celui du conteneur |
| `--env-file .env` | passe la clé Mistral au démarrage, sans jamais l'écrire dans l'image |
| `-v "${PWD}/data:/app/data"` | partage l'index : le conteneur le lit depuis ton disque |

### Ou en une seule commande

```powershell
docker compose up --build
```

`docker-compose.yml` décrit les trois options ci-dessus, il n'y a plus rien à retenir.
`Ctrl+C` pour arrêter, puis `docker compose down` pour nettoyer.

### Vérifier que le conteneur est sain

```powershell
docker ps
```

La colonne STATUS affiche `healthy` en quelques secondes. C'est la sonde définie dans le
Dockerfile : Docker interroge `/health` toutes les 30 secondes, après un délai de grâce de
15 secondes au démarrage (l'API répond en 1,6 s, l'index étant chargé à la première
question et non au boot).

`healthy` signifie que l'index est en place. Sans index monté, `/health` renvoie `503` et le
conteneur passe `unhealthy` : c'est voulu, un conteneur qui ne peut répondre à aucune
question ne doit pas s'annoncer prêt. En revanche un quota Mistral épuisé ne le rend **pas**
`unhealthy` — la sonde ne contacte jamais Mistral, et `/ask` bascule en mode dégradé.

```powershell
docker logs puls-events-api          # ce que le serveur affiche
docker logs -f puls-events-api       # en continu
```

Le test complet fonctionne depuis l'hôte, le conteneur étant exposé sur le même port :

```powershell
python scripts\api_test.py
```

### Lancer les tests dans le conteneur

```powershell
docker run --rm puls-events pytest
```

Prouve que l'image contient un environnement complet et fonctionnel, indépendamment de ta
machine. C'est le vrai argument de Docker : « ça marche chez moi » devient vérifiable.

### Ce que l'image contient, et ce qu'elle ne contient pas

| Dans l'image | Monté depuis l'hôte |
|---|---|
| Python, les dépendances, le code | l'index Faiss et les données nettoyées |
| Le modèle d'embeddings (220 Mo) | la clé API, passée par `--env-file` |

L'index est exclu volontairement : c'est une donnée reconstructible de plusieurs dizaines de
mégaoctets, qui change à chaque rafraîchissement. L'embarquer obligerait à reconstruire
l'image à chaque mise à jour des données.

---

## 12. Quand un message d'erreur apparaît

| Message | Ce que ça veut dire | Quoi faire |
|---|---|---|
| `(venv)` absent de la ligne | environnement non activé | relancer la commande d'activation |
| `MISTRAL_API_KEY absente` | pas de fichier `.env` | copier `.env.example` en `.env` et y mettre la clé |
| `401 Invalid API Key` | la clé est fausse ou incomplète | en créer une nouvelle sur console.mistral.ai |
| `429 Rate limit exceeded` | quota Mistral atteint | attendre, ou activer le compte ; la recherche seule reste disponible |
| `No module named ...` | bibliothèque manquante ou mauvaise version | `pip install -r requirements.txt` dans le venv |
| `Aucun index dans data/index` | index pas encore construit | lancer `build_index.py` |
| `Échec de l'appel à l'API OpenAgenda` | pas de connexion | vérifier Internet, ou utiliser `--source csv` |
| `403` sur `/rebuild` | `REBUILD_TOKEN` absent de `.env` | l'ajouter, puis redémarrer le serveur |
| `401` sur `/rebuild` | le jeton envoyé ne correspond pas | vérifier l'en-tête `X-Rebuild-Token` |
| `409` sur `/rebuild` | une reconstruction tourne déjà | suivre son avancement sur `/health` |
| `503` sur `/ask` | aucun index chargé | lancer `build_index.py`, ou `POST /rebuild` |
| `Aucun serveur sur http://...` | le serveur n'est pas lancé | `python scripts\serve.py` dans un autre terminal |
| `address already in use` | le port 8000 est occupé | `python scripts\serve.py --port 9000` |
| `Aucune annotation` | jeu d'évaluation pas encore annoté | `python scripts\evaluate.py --annotate` |
| `Cannot connect to the Docker daemon` | Docker Desktop n'est pas lancé | l'ouvrir et attendre que la baleine se stabilise |
| `port is already allocated` | le port 8000 est pris par un autre conteneur | `docker ps` puis `docker stop <nom>`, ou changer `-p 9000:8000` |
| conteneur `unhealthy` dans `docker ps` | l'API ne répond pas sur `/health` | `docker logs` ; le plus souvent l'index n'est pas monté |
| `Aucun index dans /app/data/index` | le volume n'a pas été monté, ou l'index n'existe pas | vérifier l'option `-v`, et avoir lancé `build_index.py` avant |
