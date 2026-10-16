#!/usr/bin/env python
import os
import sys


def main():
    os.environ.setdefault("DJANGO_SETTINGS_MODULE", "interfaces.django_ui.settings")
    try:
        from django.core.management import execute_from_command_line
    except ImportError:
        sys.exit(
            "Django est introuvable pour cet interpréteur Python :\n"
            f"  {sys.executable}\n\n"
            "Installez les dépendances avec cet interpréteur :\n"
            f'  "{sys.executable}" -m pip install -r requirements-dev.txt\n'
            "ou, dans VS Code : Ctrl+Maj+P > Tasks: Run Task > "
            "« Installer : dépendances (interpréteur sélectionné) ».\n"
            "Vérifiez aussi l'interpréteur choisi : Ctrl+Maj+P > Python: Select Interpreter (idéalement .venv)."
        )

    execute_from_command_line(sys.argv)


if __name__ == "__main__":
    main()
