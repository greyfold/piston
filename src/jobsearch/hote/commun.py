"""Ce qui sert à plusieurs systèmes sans en dépendre."""


def code_pays(valeur):
    """« fr_CH.UTF-8 », « fr-CH », « en_FR » → « CH », « FR » ; sinon ""."""
    partie = (valeur or "").strip().split(".")[0].split("@")[0].replace("-", "_")
    code = partie.split("_")[-1] if "_" in partie else ""
    return code.upper() if len(code) == 2 and code.isalpha() else ""


def region_env(env):
    """Le pays d'après les variables de langue (LC_ALL, LC_MESSAGES, LANG)."""
    return code_pays(env.get("LC_ALL") or env.get("LC_MESSAGES") or env.get("LANG") or "")


def echapper_xml(texte):
    return (str(texte).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
            .replace('"', "&quot;").replace("'", "&apos;"))


class CoffreMemoire:
    """Un coffre en mémoire : celui des tests, et le repli d'un système sans coffre."""

    def __init__(self, **entrees):
        self.entrees = dict(entrees)

    def lire(self, service):
        return self.entrees.get(service)

    def ecrire(self, service, compte, valeur):
        self.entrees[service] = valeur
        return True

    def retirer(self, service):
        return self.entrees.pop(service, None) is not None
