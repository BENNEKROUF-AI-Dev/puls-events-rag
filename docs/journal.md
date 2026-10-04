# Journal de bord : choix et anomalies

Ce fichier garde la trace de chaque décision et de chaque problème rencontré.
Il sert de matière première au rapport technique (sections 1, 3, 4 et 5 du template).

---

## Étape 1 : environnement

- Python 3.11 / 3.12, dépendances figées dans `requirements.txt`.
- Deux incompatibilités trouvées en testant l'installation :
  - `ragas` 0.4.3 importe un module retiré de `langchain-community` 0.4 → on reste en `langchain-community` 0.3.31 ;
  - `instructor` (dépendance de `ragas`) ne fonctionne pas avec le SDK `mistralai` 2.x → on reste en `mistralai` 1.12.4.
- Les imports proposés dans l'énoncé sont obsolètes (`from mistral import MistralClient` n'existe plus).

---

## Étape 2 : récupération et nettoyage des données

### Source

- Jeu de données public **« Événements publics OpenAgenda »** sur le portail Opendatasoft (Huwise),
  API Explore v2.1, endpoint `/exports/json` (non paginé, contrairement à `/records`).
- Filtre envoyé à l'API (ODSQL) :
  `location_department = "Loire-Atlantique" AND lastdate_end >= date'<réf - 365 j>' AND firstdate_begin <= date'<réf + 365 j>'`
- Seuls 25 champs utiles sont demandés (`select`) : pas d'email ni de téléphone des contributeurs.
- L'export CSV du portail sert de solution de secours hors ligne (`--source csv`) et alimente les tests.

**Écart constaté entre les deux sources.** L'export CSV utilisé pendant l'exploration ne contenait que
4 296 événements pour la Loire-Atlantique, contre **15 925 renvoyés par l'API** sur la même fenêtre :
cet export n'était qu'un échantillon du jeu complet. Les chiffres ci-dessous sont ceux de l'API,
seule source de référence. Les deux chemins de code produisent le même format de sortie, ce qui a permis
de valider le nettoyage hors ligne avant de le confronter aux données réelles.

### Choix de la zone : la Loire-Atlantique

Le premier réflexe était de prendre le département le plus fourni (le Nord). Mais en regardant **d'où
viennent** les événements, une grande partie ne sont pas culturels : ce sont des ateliers de recherche
d'emploi publiés par l'agenda « Mes événements France Travail ».

| Département | Événements sur 1 an | dont non culturels | Événements culturels |
|---|---:|---:|---:|
| Loire-Atlantique | 1 336 | 249 (19 %) | **1 087** |
| Nord | 1 719 | 724 (42 %) | 995 |
| Gironde | 1 278 | 325 (25 %) | 953 |
| Paris | 898 | 196 (22 %) | 702 |

*(comparaison réalisée sur l'échantillon CSV : les valeurs absolues sont sous-estimées, mais les
proportions restent valables.)*

Trois échelles ont aussi été comparées avant de trancher, pour vérifier qu'un département était le
bon niveau de granularité : Metz seule n'offrait que 27 événements culturels sur un an, la Moselle 118,
et la région Grand Est 958 sur un territoire de 400 km de large. Un département dense est le meilleur
compromis entre volume et cohérence géographique.

→ La Loire-Atlantique garde le plus d'événements culturels avec le moins de bruit.
Le département se change en une ligne (`EVENTS_DEPARTMENT` dans `.env`).

### Résultat du nettoyage (API, date de référence : 23/09/2026)

| Étape | Retirés | Restants |
|---|---:|---:|
| Données brutes reçues de l'API | – | 15 925 |
| Hors zone, identifiant en double, dates invalides, hors fenêtre | 0 | 15 925 |
| Événement annulé | 151 | 15 774 |
| Événement uniquement en ligne | 14 | 15 760 |
| Agenda non culturel (France Travail, Geovelo) | 2 809 | 12 951 |
| Titre ou description manquants | 0 | 12 951 |
| Doublon (même titre, même lieu, même date) | 221 | **12 730** |

**12 730 événements** propres, dont **2 207 à venir**.

Les quatre premiers filtres ne retirent rien, et c'est le résultat attendu : l'API applique déjà la zone
et la fenêtre temporelle côté serveur. Ces règles restent en place car elles servent de contrat vérifié
par les tests, et elles sont indispensables quand la source est le CSV, qui n'est pas filtré.

### Anomalies rencontrées et corrections

| Anomalie | Ampleur (sur 15 925) | Correction |
|---|---|---|
| HTML dans la description longue | 15 644 (98 %) | Balises supprimées avec BeautifulSoup, paragraphes gardés en retours à la ligne |
| Entités HTML (`&nbsp;`, `&amp;`…) | 1 945 | Décodées (`html.unescape`) |
| Code pays vide ou mal saisi (`fr`, `33`) | 211 | Pas d'exclusion : le département fait foi ; seuls les vrais codes étrangers (`CH`…) sont exclus |
| Ville écrite de plusieurs façons (`NANTES`, `Le pouliguen`, `SUCE SUR ERDRE`) | 135 | Harmonisées vers l'orthographe la plus fréquente (comparaison sans casse, accents ni tirets) + table d'alias |
| Ville manquante | 8 | Complétée à partir du code postal |
| Fin d'événement avant son début (soirée finissant après minuit saisie sans changer de jour) | 114 dans l'export CSV complet, 0 ici | Corrigée (+1 jour) au lieu de supprimer l'événement |
| Champs « statut » et « mode » en JSON multilingue | tous | Libellé français extrait (`Programmé`, `Sur place`…) |
| Mots-clés absents | 4 603 (36 %) | Liste vide ; le titre et la description restent les sources principales |
| Colonne « Catégorie » | vide partout | Non utilisée |

**Exemple avant / après :**

```
AVANT : <p>Visites commentées&nbsp;: partez sur les traces de ce lieu tour à tour cinéma, supérette,
        salle de concert et désormais espace de travail et de recherche artistique.<br>Inscription
        obligatoire et uniquement à partir du 25 août.</p>
APRÈS : Visites commentées : partez sur les traces de ce lieu tour à tour cinéma, supérette,
        salle de concert et désormais espace de travail et de recherche artistique.
        Inscription obligatoire et uniquement à partir du 25 août.
```

### Autres décisions

- **Événements « Reporté » ou « Complet » conservés** : ils restent réels, le chatbot pourra le préciser.
- **Événements en cours depuis longtemps conservés** (ex. exposition commencée en 2024 et qui dure encore) :
  la règle porte sur la date de **fin**.
- **Format de sortie JSON Lines** (`events.jsonl`) : lisible, un événement par ligne, et garde les listes (mots-clés).
- **Date de référence paramétrable** (`--reference-date` ou `REFERENCE_DATE`) pour des traitements reproductibles.

### Limites notées pour la suite

- 266 descriptions dépassent 2 000 caractères → découpage en chunks nécessaire à l'étape 3.
- Le volume (12 730 événements) rend le débit d'embedding déterminant : l'offre gratuite Mistral renvoie
  des erreurs `429` (limite de débit). Deux stratégies seront comparées à l'étape 3, un modèle local
  gratuit et `mistral-embed`.
- `date_text` (« Mardi 17 février, 16h30 ») n'indique pas toujours l'année : utiliser `date_begin` / `date_end` dans le prompt.
- Les mots-clés ne sont pas harmonisés entre événements (« Concert » / « concert ») : sans effet sur les embeddings.

---

## Étape 3 : base vectorielle Faiss

### Du texte pour la recherche, pas pour l'affichage

Chaque événement devient une fiche structurée, car c'est ce texte qui est vectorisé :

```
Titre : Festival des arts de la rue
Date : du 2 octobre 2026 au 4 octobre 2026
Lieu : Centre-ville, Place du Commerce, 44000 Nantes
Ville : Nantes
Mots-clés : festival, cirque
Conditions : Gratuit
Description : Compagnies de cirque, fanfares et théâtre de rue...
```

Les libellés (`Titre :`, `Date :`, `Ville :`) aident le modèle d'embeddings à situer
l'information, et surtout ils rendent le texte directement utilisable par le LLM à l'étape 4 :
c'est ce bloc qui sera collé dans le prompt.

### Découpage : seulement quand c'est nécessaire

La médiane des descriptions est d'environ 500 caractères : découper systématiquement
n'apporterait rien et éparpillerait l'information d'un même événement.

- Taille de chunk : **1 200 caractères**, recouvrement **150**.
- Une fiche qui tient dans cette limite reste **entière** : un événement = un document.
- Seules les descriptions longues sont découpées, avec `RecursiveCharacterTextSplitter`.
- **Chaque morceau reporte l'en-tête** (titre, date, lieu, ville). Sans cela, le deuxième
  morceau d'une longue description serait un texte orphelin, impossible à rattacher à un
  événement au moment de répondre.

Résultat mesuré : **1,13 document par événement** — l'immense majorité des fiches ne sont pas
découpées.

### Choix du modèle d'embeddings

| Fournisseur | Modèle | Dimensions | Coût | Limite de débit |
|---|---|---:|---|---|
| `local` (défaut) | paraphrase-multilingual-MiniLM-L12-v2 (ONNX via fastembed) | 384 | gratuit | aucune |
| `mistral` | mistral-embed | 1024 | payant | oui (erreurs 429 constatées sur l'offre gratuite) |
| `test` | hachage déterministe | 128 | gratuit | aucune |

Le mode local est le choix par défaut pour trois raisons : le volume (12 730 événements,
~14 000 documents) dépasse les quotas gratuits de l'API ; la démo de soutenance ne doit pas
dépendre d'une connexion ; et le coût reste nul. `mistral-embed` reste activable en une
variable d'environnement, ce qui permet de **comparer les deux** sur le jeu de test de l'étape 5.

Le fournisseur `test` n'est pas un gadget : il permet de faire tourner toute la chaîne
d'indexation et de recherche dans les tests automatiques, sans réseau, sans clé et sans
téléchargement de modèle. C'est lui qui rend la CI possible.

### Type d'index Faiss

`IndexFlatIP` sur des **vecteurs normalisés**, ce qui équivaut à une similarité cosinus.

- La recherche est **exacte** : aucun risque de manquer un événement pertinent.
- À cette échelle (~14 000 vecteurs), elle prend quelques millisecondes.
- Les index approchés (IVF, HNSW) n'ont d'intérêt qu'à partir de centaines de milliers de
  vecteurs : ils gagnent du temps en acceptant un rappel imparfait. Ce serait la première
  évolution si Puls-Events passait de la Loire-Atlantique à la France entière.

Détail vérifié dans le code de LangChain : l'avertissement
« Normalizing L2 is not applicable for metric type MAX_INNER_PRODUCT » est trompeur.
La normalisation est bien appliquée aux documents **et** aux requêtes ; l'avertissement est
donc masqué volontairement, avec un commentaire qui l'explique.

### Métadonnées conservées

Pour chaque document : `uid`, `title`, `date_begin`, `date_end`, `date_text` (date en toutes
lettres), `city`, `location_name`, `url`, `keywords`, `status`, `chunk_index`, `n_chunks`.
Elles serviront à citer les sources dans la réponse, à filtrer par date et à dédoublonner.

### Recherche : un seul morceau par événement

`search()` récupère trois fois plus de résultats que demandé puis ne garde que le meilleur
morceau de chaque événement. Sans cela, un événement long découpé en quatre morceaux
occuperait à lui seul la moitié des résultats envoyés au LLM.

### Persistance

`data/index/` contient `index.faiss` (les vecteurs), `index.pkl` (les documents et leurs
métadonnées) et `index_info.json` (modèle utilisé, dimensions, nombre de documents, durée de
construction). Ce dernier fichier documente l'index et servira à l'endpoint de santé de l'API.
Le dossier n'est pas versionné : il se reconstruit en une commande.

---

## Étape 4 : la chaîne RAG (LangChain + Mistral)

### Ce qui est envoyé au modèle

À chaque question, le service exécute trois opérations : rechercher dans l'index,
mettre les fiches en forme, appeler le LLM. Le prompt système contient quatre garde-fous :

1. **Répondre uniquement à partir des fiches fournies**, ne jamais inventer un événement,
   une date, un lieu ou un prix.
2. **Dire clairement quand rien ne correspond**, au lieu de broder.
3. **Citer titre, date, lieu et ville** de chaque événement recommandé, puis les liens.
4. **La date du jour est injectée dans le prompt.** Sans elle, le modèle ne peut pas
   traiter « ce week-end » ni signaler qu'un événement est passé : un LLM n'a aucune
   notion du moment présent.

### Filtrer les événements déjà terminés

Sur 12 730 événements, **2 207 seulement sont encore à venir**. Sans filtre, une question
du type « que faire ce week-end ? » remonterait majoritairement des événements passés, car
la recherche sémantique ignore le temps.

Le filtre est appliqué au niveau de Faiss (`filter` sur la métadonnée `date_end`, avec un
`fetch_k` élargi pour conserver assez de candidats), et non après coup, afin de toujours
retourner le nombre de résultats demandé. Il est désactivable (`--all-dates`) pour les
questions portant sur le passé.

### Choix du modèle de génération

`mistral-small-latest`, avec `temperature=0.2`. La tâche consiste à reformuler des fiches
déjà fournies, pas à produire du raisonnement complexe : un petit modèle suffit, coûte
moins cher et répond plus vite. `LLM_MODEL` permet de basculer sur `mistral-large-latest`
pour comparer les deux à l'étape 5.

### Séparation des responsabilités

Toute la logique vit dans la classe `RAGService`, avec trois méthodes : `ask()`, `retrieve()`
et `health()`. L'index et le modèle sont chargés **une seule fois**, au premier appel, puis
réutilisés : l'API ne doit pas tout recharger à chaque question. Le code web de l'étape 5
ne contiendra aucune règle métier.

Conformément à l'énoncé, **l'historique de conversation n'est pas géré** : chaque question
est traitée indépendamment.

### Comment c'est testé sans réseau

Le LLM est remplacé dans les tests par un modèle factice qui enregistre les messages reçus.
Cela permet de vérifier le point le plus critique d'un système RAG : **ce qui est réellement
envoyé au modèle**. Les tests contrôlent que le contexte contient bien les fiches retrouvées,
que la date du jour figure dans le prompt, qu'une question vide est refusée, qu'un événement
terminé est écarté, et qu'en l'absence de résultat le LLM n'est pas appelé du tout.

### Résultats mesurés de l'indexation (24/09/2026)

| Mesure | Valeur |
|---|---|
| Événements indexés | 12 730 |
| Documents vectorisés | 14 815 (1,16 par événement) |
| Modèle | paraphrase-multilingual-MiniLM-L12-v2, 384 dimensions |
| Durée de vectorisation | 1 340 s (~22 min) sur un portable, sans GPU |
| Type d'index | IndexFlatIP, similarité cosinus, recherche exacte |

**Questions de contrôle, embeddings locaux :**

| Question | Premier résultat | Score | Verdict |
|---|---|---|---|
| un concert de musique classique | Concert Vibrations – Musique de Chambre | 0,762 | pertinent (le mot « classique » est absent de la fiche) |
| une exposition à voir en famille | Écrans trop présents ? | 0,581 | partiel : « famille » a primé sur « exposition » |
| atelier pour apprendre à cuisiner | Démonstrations de recettes – Saveurs d'octobre | 0,579 | pertinent |

Les mêmes questions posées avec le fournisseur `test` (hachage de mots) renvoyaient un
atelier de réparation d'ordinateurs sur la première : la comparaison illustre concrètement
la différence entre une recherche par mots et une recherche par le sens.

**Limite identifiée.** Sur une requête combinant un type d'événement et un public
(« exposition » + « en famille »), le modèle privilégie le thème global et perd le type.
Piste d'amélioration : recherche hybride (similarité sémantique + correspondance de
mots-clés, type BM25), ou filtre par mots-clés en amont.

**Correction apportée.** Open Agenda publie parfois deux fiches pour le même spectacle,
avec un nom de lieu orthographié différemment : elles échappaient au dédoublonnage du
nettoyage. La recherche fusionne désormais les résultats partageant le même titre et la
même date de début, pour ne pas gaspiller une place dans les fiches envoyées au LLM.

**Deuxième correction : le filtre « à venir » appauvrissait les résultats.** Faiss filtre
*après* avoir cherché : il examine une fenêtre de `fetch_k` documents, puis écarte ceux qui
ne passent pas le filtre. Avec une fenêtre fixe de 100 documents sur un index de 14 815, et
seulement 17 % d'événements à venir, une question sur les concerts ne renvoyait plus qu'un
seul résultat au lieu de cinq. La fenêtre est désormais élargie progressivement jusqu'à
obtenir assez de candidats, ou jusqu'à avoir parcouru tout l'index. Un test reproduit le cas
limite : un seul événement à venir noyé parmi soixante événements passés très similaires.

**Troisième correction : un événement bavard masquait les autres.** Faiss tronque les
résultats avant que le dédoublonnage ne s'applique. Un événement à la description longue,
découpé en une dizaine de morceaux tous pertinents, remplissait donc à lui seul la liste :
après fusion, il ne restait qu'un seul événement à proposer. La recherche compte désormais
les événements **distincts** et élargit la fenêtre tant qu'il en manque. Test associé :
un événement de 400 phrases face à vingt événements comparables, la recherche doit
renvoyer cinq événements différents.

### Mode dégradé quand le LLM est indisponible

L'offre gratuite de Mistral renvoie régulièrement des erreurs `429` (limite de débit).
Plutôt que de laisser remonter une erreur technique, le service attrape l'échec de l'appel
et renvoie les **événements trouvés** avec leurs dates, lieux et liens, précédés d'une phrase
expliquant que la rédaction est momentanément indisponible. La recherche vectorielle ayant
déjà fait son travail, l'utilisateur obtient une réponse utile ; le champ `llm_disponible`
signale l'état au reste du système.

Trois causes d'échec sont traduites en langage clair : limite de débit (429), clé refusée
(401), service injoignable (timeout). Cette gestion d'erreur sera reprise telle quelle par
l'endpoint `/ask` de l'API à l'étape 5, où la robustesse est un point de vigilance explicite.

### Premier test de bout en bout : une date inventée

Premier appel réel au LLM (`ministral-3b-latest`, seul modèle accessible sur le compte, les
autres renvoyant `429`). La réponse est correctement rédigée, cite de vrais événements et
leurs liens, en 3,65 s. Mais elle contient **une date fausse** : « Beethoven et compagnie »
est annoncé au 20/11/2026 alors que la fiche indique le 10 novembre 2026. Deux autres
défauts : la réponse commence par affirmer qu'aucun concert classique n'est prévu avant d'en
citer deux, et trois sources pertinentes sur cinq sont ignorées.

Corrections apportées : température ramenée de 0,2 à **0** (la tâche est de restituer des
fiches, pas d'inventer), et deux règles ajoutées au prompt — recopier dates, titres et lieux
mot pour mot, et ne pas annoncer une absence de résultat quand les fiches en contiennent.

Enseignement pour l'étape 5 : sans mesure automatique, ce genre d'erreur passe inaperçu.
C'est exactement ce que mesure la métrique *faithfulness* de Ragas. Le jeu de test annoté
devra contenir des questions dont la réponse attendue comporte une date précise.

## Étape 5 — L'API REST et l'évaluation

### Ce que l'API ne fait pas

Le fichier `src/rag/api.py` ne contient aucune règle métier : il traduit du HTTP vers
`RAGService` et retour. C'est délibéré. Toute la logique du RAG vit dans `rag.chain`, donc
elle se teste sans serveur, s'utilise depuis un script ou un notebook, et resterait valable
si l'on remplaçait FastAPI par autre chose. Le même raisonnement a fait naître
`rag.pipeline` (le rafraîchissement complet) et `rag.evaluation` (le calcul des métriques) :
du code testable dans le paquet, des scripts réduits à des interfaces en ligne de commande.

### Le service est chargé une fois, pas à chaque requête

L'index est chargé au premier appel, puis gardé en mémoire. Le charger à chaque question
coûterait plusieurs secondes et autant de mémoire pour rien.

Un détail est apparu à l'écriture : sous une API, plusieurs requêtes peuvent arriver
*pendant* ce premier chargement, et chacune chargerait sa propre copie des 15 000 vecteurs.
Un verrou (`threading.Lock`) règle le cas. Même raison pour un second changement : le
paramètre « uniquement les événements à venir » était un attribut du service, modifié au
passage par chaque requête — deux requêtes simultanées se marchaient donc dessus. Il est
devenu un paramètre d'appel. Ce sont deux bugs qui ne se voient jamais en usage manuel.

### `/rebuild` : reconstruire sans jamais casser l'index qui répond

Reconstruire l'index prend plusieurs minutes. Trois précautions :

1. **Réponse immédiate (202), travail en tâche de fond.** Le client suit l'avancement sur
   `/health` plutôt que d'attendre au bout d'une requête HTTP qui expirerait.
2. **Construction à côté, puis renommage.** Le nouvel index est bâti dans un dossier voisin
   et mis en place par deux renommages, quelques millisecondes. Un échec en cours de route
   laisse donc l'ancien index intact et interrogeable. Un test simule un échec au moment de
   la mise en place et vérifie que l'ancien index est bien restauré.
3. **Jeton obligatoire.** L'endpoint est protégé par l'en-tête `X-Rebuild-Token`, comparé
   avec `secrets.compare_digest` (comparaison à durée constante). Sans `REBUILD_TOKEN`
   configuré, l'endpoint est **désactivé** : rien n'est exposé par accident.

Un garde-fou s'est ajouté en écrivant le pipeline : si le nettoyage ne laisse aucun
événement — mauvais département, API qui renvoie une réponse vide — le rafraîchissement est
abandonné avant de toucher à l'index. Sans cela, une erreur de saisie suffisait à remplacer
un index de 15 000 vecteurs par un index vide.

### Le mode dégradé est un succès HTTP, pas une erreur

Quand Mistral est indisponible, `/ask` répond **200** avec `llm_disponible: false` et la
liste des événements trouvés. Renvoyer 503 serait faux : la recherche a fonctionné, la
réponse est utile, seule la mise en forme manque. Seul un index absent donne un 503, avec la
commande à lancer dans le message.

### Deux évaluations, parce qu'il y a deux façons d'échouer

Un RAG peut se tromper à deux endroits, et une note globale les mélangerait :

| Ce qui est mesuré | Comment | Dépend du LLM ? |
|---|---|---|
| La **recherche** trouve-t-elle le bon événement ? | précision@k, rappel@k, MRR sur annotations humaines | non |
| La **génération** reste-t-elle fidèle aux fiches ? | Ragas : faithfulness, answer relevancy, context precision | oui |

Séparer les deux a un intérêt très concret ici : le quota Mistral étant épuisé, la qualité
de la recherche reste mesurable. C'est la moitié du système que l'on peut noter aujourd'hui.

Le jeu de `eval/questions.json` compte 20 questions écrites comme les poserait un habitant
(courtes, familières, parfois imprécises) : thème, lieu, public, période, prix — et trois
questions **hors périmètre** (un match à Marseille, la météo, une réservation). Ces trois-là
sont comptées à part : elles n'ont pas de bonne réponse à trouver, bien y répondre c'est ne
rien proposer. Les mêler aux autres gonflerait les moyennes.

L'annotation est manuelle (`--annotate`), avec une pré-sélection par mots-clés seulement
comme aide à la saisie. Évaluer une recherche sémantique avec une recherche par mots-clés
n'aurait mesuré que leur ressemblance.

**Choix du MRR.** Précision et rappel ne disent pas *où* le bon résultat apparaît. Deux
recherches de précision identique n'ont pas la même valeur selon que l'événement pertinent
sort en première ou en cinquième position, puisque l'utilisateur lit les premières
propositions et rarement les suivantes. Un test vérifie ce cas précis, et il a servi : il a
pris en défaut mon propre calcul à la main, pas le code.

**Ragas : l'API historique plutôt que la nouvelle.** Ragas 0.4.3 déprécie `ragas.metrics` au
profit de `ragas.metrics.collections`. La nouvelle exige un client passant par `instructor`,
dont la version est déjà contrainte sur ce projet (voir `requirements.txt`, où `instructor`
a imposé `mistralai < 2`). L'ancienne API fonctionne avec le wrapper LangChain et n'émet
qu'un avertissement de dépréciation : c'est le choix le moins risqué pour un POC, et il est
documenté à l'endroit où il est fait.

### Deux niveaux de tests pour l'API

- `tests/test_api.py` (20 tests) appelle l'application **en mémoire** avec `TestClient` :
  aucun port ouvert, index factice de quatre événements, embeddings déterministes. C'est ce
  qui tourne dans `pytest`. L'injection de dépendance de FastAPI rend cela possible :
  `app.dependency_overrides` remplace le service réel par un service de test.
- `scripts/api_test.py` interroge un **vrai serveur** sur le **vrai index**, et affiche un
  décompte de vérifications réussies. C'est la démonstration de bout en bout.

Total : 146 tests.

### Deuxième itération sur le prompt : trois défauts, trois règles

Une fois le quota débloqué (voir plus bas), premier vrai test de la chaîne complète. La
date inventée a disparu : « Beethoven et compagnie » s'affiche bien au 10 novembre 2026.
La température à 0 et la consigne de recopie mot pour mot ont tenu.

Deux défauts restaient, visibles seulement sur une réponse réelle :

**La réponse s'ouvrait sur une négation.** « Aucun concert de musique classique strictement
classique n'est encore disponible » — avant de citer trois concerts de musique classique.
La règle 7 interdisait déjà d'annoncer une absence quand les fiches contiennent des
événements correspondants ; le modèle l'a contournée avec l'adverbe « strictement ». La
règle a été réécrite en nommant l'échappatoire : ne jamais ouvrir sur ce qui manque, sur ce
qui n'est pas disponible, ou sur ce qui ne correspond « pas strictement » ; présenter les
événements proches comme des recommandations, pas comme des pis-aller.

**Les lieux avaient disparu.** La règle 3 demandait « titre, date, lieu et ville » ; le
modèle ne donnait que titre et date. Pour un assistant de sorties, c'est l'information la
plus utile qui manquait. Une consigne énumérative se perd ; un gabarit, non. La règle impose
désormais une forme exacte — `**Titre** — Lieu, Ville — Date` — assortie de sa raison :
« une recommandation de sortie sans lieu ne sert à rien ».

Résultat au test suivant : format respecté à la lettre, ouverture directe sur le premier
événement, lieux présents. Le modèle a même évité la redondance « Conservatoire de Nantes,
Nantes ».

Deux assertions ont été ajoutées à `test_chain.py` : le test vérifie que ces garde-fous
figurent bien dans le prompt. Un prompt est du code ; le retirer par mégarde doit casser un
test, comme n'importe quelle régression.

### Un même code 429, deux causes sans rapport

En cours de route, une erreur a révélé un défaut de mon propre diagnostic :

```
429 {"message":"Not enough capacity available for this request, please retry later.",
     "type":"backend_out_of_capacity","code":"3505"}
```

Ce n'est pas un plafond de compte : ce sont les serveurs de Mistral qui sont saturés à
l'instant T. La requête précédente était passée, avec la même clé et le même modèle.

Or `describe_llm_error` annonçait « limite de débit de l'API atteinte » dans les deux cas.
Le message était faux, et surtout trompeur : il envoyait chercher un problème de quota
inexistant. Les deux causes n'appellent pas la même réaction — un plafond de compte demande
d'attendre des heures ou de changer d'offre, une saturation quelques secondes.

Le cas `backend_out_of_capacity` est donc traité **avant** le 429 générique, puisque son
message contient aussi « 429 ». L'ordre des conditions porte ici toute la distinction ; un
test fige les deux messages.

Enseignement : un code HTTP ne suffit pas à qualifier une panne. Tant qu'on se contente du
statut, on range sous la même étiquette des situations qui n'ont rien à voir.

### Le modèle configuré ne doit jamais être supposé

Le 429 a persisté deux jours sur ce poste. Cause réelle : `.env` contenait
`LLM_MODEL=mistral-medium-latest`, alors que le diagnostic de l'étape 4 avait identifié
`ministral-3b-latest` comme le seul modèle accessible — la ligne n'avait jamais été changée.
Les gros modèles sont les premiers plafonnés sur l'offre gratuite.

Ce qui l'a révélé : `GET /health`, qui affiche le modèle réellement utilisé. L'information
était sous les yeux depuis le début, mais nulle part dans les sorties de `ask.py`.

Deux corrections en découlent. `check_env.py --online` teste maintenant le modèle de
`config.LLM_MODEL` au lieu d'un nom codé en dur : sans cela, la vérification pouvait passer
au vert pendant que l'application échouait. Et `/health` expose le modèle, ce qui a servi
exactement à ça.

## Résultats de l'évaluation

### Les chiffres

Jeu de 20 questions, dont 3 hors périmètre comptées à part. Annotation manuelle des
5 premiers résultats de chaque question, sur l'index de 14 815 documents.

| Recherche (annotation humaine) | |
|---|---|
| questions notées | 17 |
| précision@5 | 0,447 |
| MRR | 0,615 |
| taux de réussite | 0,765 |

| Génération (Ragas, 17 questions) | |
|---|---|
| faithfulness | 0,936 |
| answer relevancy | 0,500 |
| context precision | 0,619 |

Le **MRR de 0,615** est le chiffre le plus parlant : quand la recherche trouve, le premier
résultat pertinent arrive en moyenne entre la première et la deuxième position. C'est ce
que perçoit un utilisateur, qui lit les premières propositions et rarement les suivantes.

La **faithfulness de 0,936** répond directement à l'épisode de la date inventée : le système
ne fabrique plus d'information absente des fiches.

### Deux méthodes indépendantes qui concordent

L'**answer relevancy de 0,500** n'est pas un résultat isolé. Le taux de réussite de la
recherche est de 0,765, soit 13 questions sur 17. Si les réponses issues de ces 13 sont
bonnes et que les 4 autres sont hors sujet, on retombe mécaniquement autour de 0,5.

Or ces deux mesures n'ont rien en commun : l'une vient d'annotations humaines sur les
résultats de recherche, l'autre d'un LLM qui juge les réponses finales sans connaître ces
annotations. Elles aboutissent au même diagnostic — quand la recherche trouve, le système
répond bien ; quand elle échoue, la réponse est inutilisable. Une métrique isolée se
discute ; deux méthodes indépendantes qui convergent, beaucoup moins.

### Trois métriques, et ce qu'elles ne disent pas

**Le rappel a été retiré du rapport.** Il valait 0,765, exactement le taux de réussite — et
ce n'est pas une coïncidence. La pertinence étant annotée *parmi les cinq résultats
affichés*, tout événement pertinent connu est par construction dans le top 5 : le rappel
vaut 1 partout où quelque chose a été trouvé, 0 ailleurs. Il ne mesure donc rien de plus
que le taux de réussite. Un vrai rappel exigerait d'annoter les 12 730 événements, ce qui
est hors de portée d'une annotation manuelle. La limite est inhérente au protocole, pas au
système.

**Le « 3/3 hors périmètre » mesure l'annotation, pas le système.** Il compte les questions
où l'annotateur n'a retenu aucun événement. La recherche, elle, a bien renvoyé cinq
résultats pour « un match de football à Marseille ». Savoir refuser relève de la
génération, pas de la recherche.

**Aucune métrique ne mesure la diversité.** Sur « une sortie avec des enfants », les cinq
résultats se réduisent à deux événements — trois tranches d'âge d'un même atelier et deux
dates d'un même rendez-vous. La précision vaut 1,0 alors que la réponse est pauvre. Même
constat sur « une rencontre autour d'un livre » : trois places sur cinq pour le même
rendez-vous à trois dates.

### Le juge est instable, et il est trop petit

Quatre exécutions successives sur les mêmes données :

| | faithfulness | context precision |
|---|---|---|
| 1 | 0,953 | 0,647 |
| 2 | 0,982 | 0,613 |
| 3 | 0,945 | 0,618 |
| 4 | 1,000 | 0,373 |

Rien n'avait changé côté recherche entre la 3 et la 4. L'écart de 25 points vient du juge,
`ministral-3b-latest` — le plus petit modèle de la gamme, retenu parce que c'était le seul
accessible sur l'offre gratuite. Deux conséquences assumées :

- ces scores sont annoncés comme des ordres de grandeur, jamais comme des valeurs exactes ;
- le même modèle rédige et juge, ce qui est une faiblesse méthodologique connue : un modèle
  note favorablement ses propres productions. Juger avec un modèle plus puissant que celui
  qui rédige serait préférable, mais inaccessible ici.

### Quatre familles de défauts, trouvées par l'évaluation

**1. Les contraintes temporelles sont ignorées** (q09, q13, q14 — trois questions à zéro).
« Un soir de semaine » renvoie des événements à 8h30 et 10h30. « Ce week-end » renvoie des
événements de décembre, à deux mois. « Pendant les vacances de Noël » renvoie cinq
événements de Noël, tous antérieurs aux vacances — succès thématique, échec temporel.
L'embedding compare du texte : il ne lit pas une heure et ne calcule pas une date.

Correction identifiée : le mécanisme existe déjà. Les événements terminés sont écartés par
un filtre structuré sur `date_end`, pas en espérant que l'embedding comprenne « à venir ».
Il faut détecter les intentions temporelles de la question et poser les mêmes filtres sur
`date_begin`.

**2. Les contraintes géographiques sont approximatives** (q11, q12). « Du côté de
Saint-Nazaire » remonte Saint-Philbert, Saint-Herblain et Saint-Hilaire : des noms qui se
ressemblent, à 60 km. « Près de la mer » remonte « Océanissime », à Nantes, à 50 km de la
côte — le thème colle, le lieu non. Le succès apparent sur « à Nantes » (q10) était de la
chance : « Nantes » est un mot distinctif. Même correction : `city` est une métadonnée, elle
doit être filtrée, pas devinée.

**3. Le sujet est capté, la forme est perdue** (q03, q04, q16). « Exposition de peinture »
remonte des ateliers où l'on peint soi-même. « Plutôt une comédie » remonte *Terreur*, une
pièce de procès. « Un festival de musique » remonte cinq concerts d'une soirée, dont un bal :
le mot « festival » a été purement ignoré.

**4. La diversité n'est pas gérée** (q07, q17), décrite plus haut.

### Un seuil de score ne réglerait pas le hors-périmètre

Première intuition devant les échecs : rejeter les résultats sous un score minimal. La
question hors périmètre « un match de football à Marseille » l'invalide — son premier
résultat obtient **0,653**, contre 0,520 pour « un atelier pour apprendre de mes mains » et
0,515 pour « des sorties gratuites », deux questions parfaitement légitimes. Un seuil à 0,45
laisserait passer le football et rejetterait l'origami.

La raison est que la similarité cosinus est **relative** : elle dit « voici les fiches les
plus proches parmi 14 815 », pas « voici à quel point c'est une bonne réponse ». Le mot
« match » a suffi à faire remonter des matchs d'improvisation.

Piste plus prometteuse observée sur « quel temps fera-t-il demain ? » : l'**écart** entre le
premier et le dernier résultat y tombe à 0,02 — tous les scores dans le bruit. Un écart
faible signale mieux l'absence de réponse qu'un seuil absolu.

### Une question impossible : le prix

« Des sorties gratuites » ne peut pas être traitée correctement, quelle que soit la qualité
de la recherche. Le champ `conditions` est vide pour une grande partie des fiches, et quand
il est rempli il mélange tarif, modalité d'inscription et jauge : « Gratuit », « Sur
réservation », « 50 places », « Sans inscription », « Prix libre ». C'est une limite de la
source, pas du RAG — et la distinction compte.

Défaut relevé au passage dans l'outil d'annotation lui-même : il affiche titre, ville et
date, mais pas les conditions. Impossible d'annoter une question de prix sans aller lire le
fichier de données à côté.

### Le nettoyage filtre la source, pas le contenu

Deux fiches non culturelles sont remontées en tête : un atelier d'insertion
professionnelle (« Détection de Potentiel Industrie ») et un point d'information
administratif (« Point Information Nantes Solidaire »). C'est le bruit écarté dans le Nord
en filtrant l'agenda France Travail — sauf que celles-ci viennent d'agendas généralistes qui
mélangent culture et service public. Un filtre par source attrape le gros du bruit, pas
tout.

## Incidents rencontrés pendant l'évaluation

### L'outil d'annotation perdait le travail

`Ctrl+C` pendant la saisie levait une `KeyboardInterrupt` non interceptée, et
l'enregistrement n'avait lieu qu'à la fin de la boucle : vingt minutes d'annotation perdues.
Corrigé en enregistrant après **chaque** question et en traitant l'interruption comme un
arrêt propre. Un outil qui demande un travail manuel long doit considérer l'interruption
comme le cas normal, pas comme une exception.

### Kaspersky cassait les appels HTTPS asynchrones

Ragas échouait sur `CERTIFICATE_VERIFY_FAILED: self-signed certificate in certificate
chain`. L'antivirus inspecte le trafic chiffré : il déchiffre, analyse et rechiffre avec son
propre certificat, que Python ne connaît pas. Les appels synchrones passaient, les appels
asynchrones de Ragas non.

Corrigé en ajoutant les certificats de confiance de Windows au magasin de `certifi` de
l'environnement virtuel. Effet secondaire mesurable : la vitesse d'évaluation est passée de
1,83 s par itération à 2,66 itérations par seconde — chaque appel ne partait plus en quatre
secondes d'attente avant de réessayer.

À distinguer d'un 429 : une erreur de certificat signifie que le serveur n'a **jamais** reçu
la requête, alors qu'un 429 est une réponse du serveur.

### Deux incompatibilités de bibliothèques derrière un même `NaN`

La métrique `answer_relevancy` est restée à `NaN` deux fois de suite, pour deux causes
distinctes et sans rapport :

1. **`TypeError: unsupported operand type(s) for +=: 'dict' and 'dict'`.** Cette métrique
   fait régénérer trois questions à partir de la réponse (`strictness=3`), et LangChain
   fusionne les générations en additionnant les compteurs de jetons renvoyés par Mistral.
   L'API renvoie désormais un dictionnaire imbriqué là où LangChain attend un nombre.
   Contourné par `strictness=1` : une seule génération, donc rien à fusionner. Compromis
   assumé — l'estimation est plus bruitée.

2. **`ValidationError for EmbeddingUsageEvent`.** Ragas journalise l'usage des embeddings et
   lit l'attribut `model` en attendant une chaîne ; `FastEmbedEmbeddings` y range l'objet du
   modèle chargé. Désactiver la télémétrie ne suffisait pas : l'objet de suivi est construit,
   et donc validé, *avant* qu'on vérifie s'il faut l'envoyer. Réglé par un adaptateur
   (`NamedEmbeddings`) qui délègue les appels en exposant un nom lisible. Trois tests figent
   le comportement.

Les deux fois, le symptôme était identique et silencieux — une métrique à `NaN`, aucune
erreur fatale — et la cause était à trois bibliothèques de distance. C'est le coût réel d'un
écosystème qui bouge vite, et la justification concrète d'un `requirements.txt` aux versions
figées.

Total : 149 tests.

## Étape 6 — Docker

### Ce que l'image embarque, et ce qu'elle laisse dehors

| Dans l'image | Monté depuis l'hôte |
|---|---|
| Python, les dépendances, le code | l'index Faiss et les données nettoyées |
| Le modèle d'embeddings (220 Mo) | la clé API Mistral, passée par `--env-file` |

Deux décisions, opposées en apparence :

**Le modèle d'embeddings est embarqué.** Il ne change jamais, et sans lui le conteneur
réclamerait 220 Mo à huggingface.co à son premier démarrage — une démonstration hors ligne
deviendrait impossible, et un réseau lent se traduirait par une première question
interminable. `HF_HUB_OFFLINE=1` est posé dans l'image : si le modèle manquait malgré tout,
l'erreur serait immédiate et explicite, au lieu d'un téléchargement silencieux.

**L'index reste dehors.** C'est l'inverse : une donnée reconstructible de plusieurs dizaines
de mégaoctets, qui change à chaque rafraîchissement. L'embarquer obligerait à reconstruire
l'image à chaque mise à jour des données, pour un résultat périmé dès le lendemain.

La règle derrière les deux : **ce qui est stable et coûteux à obtenir entre dans l'image ;
ce qui change souvent reste à l'extérieur.**

### Le modèle a un emplacement fixe

fastembed choisit sinon un dossier temporaire. Dans un conteneur, cela signifierait un
retéléchargement à chaque démarrage — sans erreur, juste très lentement, ce qui est la pire
forme de panne. Un réglage `EMBEDDING_CACHE_DIR` a donc été ajouté, utilisé à la fois par
l'étape de construction et par l'exécution, pour que les deux pointent au même endroit.

Trois tests figent ce comportement, dont un qui vérifie qu'en l'absence de réglage le
paramètre n'est **pas** transmis : passer `None` à fastembed le ferait écrire dans le dossier
courant, ce qui n'est pas la même chose que le laisser décider.

### Construction en deux étapes

La première installe `build-essential` — faiss-cpu compile des extensions C — et les
dépendances dans un environnement virtuel. La seconde ne récupère que cet environnement et
le modèle. Les outils de compilation ne sont jamais livrés.

Les dépendances sont copiées et installées **avant** le code. Tant que `requirements.txt` ne
bouge pas, Docker réutilise cette couche : modifier une ligne de `chain.py` reconstruit
l'image en quelques secondes au lieu de dix minutes.

### Trois détails qui font échouer silencieusement

**`--host 0.0.0.0` et non `127.0.0.1`.** À l'intérieur d'un conteneur, `127.0.0.1` ne désigne
que le conteneur lui-même. Le serveur démarrerait normalement, afficherait ses journaux
habituels, et aucune requête venant de l'hôte n'arriverait jamais.

**`.env` dans le `.dockerignore`.** Une clé copiée dans une couche d'image y reste lisible
par quiconque récupère l'image, même si un `RUN rm` la supprime ensuite : les couches sont
empilées, pas réécrites. La clé est passée au démarrage.

**Un utilisateur sans privilèges.** Un conteneur n'a aucune raison de tourner en root.
Contrepartie assumée : si l'on appelle `/rebuild` depuis le conteneur, le volume monté doit
être accessible en écriture à l'uid 1000. Pour la démonstration, qui ne fait que lire
l'index, la question ne se pose pas.

### Une sonde plutôt qu'un pari

`HEALTHCHECK` interroge `/health` toutes les 30 secondes, avec 40 secondes de grâce au
démarrage — le temps de charger l'index. `docker ps` affiche alors `healthy`, ce qui dit
quelque chose de plus utile que « le processus tourne » : l'API répond et son index est là.

C'est la justification concrète d'avoir écrit `/health` à l'étape 5 : il sert à l'extérieur
du code.

### Ce que Docker prouve réellement

```
docker run --rm puls-events pytest
```

Les 159 tests s'exécutent dans l'image, indépendamment de la machine hôte. C'est le vrai
argument : « ça marche chez moi » devient vérifiable par quelqu'un d'autre.

Total : 159 tests.

### Mesures relevées sur l'image construite

| | |
|---|---|
| Temps de construction, cache vide | 3 min 38 s |
| Taille sur disque | 1,92 Go |
| Taille compressée, celle qui transite sur le réseau | 548 Mo |
| Tests exécutés dans l'image | 149 réussis, 10 ignorés |
| Vérifications de l'API sur le conteneur | 23 / 23 |
| Première question | 4,0 s |
| Questions suivantes | 0,9 à 1,5 s |

Répartition de la taille : Python et le système d'exploitation pour environ 150 Mo, les
dépendances scientifiques (faiss, onnxruntime, pandas, numpy) pour l'essentiel du reste, le
modèle d'embeddings pour 220 Mo. Le code du projet pèse moins de 100 Ko. Deux leviers
existent pour réduire l'image, non appliqués ici : retirer les dépendances d'évaluation
(ragas, instructor, openai ne servent qu'au développement) et partir d'une base Alpine, au
prix d'une compilation nettement plus longue.

**Les 10 tests ignorés sont ceux de la qualité des données**, exclues de l'image par le
`.dockerignore`. C'est le comportement voulu : ces tests contrôlent les données, pas le code,
et ils se réactivent dès que le volume est monté.

**L'écart entre la première question et les suivantes** — 4,0 s contre 1,0 s — est le
chargement de l'index en mémoire, qui n'a lieu qu'une fois. C'est le chargement paresseux de
l'étape 5, mesurable de l'extérieur.

Le conteneur a tourné trois heures sans intervention, sonde au vert, index lu depuis le
volume monté et clé API transmise au démarrage : aucune des deux n'est dans l'image.
