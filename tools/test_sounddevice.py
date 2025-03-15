import sounddevice as sd
import numpy as np
import wave

# 📡 Detectar dispositivos de audio disponibles
print(sd.query_devices())

# Seleccionar el ID del dispositivo "BlackHole"
DEVICE_ID = None
for i, dev in enumerate(sd.query_devices()):
    if "BlackHole" in dev['name']:
        DEVICE_ID = i
        break

if DEVICE_ID is None:
    print("❌ No se encontró BlackHole. Asegúrate de haberlo instalado correctamente.")
    exit()

# 🎙 Grabar audio del sistema
SAMPLE_RATE = 44100  # Calidad CD
CHANNELS = 2  # Estéreo
DURATION = 10  # Segundos

print(f"🎧 Grabando audio del escritorio con BlackHole ({DURATION}s)...")
audio_data = sd.rec(int(DURATION * SAMPLE_RATE), samplerate=SAMPLE_RATE, channels=CHANNELS, dtype=np.int16, device=DEVICE_ID)
sd.wait()

# Guardar el archivo en formato WAV
archivo_wav = "audio_escritorio.wav"
with wave.open(archivo_wav, "wb") as wf:
    wf.setnchannels(CHANNELS)
    wf.setsampwidth(2)
    wf.setframerate(SAMPLE_RATE)
    wf.writeframes(audio_data.tobytes())

print(f"✅ Grabación guardada en: {archivo_wav}")
