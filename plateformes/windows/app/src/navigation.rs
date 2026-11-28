// La fenêtre : le tableau de bord dans WebView2, les règles de navigation, les téléchargements, le pont.
//
// Mêmes règles que la vue web du Mac (PageWeb.swift) :
// - seul le tableau de bord (localhost et son port) se charge dans la fenêtre, avec les écrans de
//   l'enveloppe ; un lien http(s) ou mailto part dans le navigateur de la personne ; tout autre schéma
//   (file:, javascript:, autres applications) n'est ouvert nulle part ;
// - un cadre de la page (l'écran de la machine virtuelle, servi sur 127.0.0.1, que le cookie du jeton ne
//   suit pas) reste dans la page ;
// - ce que la vue ne montre pas (Word, PDF joint) va dans Téléchargements, sous un nom libre, puis
//   l'Explorateur le montre.
// Le cookie du jeton est posé par /entree (superviseur.py) : l'enveloppe n'en manipule aucun.

use std::path::{Path, PathBuf};

use serde_json::Value;
use tauri::webview::DownloadEvent;
use tauri::{AppHandle, Manager, Url, WebviewUrl, WebviewWindowBuilder};

use crate::Enveloppe;

pub const FENETRE: &str = "principale";

/// L'adresse des écrans de l'enveloppe (ecran/index.html).
fn adresse_locale() -> &'static str {
    if cfg!(windows) {
        "http://tauri.localhost/index.html"
    } else {
        "tauri://localhost/index.html"
    }
}

pub fn interne(url: &Url, port: u16) -> bool {
    let local = matches!(url.scheme(), "tauri") || url.host_str() == Some("tauri.localhost");
    let tableau = matches!(url.scheme(), "http" | "https")
        && url.host_str().map(|h| h.eq_ignore_ascii_case("localhost")) == Some(true)
        && url.port_or_known_default() == Some(port);
    local || tableau || url.as_str() == "about:blank"
}

pub fn externe(url: &Url) -> bool {
    matches!(url.scheme(), "http" | "https" | "mailto")
}

/// Le pont posé dans chaque page du tableau de bord : la dictée (`window.piston.dictee`, même protocole
/// que celui du Mac, voir compositeur.js), et les liens qui ouvriraient une fenêtre (`target`,
/// `window.open`), ramenés dans celle-ci, où la règle de navigation décide.
fn pont(port: u16) -> String {
    format!(
        r#"(function () {{
  if (location.hostname !== "localhost" || location.port !== "{port}") return;
  var i = window.__TAURI_INTERNALS__;
  if (i && i.invoke) window.piston = {{ dictee: {{ postMessage: function (m) {{ i.invoke("dictee", {{ message: m }}); }} }} }};
  document.addEventListener("click", function (e) {{
    var a = e.target && e.target.closest && e.target.closest("a[target]");
    if (a && a.target !== "_self" && a.href && !e.defaultPrevented) {{ e.preventDefault(); location.href = a.href; }}
  }}, true);
  window.open = function (u) {{ if (u) location.href = new URL(u, location.href).href; return null; }};
}})();"#
    )
}

pub fn creer_fenetre(app: &AppHandle, visible: bool) -> tauri::Result<()> {
    let port = app.state::<Enveloppe>().chemins.port;
    let telecharger = app.clone();
    WebviewWindowBuilder::new(app, FENETRE, WebviewUrl::App("index.html".into()))
        .title("Piston")
        .inner_size(1180.0, 820.0)
        .min_inner_size(900.0, 640.0)
        .visible(visible)
        .initialization_script(&pont(port))
        .on_navigation(move |url| {
            if interne(url, port) {
                return true;
            }
            if externe(url) {
                let _ = tauri_plugin_opener::open_url(url.as_str(), None::<&str>);
            }
            false
        })
        .on_download(move |_webview, evenement| {
            match evenement {
                DownloadEvent::Requested { destination, .. } => {
                    let nom = destination.file_name().map(|n| n.to_owned()).unwrap_or_else(|| "document".into());
                    let dossier = telecharger.path().download_dir().unwrap_or_else(|_| destination.parent()
                        .map(Path::to_path_buf).unwrap_or_default());
                    *destination = nom_libre(&dossier, Path::new(&nom));
                }
                DownloadEvent::Finished { path, success, .. } => {
                    if success {
                        if let Some(p) = path {
                            let _ = tauri_plugin_opener::reveal_item_in_dir(p);
                        }
                    }
                }
                _ => {}
            }
            true
        })
        .build()?;
    Ok(())
}

/// « nom.docx », sinon « nom 1.docx », « nom 2.docx »…
fn nom_libre(dossier: &Path, nom: &Path) -> PathBuf {
    let base = nom.file_stem().map(|s| s.to_string_lossy().into_owned()).unwrap_or_else(|| "document".into());
    let ext = nom.extension().map(|s| s.to_string_lossy().into_owned());
    let mut cible = dossier.join(nom);
    let mut n = 1;
    while cible.exists() {
        let essai = match &ext {
            Some(e) => format!("{base} {n}.{e}"),
            None => format!("{base} {n}"),
        };
        cible = dossier.join(essai);
        n += 1;
    }
    cible
}

pub fn aller(app: &AppHandle, adresse: &str) {
    if let (Some(f), Ok(url)) = (app.get_webview_window(FENETRE), Url::parse(adresse)) {
        let _ = f.navigate(url);
    }
}

/// Un écran de l'enveloppe : attente, erreur (avec « Réessayer ») ou arret.
pub fn ecran(app: &AppHandle, nom: &str, texte: &str, note: Option<&str>) {
    let Some(f) = app.get_webview_window(FENETRE) else { return };
    let message = serde_json::json!({"ecran": nom, "texte": texte, "note": note});
    let deja_la = f.url().map(|u| !interne_tableau(&u)).unwrap_or(false);
    if deja_la {
        let _ = f.eval(&format!("window.montrer && window.montrer({message})"));
    } else if let Ok(mut url) = Url::parse(adresse_locale()) {
        url.query_pairs_mut().append_pair("m", &message.to_string());
        let _ = f.navigate(url);
    }
}

fn interne_tableau(url: &Url) -> bool {
    url.host_str() == Some("localhost") && matches!(url.scheme(), "http" | "https")
}

/// Après le démarrage de la VM, l'accueil se recharge pour dire si l'IA est connectée ; seulement si la
/// personne y est encore.
pub fn recharger_accueil(app: &AppHandle) {
    if let Some(f) = app.get_webview_window(FENETRE) {
        if f.url().map(|u| interne_tableau(&u) && u.path() == "/").unwrap_or(false) {
            let _ = f.eval("location.reload()");
        }
    }
}

/// Une réponse de la dictée à la page : window.dicteeRetour(m), comme sur Mac.
pub fn dictee_retour(app: &AppHandle, m: Value) {
    if let Some(f) = app.get_webview_window(FENETRE) {
        let _ = f.eval(&format!("window.dicteeRetour && window.dicteeRetour({m})"));
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn regles_de_navigation() {
        let u = |s: &str| Url::parse(s).unwrap();
        assert!(interne(&u("http://localhost:8775/sante"), 8775));
        assert!(!interne(&u("http://localhost:8777/vnc.html"), 8775)); // l'écran : jamais dans la page principale
        assert!(!interne(&u("http://127.0.0.1:8775/"), 8775));
        assert!(interne(&u("http://tauri.localhost/index.html"), 8775));
        assert!(externe(&u("https://www.jobup.ch/fr/emplois/")) && externe(&u("mailto:rh@exemple.ch")));
        assert!(!externe(&u("file:///C:/Users/x/secret.txt")) && !externe(&u("javascript:alert(1)")));
    }

    #[test]
    fn noms_libres() {
        let d = std::env::temp_dir().join(format!("piston-test-{}", std::process::id()));
        std::fs::create_dir_all(&d).unwrap();
        std::fs::write(d.join("CV.docx"), b"x").unwrap();
        assert_eq!(nom_libre(&d, Path::new("CV.docx")), d.join("CV 1.docx"));
        assert_eq!(nom_libre(&d, Path::new("Lettre.docx")), d.join("Lettre.docx"));
        let _ = std::fs::remove_dir_all(d);
    }
}
