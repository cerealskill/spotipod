import sounddevice as sd
import numpy as np
import wave

# 🔍 Detectar el ID del dispositivo BlackHole
def obtener_id_blackhole():
    dispositivos = sd.query_devices()
    for i, dispositivo in enumerate(dispositivos):
        if "BlackHole" in dispositivo["name"] and dispositivo["max_input_channels"] > 0:
            print(f"🎤 Usando BlackHole en el dispositivo {i}: {dispositivo['name']}")
            return i
    print("❌ No se encontró BlackHole. Verifica la configuración de sonido.")
    exit()

# 🔹 Configuración
SAMPLE_RATE = 44100  # Puedes probar con 48000 si lo necesitas
CHANNELS = 2  # BlackHole suele usar estéreo
DURATION = 10  # Duración de la grabación en segundos
DISPOSITIVO_ID = obtener_id_blackhole()

# 🎙 Grabar el audio del sistema
print("🎙 Capturando el sonido del sistema con BlackHole...")
audio_data = sd.rec(int(DURATION * SAMPLE_RATE), samplerate=SAMPLE_RATE, channels=CHANNELS, dtype="int16", device=DISPOSITIVO_ID)
sd.wait()
print("✅ Grabación finalizada.")

# 📂 Guardar archivo WAV
archivo_wav = "captura_blackhole.wav"
with wave.open(archivo_wav, "wb") as wf:
    wf.setnchannels(CHANNELS)
    wf.setsampwidth(2)  # 16 bits por muestra
    wf.setframerate(SAMPLE_RATE)
    wf.writeframes(audio_data.tobytes())

print(f"📂 Archivo guardado: {archivo_wav}")