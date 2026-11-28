// Le superviseur Python : lancé ici, suivi ligne à ligne (événements JSON), commandé par son entrée.
// Voir src/jobsearch/superviseur.py pour le dialogue.

use std::io::{BufRead, BufReader, Write};
use std::process::{Child, ChildStdin, Command, Stdio};
use std::sync::atomic::Ordering;
use std::sync::Mutex;
use std::time::Duration;

use serde_json::{json, Value};
use tauri::{AppHandle, Manager};

use crate::{majs, navigation, plateau, Enveloppe};

#[derive(Default)]
pub struct Processus {
    enfant: Mutex<Option<Child>>,
    entree: Mutex<Option<ChildStdin>>,
}

/// Les variables qui ne doivent pas passer de l'environnement de la personne au Python embarqué.
const RETIREES: &[&str] = &[
    "JOBSEARCH_DB", "JOBSEARCH_PROFIL", "JOBSEARCH_DEFAUTS", "PYTHONPATH", "PYTHONHOME", "VIRTUAL_ENV",
    "LIMA_HOME", "CLAUDE_CONFIG_DIR", "ANTHROPIC_API_KEY", "OPENAI_API_KEY", "XAI_API_KEY", "GEMINI_API_KEY",
];

pub fn lancer(app: &AppHandle) {
    let e = app.state::<Enveloppe>();
    let c = &e.chemins;
    if !c.python.exists() {
        navigation::ecran(app, "erreur", &format!(
            "Python embarqué introuvable : {}. L'application est incomplète : réinstalle-la.", c.python.display()), None);
        return;
    }
    let _ = std::fs::create_dir_all(&c.donnees);
    let mut cmd = Command::new(&c.python);
    cmd.args(["-m", "jobsearch", "superviseur", "--hote", "127.0.0.1", "--port", &c.port.to_string()])
        .current_dir(&c.donnees)
        .stdin(Stdio::piped())
        .stdout(Stdio::piped())
        .stderr(Stdio::null())
        .env("JOBSEARCH_HOME", &c.racine)
        .env("JOBSEARCH_DATA", &c.donnees)
        .env("JOBSEARCH_PORT", c.port.to_string())
        .env("PYTHONNOUSERSITE", "1")          // rien du Python de la personne
        .env("PYTHONDONTWRITEBYTECODE", "1")   // l'application installée reste intacte
        .env("PYTHONUNBUFFERED", "1")          // les événements arrivent sans attendre
        .env("PYTHONUTF8", "1");
    for v in RETIREES {
        cmd.env_remove(v);
    }
    #[cfg(windows)]
    {
        use std::os::windows::process::CommandExt;
        cmd.creation_flags(0x0800_0000); // CREATE_NO_WINDOW : pas de console
    }
    let mut enfant = match cmd.spawn() {
        Ok(enfant) => enfant,
        Err(err) => {
            navigation::ecran(app, "erreur", &format!("Le superviseur ne démarre pas : {err}"), None);
            return;
        }
    };
    let sortie = enfant.stdout.take();
    *e.superviseur.entree.lock().unwrap() = enfant.stdin.take();
    *e.superviseur.enfant.lock().unwrap() = Some(enfant);
    navigation::ecran(app, "attente", "Démarrage du tableau de bord…", None);

    let app = app.clone();
    std::thread::spawn(move || {
        if let Some(sortie) = sortie {
            for ligne in BufReader::new(sortie).lines() {
                let Ok(ligne) = ligne else { break };
                if let Ok(evenement) = serde_json::from_str::<Value>(&ligne) {
                    recevoir(&app, evenement);
                }
            }
        }
        termine(&app);
    });
}

/// Un ordre au superviseur, une ligne JSON.
pub fn ordonner(app: &AppHandle, ordre: Value) {
    let e = app.state::<Enveloppe>();
    let mut entree = e.superviseur.entree.lock().unwrap();
    if let Some(flux) = entree.as_mut() {
        let _ = writeln!(flux, "{ordre}");
        let _ = flux.flush();
    }
}

fn vivant(app: &AppHandle) -> bool {
    let e = app.state::<Enveloppe>();
    let mut enfant = e.superviseur.enfant.lock().unwrap();
    matches!(enfant.as_mut().map(|c| c.try_wait()), Some(Ok(None)))
}

pub fn reessayer(app: &AppHandle) {
    if vivant(app) {
        ordonner(app, json!({"ordre": "reessayer"}));
    } else {
        lancer(app);
    }
}

/// « Quitter Piston » : le superviseur arrête ce qui tourne, éteint la machine virtuelle, puis le tableau de
/// bord, et annonce la fin ; l'application sort alors (ou après quatre minutes et demie, au plus).
pub fn quitter(app: &AppHandle) {
    let e = app.state::<Enveloppe>();
    if e.quitte.swap(true, Ordering::SeqCst) {
        return;
    }
    if !vivant(app) {
        app.exit(0);
        return;
    }
    ordonner(app, json!({"ordre": "quitter"}));
    let app = app.clone();
    std::thread::spawn(move || {
        std::thread::sleep(Duration::from_secs(270));
        app.exit(0);
    });
}

fn texte(v: &Value, cle: &str) -> Option<String> {
    v.get(cle).and_then(Value::as_str).map(str::to_owned)
}

fn recevoir(app: &AppHandle, ev: Value) {
    let e = app.state::<Enveloppe>();
    match ev.get("evenement").and_then(Value::as_str).unwrap_or("") {
        "tableau" => {
            if ev.get("pret").and_then(Value::as_bool) == Some(true) {
                e.tableau_pret.store(true, Ordering::SeqCst);
                let adresse = texte(&ev, "entree").unwrap_or_else(|| e.base());
                navigation::aller(app, &adresse);
            } else {
                e.tableau_pret.store(false, Ordering::SeqCst);
                let erreur = texte(&ev, "erreur").unwrap_or_else(|| "Le tableau de bord ne répond pas.".into());
                navigation::ecran(app, "erreur", &erreur, None);
            }
        }
        "etat" => {
            if let Some(c) = ev.get("claude") {
                if let Some(id) = texte(c, "id").filter(|s| !s.is_empty()) {
                    *e.ia_id.lock().unwrap() = id;
                }
                if let Some(nom) = texte(c, "nom").filter(|s| !s.is_empty()) {
                    *e.ia_nom.lock().unwrap() = nom;
                }
            }
            if let Some(m) = texte(&ev, "mode") {
                *e.mode.lock().unwrap() = m;
            }
            *e.vm_statut.lock().unwrap() =
                ev.get("vm").filter(|v| !v.is_null()).map(|v| texte(v, "statut").unwrap_or_else(|| "absente".into()));
            plateau::etat(app, &ev);
        }
        "occupe" => {
            let titre = texte(&ev, "titre");
            *e.occupe.lock().unwrap() = titre.clone();
            plateau::occupe(app, titre.as_deref(), None);
        }
        "etape" => plateau::occupe(app, e.occupe.lock().unwrap().clone().as_deref(), texte(&ev, "texte").as_deref()),
        "message" => {
            if !e.quitte.load(Ordering::SeqCst) {
                plateau::message(app, texte(&ev, "texte").as_deref().unwrap_or(""));
            }
        }
        "page" => {
            if e.tableau_pret.load(Ordering::SeqCst) {
                if let Some(page) = texte(&ev, "page") {
                    navigation::aller(app, &format!("{}{}", e.base(), page));
                }
            }
        }
        "recharger_accueil" => navigation::recharger_accueil(app),
        "arret" => {
            let interrompu = ev.get("passage_interrompu").and_then(Value::as_bool) == Some(true);
            crate::montrer(app);
            navigation::ecran(app, "arret", "Arrêt de la machine virtuelle…", Some(if interrompu {
                "Le passage en cours est interrompu : il reprendra au prochain lancement."
            } else {
                "Cela prend quelques secondes, deux minutes au plus."
            }));
        }
        "fin" => {
            if e.quitte.load(Ordering::SeqCst) {
                majs::fin(app);
            }
        }
        _ => {}
    }
}

/// Le superviseur s'est arrêté : à la fermeture, c'est la fin ; sinon une panne, que « Réessayer » relance.
fn termine(app: &AppHandle) {
    let e = app.state::<Enveloppe>();
    let code = e.superviseur.enfant.lock().unwrap().as_mut().and_then(|c| c.wait().ok()).and_then(|s| s.code());
    *e.superviseur.entree.lock().unwrap() = None;
    if e.quitte.load(Ordering::SeqCst) {
        majs::fin(app);
        return;
    }
    e.tableau_pret.store(false, Ordering::SeqCst);
    navigation::ecran(app, "erreur", &format!(
        "Le superviseur de l'application s'est arrêté (code {}). Réessaie, ou réinstalle l'application si cela persiste.",
        code.map(|c| c.to_string()).unwrap_or_else(|| "?".into())), None);
}
