"""
ALEX — mot d'activation vocal (étape 3/4)

Écoute en continu, en local, uniquement le mot "Alex" (via Porcupine).
Tourne dans un thread séparé du serveur FastAPI (Porcupine/PvRecorder
sont synchrones), et prévient le reste de l'appli via un callback quand
le mot est détecté.

Prérequis (voir README.md) :
  - Un compte gratuit sur https://console.picovoice.ai/
  - Un fichier de mot-clé "Alex" entraîné et téléchargé pour Linux (.ppn)
  - Une clé d'accès (AccessKey), gratuite

Variables d'environnement attendues :
  ALEX_PORCUPINE_ACCESS_KEY   — la clé d'accès Picovoice
  ALEX_PORCUPINE_KEYWORD_PATH — chemin vers le fichier .ppn "Alex"

Si l'une des deux manque, le wake word est simplement désactivé
(le reste d'Alex continue de fonctionner normalement).
"""

import os
import threading

ACCESS_KEY = os.environ.get("ALEX_PORCUPINE_ACCESS_KEY")
KEYWORD_PATH = os.environ.get("ALEX_PORCUPINE_KEYWORD_PATH")

_callbacks = []
_thread = None


def on_wake(callback):
    """Enregistre une fonction à appeler à chaque détection du mot 'Alex'."""
    _callbacks.append(callback)


def _run():
    if not ACCESS_KEY or not KEYWORD_PATH:
        print(
            "[wake_word] ALEX_PORCUPINE_ACCESS_KEY ou ALEX_PORCUPINE_KEYWORD_PATH "
            "manquant — mot d'activation désactivé (le reste d'Alex fonctionne normalement)."
        )
        return

    try:
        import pvporcupine
        from pvrecorder import PvRecorder
    except ImportError:
        print(
            "[wake_word] pvporcupine/pvrecorder non installés — "
            "lance `pip install -r brain/requirements.txt`."
        )
        return

    try:
        porcupine = pvporcupine.create(access_key=ACCESS_KEY, keyword_paths=[KEYWORD_PATH])
    except Exception as e:
        print(f"[wake_word] Impossible d'initialiser Porcupine : {e}")
        return

    recorder = PvRecorder(frame_length=porcupine.frame_length)
    recorder.start()
    print("[wake_word] En écoute passive du mot \"Alex\"...")

    try:
        while True:
            pcm = recorder.read()
            result = porcupine.process(pcm)
            if result >= 0:
                print("[wake_word] \"Alex\" détecté !")
                for cb in list(_callbacks):
                    try:
                        cb()
                    except Exception as e:
                        print(f"[wake_word] Erreur dans un callback : {e}")
    finally:
        recorder.stop()
        recorder.delete()
        porcupine.delete()


def start_background():
    """Démarre l'écoute dans un thread daemon (ne bloque pas FastAPI)."""
    global _thread
    if _thread is not None:
        return _thread
    _thread = threading.Thread(target=_run, daemon=True)
    _thread.start()
    return _thread
