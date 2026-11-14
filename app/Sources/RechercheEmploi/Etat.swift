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
    let pid: Int32?          // le processus du tableau de bord qui répond
    let racine: String?      // le dossier (résolu) du paquet `jobsearch` qu'il sert ; absent : ancien serveur
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
    /// Le jeton du tableau de bord (fichier `jeton` du dossier de données, écrit par le serveur avant
    /// d'écouter) : exigé pour toute requête, en en-tête ici, en cookie dans la vue web (PageWeb.swift).
    private(set) var jeton: String?
    private var enDemarrage = false
    private var quitte = false
    private var relances = 0              // relances automatiques du tableau de bord tombé
    private var lanceLe = Date.distantPast
    private var refusSuccessifs = 0       // lectures de l'état refusées de suite (personne sur le port)

    /// Une sortie de processus lue au fil de l'eau : le tube ne se remplit jamais (64 Ko), et seule
    /// la fin est gardée.
    final class Tampon: @unchecked Sendable {
        private var donnees = Data()
        private let verrou = NSLock()
        func ajouter(_ d: Data) {
            verrou.lock(); defer { verrou.unlock() }
            donnees.append(d)
            if donnees.count > 65_536 { donnees = donnees.suffix(32_768) }
        }
        var texte: String {
            verrou.lock(); defer { verrou.unlock() }
            return String(decoding: donnees, as: UTF8.self)
        }
        func brancher(_ tube: Pipe) {
            tube.fileHandleForReading.readabilityHandler = { h in
                let d = h.availableData
                if d.isEmpty { h.readabilityHandler = nil } else { self.ajouter(d) }
            }
        }
        /// Le processus est fini : ce qui reste dans le tube, puis plus rien.
        func vider(_ tube: Pipe) {
            tube.fileHandleForReading.readabilityHandler = nil
            ajouter(tube.fileHandleForReading.readDataToEndOfFile())
        }
    }

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
        // Ce que l'application embarque, puis le système : rien de Homebrew ni de ~/.local/bin
        // (le Mac ne lance jamais `claude` ; Claude Code tourne dans la machine virtuelle).
        let ressources = Bundle.main.resourcePath ?? ""
        env["PATH"] = [ressources + "/python/bin", ressources + "/lima/bin", "/usr/bin", "/bin", "/usr/sbin",
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
        let tampon = Tampon()
        tampon.brancher(sortie)
        p.standardOutput = sortie
        p.standardError = FileHandle.nullDevice
        p.terminationHandler = { _ in
            tampon.vider(sortie)
            let texte = tampon.texte
            Task { @MainActor in
                self.echeances = nil
                if texte.contains("\"passage\"") { self.dernierMessage = "Passage du matin : terminé." }
                await self.rafraichir()
            }
        }
        do { try p.run(); echeances = p } catch { echeances = nil; return }
        Etat.garde(p, secondes: Etat.delaiPassage)
    }

    /// Le passage compte une douzaine d'étapes bornées chacune à `duree_max_etape_s` (900 s par
    /// défaut), dépassables d'un appel à l'IA (jusqu'à 900 s) : au pire environ six heures. Au-delà,
    /// le processus est tenu pour bloqué ; le verrou du passage tombe avec lui (flock) et le passage
    /// resté « démarré » est clos au suivant (`clore_interrompus`).
    static let delaiPassage: TimeInterval = 6 * 3600

    /// Un délai de garde : passé `secondes`, le processus reçoit SIGTERM, puis SIGKILL 30 s plus tard.
    static func garde(_ p: Process, secondes: TimeInterval) {
        let pid = p.processIdentifier
        DispatchQueue.global().asyncAfter(deadline: .now() + secondes) {
            guard p.isRunning, p.processIdentifier == pid else { return }
            p.terminate()
            DispatchQueue.global().asyncAfter(deadline: .now() + 30) {
                if p.isRunning { kill(pid, SIGKILL) }
            }
        }
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

    /// Une requête vers le tableau de bord, jeton en en-tête.
    func requete(_ chemin: String) -> URLRequest {
        var req = URLRequest(url: base.appendingPathComponent(chemin))
        if let jeton { req.setValue(jeton, forHTTPHeaderField: "X-Jobsearch-Jeton") }
        return req
    }

    /// Le jeton écrit par le serveur dans le dossier de données ; nil tant qu'il n'existe pas.
    @discardableResult
    private func lireJeton() -> String? {
        let lu = (try? String(contentsOfFile: donnees + "/jeton", encoding: .utf8))?
            .trimmingCharacters(in: .whitespacesAndNewlines)
        if let lu, !lu.isEmpty { jeton = lu }
        return jeton
    }

    /// Ce qui répond sur le port.
    private enum Sonde {
        case rien                  // personne n'écoute
        case tableau(EtatAPI)      // un tableau de bord qui accepte le jeton
        case refus                 // un tableau de bord qui refuse le jeton (une autre installation)
        case etranger              // un autre programme
    }

    private func sonder() async -> Sonde {
        var req = requete("api/etat")
        req.timeoutInterval = 3
        let d: Data, rep: URLResponse
        do { (d, rep) = try await URLSession.shared.data(for: req) } catch {
            // Connexion refusée : le port est libre. Autre chose (pas de réponse HTTP…) : il est pris.
            let code = (error as? URLError)?.code
            return code == .cannotConnectToHost || code == .cannotFindHost ? .rien : .etranger
        }
        switch (rep as? HTTPURLResponse)?.statusCode ?? 0 {
        case 200: return (try? JSONDecoder().decode(EtatAPI.self, from: d)).map { .tableau($0) } ?? .etranger
        case 401, 403: return .refus
        default: return .etranger
        }
    }

    private func memeDossier(_ autre: String) -> Bool {
        URL(fileURLWithPath: autre).standardizedFileURL.path == URL(fileURLWithPath: donnees).standardizedFileURL.path
    }

    /// Un chemin résolu comme le fait Python (`Path.resolve()`) : realpath, liens symboliques suivis.
    private static func reel(_ chemin: String) -> String {
        guard let r = realpath(chemin, nil) else { return URL(fileURLWithPath: chemin).standardizedFileURL.path }
        defer { free(r) }
        return String(cString: r)
    }

    /// Le paquet `jobsearch` de cette application : `racine/src/jobsearch`, trouvé par le .pth
    /// relatif du Python embarqué (scripts/construire-app.sh).
    private var paquetAttendu: String { Etat.reel(racine + "/src/jobsearch") }

    /// Le processus qui écoute sur le port, s'il est bien un tableau de bord `jobsearch web`
    /// (ancien serveur, sans `pid` dans /api/etat) ; nil si on ne peut pas l'identifier.
    /// `lsof` et `ps` tournent hors du fil principal : la fenêtre ne se fige pas pendant ce temps.
    private func processusSurLePort() async -> pid_t? {
        let port = port
        return await Task.detached { Etat.processusEcoutant(port) }.value
    }

    nonisolated private static func processusEcoutant(_ port: Int) -> pid_t? {
        func sortie(_ outil: String, _ args: [String]) -> String? {
            let p = Process()
            p.executableURL = URL(fileURLWithPath: outil)
            p.arguments = args
            let tube = Pipe()
            p.standardOutput = tube
            p.standardError = FileHandle.nullDevice
            guard (try? p.run()) != nil else { return nil }
            let d = tube.fileHandleForReading.readDataToEndOfFile()
            p.waitUntilExit()
            return String(decoding: d, as: UTF8.self)
        }
        guard let pids = sortie("/usr/sbin/lsof", ["-nP", "-t", "-iTCP:\(port)", "-sTCP:LISTEN"]) else { return nil }
        let liste = pids.split(whereSeparator: \.isNewline).compactMap { pid_t($0) }
        guard liste.count == 1, let pid = liste.first,
              let commande = sortie("/bin/ps", ["-o", "command=", "-p", String(pid)]),
              commande.contains("-m jobsearch web") else { return nil }
        return pid
    }

    /// Termine un ancien tableau de bord : SIGTERM, courte attente, SIGKILL s'il tient encore.
    /// Rend vrai une fois le port libre.
    private func terminer(_ pid: pid_t) async -> Bool {
        guard pid > 1, pid != getpid() else { return false }
        kill(pid, SIGTERM)
        for i in 0..<20 {
            try? await Task.sleep(nanoseconds: 250_000_000)
            if kill(pid, 0) != 0 { break }
            if i == 11 { kill(pid, SIGKILL) }
        }
        for _ in 0..<12 {
            if case .rien = await sonder() { return true }
            try? await Task.sleep(nanoseconds: 250_000_000)
        }
        return false
    }

    /// « Réessayer » de la fenêtre : les relances automatiques repartent de zéro.
    func reessayer() async {
        relances = 0
        await assurerTableau()
    }

    /// Le tableau de bord de cette installation : déjà lancé par l'application (et de ce code-ci)
    /// ou lancé ici. Un ancien tableau de bord de cette installation resté sur le port (plantage,
    /// application mise à jour ou déplacée) est arrêté puis remplacé ; celui d'une autre
    /// installation ou un autre programme sur le port est refusé, avec un message.
    func assurerTableau() async {
        guard !enDemarrage, !quitte else { return }
        enDemarrage = true
        defer { enDemarrage = false }
        erreurTableau = nil
        lireJeton()
        switch await sonder() {
        case .rien:
            break
        case .tableau(let e):
            guard memeDossier(e.donnees ?? "") else {
                erreurTableau = "Le port \(port) est pris par le tableau de bord d'une autre installation (\(e.donnees ?? "?"))."
                return
            }
            if let r = e.racine, Etat.reel(r) == paquetAttendu { tableauPret = true; return }
            // Ancien serveur : un autre code que celui de l'application.
            let identifie: pid_t?
            if let pid = e.pid { identifie = pid } else { identifie = await processusSurLePort() }
            guard let pid = identifie else {
                erreurTableau = "Un ancien tableau de bord occupe le port \(port) et n'a pas pu être identifié : "
                    + "quitte-le (Moniteur d'activité, processus « python3 ») puis réessaie."
                return
            }
            guard await terminer(pid) else {
                erreurTableau = "L'ancien tableau de bord (processus \(pid)) ne s'arrête pas : le port \(port) reste pris."
                return
            }
        case .refus:
            erreurTableau = "Le port \(port) est pris par un tableau de bord qui refuse le jeton de cette "
                + "installation (une autre installation ?). Quitte-la puis réessaie."
            return
        case .etranger:
            erreurTableau = "Le port \(port) est pris par un autre programme : quitte-le puis réessaie."
            return
        }
        guard FileManager.default.isExecutableFile(atPath: python) else {
            erreurTableau = "Python embarqué introuvable : \(python). L'application est incomplète : reconstruis-la."
            return
        }
        let p = processus(["web", "--hote", "127.0.0.1", "--port", String(port)])
        p.standardOutput = FileHandle.nullDevice
        p.standardError = FileHandle.nullDevice
        p.terminationHandler = { [weak self] proc in
            Task { @MainActor in self?.tableauArrete(proc) }
        }
        do { try p.run() } catch {
            erreurTableau = "Le tableau de bord ne démarre pas : \(error.localizedDescription)"
            return
        }
        processusWeb = p
        lanceLe = Date()
        for _ in 0..<60 {
            try? await Task.sleep(nanoseconds: 500_000_000)
            guard p.isRunning else {
                // Mort au démarrage : le plus souvent le port, pris entre-temps par un autre programme.
                erreurTableau = "Le tableau de bord s'est arrêté au démarrage (code \(p.terminationStatus)) : "
                    + "le port \(port) est peut-être pris par un autre programme."
                return
            }
            lireJeton()                       // écrit par le serveur avant qu'il écoute
            if case .tableau = await sonder() { tableauPret = true; return }
        }
        erreurTableau = "Le tableau de bord ne répond pas sur le port \(port)."
    }

    /// Le tableau de bord lancé ici s'est arrêté alors que l'application tourne : relancé, au plus
    /// trois fois de suite, de plus en plus espacées ; ensuite un message et « Réessayer ».
    private func tableauArrete(_ p: Process) {
        guard p === processusWeb, !quitte else { return }
        processusWeb = nil
        // Mort avant d'avoir répondu : assurerTableau() le voit et le dit, sans relance.
        guard tableauPret else { return }
        tableauPret = false
        if Date().timeIntervalSince(lanceLe) > 600 { relances = 0 }   // il avait tenu : nouvelle série
        guard relances < 3 else {
            erreurTableau = "Le tableau de bord s'arrête sans cesse (code \(p.terminationStatus)). Réessaie, "
                + "ou reconstruis l'application si cela persiste."
            return
        }
        relances += 1
        let attente = UInt64(5 * relances)
        Task {
            try? await Task.sleep(nanoseconds: attente * 1_000_000_000)
            await assurerTableau()
        }
    }

    func rafraichir() async {
        let lu: Data
        do { (lu, _) = try await URLSession.shared.data(for: requete("api/etat")) } catch {
            if (error as? URLError)?.code == .cannotConnectToHost { await personneSurLePort() }
            return
        }
        refusSuccessifs = 0
        guard let e = try? JSONDecoder().decode(EtatAPI.self, from: lu) else { return }
        passage = e.passage.texte
        niveau = e.passage.niveau
        aTrancher = e.a_trancher
        claudeConnecte = e.claude.connecte
        claudeMessage = e.claude.message
        mode = e.mode
        vmStatut = e.vm?.statut ?? (e.vm == nil ? nil : "absente")
    }

    /// Le tableau de bord ne répond plus du tout (connexion refusée). Celui lancé ici est relancé par
    /// `tableauArrete` ; celui trouvé sur le port et adopté (`processusWeb` nil) ne prévient pas de sa
    /// mort : après trois refus de suite (une minute et demie), il repasse par `assurerTableau`, dans
    /// la même limite de relances.
    private func personneSurLePort() async {
        refusSuccessifs += 1
        guard refusSuccessifs >= 3, processusWeb == nil, tableauPret, !enDemarrage, !quitte else { return }
        refusSuccessifs = 0
        tableauPret = false
        guard relances < 3 else {
            erreurTableau = "Le tableau de bord s'arrête sans cesse. Réessaie, ou reconstruis l'application si cela persiste."
            return
        }
        relances += 1
        await assurerTableau()
    }

    // MARK: - Commandes

    /// Une commande `jobsearch` en arrière-plan ; une seule à la fois. `apres` : une fois terminée et l'état relu.
    func executer(_ arguments: [String], titre: String, apres: (@MainActor () -> Void)? = nil) {
        guard occupe == nil else { return }
        occupe = titre
        let p = processus(arguments)
        let sortie = Pipe()
        let tampon = Tampon()
        tampon.brancher(sortie)
        p.standardOutput = sortie
        p.standardError = sortie
        p.terminationHandler = { proc in
            tampon.vider(sortie)
            let texte = tampon.texte
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
            return
        }
        // Un passage peut durer des heures ; la machine virtuelle se crée (image à télécharger) en
        // moins d'une heure.
        Etat.garde(p, secondes: arguments.first == "passage" ? Etat.delaiPassage : 3600)
    }

    func lancerPassage() { executer(["passage"], titre: "Passage") }
    func demarrerVM() { executer(["vm", "demarrer"], titre: "Démarrage de la machine virtuelle") }
    func arreterVM() { executer(["vm", "arreter"], titre: "Arrêt de la machine virtuelle") }

    // MARK: - Fin

    func quitter() {
        quitte = true                         // pas de relance du tableau de bord qu'on arrête
        processusWeb?.terminate()             // seulement s'il a été lancé par l'application
        // Un passage du matin en cours (echeances) va à son terme : il est détaché du menu.
    }
}
