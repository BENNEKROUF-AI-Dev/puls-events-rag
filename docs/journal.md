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
- L'export CSV du portail (330 Mo, 125 301 événements) sert à l'exploration et de solution de secours hors ligne
  (`--source csv`). Les deux sources produisent exactement le même résultat.

### Choix de la zone : la Loire-Atlantique

J'avais d'abord pensé au Nord (la zone la plus fournie), mais en regardant **d'où viennent** les événements,
une bonne partie ne sont pas culturels : ce sont des ateliers de recherche d'emploi publiés par
l'agenda « Mes événements France Travail ».

| Département | Événements sur 1 an | dont non culturels | Événements culturels |
|---|---:|---:|---:|
| Loire-Atlantique | 1 336 | 249 (19 %) | **1 087** |
| Nord | 1 719 | 724 (42 %) | 995 |
| Gironde | 1 278 | 325 (25 %) | 953 |
| Paris | 898 | 196 (22 %) | 702 |

→ La Loire-Atlantique garde le plus d'événements culturels, avec le moins de bruit.
Le département se change en une ligne (`EVENTS_DEPARTMENT` dans `.env`).

### Résultat du nettoyage (date de référence : 19/09/2026)

| Étape | Retirés | Restants |
|---|---:|---:|
| Données brutes (département Loire-Atlantique) | – | 4 296 |
| Terminé depuis plus d'un an | 2 960 | 1 336 |
| Date de début aberrante (> 1 an dans le futur) | 3 | 1 333 |
| Événement annulé | 22 | 1 311 |
| Agenda non culturel (France Travail, Geovelo) | 249 | 1 062 |
| Doublon (même titre, même lieu, même date) | 1 | **1 061** |

Les autres règles (hors zone, identifiant en double, titre ou description manquants, événement en ligne)
n'ont rien retiré sur ces données, mais elles sont testées sur le jeu de test.

**1 061 événements** propres, dans **82 communes** (Nantes : 725), dont **244 à venir**.

### Anomalies rencontrées et corrections

| Anomalie | Ampleur (dans la fenêtre d'un an) | Correction |
|---|---|---|
| HTML dans la description longue | 1 316 événements sur 1 336 | Balises supprimées avec BeautifulSoup, paragraphes gardés en retours à la ligne |
| Entités HTML (`&nbsp;`, `&amp;`…) | 174 | Décodées (`html.unescape`) |
| Code pays vide ou en minuscules (`fr`, `33`) | 27 | Pas d'exclusion : le département fait foi ; seuls les vrais codes étrangers (`CH`…) sont exclus |
| Ville écrite de plusieurs façons (`NANTES`, `Le pouliguen`, `SUCE SUR ERDRE`) | 12 | Harmonisées vers l'orthographe la plus fréquente (comparaison sans casse, accents ni tirets) + table d'alias |
| Ville manquante | 2 | Complétée à partir du code postal quand c'est possible (1 sur 2) |
| Fin d'événement avant son début (soirée finissant après minuit saisie sans changer de jour, ex. 22h → 02h) | 114 dans l'export complet, 0 dans la fenêtre | Corrigée (+1 jour) au lieu de supprimer l'événement |
| Dates aberrantes (jusqu'en 2052) | 3 | Événements commençant plus d'un an après la date de référence exclus |
| Champs « statut » et « mode » en JSON multilingue | tous | Libellé français extrait (`Programmé`, `Sur place`…) |
| Mots-clés absents | 437 sur 1 061 (41 %) | Liste vide ; le titre et la description restent les sources principales |
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

- Les descriptions vont de 0 à 8 000 caractères (médiane 508) : 18 dépassent 2 000 caractères
  → à découper en chunks à l'étape 3.
- `date_text` (« Mardi 17 février, 16h30 ») n'indique pas toujours l'année : utiliser `date_begin` / `date_end` dans le prompt.
- Les mots-clés ne sont pas harmonisés entre événements (« Concert » / « concert ») : sans effet sur les embeddings.
- L'appel à l'API n'a pas pu être testé depuis l'environnement de développement (accès réseau restreint) ;
  il est couvert par des tests avec une réponse simulée. **À vérifier sur une machine avec Internet.**
