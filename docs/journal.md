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
