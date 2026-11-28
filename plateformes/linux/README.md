# Piston pour Linux (à venir)

Rien n'est encore construit pour Linux. Ce qui est déjà prêt, en commun avec les autres systèmes :

- `src/jobsearch/hote/linux.py` : dossiers XDG, Secret Service (`secret-tool`), `notify-send` ;
- la machine virtuelle par Lima (`src/jobsearch/vm/lima.py`, QEMU/KVM au lieu de Virtualization.framework) ;
- l'enveloppe Tauri de Windows (`plateformes/windows/app`), qui tourne aussi sous Linux (WebKitGTK),
  avec sa dictée locale (whisper.cpp) et son pont `window.piston`.

Restera à faire : un paquet (AppImage ou deb), le Python embarqué pour Linux, et une recette complète.
