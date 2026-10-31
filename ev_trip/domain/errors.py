"""Package exceptions."""


class EVTripError(Exception):
    """Base simulator error."""


class GeocodingError(EVTripError):
    """Address not found or geocoding service unavailable."""


class GeocodingNotFound(GeocodingError):
    """The address matches nothing (typo): the user should fix it."""


class GeocodingUnavailable(GeocodingError):
    """The geocoding service could not be reached; the message is a short cause for the user."""


class RoutingError(EVTripError):
    """No route found or routing service unavailable."""


class RoadClassificationError(EVTripError):
    """Road classification service (Overpass) unavailable."""
