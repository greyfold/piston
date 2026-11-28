#!/bin/zsh
# Ancien chemin, gardé pour l'habitude : l'application Mac se construit par plateformes/macos/construire.sh.
exec "${0:A:h:h}/plateformes/macos/construire.sh" "$@"
