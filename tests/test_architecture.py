"""Dependency rules between layers: domain <- providers, application <- interfaces (never the reverse)."""
import ast
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
FORBIDDEN = {
    "ev_trip/domain": ["ev_trip.providers", "ev_trip.application", "django", "requests", "interfaces"],
    "ev_trip/providers": ["ev_trip.application", "django", "interfaces"],
    "ev_trip/application": ["django", "dash", "interfaces"],
    # the two interfaces are independent of each other
    "interfaces/dash_ui": ["django", "interfaces.django_ui"],
    "interfaces/django_ui": ["dash", "dash_bootstrap_components", "dash_leaflet", "interfaces.dash_ui"],
}
# The domain does no I/O: no network, no files, no blocking clock.
DOMAIN_IO = ["requests", "urllib", "http", "socket", "subprocess", "time", "json", "pathlib"]


def imports(path: Path) -> set[str]:
    found = set()
    for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
        if isinstance(node, ast.Import):
            found.update(a.name for a in node.names)
        elif isinstance(node, ast.ImportFrom):
            if node.level:
                pytest.fail(f"{path.relative_to(ROOT)} : import relatif interdit (utiliser ev_trip.…)")
            found.add(node.module or "")
    return found


def violations(layer: str, banned: list[str]) -> list[str]:
    out = []
    for path in sorted((ROOT / layer).glob("*.py")):
        for mod in imports(path):
            if any(mod == b or mod.startswith(b + ".") for b in banned):
                out.append(f"{path.relative_to(ROOT)} importe {mod}")
    return out


@pytest.mark.parametrize("layer,banned", FORBIDDEN.items())
def test_layers_do_not_depend_on_upper_layers(layer, banned):
    assert violations(layer, banned) == []


def test_domain_does_no_io():
    assert violations("ev_trip/domain", DOMAIN_IO) == []
