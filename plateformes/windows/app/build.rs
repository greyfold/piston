fn main() {
    // La commande « dictee » est appelée depuis le tableau de bord (http://localhost:8775), une origine
    // distante pour Tauri : elle n'est permise que par la capacité capabilities/principale.json.
    tauri_build::try_build(
        tauri_build::Attributes::new()
            .app_manifest(tauri_build::AppManifest::new().commands(&["dictee", "reessayer"])),
    )
    .expect("construction de l'enveloppe");
}
