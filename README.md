# ev_trip : charge restante d'une voiture électrique sur un trajet

Cœur Python pur (sans Django), basé sur OpenStreetMap :
Nominatim (géocodage) -> OSRM (itinéraire) -> Overpass (type de route) -> tranches <= 10 km -> simulation.

## Démarrage avec VS Code

1. `Fichier > Ouvrir le dossier…` et choisir le dossier du projet (celui qui contient `manage.py`).
2. Accepter l'installation des extensions recommandées (Python, Python Debugger).
3. `Ctrl+Maj+P` > **Tasks: Run Task** > **Installer : venv + dépendances** (crée `.venv` et installe tout).
4. `Ctrl+Maj+P` > **Python: Select Interpreter** > choisir `.venv` si VS Code ne l'a pas déjà pris.
5. Onglet **Exécuter et déboguer** (`Ctrl+Maj+D`), puis `F5` sur :
   - **Django : serveur web** : ouvrir http://127.0.0.1:8000 ;
   - **CLI : Paris -> Saint-Lô** : demande la température et affiche le tableau dans le terminal ;
   - **Tests : pytest** (les tests apparaissent aussi dans le panneau Testing).

**Erreur « No module named 'django' »** : VS Code utilise un interpréteur Python où les dépendances ne sont pas
installées. Vérifier l'interpréteur affiché en bas à droite de la fenêtre (doit être `.venv`), le changer avec
**Python: Select Interpreter**, puis lancer la tâche **Installer : dépendances (interpréteur sélectionné)**.

Sans VS Code, en ligne de commande :

    python -m venv .venv
    .venv\Scripts\activate          # Linux/macOS : source .venv/bin/activate
    pip install -r requirements-dev.txt
    python manage.py runserver

## Installation (paquet)

    pip install -e ".[dev]"

## Utilisation

    python -m ev_trip "Paris" "Saint-Lô" --temp 8 --battery 52 --soc 90 --min-soc 10 --csv trajet.csv

| Option | Rôle |
|---|---|
| `--temp` | température extérieure uniforme (°C) |
| `--battery` | capacité de la batterie (kWh) |
| `--soc` | charge au départ (%) |
| `--min-soc` | seuil minimum (%), défaut 10 : alerte avec le km de franchissement |
| `--config` | JSON de consommations / paliers de température (voir `config.example.json`) |
| `--max-slice-km` | longueur max d'une tranche (défaut 10) |
| `--no-overpass` | type de route estimé d'après la vitesse OSRM (précision réduite) |

## Fonctionnement

1. L'itinéraire OSRM est rééchantillonné tous les 500 m ; chaque tronçon est classé via la voie OSM la plus proche
   (`motorway` -> autoroute ; `trunk` selon `maxspeed` ; `primary/secondary/tertiary` -> route ;
   `residential/service` ou `maxspeed <= 50` -> ville).
2. Les changements de type isolés de moins de 1 km (bruit d'appariement) sont absorbés (`min_run_km`).
3. Les tronçons sont fusionnés en tranches de 10 km max, coupées à chaque changement de type.
4. Énergie d'une tranche = longueur x consommation(type, scénario) x (1 + surconsommation(température)), pour chacun des 3 scénarios.
5. Si Overpass est indisponible, repli automatique sur la vitesse moyenne, avec un avertissement.

## Trois estimations de consommation

Chaque type de route a une fourchette (basse, haute) en kWh/100 km à 20 °C ; le trajet est simulé 3 fois :

| Type | Basse (**optimiste**) | Milieu (**moyenne**) | Haute (**pessimiste**) |
|---|---|---|---|
| ville | 11 | 12,5 | 14 |
| route (mixte) | 14 | 14,8 | 15,6 |
| autoroute | 18 | 19,5 | 21 |

Surconsommation selon la température (appliquée aux trois) : < -5 °C +35 % ; [-5, 0[ +25 % ; [0, 10[ +15 % ;
[10, 15[ +8 % ; [15, 25[ 0 % ; >= 25 °C +5 %.
Fourchettes et paliers sont modifiables dans un JSON (`--config`, voir `config.example.json`).

La console, le CSV (séparateur `;`, ouvrable directement dans Excel) et la page web présentent une colonne
kWh/100 km, kWh et charge % **par scénario**, plus une synthèse : km parcourus, énergie, charge finale, statut.
Les cellules « - » indiquent une tranche non atteinte (réserve atteinte avant).

## Comportement de la simulation

La réserve (seuil minimum) n'est jamais entamée : la tranche qui la ferait franchir est tronquée au km exact
où elle est atteinte et le trajet s'arrête là (la suite apparaît en pointillés sur la carte). Avec un seuil de 0 %,
l'arrêt se fait à batterie vide : la charge ne peut donc jamais descendre sous le seuil, ni sous 0 %.

## Variables d'environnement (facultatives)

| Variable | Rôle |
|---|---|
| `EV_TRIP_USER_AGENT` | User-Agent envoyé à Nominatim/OSRM/Overpass (conseillé : y mettre un contact) |
| `EV_TRIP_OVERPASS_URL` | serveur Overpass prioritaire (les miroirs officiels servent de repli) |
| `EV_TRIP_TILE_URL`, `EV_TRIP_TILE_ATTRIBUTION` | fond de carte (OpenStreetMap par défaut) |

## Tests

    pytest        # 93 tests hors-ligne (services OSM simulés)

## Interface web (Django)

    pip install -e ".[web]"
    python manage.py runserver      # puis http://127.0.0.1:8000

Formulaire (départ, arrivée, température, capacité, charge initiale, seuil) -> synthèse, alerte, carte Leaflet
(trajet coloré par type de route, point d'arrêt de chaque scénario), graphe de charge à 3 courbes, comparaison des 3 estimations et tableau par tranche.
Le formulaire est en GET : l'URL d'un résultat est partageable. Pas de base de données.
Le calcul est synchrone : avec Overpass, comptez plusieurs dizaines de secondes sur un long trajet.
En production : définir `DJANGO_DEBUG=0`, `DJANGO_SECRET_KEY`, `DJANGO_ALLOWED_HOSTS`.

## Architecture


`ev_trip.run_trip(...)` renvoie un `TripReport` (itinéraire, tranches, résultats par tranche, alerte, avertissements) :
`trips/views.py` l'appelle et `trips/services.py` prépare les données d'affichage.
