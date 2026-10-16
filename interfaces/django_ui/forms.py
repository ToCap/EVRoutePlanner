from django import forms


def _number(label, *, min_value, max_value, initial, required=True):
    """Numeric field: bounds checked server-side and browser-side (min/max attributes)."""
    return forms.FloatField(
        label=label,
        min_value=min_value,
        max_value=max_value,
        initial=initial,
        required=required,
        widget=forms.NumberInput(attrs={"inputmode": "decimal", "step": "any", "autocomplete": "off"}),
        error_messages={
            "invalid": "Saisir une valeur numérique.",
            "required": "Ce champ est obligatoire.",
        },
    )


class TripForm(forms.Form):
    origin = forms.CharField(label="Départ", max_length=200, widget=forms.TextInput(attrs={"placeholder": "Paris"}))
    destination = forms.CharField(
        label="Arrivée", max_length=200, widget=forms.TextInput(attrs={"placeholder": "Saint-Lô"})
    )
    temp_c = _number("Température extérieure (°C)", min_value=-50, max_value=60, initial=15)
    battery_kwh = _number("Capacité batterie (kWh)", min_value=1, max_value=300, initial=52)
    start_soc = _number("Charge au départ (%)", min_value=0, max_value=100, initial=90)
    min_soc = _number("Seuil minimum (%)", min_value=0, max_value=100, initial=10)
    max_detour_km = _number(
        "Détour max vers une borne (km, 0 = pas de recherche)", min_value=0, max_value=50, initial=5, required=False
    )
    use_overpass = forms.BooleanField(
        label="Type de route via OpenStreetMap (plus précis, plus lent)", required=False, initial=True
    )
