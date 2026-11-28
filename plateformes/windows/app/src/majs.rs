// Les mises à jour de Piston pour Windows (greffon updater de Tauri) : comme Sparkle sur Mac, une
// vérification par jour auprès du flux publié avec l'application, les nouveautés montrées, et l'installation
// proposée. Une mise à jour n'est acceptée que signée par la clé de publication (minisign) : sa clé publique
// est fixée à la construction (PISTON_CLE_MAJ, plateformes/windows/construire.ps1). Sans elle, ni
// vérification ni menu : comme une construction Mac sans --publier.
//
// L'installation quitte l'application par le chemin habituel (le superviseur éteint d'abord la VM), puis
// l'installateur remplace l'application et la relance ; les données, hors de l'application, ne bougent pas.

use std::sync::atomic::Ordering;
use std::sync::Mutex;
use std::time::Duration;

use tauri::{AppHandle, Manager, Url};

use crate::{superviseur, Enveloppe};

const CLE: Option<&str> = option_env!("PISTON_CLE_MAJ");
const FLUX: &str = match option_env!("PISTON_FLUX_MAJ") {
    Some(f) => f,
    None => "https://greyfold.ch/downloads/piston/windows/latest.json",
};

/// La mise à jour téléchargée, à installer une fois le superviseur arrêté.
static EN_ATTENTE: Mutex<Option<(tauri_plugin_updater::Update, Vec<u8>)>> = Mutex::new(None);

pub fn active() -> bool {
    CLE.is_some_and(|c| !c.is_empty())
}

pub fn greffon() -> Option<tauri::plugin::TauriPlugin<tauri::Wry, tauri_plugin_updater::Config>> {
    CLE.filter(|c| !c.is_empty()).map(|c| tauri_plugin_updater::Builder::new().pubkey(c).build())
}

fn fichier_reglages(app: &AppHandle) -> Option<std::path::PathBuf> {
    app.path().app_config_dir().ok().map(|d| d.join("mises-a-jour.json"))
}

pub fn automatique(app: &AppHandle) -> bool {
    fichier_reglages(app)
        .and_then(|f| std::fs::read_to_string(f).ok())
        .and_then(|t| serde_json::from_str::<serde_json::Value>(&t).ok())
        .and_then(|v| v.get("automatique").and_then(serde_json::Value::as_bool))
        .unwrap_or(true)
}

pub fn basculer_automatique(app: &AppHandle) -> bool {
    let v = !automatique(app);
    if let Some(f) = fichier_reglages(app) {
        if let Some(d) = f.parent() {
            let _ = std::fs::create_dir_all(d);
        }
        let _ = std::fs::write(f, serde_json::json!({ "automatique": v }).to_string());
    }
    v
}

/// La vérification quotidienne (la première une minute après le lancement).
pub fn demarrer(app: &AppHandle) {
    if !active() {
        return;
    }
    let app = app.clone();
    std::thread::spawn(move || {
        std::thread::sleep(Duration::from_secs(60));
        loop {
            if automatique(&app) {
                verifier(&app, false);
            }
            std::thread::sleep(Duration::from_secs(24 * 3600));
        }
    });
}

/// « Rechercher les mises à jour… » (`dire` : aussi quand il n'y a rien, ou en cas d'erreur).
pub fn verifier(app: &AppHandle, dire: bool) {
    if !active() {
        return;
    }
    let app = app.clone();
    std::thread::spawn(move || {
        use tauri_plugin_dialog::{DialogExt, MessageDialogButtons};
        use tauri_plugin_updater::UpdaterExt;
        let resultat = tauri::async_runtime::block_on(async {
            let flux = Url::parse(FLUX).map_err(|e| e.to_string())?;
            let u = app.updater_builder().endpoints(vec![flux]).map_err(|e| e.to_string())?
                .build().map_err(|e| e.to_string())?;
            u.check().await.map_err(|e| e.to_string())
        });
        match resultat {
            Ok(Some(maj)) => {
                let notes = maj.body.clone().unwrap_or_default();
                let oui = app.dialog()
                    .message(format!("Piston {} est disponible (tu as la {}).\n\n{}", maj.version, maj.current_version, notes))
                    .title("Mise à jour de Piston")
                    .buttons(MessageDialogButtons::OkCancelCustom("Installer et relancer".into(), "Plus tard".into()))
                    .blocking_show();
                if !oui {
                    return;
                }
                match tauri::async_runtime::block_on(maj.download(|_, _| {}, || {})) {
                    Ok(octets) => {
                        *EN_ATTENTE.lock().unwrap() = Some((maj, octets));
                        superviseur::quitter(&app); // la VM s'éteint d'abord ; fin() installe
                    }
                    Err(e) => {
                        app.dialog().message(format!("Téléchargement impossible : {e}")).title("Mise à jour de Piston").blocking_show();
                    }
                }
            }
            Ok(None) if dire => {
                app.dialog().message("Piston est à jour.").title("Mise à jour de Piston").blocking_show();
            }
            Err(e) if dire => {
                app.dialog().message(format!("Vérification impossible : {e}")).title("Mise à jour de Piston").blocking_show();
            }
            _ => {}
        }
    });
}

/// La fin du superviseur : on installe la mise à jour en attente (l'installateur relance l'application), ou on sort.
pub fn fin(app: &AppHandle) {
    let e = app.state::<Enveloppe>();
    e.quitte.store(true, Ordering::SeqCst);
    if let Some((maj, octets)) = EN_ATTENTE.lock().unwrap().take() {
        if maj.install(octets).is_ok() {
            app.restart();
        }
    }
    app.exit(0);
}
