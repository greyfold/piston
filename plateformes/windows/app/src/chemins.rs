// Ce que l'application embarque (Python, code) et où vivent les données de la personne.
//
// Construite (plateformes/windows/construire.ps1) : `python/` et `racine/` dans les ressources de l'application,
// les données dans %LOCALAPPDATA%\Recherche emploi. Pour l'essayer sans construire (développement, y compris
// sur Mac) : PISTON_PYTHON, PISTON_RACINE, PISTON_DONNEES et PISTON_PORT remplacent ces valeurs.

use std::path::PathBuf;

use tauri::{AppHandle, Manager};

pub struct Chemins {
    pub python: PathBuf,
    pub racine: PathBuf,
    pub donnees: PathBuf,
    pub port: u16,
}

impl Chemins {
    pub fn lire(app: &AppHandle) -> Self {
        let ressources = sans_prefixe_win(app.path().resource_dir().unwrap_or_default());
        let python = std::env::var_os("PISTON_PYTHON").map(PathBuf::from).unwrap_or_else(|| {
            if cfg!(windows) {
                ressources.join("python").join("python.exe")
            } else {
                ressources.join("python").join("bin").join("python3")
            }
        });
        let racine = std::env::var_os("PISTON_RACINE").map(PathBuf::from).unwrap_or_else(|| ressources.join("racine"));
        let donnees = std::env::var_os("PISTON_DONNEES").map(PathBuf::from).unwrap_or_else(dossier_des_donnees);
        let port = std::env::var("PISTON_PORT").ok().and_then(|p| p.parse().ok()).unwrap_or(8775);
        Chemins { python, racine, donnees, port }
    }
}

/// Le même dossier que `jobsearch.hote.dossier_application()`.
fn dossier_des_donnees() -> PathBuf {
    if cfg!(windows) {
        let base = std::env::var_os("LOCALAPPDATA").map(PathBuf::from).unwrap_or_else(|| {
            PathBuf::from(std::env::var_os("USERPROFILE").unwrap_or_default()).join("AppData").join("Local")
        });
        base.join("Recherche emploi")
    } else if cfg!(target_os = "macos") {
        PathBuf::from(std::env::var_os("HOME").unwrap_or_default())
            .join("Library")
            .join("Application Support")
            .join("Recherche emploi")
    } else {
        std::env::var_os("XDG_DATA_HOME")
            .map(PathBuf::from)
            .unwrap_or_else(|| PathBuf::from(std::env::var_os("HOME").unwrap_or_default()).join(".local").join("share"))
            .join("recherche-emploi")
    }
}

/// Sous Windows, `resource_dir()` renvoie un chemin étendu (`\\?\C:\…`) : Python s'y lance, mais
/// Jinja2 (`FileSystemLoader`) refuse de charger les gabarits depuis un chemin `\\?\`.
fn sans_prefixe_win(chemin: PathBuf) -> PathBuf {
    let Some(s) = chemin.to_str() else { return chemin };
    if let Some(unc) = s.strip_prefix(r"\\?\UNC\") {
        PathBuf::from(format!(r"\\{unc}"))
    } else if let Some(local) = s.strip_prefix(r"\\?\") {
        PathBuf::from(local)
    } else {
        chemin
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn retire_le_prefixe_etendu_windows() {
        assert_eq!(
            sans_prefixe_win(PathBuf::from(r"\\?\C:\Users\user\AppData\Local\Piston")),
            PathBuf::from(r"C:\Users\user\AppData\Local\Piston"),
        );
        assert_eq!(
            sans_prefixe_win(PathBuf::from(r"\\?\UNC\serveur\partage\Piston")),
            PathBuf::from(r"\\serveur\partage\Piston"),
        );
        assert_eq!(
            sans_prefixe_win(PathBuf::from("/Applications/Piston.app/Contents/Resources")),
            PathBuf::from("/Applications/Piston.app/Contents/Resources"),
        );
    }
}
