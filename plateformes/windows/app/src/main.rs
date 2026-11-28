// Piston pour Windows : l'enveloppe native. Elle ne contient aucune logique métier.
//
// Elle lance le superviseur Python embarqué (`python -m jobsearch superviseur`, src/jobsearch/superviseur.py),
// commun à tous les systèmes, et suit ses événements (lignes JSON) : le tableau de bord à montrer, l'état pour
// le menu, les étapes du démarrage de la machine virtuelle, la fermeture. Ce qui reste ici est propre au
// système : la fenêtre (WebView2), l'icône de la zone de notification et son menu, la dictée sur
// l'ordinateur, les mises à jour, l'ouverture à la connexion, les liens externes et les téléchargements.
//
// Elle tourne aussi sur Mac et Linux (Tauri) : de quoi l'essayer sans Windows (README de plateformes/windows).

#![cfg_attr(not(debug_assertions), windows_subsystem = "windows")]

mod chemins;
#[cfg(feature = "dictee")]
mod dictee;
mod majs;
mod navigation;
mod plateau;
mod superviseur;

use std::sync::atomic::{AtomicBool, Ordering};
use std::sync::Mutex;

use tauri::{AppHandle, Manager, RunEvent, WindowEvent};

/// L'état partagé de l'enveloppe.
pub struct Enveloppe {
    pub chemins: chemins::Chemins,
    pub superviseur: superviseur::Processus,
    /// La fermeture est demandée : on attend la fin du superviseur (VM éteinte), puis on sort.
    pub quitte: AtomicBool,
    pub tableau_pret: AtomicBool,
    pub ia_id: Mutex<String>,
    pub ia_nom: Mutex<String>,
    pub mode: Mutex<String>,
    pub vm_statut: Mutex<Option<String>>,
    pub occupe: Mutex<Option<String>>,
}

impl Enveloppe {
    pub fn base(&self) -> String {
        format!("http://localhost:{}/", self.chemins.port)
    }
}

#[tauri::command]
fn reessayer(app: AppHandle) {
    superviseur::reessayer(&app);
}

#[tauri::command]
fn dictee(app: AppHandle, message: serde_json::Value) {
    #[cfg(feature = "dictee")]
    dictee::recevoir(&app, message);
    #[cfg(not(feature = "dictee"))]
    {
        let _ = message;
        navigation::dictee_retour(&app, serde_json::json!({"type": "erreur", "message": "la dictée n'est pas incluse dans cette construction"}));
    }
}

/// Montre la fenêtre principale (menu, clic sur l'icône, deuxième lancement, notification).
pub fn montrer(app: &AppHandle) {
    if let Some(f) = app.get_webview_window(navigation::FENETRE) {
        let _ = f.unminimize();
        let _ = f.show();
        let _ = f.set_focus();
    }
}

fn main() {
    let mut constructeur = tauri::Builder::default()
        // Une seule instance : un deuxième lancement (ou un clic sur une notification, piston://ouvrir)
        // ramène la fenêtre de la première.
        .plugin(tauri_plugin_single_instance::init(|app, _argv, _cwd| montrer(app)))
        .plugin(tauri_plugin_deep_link::init())
        .plugin(tauri_plugin_autostart::init(
            tauri_plugin_autostart::MacosLauncher::LaunchAgent,
            Some(vec!["--cache"]),
        ))
        .plugin(tauri_plugin_opener::init())
        .plugin(tauri_plugin_dialog::init());
    if let Some(p) = majs::greffon() {
        constructeur = constructeur.plugin(p);
    }
    let app = constructeur
        .invoke_handler(tauri::generate_handler![reessayer, dictee])
        .setup(|app| {
            let chemins = chemins::Chemins::lire(app.handle());
            app.manage(Enveloppe {
                chemins,
                superviseur: superviseur::Processus::default(),
                quitte: AtomicBool::new(false),
                tableau_pret: AtomicBool::new(false),
                ia_id: Mutex::new("claude".into()),
                ia_nom: Mutex::new("Claude".into()),
                mode: Mutex::new("local".into()),
                vm_statut: Mutex::new(None),
                occupe: Mutex::new(None),
            });
            #[cfg(windows)]
            {
                use tauri_plugin_deep_link::DeepLinkExt;
                let _ = app.deep_link().register_all();
            }
            {
                use tauri_plugin_deep_link::DeepLinkExt;
                let h = app.handle().clone();
                app.deep_link().on_open_url(move |_| montrer(&h));
            }
            // Ouverte à la connexion de session (« --cache ») : seulement l'icône, comme sur Mac.
            let cache = std::env::args().any(|a| a == "--cache");
            navigation::creer_fenetre(app.handle(), !cache)?;
            plateau::creer(app.handle())?;
            superviseur::lancer(app.handle());
            majs::demarrer(app.handle());
            Ok(())
        })
        .on_window_event(|fenetre, evenement| {
            // La fenêtre se ferme, l'application reste dans la zone de notification.
            if let WindowEvent::CloseRequested { api, .. } = evenement {
                if fenetre.label() == navigation::FENETRE {
                    let e = fenetre.app_handle().state::<Enveloppe>();
                    if !e.quitte.load(Ordering::SeqCst) {
                        api.prevent_close();
                        let _ = fenetre.hide();
                    }
                }
            }
        })
        .build(tauri::generate_context!())
        .expect("l'enveloppe de Piston ne démarre pas");

    app.run(|app, evenement| {
        if let RunEvent::ExitRequested { api, code, .. } = evenement {
            // Sortie demandée par le système ou la dernière fenêtre : on passe d'abord par la fermeture
            // du superviseur (VM éteinte). Une sortie avec un code vient de nous : on la laisse faire.
            if code.is_none() {
                api.prevent_exit();
                superviseur::quitter(app);
            }
        }
    });
}
