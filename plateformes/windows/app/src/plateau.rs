// L'icône de la zone de notification et son menu : le même que la barre des menus du Mac (App.swift).
// L'état d'abord (passage, annonces à trancher, IA, machine virtuelle, ce qui tourne), puis les actions.

use std::sync::atomic::Ordering;
use std::sync::Mutex;

use serde_json::{json, Value};
use tauri::menu::{CheckMenuItem, Menu, MenuItem, PredefinedMenuItem};
use tauri::tray::{MouseButton, MouseButtonState, TrayIconBuilder, TrayIconEvent};
use tauri::{AppHandle, Manager, Wry};
use tauri_plugin_autostart::ManagerExt;

use crate::{majs, navigation, superviseur, Enveloppe};

pub struct Plateau {
    passage: MenuItem<Wry>,
    trancher: MenuItem<Wry>,
    ia: MenuItem<Wry>,
    vm: MenuItem<Wry>,
    en_cours: MenuItem<Wry>,
    message: MenuItem<Wry>,
    connexion: MenuItem<Wry>,
    lancer: MenuItem<Wry>,
    vm_action: MenuItem<Wry>,
    ouverture: CheckMenuItem<Wry>,
    majs_auto: Option<CheckMenuItem<Wry>>,
    vm_allumee: Mutex<bool>,
}

fn info(app: &AppHandle, id: &str, texte: &str) -> tauri::Result<MenuItem<Wry>> {
    MenuItem::with_id(app, id, texte, false, None::<&str>)
}

fn action(app: &AppHandle, id: &str, texte: &str) -> tauri::Result<MenuItem<Wry>> {
    MenuItem::with_id(app, id, texte, true, None::<&str>)
}

pub fn creer(app: &AppHandle) -> tauri::Result<()> {
    let ouverture_auto = app.autolaunch().is_enabled().unwrap_or(false);
    let p = Plateau {
        passage: info(app, "i-passage", "Lecture de l'état…")?,
        trancher: info(app, "i-trancher", "")?,
        ia: info(app, "i-ia", "IA : …")?,
        vm: info(app, "i-vm", "Machine virtuelle : …")?,
        en_cours: info(app, "i-en-cours", "")?,
        message: info(app, "i-message", "")?,
        connexion: action(app, "connexion", "Se connecter à Claude…")?,
        lancer: action(app, "passage", "Lancer un passage")?,
        vm_action: action(app, "vm", "Démarrer la machine virtuelle")?,
        ouverture: CheckMenuItem::with_id(app, "ouverture", "Ouvrir à la connexion", true, ouverture_auto, None::<&str>)?,
        majs_auto: if majs::active() {
            Some(CheckMenuItem::with_id(app, "majs-auto", "Vérifier les mises à jour chaque jour", true,
                                        majs::automatique(app), None::<&str>)?)
        } else {
            None
        },
        vm_allumee: Mutex::new(false),
    };
    let ouvrir = action(app, "ouvrir", "Ouvrir le tableau de bord")?;
    let navigateur = action(app, "navigateur", "Navigateur de la machine virtuelle")?;
    let verifier = action(app, "majs", "Rechercher les mises à jour…")?;
    let quitter = action(app, "quitter", "Quitter Piston")?;
    let sep = || PredefinedMenuItem::separator(app);
    let (s1, s2, s3, s4) = (sep()?, sep()?, sep()?, sep()?);
    let mut items: Vec<&dyn tauri::menu::IsMenuItem<Wry>> = vec![
        &p.passage, &p.trancher, &p.ia, &p.vm, &p.en_cours, &p.message, &s1,
        &ouvrir, &navigateur, &p.lancer, &p.connexion, &s2, &p.vm_action, &s3, &p.ouverture,
    ];
    if let Some(m) = &p.majs_auto {
        items.push(m);
        items.push(&verifier);
    }
    items.push(&s4);
    items.push(&quitter);
    let menu = Menu::with_items(app, &items)?;
    let _ = p.trancher.set_text("");
    TrayIconBuilder::with_id("plateau")
        .icon(app.default_window_icon().cloned().expect("icône de l'application"))
        .tooltip("Piston")
        .menu(&menu)
        .show_menu_on_left_click(false)
        .on_menu_event(|app, evenement| choisir(app, evenement.id().as_ref()))
        .on_tray_icon_event(|icone, evenement| {
            if let TrayIconEvent::Click { button: MouseButton::Left, button_state: MouseButtonState::Up, .. } = evenement {
                crate::montrer(icone.app_handle());
            }
        })
        .build(app)?;
    app.manage(p);
    Ok(())
}

fn choisir(app: &AppHandle, id: &str) {
    let e = app.state::<Enveloppe>();
    match id {
        "ouvrir" => crate::montrer(app),
        "navigateur" => {
            crate::montrer(app);
            if e.tableau_pret.load(Ordering::SeqCst) {
                navigation::aller(app, &format!("{}navigateur", e.base()));
            }
        }
        "passage" => superviseur::ordonner(app, json!({"ordre": "passage"})),
        "connexion" => {
            // La modale de connexion du tableau de bord (?connexion=<id>, _connexion_ia.html).
            crate::montrer(app);
            if e.tableau_pret.load(Ordering::SeqCst) {
                let id = e.ia_id.lock().unwrap().clone();
                navigation::aller(app, &format!("{}?connexion={}", e.base(), id));
            }
        }
        "vm" => {
            let allumee = *app.state::<Plateau>().vm_allumee.lock().unwrap();
            superviseur::ordonner(app, json!({"ordre": if allumee { "vm_arreter" } else { "vm_demarrer" }}));
        }
        "ouverture" => {
            let a = app.autolaunch();
            let actif = a.is_enabled().unwrap_or(false);
            let r = if actif { a.disable() } else { a.enable() };
            if let Err(err) = r {
                message(app, &format!("Ouverture à la connexion impossible : {err}"));
            }
            let _ = app.state::<Plateau>().ouverture.set_checked(a.is_enabled().unwrap_or(false));
        }
        "majs-auto" => {
            let v = majs::basculer_automatique(app);
            if let Some(m) = &app.state::<Plateau>().majs_auto {
                let _ = m.set_checked(v);
            }
        }
        "majs" => majs::verifier(app, true),
        "quitter" => superviseur::quitter(app),
        _ => {}
    }
}

fn texte(v: &Value, cle: &str) -> Option<String> {
    v.get(cle).and_then(Value::as_str).map(str::to_owned)
}

/// L'état lu par le superviseur (/api/etat).
pub fn etat(app: &AppHandle, e: &Value) {
    let p = app.state::<Plateau>();
    if let Some(pa) = e.get("passage") {
        let _ = p.passage.set_text(texte(pa, "texte").unwrap_or_default());
    }
    let _ = p.trancher.set_text(match e.get("a_trancher").and_then(Value::as_i64) {
        Some(0) => "Rien à trancher".to_string(),
        Some(n) => format!("{n} annonce(s) à trancher"),
        None => String::new(),
    });
    let c = e.get("claude").cloned().unwrap_or(Value::Null);
    let nom = texte(&c, "nom").unwrap_or_else(|| "Claude".into());
    let connecte = c.get("connecte").and_then(Value::as_bool) == Some(true);
    let _ = p.ia.set_text(format!("{nom} : {}", if connecte { "connecté" } else { "pas connecté" }));
    let _ = p.connexion.set_text(format!("{} à {nom}…", if connecte { "Se reconnecter" } else { "Se connecter" }));
    let distant = texte(e, "mode").as_deref() == Some("distant");
    let statut = e.get("vm").filter(|v| !v.is_null()).map(|v| texte(v, "statut").unwrap_or_else(|| "absente".into()));
    let libelle = match statut.as_deref() {
        Some("Running") => "démarrée",
        Some("Stopped") => "arrêtée",
        None | Some("absente") => "pas encore créée",
        Some(autre) => autre,
    };
    let _ = p.vm.set_text(if distant { format!("Machine virtuelle : {libelle}") } else { String::new() });
    let allumee = statut.as_deref() == Some("Running");
    *p.vm_allumee.lock().unwrap() = allumee;
    let _ = p.vm_action.set_text(if allumee { "Arrêter la machine virtuelle" } else { "Démarrer la machine virtuelle" });
    let _ = p.vm_action.set_enabled(distant && app.state::<Enveloppe>().occupe.lock().unwrap().is_none());
}

/// Ce qui tourne (commande du menu), et l'étape en cours du démarrage de la VM.
pub fn occupe(app: &AppHandle, titre: Option<&str>, etape: Option<&str>) {
    let p = app.state::<Plateau>();
    let _ = p.en_cours.set_text(match (titre, etape) {
        (_, Some(e)) => format!("En cours : {e}…"),
        (Some(t), None) => format!("En cours : {t}…"),
        (None, None) => String::new(),
    });
    let libre = titre.is_none();
    let _ = p.lancer.set_enabled(libre);
    let distant = app.state::<Enveloppe>().mode.lock().unwrap().as_str() == "distant";
    let _ = p.vm_action.set_enabled(libre && distant);
}

pub fn message(app: &AppHandle, texte: &str) {
    let _ = app.state::<Plateau>().message.set_text(texte);
}
