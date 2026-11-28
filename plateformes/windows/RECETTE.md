# Recette de parité : Piston pour Windows

À dérouler sur un Windows 11 neuf (Famille et Professionnel), d'abord en x64, puis en ARM64. Chaque ligne
reprend une fonction de l'application Mac (README, « Ce que fait l'application », et le menu de la barre des
menus). Noter pour chacune : ✅ conforme, ⚠️ écart accepté (dire lequel), ❌ défaut (ouvrir un ticket).

Ce qui est déjà éprouvé sans Windows, à ne pas refaire en détail : le code commun (tests sur Windows, Mac,
Linux), l'invité de la VM en amd64 et arm64 avec l'isolation réseau de Chrome (`invite.yml`), la construction
de l'installateur (`windows.yml`), l'enveloppe sur Mac (fenêtre, entrée, instance unique, arrêt).

## Installation et premier lancement

- [ ] L'installateur s'ouvre sans droits d'administrateur (installation pour le compte) ; SmartScreen avertit
      tant que l'installateur n'est pas signé.
- [ ] Sans WSL : l'écran de démarrage dit d'exécuter `wsl --install --no-distribution` puis de redémarrer.
- [ ] Avec WSL : `/demarrage` montre les étapes (création, système, application, lancement), la barre et le
      temps restant ; « Continuer sans attendre » ouvre l'application, le bandeau suit l'avancement.
- [ ] `wsl -l -v` montre « Piston-emploi » ; son disque est dans `%LOCALAPPDATA%\Recherche emploi\wsl\`.
- [ ] Le dossier des données n'est lisible que par le compte (Propriétés › Sécurité : le compte, SYSTEM,
      Administrateurs ; héritage désactivé).

## La machine virtuelle et sa frontière

- [ ] Dans la VM (`wsl -d Piston-emploi`) : on est `piston`, `sudo -n true` échoue, `ls /mnt` est vide,
      `cmd.exe` est introuvable (interopérabilité coupée), `mount` ne montre de Windows que `/donnees`.
- [ ] Depuis Windows : `curl http://127.0.0.1:8776/etat` sans jeton est refusé ; `127.0.0.1:9222` (DevTools)
      et `127.0.0.1:5901` (VNC) ne répondent pas ; `127.0.0.1:8777` (noVNC) demande le mot de passe.
- [ ] Quitter Piston éteint la VM (`wsl -l --running` ne la montre plus) ; le passage en cours reprend au
      lancement suivant.
- [ ] Après une mise à jour du code, la VM réinstalle l'application (empreinte) sans être recréée.

## Les moteurs d'IA

- [ ] Page IA : se connecter à Claude, Codex, Antigravity et Grok (adresse et code dans la modale) ; le dernier
      connecté devient celui de l'application ; « Se déconnecter » marche.
- [ ] Une tâche d'IA (note d'une annonce, dossier) aboutit ; les coordonnées partent masquées.

## Le tableau de bord et la fenêtre

- [ ] La fenêtre s'ouvre sur Aujourd'hui (ou sur les conditions la première fois), sans passer par /connexion.
- [ ] Un lien vers une annonce s'ouvre dans le navigateur de Windows ; `target=_blank` aussi.
- [ ] Un CV ou une lettre (Word, PDF) va dans Téléchargements (« CV 1.docx » si le nom est pris), et
      l'Explorateur le montre.
- [ ] L'import du CV (page Ton parcours) ouvre le sélecteur de fichiers de Windows.
- [ ] Les confirmations (« Retirer cette source ? ») s'affichent et se respectent.
- [ ] Les textes disent « ton PC », « Paramètres Windows », « gestionnaire d'identification », « Ctrl ↵ ».
- [ ] Fermer la fenêtre laisse l'icône ; un clic sur l'icône la rouvre ; relancer Piston la ramène.

## L'icône de la zone de notification

- [ ] L'état : passage, annonces à trancher, IA connectée ou non, machine virtuelle, ce qui tourne, le dernier
      message.
- [ ] Ouvrir le tableau de bord ; Navigateur de la machine virtuelle (l'écran de Chrome s'affiche) ; Lancer un
      passage ; Se connecter à l'IA ; Démarrer / Arrêter la machine virtuelle ; Quitter Piston.
- [ ] Ouvrir à la connexion : après une nouvelle session, Piston tourne avec seulement l'icône.

## Le passage et les échéances

- [ ] Le passage du matin part à l'heure (ou au réveil) : sourcing, lecture, ingestion, pré-tri, note, dossiers.
- [ ] L'alerte du matin et la surveillance arrivent en notification ; un clic ramène Piston.
- [ ] Navigateur : se connecter à un site dans le Chrome de la VM ; un pré-remplissage s'y fait sans envoi.

## Courrier, clés, dictée

- [ ] Connecteurs : Outlook (classique) se teste, lit les réponses des employeurs sans rien marquer lu ;
      Apple Mail n'apparaît pas ; Gmail marche avec Claude.
- [ ] LinkedIn : le jeton Apify se range dans le gestionnaire d'identification (« jobsearch-apify »), ses quatre
      derniers caractères s'affichent ; le retirer l'efface.
- [ ] Dictée d'un entretien : la première fois, le modèle se télécharge (190 Mo) ; la transcription arrive
      pendant qu'on parle, la finale à l'arrêt ; micro refusé : message et lien vers Paramètres › Microphone.
      Rien ne part en ligne (moniteur réseau pendant la dictée, une fois le modèle là).

## Mises à jour et désinstallation

- [ ] « Rechercher les mises à jour… » dit la version à jour, ou montre les nouveautés et installe (la VM
      s'éteint d'abord, Piston se relance) ; la vérification quotidienne se coupe dans le menu.
- [ ] Désinstaller depuis Paramètres › Applications retire l'application ; les données restent ;
      `wsl --unregister Piston-emploi` et la suppression du dossier effacent tout.
