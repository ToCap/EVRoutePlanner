"""Exceptions du paquet."""


class EVTripError(Exception):
    """Erreur de base du simulateur."""


class GeocodingError(EVTripError):
    """Adresse introuvable ou service de géocodage indisponible."""


class RoutingError(EVTripError):
    """Aucun itinéraire trouvé ou service de routage indisponible."""


class RoadClassificationError(EVTripError):
    """Service de classification des routes (Overpass) indisponible."""
