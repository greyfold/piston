// L'état que l'application montre et les commandes qu'elle lance.
//
// Tout passe par la plateforme Python embarquée (Contents/Resources/python et racine) : l'état se
// lit sur le tableau de bord (GET /api/etat), les actions sont des commandes `jobsearch` (passage,
// vm, connexion). L'application ne touche jamais la base elle-même.
//
// Tout tourne dans l'application, rien à côté : son dossier de données (base, profil, dossiers,
// réservé au compte), son tableau de bord sur son propre port, la machine virtuelle (Lima embarqué)
// où tournent Claude Code et Chrome, et le passage du matin,
// l'alerte et la surveillance, lancés toutes les cinq minutes par `jobsearch echeances` (ce qui
// est dû part, le reste attend). « Ouvrir à la connexion » la lance à l'ouverture de session.

import AppKit
import Foundation
import ServiceManagement

struct EtatAPI: Decodable {
    struct Passage: Decodable { let niveau: String; let texte: String }
    struct Claude: Decodable { let connecte: Bool; let message: String }
    struct VM: Decodable { let existe: Bool; let statut: String? }
    let donnees: String?
    let passage: Passage
    let a_trancher: Int
    let claude: Claude
    let mode: String
    let vm: VM?
}

@MainActor
final class Etat: ObservableObject {
    @Published var tableauPret = false
    @Published var erreurTableau: String?
    @Published var passage = "Lecture de l'état…"
    @Published var niveau = "ok"
    @Published var aTrancher = 0
    @Published var claudeConnecte = false
    @Published var claudeMessage = ""
    @Published var mode = "local"
    @Published var vmStatut: String?
    @Published var occupe: String?
    @Published var dernierMessage: String?

    /// « Se connecter à Claude… » du menu : la page ouvre sa modale de connexion dès qu'elle est prête.
    @Published var connexionDemandee = false
    /// Une page du tableau de bord à montrer dans la fenêtre (« navigateur » : l'écran de la VM).
    @Published var pageDemandee: String?
    /// Au lancement, la machine virtuelle démarre pendant que l'accueil s'affiche : une fois qu'elle
    /// répond, l'accueil se recharge pour dire si l'IA est connectée (bandeau de l'accueil).
    @Published var rechargerAccueil = false
    @Published var ouvertureAuto = SMAppService.mainApp.status == .enabled

    let racine: String                    // le code embarqué : src, config, pyproject.toml
    let python: String                    // le Python embarqué
    let limactl: String                   // Lima embarqué (machine virtuelle)
    let donnees: String
    let port = 8775
    let base: URL
    private var processusWeb: Process?
    private var echeances: Process?
    private var minuterie: Timer?
    private var minuterieEcheances: Timer?
    private var demarre = false

    init() {
        let ressources = Bundle.main.resourcePath ?? ""
        racine = ressources + "/racine"
        python = ressources + "/python/bin/python3"
        limactl = ressources + "/lima/bin/limactl"
        let maison = FileManager.default.homeDirectoryForCurrentUser.path
        donnees = maison + "/Library/Application Support/Recherche emploi"
        base = URL(string: "http://localhost:\(port)/")!
        // Coordonnées, recherche, dossiers : le dossier n'est ouvert qu'au compte de la personne.
        try? FileManager.default.createDirectory(atPath: donnees, withIntermediateDirectories: true,
                                                 attributes: [.posixPermissions: 0o700])
        try? FileManager.default.setAttributes([.posixPermissions: 0o700], ofItemAtPath: donnees)
    }

    private var environnement: [String: String] {
        var env = ProcessInfo.processInfo.environment
        let maison = FileManager.default.homeDirectoryForCurrentUser.path
        env["PATH"] = ["/opt/homebrew/bin", "/usr/local/bin", maison + "/.local/bin", "/usr/bin", "/bin", "/usr/sbin",
                       "/sbin"].joined(separator: ":")
        env["JOBSEARCH_HOME"] = racine
        env["JOBSEARCH_DATA"] = donnees
        env["JOBSEARCH_PORT"] = String(port)
        env["JOBSEARCH_LIMACTL"] = limactl                     // Claude Code et Chrome : dans la VM
        env["PYTHONNOUSERSITE"] = "1"                          // rien du Python de l'utilisateur
        env["PYTHONDONTWRITEBYTECODE"] = "1"                   // l'application signée reste intacte
        for cle in ["JOBSEARCH_DB", "JOBSEARCH_PROFIL", "JOBSEARCH_DEFAUTS", "PYTHONPATH", "PYTHONHOME",
                    "VIRTUAL_ENV", "LIMA_HOME", "CLAUDE_CONFIG_DIR", "ANTHROPIC_API_KEY"] {
            env.removeValue(forKey: cle)
        }
        return env
    }

    // MARK: - Démarrage

    func demarrer() {
        guard !demarre else { return }
        demarre = true
        Task {
            await assurerTableau()
            await rafraichir()
            if mode == "distant", vmStatut != "Running" {
                executer(["vm", "demarrer"], titre: "Démarrage de la machine virtuelle") { [weak self] in
                    self?.rechargerAccueil = true
                }
            }
            lancerEcheances()
        }
        minuterie = Timer.scheduledTimer(withTimeInterval: 30, repeats: true) { [weak self] _ in
            Task { @MainActor in await self?.rafraichir() }
        }
        minuterieEcheances = Timer.scheduledTimer(withTimeInterval: 300, repeats: true) { [weak self] _ in
            Task { @MainActor in self?.lancerEcheances() }
        }
    }

    /// Un processus Python embarqué : `python3 -m jobsearch <arguments>`.
    private func processus(_ arguments: [String]) -> Process {
        let p = Process()
        p.executableURL = URL(fileURLWithPath: python)
        p.arguments = ["-m", "jobsearch"] + arguments
        p.currentDirectoryURL = URL(fileURLWithPath: donnees)
        p.environment = environnement
        return p
    }

    /// Ce qui est dû (passage du matin, alerte, surveillance), en arrière-plan ; un seul à la fois.
    /// Indépendant des commandes du menu : un passage lancé à la main pendant ce temps est refusé
    /// par le verrou du passage, et réciproquement.
    func lancerEcheances() {
        guard echeances == nil, FileManager.default.isExecutableFile(atPath: python) else { return }
        let p = processus(["echeances"])
        let sortie = Pipe()
        p.standardOutput = sortie
        p.standardError = FileHandle.nullDevice
        p.terminationHandler = { _ in
            let texte = String(data: sortie.fileHandleForReading.readDataToEndOfFile(), encoding: .utf8) ?? ""
            Task { @MainActor in
                self.echeances = nil
                if texte.contains("\"passage\"") { self.dernierMessage = "Passage du matin : terminé." }
                await self.rafraichir()
            }
        }
        do { try p.run(); echeances = p } catch { echeances = nil }
    }

    /// « Ouvrir à la connexion » : l'application démarre avec la session, pour que le passage du
    /// matin parte à l'heure sans qu'on y pense.
    func basculerOuvertureAuto() {
        do {
            if SMAppService.mainApp.status == .enabled {
                try SMAppService.mainApp.unregister()
            } else {
                try SMAppService.mainApp.register()
            }
        } catch {
            dernierMessage = "Ouverture à la connexion impossible : \(error.localizedDescription)"
        }
        ouvertureAuto = SMAppService.mainApp.status == .enabled
    }

    /// Le dossier de données du tableau de bord qui répond sur le port, nil si aucun ne répond.
    private func repond() async -> String? {
        var req = URLRequest(url: base.appendingPathComponent("api/etat"))
        req.timeoutInterval = 3
        guard let (d, rep) = try? await URLSession.shared.data(for: req),
              (rep as? HTTPURLResponse)?.statusCode == 200 else { return nil }
        return (try? JSONDecoder().decode(EtatAPI.self, from: d))?.donnees ?? ""
    }

    private func memeDossier(_ autre: String) -> Bool {
        URL(fileURLWithPath: autre).standardizedFileURL.path == URL(fileURLWithPath: donnees).standardizedFileURL.path
    }

    /// Le tableau de bord de cette installation : déjà lancé (par l'application ou launchd)
    /// ou lancé ici. Un tableau de bord d'une autre installation sur le même port est refusé.
    func assurerTableau() async {
        erreurTableau = nil
        if let autre = await repond() {
            if memeDossier(autre) { tableauPret = true; return }
            erreurTableau = "Le port \(port) est pris par le tableau de bord d'une autre installation (\(autre))."
            return
        }
        guard FileManager.default.isExecutableFile(atPath: python) else {
            erreurTableau = "Python embarqué introuvable : \(python). L'application est incomplète : reconstruis-la."
            return
        }
        let p = processus(["web", "--hote", "127.0.0.1", "--port", String(port)])
        p.standardOutput = FileHandle.nullDevice
        p.standardError = FileHandle.nullDevice
        do { try p.run() } catch {
            erreurTableau = "Le tableau de bord ne démarre pas : \(error.localizedDescription)"
            return
        }
        processusWeb = p
        for _ in 0..<60 {
            try? await Task.sleep(nanoseconds: 500_000_000)
            if await repond() != nil { tableauPret = true; return }
        }
        erreurTableau = "Le tableau de bord ne répond pas sur le port \(port)."
    }

    func rafraichir() async {
        guard let (donnees, _) = try? await URLSession.shared.data(from: base.appendingPathComponent("api/etat")),
              let e = try? JSONDecoder().decode(EtatAPI.self, from: donnees) else { return }
        passage = e.passage.texte
        niveau = e.passage.niveau
        aTrancher = e.a_trancher
        claudeConnecte = e.claude.connecte
        claudeMessage = e.claude.message
        mode = e.mode
        vmStatut = e.vm?.statut ?? (e.vm == nil ? nil : "absente")
    }

    // MARK: - Commandes

    /// Une commande `jobsearch` en arrière-plan ; une seule à la fois. `apres` : une fois terminée et l'état relu.
    func executer(_ arguments: [String], titre: String, apres: (@MainActor () -> Void)? = nil) {
        guard occupe == nil else { return }
        occupe = titre
        let p = processus(arguments)
        let sortie = Pipe()
        p.standardOutput = sortie
        p.standardError = sortie
        p.terminationHandler = { proc in
            let texte = String(data: sortie.fileHandleForReading.readDataToEndOfFile(), encoding: .utf8) ?? ""
            let code = proc.terminationStatus
            Task { @MainActor in
                self.occupe = nil
                let derniere = texte.split(separator: "\n").last.map(String.init) ?? ""
                self.dernierMessage = code == 0 ? "\(titre) : terminé." : "\(titre) : échec. \(derniere)"
                await self.rafraichir()
                apres?()
            }
        }
        do { try p.run() } catch {
            occupe = nil
            dernierMessage = "\(titre) : impossible de lancer la commande (\(error.localizedDescription))."
        }
    }

    func lancerPassage() { executer(["passage"], titre: "Passage") }
    func demarrerVM() { executer(["vm", "demarrer"], titre: "Démarrage de la machine virtuelle") }
    func arreterVM() { executer(["vm", "arreter"], titre: "Arrêt de la machine virtuelle") }

    // MARK: - Fin

    func quitter() {
        processusWeb?.terminate()             // seulement s'il a été lancé par l'application
        // Un passage du matin en cours (echeances) va à son terme : il est détaché du menu.
    }
}
