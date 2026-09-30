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

## 6. Lancer les tests

```powershell
pytest
```

107 tests en une dizaine de secondes. La plupart tournent sans Internet, sans clé API et
sans modèle, parce que l'API Open Agenda, les embeddings et le modèle de langage sont
remplacés par des doublures.

```powershell
pytest -v tests\test_preprocess.py   # un seul fichier, en détail
pytest -k "doublon"                  # seulement les tests dont le nom contient "doublon"
```

Si `test_data_quality.py` affiche `skipped`, c'est normal : ces tests attendent les vraies
données produites par `fetch_data.py`.

---

## 7. Enregistrer et publier sur GitHub

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

## 8. L'enchaînement pour une démonstration

```powershell
.\venv\Scripts\Activate.ps1
pytest
python scripts\ask.py --retrieval-only "un concert de musique classique"
type data\processed\cleaning_report.json
```

Ce qu'on dit pendant que ça tourne :

1. **`pytest`** : « 107 tests, dont la plupart sans Internet ni clé API. »
2. **`ask.py`** : « Le premier résultat est un concert de musique de chambre alors que le
   mot *classique* n'apparaît pas dans la fiche. C'est la recherche par le sens. »
3. **le rapport** : « Voilà ce que le nettoyage a retiré, règle par règle. »

Rien dans cet enchaînement ne dépend d'Internet, donc aucune mauvaise surprise en direct.

---

## 9. Quand un message d'erreur apparaît

| Message | Ce que ça veut dire | Quoi faire |
|---|---|---|
| `(venv)` absent de la ligne | environnement non activé | relancer la commande d'activation |
| `MISTRAL_API_KEY absente` | pas de fichier `.env` | copier `.env.example` en `.env` et y mettre la clé |
| `401 Invalid API Key` | la clé est fausse ou incomplète | en créer une nouvelle sur console.mistral.ai |
| `429 Rate limit exceeded` | quota Mistral atteint | attendre, ou activer le compte ; la recherche seule reste disponible |
| `No module named ...` | bibliothèque manquante ou mauvaise version | `pip install -r requirements.txt` dans le venv |
| `Aucun index dans data/index` | index pas encore construit | lancer `build_index.py` |
| `Échec de l'appel à l'API OpenAgenda` | pas de connexion | vérifier Internet, ou utiliser `--source csv` |
