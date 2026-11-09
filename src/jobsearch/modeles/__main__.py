"""`uv run python -m jobsearch.modeles` : refait CV.docx et Lettre.docx."""

from . import refaire

for chemin in refaire():
    print(chemin)
