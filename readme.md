# Benötigt python und flet 1.0.

pip install pyinstaller
pip install flet[all]

# Erzeugt die exe Datei (knapp 80MB)
flet pack main.py --name Dateiumbenenner --icon icon.png

# Die App

Dateiumbenenner ist ein Dialog, um das umbenennen mehrere Dateien oder Verzeichnisse mit einer Vorschau vorzubereiten.
