"""Package exceptions."""


class EVTripError(Exception):
    """Base simulator error."""


class GeocodingError(EVTripError):
    """Address not found or geocoding service unavailable."""


class RoutingError(EVTripError):
    """No route found or routing service unavailable."""


class RoadClassificationError(EVTripError):
    """Road classification service (Overpass) unavailable."""
