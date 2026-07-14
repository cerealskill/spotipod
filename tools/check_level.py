"""Graba unos segundos desde el dispositivo virtual y reporta el nivel de pico,
para verificar que el audio del escritorio llega a BlackHole antes de grabar en serio."""
import sys

import numpy as np
import sounddevice as sd


def dispositivo_captura():
    for i, d in enumerate(sd.query_devices()):
        if any(k in d["name"] for k in ("BlackHole", "CABLE Input", "Loopback")) and d["max_input_channels"] > 0:
            return i, d["name"]
    return None, None


def medir_nivel(segundos=3, samplerate=None, log=print):
    """ Graba 'segundos' desde el dispositivo virtual. Devuelve el pico (0..32767) o None. """
    dev, nombre = dispositivo_captura()
    if dev is None:
        log("❌ No se encontró BlackHole como dispositivo de entrada.")
        return None

    if samplerate is None:  # usar la frecuencia por defecto del dispositivo
        try:
            samplerate = int(round(sd.query_devices(dev)["default_samplerate"]))
        except Exception:
            samplerate = 48000

    log("▶️  Asegúrate de tener MÚSICA SONANDO en Spotify AHORA.")
    log(f"🎙  Grabando {segundos}s desde '{nombre}'...")
    grabacion = sd.rec(int(samplerate * segundos), samplerate=samplerate,
                       channels=2, dtype="int16", device=dev)
    sd.wait()

    pico = int(np.abs(grabacion).max()) if grabacion.size else 0
    pct = pico / 32767 * 100
    log(f"📊 Pico: {pico}/32767 ({pct:.1f}%)")
    if pico < 300:
        log("❌ SILENCIO: el audio NO está llegando a BlackHole.")
        log("   → Pon la salida del sistema en el 'Dispositivo de salida múltiple' (no en las bocinas).")
    else:
        log("✅ ¡Señal detectada! El enrutamiento funciona, ya puedes grabar.")
    return pico


def main():
    sr = int(sys.argv[1]) if len(sys.argv) > 1 else None
    pico = medir_nivel(samplerate=sr)
    sys.exit(0 if pico and pico >= 300 else 1)


if __name__ == "__main__":
    main()
