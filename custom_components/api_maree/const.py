"""Constantes de l'intégration API Marée."""

from datetime import timedelta
from typing import Final

DOMAIN: Final = "api_maree"

BASE_URL: Final = "https://api-maree.fr"
# Clé publique de démonstration (accès limité aux sites marqués « demo »).
DEMO_API_KEY: Final = "00000000000000000000000000000000"

CONF_SITE_ID: Final = "site_id"
CONF_SITE_NAME: Final = "site_name"
CONF_LATITUDE: Final = "latitude"
CONF_LONGITUDE: Final = "longitude"
CONF_SPRING_TIDE_THRESHOLD: Final = "spring_tide_threshold"

DEFAULT_SPRING_TIDE_THRESHOLD: Final = 70

# Les marées sont prévisibles : un rafraîchissement toutes les 6 h suffit
# largement (3 requêtes par rafraîchissement et par port).
UPDATE_INTERVAL: Final = timedelta(hours=6)
# Recalcul local des valeurs (hauteur actuelle, prochaine marée…), sans appel API.
RECOMPUTE_INTERVAL: Final = timedelta(minutes=1)

# Fenêtres de données récupérées.
EXTREMA_DAYS_BEFORE: Final = 1
EXTREMA_DAYS_AFTER: Final = 7
CURVE_HOURS_BEFORE: Final = 12
CURVE_HOURS_AFTER: Final = 48
CURVE_STEP_MINUTES: Final = 10

ATTRIBUTION_FALLBACK: Final = "Données de marée fournies par api-maree.fr sous licence CC BY 4.0"
