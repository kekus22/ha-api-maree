# API Marée pour Home Assistant

Intégration [Home Assistant](https://www.home-assistant.io/) qui récupère les horaires de marée,
les coefficients et les hauteurs d'eau des ports français depuis
[api-maree.fr](https://api-maree.fr/), ainsi que les niveaux de l'**estuaire de la Rance** et les
horaires de l'**écluse du barrage** publiés par [EDF](https://www.edf.fr/usine-maremotrice-rance/marees-en-rance).

Une carte Lovelace dédiée (courbe de marée, tableau des prochains jours) est disponible dans le
dépôt [ha-api-maree-card](https://github.com/kekus22/ha-api-maree-card).

## Avec ou sans compte

| | Sans compte | Avec un compte (gratuit) |
|---|---|---|
| Clé API | clé de démonstration publique, utilisée automatiquement | clé personnelle |
| Ports | 9 (Brest, Bénodet, Calais, Cherbourg, Dunkerque, Ouistreham, Port-en-Bessin, Saint-Jean-de-Luz, Saint-Malo) | tous (≈ 140) |
| Quota | partagé avec tous les utilisateurs de la clé de démo | 360 requêtes / heure pour vous seul |

Un compte se crée sur <https://api-maree.fr/register> ; la clé est affichée sur la page du compte.

## Installation

### HACS (recommandé)

1. HACS → menu ⋮ → *Dépôts personnalisés* → ajouter `https://github.com/kekus22/ha-api-maree`,
   catégorie **Intégration**.
2. Installer **API Marée**, puis redémarrer Home Assistant.

### Manuelle

Copier `custom_components/api_maree` dans le dossier `config/custom_components/` de Home Assistant,
puis redémarrer.

## Configuration

*Paramètres → Appareils et services → Ajouter une intégration → API Marée*

1. Choisir la source : **Port (api-maree.fr)** ou **Estuaire de la Rance (EDF)**.
2. Pour un port : saisir la clé API (ou laisser vide pour le mode démo), puis choisir le port.
   Pour la Rance : simplement valider, aucune clé n'est nécessaire.

Répéter l'opération pour suivre plusieurs ports (la clé déjà saisie est proposée).
Dans les **options** de l'intégration, on règle le seuil du capteur *Vives eaux* (70 par défaut).

## Entités créées (par port)

| Entité | Description |
|---|---|
| `sensor.maree_<port>_hauteur_d_eau` | hauteur d'eau actuelle (m). Porte aussi les données de la carte (courbe, marées sur 8 jours, soleil, attribution) |
| `sensor.maree_<port>_maree` | `rising` / `falling` (affiché *Montante* / *Descendante*) |
| `sensor.maree_<port>_niveau_de_maree` | position entre basse mer (0 %) et pleine mer (100 %) |
| `sensor.maree_<port>_prochaine_pleine_mer` | horodatage ; attributs `height`, `coefficient` |
| `sensor.maree_<port>_prochaine_basse_mer` | horodatage ; attribut `height` |
| `sensor.maree_<port>_coefficient_de_maree` | coefficient de la prochaine pleine mer ; attributs `today`, `tomorrow` |
| `binary_sensor.maree_<port>_vives_eaux` | actif si le prochain coefficient ≥ seuil |
| `sensor.maree_<port>_hauteur_prochaine_*` | hauteurs des prochaines pleine/basse mers (désactivées par défaut) |

Les identifiants exacts dépendent de la langue de votre installation.

### Estuaire de la Rance (source EDF)

En amont du barrage, le niveau de la Rance dépend du fonctionnement de l'usine marémotrice et non
de la seule marée. EDF publie des prévisions sur 3 semaines au marégraphe de **Saint-Suliac**
(pas de 5 minutes, zéro des cartes, ± 30 cm hors surcote/décote) et les horaires d'éclusage.

| Entité | Description |
|---|---|
| `sensor.maree_rance_saint_suliac_hauteur_d_eau` | niveau de l'estuaire (m). Porte les données de la carte (courbe estuaire et mer, niveaux hauts/bas, écluse) |
| `sensor.maree_rance_saint_suliac_niveau_de_la_mer` | niveau de la mer côté large (m) |
| `sensor.maree_rance_saint_suliac_maree` | estuaire montant / descendant |
| `sensor.maree_rance_saint_suliac_prochaine_pleine_mer` / `…_basse_mer` | prochain niveau haut / bas de l'estuaire ; attributs `height`, `plateau_start`, `plateau_end` (palier maintenu par l'usine) |
| `sensor.maree_rance_saint_suliac_ecluse` | `sas`, `on_demand`, `uncertain`, `no_sas` ; attributs `until`, `next_status` |
| `sensor.maree_rance_saint_suliac_prochaine_ouverture_de_l_ecluse` | début de la prochaine fenêtre de passage (sas ou sas à la demande) ; attribut `end` |
| `sensor.maree_rance_saint_suliac_fin_de_l_ouverture_de_l_ecluse` | fin de la fenêtre de passage en cours |
| `binary_sensor.maree_rance_saint_suliac_ecluse_praticable` | actif pendant un sas ou un sas à la demande |

Les niveaux hauts et bas sont calculés à partir de la courbe EDF ; ils reproduisent le tableau
« Niveaux de l'estuaire » de la page EDF (mêmes hauteurs, mêmes créneaux à 5 minutes près).

Requêtes : toutes les 6 heures, un fichier JSON par semaine publiée (jusqu'à 4) et un par jour
d'éclusage (8 jours). Ces adresses sont celles qu'utilise la page web d'EDF ; ce n'est pas une API
documentée, elles peuvent changer sans préavis. Le site d'EDF filtre aussi certains accès
automatisés : si l'ajout échoue, vérifiez que Home Assistant peut joindre `www.edf.fr`.

### Exemple d'automatisation

```yaml
automation:
  - alias: "Grande marée demain matin"
    triggers:
      - trigger: state
        entity_id: binary_sensor.maree_saint_malo_vives_eaux
        to: "on"
    actions:
      - action: notify.mobile_app_mon_telephone
        data:
          message: >
            Coefficient {{ states('sensor.maree_saint_malo_coefficient_de_maree') }},
            pleine mer à {{ as_timestamp(states('sensor.maree_saint_malo_prochaine_pleine_mer'))
            | timestamp_custom('%H:%M') }}
```

## Fonctionnement et quota

- Toutes les **6 heures**, 3 requêtes par port : pleines/basses mers (J-1 à J+7), courbe de hauteurs
  (pas de 10 min sur 60 h) et lever/coucher du soleil. Soit ≈ 12 requêtes par jour et par port.
- Entre deux appels, les valeurs (hauteur actuelle, état, prochaine marée…) sont **recalculées
  chaque minute** localement, sans appel à l'API.
- Les attributs volumineux sont exclus de l'historique (recorder) pour ne pas alourdir la base.
- En cas de clé refusée, Home Assistant propose de saisir une nouvelle clé.

## Données, licence et avertissement

**Source EDF (Rance)** : prévisions publiées par EDF sur
[edf.fr](https://www.edf.fr/usine-maremotrice-rance/marees-en-rance), affichées avec mention de la
source. Elles peuvent être modifiées par EDF (incident, travaux, arrêté préfectoral).

**Source api-maree.fr** :

Les données sont fournies par api-maree.fr sous licence **CC BY 4.0**, calculées à partir des
composantes harmoniques Ifremer / PREVIMER (CC BY) et recalées sur les observations du réseau REFMAR
(Shom et producteurs partenaires, Licence Ouverte 2.0). L'attribution complète est exposée dans
l'attribut `data_attribution` et affichée par la carte.

Modifications apportées par l'intégration : arrondi des hauteurs au centimètre et estimation de la
hauteur actuelle par interpolation entre deux points de la courbe.

> ⚠️ Les coefficients sont des valeurs **non officielles**. Données fournies sans garantie :
> **ne pas utiliser pour la navigation**.

## Développement

```bash
pip install -r requirements_test.txt
pytest
ruff check . && ruff format --check .
```

Code sous licence MIT.
