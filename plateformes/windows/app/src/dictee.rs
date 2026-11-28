// La dictée des entretiens : le micro de l'ordinateur (cpal) et la reconnaissance sur l'ordinateur
// (whisper.cpp). La voix ne quitte jamais le PC : la reconnaissance vocale de Windows, elle, l'enverrait
// à Microsoft. Le modèle (whisper « small », multilingue, quantifié) est téléchargé une fois, à la première
// dictée, depuis le dépôt de whisper.cpp, empreinte SHA-256 vérifiée, dans le dossier des données.
//
// Même protocole que la dictée du Mac (Dictee.swift, compositeur.js) :
//   page → enveloppe   {action: "demarrer", langue: "fr-FR"} | {action: "arreter"} | {action: "reglages", page}
//   enveloppe → page   window.dicteeRetour({type: "pret" | "partiel" | "final" | "fin" | "erreur", texte?, message?, reglages?})

use std::io::{Read, Write};
use std::path::PathBuf;
use std::sync::atomic::{AtomicBool, Ordering};
use std::sync::{Arc, Mutex, OnceLock};
use std::time::{Duration, Instant};

use cpal::traits::{DeviceTrait, HostTrait, StreamTrait};
use serde_json::{json, Value};
use sha2::{Digest, Sha256};
use tauri::{AppHandle, Manager};
use whisper_rs::{FullParams, SamplingStrategy, WhisperContext, WhisperContextParameters};

use crate::{navigation, Enveloppe};

const MODELE: &str = "ggml-small-q5_1.bin";
const ADRESSE: &str = "https://huggingface.co/ggerganov/whisper.cpp/resolve/main/ggml-small-q5_1.bin";
const EMPREINTE: &str = "ae85e4a935d7a567bd102fe55afc16bb595bdb618e11b2fc7591bc08120411bb";
const TAUX: u32 = 16_000;
/// Au-delà, la dictée s'arrête d'elle-même (une réponse d'entretien, pas une conférence).
const DUREE_MAX: Duration = Duration::from_secs(300);

static EN_COURS: AtomicBool = AtomicBool::new(false);
static ARRET: OnceLock<Arc<AtomicBool>> = OnceLock::new();
static CONTEXTE: Mutex<Option<Arc<WhisperContext>>> = Mutex::new(None);

fn arret() -> Arc<AtomicBool> {
    ARRET.get_or_init(|| Arc::new(AtomicBool::new(false))).clone()
}

fn envoyer(app: &AppHandle, m: Value) {
    navigation::dictee_retour(app, m);
}

fn erreur(app: &AppHandle, message: &str, reglages: Option<&str>) {
    EN_COURS.store(false, Ordering::SeqCst);
    let mut m = json!({"type": "erreur", "message": message});
    if let Some(r) = reglages {
        m["reglages"] = json!(r);
    }
    envoyer(app, m);
}

pub fn recevoir(app: &AppHandle, message: Value) {
    match message.get("action").and_then(Value::as_str).unwrap_or("") {
        "demarrer" => {
            if EN_COURS.swap(true, Ordering::SeqCst) {
                return;
            }
            arret().store(false, Ordering::SeqCst);
            let langue = message.get("langue").and_then(Value::as_str).unwrap_or("fr-FR").to_string();
            let app = app.clone();
            std::thread::spawn(move || {
                if let Err((m, r)) = dicter(&app, &langue) {
                    erreur(&app, &m, r);
                }
            });
        }
        "arreter" => {
            if EN_COURS.load(Ordering::SeqCst) {
                arret().store(true, Ordering::SeqCst);
            } else {
                envoyer(app, json!({"type": "fin"}));
            }
        }
        "reglages" => {
            // Seules ces pages des Paramètres de Windows peuvent être ouvertes.
            let page = match message.get("page").and_then(Value::as_str).unwrap_or("") {
                "micro" => Some("ms-settings:privacy-microphone"),
                "son" => Some("ms-settings:sound"),
                _ => None,
            };
            if let Some(p) = page {
                let _ = tauri_plugin_opener::open_url(p, None::<&str>);
            }
        }
        _ => {}
    }
}

type Echec = (String, Option<&'static str>);

fn dicter(app: &AppHandle, langue: &str) -> Result<(), Echec> {
    let ctx = contexte(app)?;
    // Le micro : l'appareil d'entrée par défaut, son format, ramené en mono.
    let hote = cpal::default_host();
    let appareil = hote.default_input_device()
        .ok_or_else(|| ("aucun micro trouvé (Paramètres Windows › Confidentialité › Microphone)".to_string(), Some("micro")))?;
    let config = appareil.default_input_config()
        .map_err(|e| (format!("le micro n'a pas pu s'ouvrir ({e})"), Some("micro")))?;
    let taux = config.sample_rate().0;
    let canaux = config.channels() as usize;
    let tampon: Arc<Mutex<Vec<f32>>> = Arc::new(Mutex::new(Vec::new()));
    let t = tampon.clone();
    let ajouter = move |donnees: &[f32]| {
        let mut b = t.lock().unwrap();
        for trame in donnees.chunks(canaux.max(1)) {
            b.push(trame.iter().sum::<f32>() / trame.len() as f32);
        }
    };
    let en_erreur = |e| eprintln!("micro : {e}");
    let flux = match config.sample_format() {
        cpal::SampleFormat::F32 => appareil.build_input_stream(&config.clone().into(), move |d: &[f32], _: &_| ajouter(d), en_erreur, None),
        cpal::SampleFormat::I16 => appareil.build_input_stream(&config.clone().into(),
            move |d: &[i16], _: &_| ajouter(&d.iter().map(|s| *s as f32 / 32768.0).collect::<Vec<_>>()), en_erreur, None),
        cpal::SampleFormat::U16 => appareil.build_input_stream(&config.clone().into(),
            move |d: &[u16], _: &_| ajouter(&d.iter().map(|s| (*s as f32 - 32768.0) / 32768.0).collect::<Vec<_>>()), en_erreur, None),
        autre => return Err((format!("format du micro non pris en charge ({autre:?})"), None)),
    }.map_err(|e| (format!("l'accès au micro est refusé ou impossible ({e}) : Paramètres Windows › Confidentialité › Microphone"), Some("micro")))?;
    flux.play().map_err(|e| (format!("le micro n'a pas pu démarrer ({e})"), Some("micro")))?;
    envoyer(app, json!({"type": "pret"}));

    // Une transcription partielle toutes les deux secondes environ, la finale à l'arrêt.
    let code = langue.split(['-', '_']).next().unwrap_or("fr").to_lowercase();
    let debut = Instant::now();
    let mut dernier = String::new();
    let mut prochain = Instant::now() + Duration::from_millis(2000);
    while !arret().load(Ordering::SeqCst) && debut.elapsed() < DUREE_MAX {
        std::thread::sleep(Duration::from_millis(100));
        if Instant::now() >= prochain {
            let audio = reechantillonner(&tampon.lock().unwrap(), taux);
            if audio.len() > TAUX as usize / 2 {
                if let Ok(texte) = transcrire(&ctx, &audio, &code) {
                    if !texte.is_empty() && texte != dernier {
                        envoyer(app, json!({"type": "partiel", "texte": texte}));
                        dernier = texte;
                    }
                }
            }
            prochain = Instant::now() + Duration::from_millis(2000);
        }
    }
    drop(flux);
    let audio = reechantillonner(&tampon.lock().unwrap(), taux);
    let texte = if audio.len() > TAUX as usize / 4 { transcrire(&ctx, &audio, &code).unwrap_or(dernier) } else { String::new() };
    if !texte.is_empty() {
        envoyer(app, json!({"type": "final", "texte": texte}));
    }
    EN_COURS.store(false, Ordering::SeqCst);
    envoyer(app, json!({"type": "fin"}));
    Ok(())
}

/// Mono, au taux du micro → 16 kHz, par interpolation linéaire.
fn reechantillonner(source: &[f32], taux: u32) -> Vec<f32> {
    if taux == TAUX || source.is_empty() {
        return source.to_vec();
    }
    let rapport = taux as f64 / TAUX as f64;
    let n = (source.len() as f64 / rapport) as usize;
    (0..n)
        .map(|i| {
            let x = i as f64 * rapport;
            let j = x.floor() as usize;
            let f = (x - j as f64) as f32;
            let a = source[j.min(source.len() - 1)];
            let b = source[(j + 1).min(source.len() - 1)];
            a + (b - a) * f
        })
        .collect()
}

fn transcrire(ctx: &WhisperContext, audio: &[f32], langue: &str) -> Result<String, String> {
    let mut etat = ctx.create_state().map_err(|e| e.to_string())?;
    let mut p = FullParams::new(SamplingStrategy::Greedy { best_of: 1 });
    p.set_language(Some(langue));
    p.set_translate(false);
    p.set_print_special(false);
    p.set_print_progress(false);
    p.set_print_realtime(false);
    p.set_print_timestamps(false);
    p.set_suppress_blank(true);
    p.set_n_threads(std::thread::available_parallelism().map(|n| n.get().min(8)).unwrap_or(4) as i32);
    etat.full(p, audio).map_err(|e| e.to_string())?;
    let mut texte = String::new();
    for i in 0..etat.full_n_segments() {
        if let Some(segment) = etat.get_segment(i) {
            if let Ok(s) = segment.to_str_lossy() {
                texte.push_str(&s);
            }
        }
    }
    Ok(texte.trim().to_string())
}

fn contexte(app: &AppHandle) -> Result<Arc<WhisperContext>, Echec> {
    if let Some(c) = CONTEXTE.lock().unwrap().as_ref() {
        return Ok(c.clone());
    }
    let chemin = modele(app)?;
    let ctx = WhisperContext::new_with_params(&chemin.to_string_lossy(), WhisperContextParameters::default())
        .map_err(|e| (format!("le modèle de dictée ne se charge pas ({e})"), None))?;
    let ctx = Arc::new(ctx);
    *CONTEXTE.lock().unwrap() = Some(ctx.clone());
    Ok(ctx)
}

/// Le modèle, dans le dossier des données ; téléchargé la première fois, empreinte vérifiée.
fn modele(app: &AppHandle) -> Result<PathBuf, Echec> {
    let dossier = app.state::<Enveloppe>().chemins.donnees.join("modeles");
    let chemin = dossier.join(MODELE);
    if chemin.exists() {
        return Ok(chemin);
    }
    envoyer(app, json!({"type": "partiel", "texte": "Préparation de la dictée : téléchargement du modèle (190 Mo, une seule fois)…"}));
    std::fs::create_dir_all(&dossier).map_err(|e| (e.to_string(), None))?;
    let partiel = dossier.join(format!("{MODELE}.part"));
    let reponse = ureq::get(ADRESSE).call().map_err(|e| (format!("téléchargement du modèle de dictée impossible ({e})"), None))?;
    let mut lecteur = reponse.into_reader();
    let mut fichier = std::fs::File::create(&partiel).map_err(|e| (e.to_string(), None))?;
    let mut h = Sha256::new();
    let mut bloc = vec![0u8; 1 << 20];
    loop {
        let n = lecteur.read(&mut bloc).map_err(|e| (format!("téléchargement interrompu ({e})"), None))?;
        if n == 0 {
            break;
        }
        h.update(&bloc[..n]);
        fichier.write_all(&bloc[..n]).map_err(|e| (e.to_string(), None))?;
    }
    drop(fichier);
    let empreinte: String = h.finalize().iter().map(|o| format!("{o:02x}")).collect();
    if empreinte != EMPREINTE {
        let _ = std::fs::remove_file(&partiel);
        return Err(("modèle de dictée : empreinte fausse, téléchargement rejeté".into(), None));
    }
    std::fs::rename(&partiel, &chemin).map_err(|e| (e.to_string(), None))?;
    Ok(chemin)
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn reechantillonnage_de_48_a_16_khz() {
        let s: Vec<f32> = (0..48_000).map(|i| (i % 3) as f32).collect();
        assert_eq!(reechantillonner(&s, 48_000).len(), 16_000);
        assert_eq!(reechantillonner(&s, 16_000).len(), 48_000);
    }
}
