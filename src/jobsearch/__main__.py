"""`python -m jobsearch …` : la ligne de commande (l'application lance ainsi son Python embarqué)."""

import sys

from .cli import main

sys.exit(main())
