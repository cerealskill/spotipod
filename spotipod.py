import sys
import time
import os
import json
import argparse
import logging
import requests
import wave
import sounddevice as sd
import numpy as np
import re
import yt_dlp
import spotipy
import glob
import subprocess

from pydub import AudioSegment
from pydub.silence import detect_leading_silence
from spotipy.oauth2 import SpotifyOAuth
from mutagen.mp3 import MP3
from mutagen.id3 import ID3, APIC, TIT2, TPE1, TALB, COMM, TPE2, TRCK, TPOS, TDRC

# .env es opcional: si python-dotenv está instalado, cargamos variables desde un .env.
try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

log = logging.getLogger("spotipod")
VERSION = "0.0.6"

BANNER = r"""
----------------------------- [START SPOTI POD] ------------------------------------

███████╗██████╗  ██████╗ ████████╗██╗    ██████╗  ██████╗ ██████╗
██╔════╝██╔══██╗██╔═══██╗╚══██╔══╝██║    ██╔══██╗██╔═══██╗██╔══██╗
███████╗██████╔╝██║   ██║   ██║   ██║    ██████╔╝██║   ██║██║  ██║
╚════██║██╔═══╝ ██║   ██║   ██║   ██║    ██╔═══╝ ██║   ██║██║  ██║
███████║██║     ╚██████╔╝   ██║   ██║    ██║     ╚██████╔╝██████╔╝
╚══════╝╚═╝      ╚═════╝    ╚═╝   ╚═╝    ╚═╝      ╚═════╝ ╚═════╝

Spotify Playlist Recorder v0.0.6 - Compatible con macOS, Windows y Linux
⚠️ AVISO LEGAL: Este software es solo para uso personal. No redistribuir ni compartir grabaciones.
------------------------------------------------------------------------------------"""

# --- Configuración (los valores por defecto se sobreescriben desde argparse en main) ---
# Spotify rechaza http://localhost desde 2025: exige HTTPS o la IP de loopback 127.0.0.1.
SPOTIFY_REDIRECT_URI = os.getenv("SPOTIFY_REDIRECT_URI", "http://127.0.0.1:8080")

OUTPUT_DIR = "Playlist"
SAMPLE_RATE = 44100
CHANNELS = 2
BITRATE = "320k"
REINTENTOS = 3

# Pico mínimo (sobre 32767 en int16) para considerar que la grabación tiene audio real.
# Por debajo asumimos silencio (p. ej. BlackHole no está como salida de Spotify).
UMBRAL_SILENCIO = 300

# Cliente de Spotify y dispositivo de captura: se inicializan en main().
sp = None
DISPOSITIVO_ID = None
APPLE_MUSIC = True
IPOD_MOUNT = None   # si se pasa --ipod: 'auto' o ruta de montaje


# ==================== Utilidades ====================

def configurar_logging(verbose=False):
    """ Consola limpia (solo el mensaje) + archivo spotipod.log con timestamps y niveles. """
    if log.handlers:  # evita duplicar handlers si se llama más de una vez (tests)
        return
    log.setLevel(logging.DEBUG)
    log.propagate = False

    consola = logging.StreamHandler()
    consola.setLevel(logging.DEBUG if verbose else logging.INFO)
    consola.setFormatter(logging.Formatter("%(message)s"))
    log.addHandler(consola)

    archivo = logging.FileHandler("spotipod.log", encoding="utf-8")
    archivo.setLevel(logging.DEBUG)
    archivo.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s", "%Y-%m-%d %H:%M:%S"))
    log.addHandler(archivo)


def limpiar_nombre_archivo(nombre):
    """ Reemplaza caracteres inválidos y normaliza el texto para comparación de archivos. """
    return "".join(c if c.isalnum() or c in " _-" else "_" for c in nombre).strip().lower()


def sanitizar_nombre(nombre):
    """ Normaliza un nombre para usarlo como archivo o carpeta (consistente en todo el flujo). """
    nombre = re.sub(r'[\/:*?"<>|]', '-', nombre)
    return nombre.replace("\n", "").replace("\t", "").strip()


def escapar_applescript(texto):
    """ Escapa backslashes y comillas dobles para interpolar texto de forma segura en AppleScript. """
    return texto.replace("\\", "\\\\").replace('"', '\\"')


def recortar_silencio(audio, umbral_db=-50.0, margen_ms=50):
    """
    Recorta el silencio del inicio y el final de un AudioSegment. Deja un pequeño
    margen para no comerse el ataque de la primera nota ni la cola/reverberación.
    Si la pista fuese silencio completo, devuelve el audio sin tocar.
    """
    inicio = detect_leading_silence(audio, silence_threshold=umbral_db)
    fin = detect_leading_silence(audio.reverse(), silence_threshold=umbral_db)

    inicio = max(0, inicio - margen_ms)
    fin_pos = len(audio) - max(0, fin - margen_ms)

    if inicio >= fin_pos:  # todo era silencio: no recortamos
        return audio
    return audio[inicio:fin_pos]


def _color(code, s):
    """ Envuelve 's' en un código ANSI si la salida es una terminal; si no, la deja igual. """
    return f"\033[{code}m{s}\033[0m" if sys.stdout.isatty() else s


def formatear_duracion(segundos):
    """ Formatea segundos como '1h 23m', '23m 45s' o '45s' para mostrar progreso legible. """
    segundos = int(round(segundos))
    horas, resto = divmod(segundos, 3600)
    minutos, seg = divmod(resto, 60)
    if horas:
        return f"{horas}h {minutos}m"
    if minutos:
        return f"{minutos}m {seg}s"
    return f"{seg}s"


def grabar_monitorizado(duracion, device_id, uri, ancho=30):
    """
    Graba `duracion`+1 s desde el dispositivo virtual y, en paralelo, vigila vía la API que
    Spotify siga reproduciendo la pista `uri` y en sincronía. Si detecta pausa, cambio de
    pista o desfase, aborta la toma. Devuelve (grabacion, ok, motivo).

    Muestra una barra de progreso en tiempo real (si la salida es una terminal).
    """
    buffer_total = duracion + 1.0
    muestras = int(SAMPLE_RATE * buffer_total)
    grabacion = sd.rec(muestras, samplerate=SAMPLE_RATE, channels=CHANNELS,
                       dtype="int16", device=DISPOSITIVO_ID)

    inicio = time.time()
    ultimo_check = 0.0
    ok, motivo = True, ""
    tty = sys.stdout.isatty()

    while (time.time() - inicio) < buffer_total:
        transcurrido = time.time() - inicio

        if tty:
            frac = min(1.0, transcurrido / duracion) if duracion > 0 else 1.0
            lleno = int(ancho * frac)
            sys.stdout.write(f"\r   ⏺ [{'█' * lleno}{'░' * (ancho - lleno)}] {frac * 100:5.1f}%  "
                             f"{formatear_duracion(transcurrido)} / {formatear_duracion(duracion)}")
            sys.stdout.flush()

        # Verificación cada ~8 s, salvo en los últimos 2 s (ahí el track termina y Spotify avanza).
        if transcurrido - ultimo_check >= 8 and (buffer_total - transcurrido) > 2.0:
            ultimo_check = transcurrido
            try:
                estado = sp.current_playback()
            except Exception:
                estado = None  # blip de red puntual: no lo tratamos como fallo
            if estado is not None:
                item = estado.get("item") or {}
                prog = (estado.get("progress_ms") or 0) / 1000
                if not estado.get("is_playing"):
                    ok, motivo = False, "reproducción pausada"
                elif item.get("uri") and item["uri"] != uri:
                    ok, motivo = False, "Spotify cambió de pista"
                elif abs(prog - transcurrido) > 8:
                    ok, motivo = False, f"desincronizado ({prog:.0f}s vs {transcurrido:.0f}s)"
                if not ok:
                    break
        time.sleep(0.2)

    sd.stop()
    if tty:
        marca = "█" * ancho if ok else "▒" * ancho
        sys.stdout.write(f"\r   ⏺ [{marca}] {'100.0%' if ok else 'ABORTADA'}  "
                         f"{formatear_duracion(duracion)} / {formatear_duracion(duracion)}\n")
        sys.stdout.flush()
    return grabacion, ok, motivo


def limpiar_wav_huerfanos(carpeta):
    """ Borra WAV y MP3 parciales (.part) de corridas anteriores que se cortaron. """
    for patron in ("*.wav", "*.mp3.part"):
        for f in glob.glob(os.path.join(carpeta, patron)):
            try:
                os.remove(f)
                log.debug(f"🧹 Archivo huérfano eliminado: {f}")
            except OSError:
                pass


# ==================== Spotify / dispositivos ====================

def crear_cliente_spotify():
    """ Inicializa el cliente de Spotify con OAuth. Termina el proceso si faltan credenciales. """
    client_id = os.getenv("SPOTIFY_CLIENT_ID")
    client_secret = os.getenv("SPOTIFY_CLIENT_SECRET")

    if not client_id or not client_secret:
        log.error("❌ ERROR: Credenciales de Spotify no configuradas. Configúralas y vuelve a intentarlo.")
        log.error("   export SPOTIFY_CLIENT_ID='tu_cliente_id'")
        log.error("   export SPOTIFY_CLIENT_SECRET='tu_secreto'")
        log.error("   (o ponlas en un archivo .env)")
        sys.exit(1)

    try:
        scope = "user-read-playback-state user-modify-playback-state playlist-read-private"
        return spotipy.Spotify(auth_manager=SpotifyOAuth(
            client_id=client_id,
            client_secret=client_secret,
            redirect_uri=SPOTIFY_REDIRECT_URI,
            scope=scope,
        ))
    except Exception as e:
        log.error(f"❌ ERROR al conectar con Spotify: {e}")
        sys.exit(1)


def obtener_dispositivo_virtual():
    """ Localiza el dispositivo de captura (BlackHole/VB-Cable/Loopback). Termina si no lo encuentra. """
    try:
        dispositivos = sd.query_devices()
        for i, dispositivo in enumerate(dispositivos):
            if ("BlackHole" in dispositivo["name"] or "CABLE Input" in dispositivo["name"] or "Loopback" in dispositivo["name"]) and dispositivo["max_input_channels"] > 0:
                log.info(f"✅ Usando dispositivo virtual en {i}: {dispositivo['name']}")
                return i
    except Exception as e:
        log.error(f"❌ ERROR al buscar dispositivo de audio virtual: {e}")

    log.error("❌ No se encontró un dispositivo de grabación virtual compatible. Verifica la configuración de sonido.")
    log.error("ℹ️ Para instalar uno:")
    log.error("  - macOS: BlackHole → https://existential.audio/blackhole/")
    log.error("  - Windows: VB-Audio Virtual Cable → https://vb-audio.com/Cable/")
    log.error("  - Linux: Loopback con PulseAudio o JACK.")
    sys.exit(1)


def sample_rate_dispositivo(dev):
    """ Frecuencia de muestreo por defecto del dispositivo de captura (fallback 44100). """
    try:
        return int(round(sd.query_devices(dev)["default_samplerate"]))
    except Exception:
        return 44100


def obtener_dispositivo_activo(esperar=True):
    """
    Devuelve el ID de un dispositivo Spotify utilizable. Prefiere el activo; si ninguno lo
    está, intenta activar el primero (transfer_playback). Si no hay ninguno, espera unos
    segundos guiando al usuario a abrir Spotify.
    """
    intentos = 6 if esperar else 1
    for i in range(intentos):
        try:
            devices = sp.devices()["devices"]
        except Exception as e:
            log.error(f"❌ ERROR al obtener dispositivos de Spotify: {e}")
            return None

        if devices:
            for d in devices:
                if d.get("is_active"):
                    return d["id"]
            # Hay dispositivos pero ninguno activo: despertamos el primero.
            did = devices[0]["id"]
            try:
                sp.transfer_playback(did, force_play=False)
                time.sleep(1)
            except Exception:
                pass
            return did

        if i == 0:
            log.warning("⏳ No hay dispositivos Spotify disponibles. Abre Spotify (escritorio o móvil) "
                        "y reproduce algo una vez para que aparezca...")
        time.sleep(2)
    return None


def detener_reproduccion(device_id=None):
    """ Pausa la reproducción de Spotify (best-effort, ignora errores). """
    if not sp:
        return
    try:
        sp.pause_playback(device_id=device_id)
    except Exception:
        pass


# ==================== Descarga de respaldo (YouTube) ====================

def descargar_mp3(nombre_cancion, output_folder):
    """
    Descarga una canción de YouTube en MP3 si aún no existe en la carpeta destino.
    Devuelve la ruta del MP3 (nuevo o ya existente) o None si no se pudo obtener.
    """
    os.makedirs(output_folder, exist_ok=True)

    existe, archivo_existente = existe_cancion(nombre_cancion, output_folder)
    if existe:
        log.info(f"✅ Ya existe en la carpeta: '{archivo_existente}'. No se descarga de nuevo.")
        return archivo_existente

    opciones_info = {
        'format': 'bestaudio/best',
        'default_search': 'ytsearch',
        'noplaylist': True,
        'quiet': True,
        'skip_download': True,  # solo obtiene información
    }

    try:
        with yt_dlp.YoutubeDL(opciones_info) as ydl:
            info = ydl.extract_info(nombre_cancion, download=False)

            if 'title' not in info:
                log.warning("⚠️ No se encontró información de la canción en YouTube.")
                return None

            titulo_real = limpiar_nombre_archivo(info['title'])
            archivo_mp3 = os.path.join(output_folder, f"{titulo_real}.mp3")

            existe, archivo_existente = existe_cancion(titulo_real, output_folder)
            if existe:
                log.info(f"✅ '{titulo_real}' ya descargada como '{archivo_existente}'.")
                return archivo_existente

            log.info(f"🎵 Descargando desde YouTube: {titulo_real}...")
            opciones_descarga = {
                'format': 'bestaudio/best',
                'outtmpl': os.path.join(output_folder, f"{titulo_real}.%(ext)s"),
                'postprocessors': [{
                    'key': 'FFmpegExtractAudio',
                    'preferredcodec': 'mp3',
                    'preferredquality': '320',  # igual que las grabaciones de Spotify
                }],
                'noplaylist': True,
                'default_search': 'ytsearch',
                'quiet': True,
            }
            with yt_dlp.YoutubeDL(opciones_descarga) as ydl_descarga:
                ydl_descarga.download([nombre_cancion])

            log.info(f"✅ Descarga completa: {archivo_mp3}")
            return archivo_mp3

    except Exception as e:
        log.error(f"❌ Error al descargar la canción: {e}")
        return None


def existe_cancion(nombre_cancion, output_folder):
    """
    Indica si ya existe un MP3 con nombre similar en la carpeta, comparando el
    término de búsqueda con el título real de los archivos.
    """
    nombre_cancion_limpio = limpiar_nombre_archivo(nombre_cancion)
    archivos_en_carpeta = glob.glob(os.path.join(output_folder, "*.mp3"))

    for archivo in archivos_en_carpeta:
        nombre_archivo_limpio = limpiar_nombre_archivo(os.path.basename(archivo).replace(".mp3", ""))

        if nombre_cancion_limpio == nombre_archivo_limpio:
            return True, archivo

        # Coincidencia parcial exigiendo solape alto para evitar falsos positivos
        # (p. ej. "love" no debe coincidir con "lovely").
        menor, mayor = sorted([nombre_cancion_limpio, nombre_archivo_limpio], key=len)
        if menor and menor in mayor and len(menor) >= 0.6 * len(mayor):
            return True, archivo

    return False, None


# ==================== Grabación / conversión ====================

def grabar_audio(archivo_wav, duracion, device_id, meta, nombre_playlist, carpeta_recuperacion):
    """
    Reproduce la pista en Spotify y graba la salida del dispositivo virtual a WAV.
    Si Spotify no logra reproducir, intenta recuperarla desde YouTube (con metadata).
    Devuelve la ruta del WAV grabado o None.
    """
    nombre = meta["titulo"]
    artista = meta["artista"]

    if duracion <= 0:
        log.error("❌ ERROR: Duración de grabación inválida.")
        return None

    os.makedirs(os.path.dirname(archivo_wav), exist_ok=True)

    # Iniciar la reproducción con reintentos hasta confirmar que Spotify realmente está sonando.
    # (Que no reproduzca a la primera casi nunca significa que el track no exista: suele ser
    # latencia de arranque o un dispositivo dormido.)
    reproduciendo = False
    for intento in range(REINTENTOS):
        try:
            sp.start_playback(device_id=device_id, uris=[meta["uri"]])
        except Exception as e:
            log.warning(f"⚠️ Intento {intento + 1}/{REINTENTOS}: no se pudo iniciar la reproducción: {e}")
        time.sleep(1.5)
        estado = sp.current_playback()
        if estado and estado.get("is_playing"):
            reproduciendo = True
            break

    if not reproduciendo:
        log.warning(f"❌ Spotify no reprodujo: {artista} - {nombre} (¿no disponible?). Intentando recuperar desde YouTube.")
        ruta = descargar_mp3(f"{artista} - {nombre}", carpeta_recuperacion)
        # Etiquetamos el MP3 recuperado con la misma metadata (incluido el comentario de
        # la playlist) para que Apple Music lo añada a la playlist del iPod como el resto.
        if ruta:
            jpg = descargar_portada(meta.get("portada"), nombre_playlist, artista, nombre)
            incrustar_metadata_mp3(ruta, jpg, meta, nombre_playlist)
        return None

    # Volumen al 100% para grabar con el nivel de señal completo (grabar bajo pierde
    # rango dinámico de forma irrecuperable y deja la playlist despareja).
    try:
        sp.volume(100, device_id=device_id)
    except Exception as e:
        log.warning(f"⚠️ No se pudo ajustar el volumen al 100%: {e}")

    # Grabación con verificación de integridad: hasta 2 tomas limpias. Antes de cada toma
    # volvemos al inicio de la pista (así no perdemos la intro tras los reintentos).
    grabacion = None
    for intento_grab in range(2):
        try:
            sp.seek_track(0, device_id=device_id)
        except Exception:
            pass
        time.sleep(0.5)

        sufijo = f" (reintento {intento_grab})" if intento_grab else ""
        log.info(f"🎙 Grabando {formatear_duracion(duracion)}{sufijo}: {archivo_wav}")
        try:
            grabacion, ok, motivo = grabar_monitorizado(duracion, device_id, meta["uri"])
        except KeyboardInterrupt:
            sd.stop()
            sys.stdout.write("\n")
            raise

        if ok:
            break
        log.warning(f"⚠️ Toma descartada: {motivo}. Reintentando la pista…")
        grabacion = None
        try:  # reconfirmar reproducción antes de reintentar
            sp.start_playback(device_id=device_id, uris=[meta["uri"]])
            time.sleep(1.5)
        except Exception:
            pass

    if grabacion is None:
        log.warning(f"❌ No se logró una grabación limpia de: {artista} - {nombre}. Se reintentará luego.")
        return None

    # Verificamos que realmente se capturó audio y no silencio (típico si BlackHole no
    # está seleccionado como salida de Spotify).
    pico = int(np.abs(grabacion).max()) if grabacion.size else 0
    if pico < UMBRAL_SILENCIO:
        log.warning(f"⚠️ ADVERTENCIA: grabación casi en silencio (pico {pico}/32767). "
                    f"Revisa que BlackHole sea la salida de audio de Spotify.")

    with wave.open(archivo_wav, "wb") as wf:
        wf.setnchannels(CHANNELS)
        wf.setsampwidth(2)
        wf.setframerate(SAMPLE_RATE)
        wf.writeframes(grabacion.tobytes())

    log.info(f"✅ Grabación guardada: {archivo_wav}")
    return archivo_wav


def convertir_a_mp3(archivo_wav, archivo_jpg, meta, nombre_playlist):
    """ Convierte el WAV a MP3 (recortando silencio) e incrusta la metadata. """
    if not os.path.exists(archivo_wav):
        log.error("❌ ERROR: No se encontró el archivo WAV para convertir.")
        return None

    archivo_mp3 = archivo_wav.replace(".wav", ".mp3")
    # Conversión atómica: trabajamos en un .part y solo renombramos al final, así un corte
    # a mitad de conversión nunca deja un MP3 parcial que luego se saltaría por error.
    parcial = archivo_mp3 + ".part"

    try:
        audio = AudioSegment.from_wav(archivo_wav)

        # Recortamos el silencio de arranque y la cola muerta del colchón de +1 s.
        dur_original = len(audio)
        audio = recortar_silencio(audio)
        recortado_ms = dur_original - len(audio)
        if recortado_ms > 0:
            log.debug(f"✂️ Silencio recortado: {recortado_ms / 1000:.1f}s")

        audio.export(parcial, format="mp3", bitrate=BITRATE)
        # Etiquetamos el parcial (portada opcional) antes de publicarlo.
        incrustar_metadata_mp3(parcial, archivo_jpg, meta, nombre_playlist)

        os.replace(parcial, archivo_mp3)  # publicación atómica
        os.remove(archivo_wav)
        log.info(f"🎵 Convertido a MP3: {archivo_mp3}")
        return archivo_mp3
    except Exception as e:
        log.error(f"❌ ERROR al convertir a MP3: {e}")
        if os.path.exists(parcial):
            try:
                os.remove(parcial)
            except OSError:
                pass
        return None


def descargar_portada(url, nombre_playlist, artista, titulo):
    """ Descarga la portada en 'OUTPUT_DIR/<playlist>/cover/'. Devuelve la ruta o None. """
    if not url:
        log.debug("⚠️ Sin URL de portada.")
        return None

    carpeta_cover = os.path.join(OUTPUT_DIR, sanitizar_nombre(nombre_playlist), "cover")
    os.makedirs(carpeta_cover, exist_ok=True)

    nombre_archivo = sanitizar_nombre(f"{artista} - {titulo}") + ".jpg"
    ruta_destino = os.path.join(carpeta_cover, nombre_archivo)

    try:
        response = requests.get(url, stream=True, timeout=10)
        if response.status_code == 200:
            with open(ruta_destino, 'wb') as file:
                for chunk in response.iter_content(1024):
                    file.write(chunk)
            log.debug(f"🖼️ Portada guardada en: {ruta_destino}")
            return ruta_destino
        log.warning(f"❌ ERROR al descargar la portada: código {response.status_code}")
    except requests.RequestException as e:
        log.warning(f"❌ ERROR al descargar la portada: {e}")
    return None


def incrustar_metadata_mp3(archivo_mp3, archivo_jpg, meta, nombre_playlist):
    """ Incrusta portada (opcional) y metadata ID3 completa en el MP3. """
    try:
        audio = MP3(archivo_mp3, ID3=ID3)
        if not audio.tags:
            audio.tags = ID3()

        # Portada (opcional)
        if archivo_jpg and os.path.exists(archivo_jpg):
            with open(archivo_jpg, "rb") as img:
                audio.tags.add(APIC(encoding=3, mime="image/jpeg", type=3, desc="Cover", data=img.read()))
        else:
            log.debug("⚠️ Sin portada, MP3 sin imagen.")

        # Metadata básica
        audio.tags.add(TIT2(encoding=3, text=meta["titulo"]))
        audio.tags.add(TPE1(encoding=3, text=meta["artista"]))
        audio.tags.add(TALB(encoding=3, text=meta["album"]))

        # Metadata enriquecida (mejora el orden y la navegación en el iPod)
        if meta.get("album_artista"):
            audio.tags.add(TPE2(encoding=3, text=meta["album_artista"]))
        if meta.get("numero"):
            numero = str(meta["numero"])
            if meta.get("total_pistas"):
                numero += f"/{meta['total_pistas']}"
            audio.tags.add(TRCK(encoding=3, text=numero))
        if meta.get("disco"):
            audio.tags.add(TPOS(encoding=3, text=str(meta["disco"])))
        if meta.get("anio"):
            audio.tags.add(TDRC(encoding=3, text=meta["anio"]))

        # Comentario con el nombre de la playlist: Music filtra por él para crear la
        # playlist que se carga en el iPod.
        audio.tags.add(COMM(encoding=3, lang="eng", desc="", text=nombre_playlist))

        audio.save()
        log.debug(f"✅ Metadata incrustada en {archivo_mp3}")
    except Exception as e:
        log.error(f"❌ ERROR al incrustar la metadata en {archivo_mp3}: {e}")


# ==================== Apple Music (macOS) ====================

def create_playlist(playlist_name):
    """ Crea una playlist en Music si no existe. """
    playlist_name = escapar_applescript(playlist_name)
    script = f'''
    tell application "Music"
        set playlistExists to false
        repeat with p in playlists
            if name of p is "{playlist_name}" then
                set playlistExists to true
                exit repeat
            end if
        end repeat
        if playlistExists is false then
            make new playlist with properties {{name:"{playlist_name}"}}
        end if
    end tell
    '''
    subprocess.run(["osascript", "-e", script])


def delete_playlist(playlist_name):
    """ Elimina una playlist en Music si existe. """
    playlist_name = escapar_applescript(playlist_name)
    script = f'''
    tell application "Music"
        set playlistExists to false
        repeat with p in playlists
            if name of p is "{playlist_name}" then
                set playlistExists to true
                exit repeat
            end if
        end repeat
        if playlistExists is true then
            delete playlist "{playlist_name}"
        else
            log "⚠️ La playlist '{playlist_name}' no existe."
        end if
    end tell
    '''
    subprocess.run(["osascript", "-e", script])


def stop_music():
    """ Pausa Music para evitar que cada MP3 importado se reproduzca automáticamente. """
    subprocess.run(["osascript", "-e", 'tell application "Music" to pause'])


def copy_tracks_with_comment(playlist_name):
    """ Copia a la playlist las canciones cuyo comentario coincide, sin duplicar. """
    create_playlist(playlist_name)  # asegurar que exista
    playlist_name = escapar_applescript(playlist_name)
    script = f'''
    tell application "Music"
        set targetPlaylist to playlist "{playlist_name}"

        set existingTracks to {{}}
        repeat with t in tracks of targetPlaylist
            set trackName to name of t
            set trackArtist to artist of t
            set existingTracks to existingTracks & {{trackName & " - " & trackArtist}}
        end repeat

        repeat with t in tracks of library playlist 1
            try
                if comment of t is not missing value and comment of t contains "{playlist_name}" then
                    set trackName to name of t
                    set trackArtist to artist of t
                    set trackKey to trackName & " - " & trackArtist
                    if trackKey is not in existingTracks then
                        duplicate t to targetPlaylist
                    end if
                end if
            end try
        end repeat
    end tell
    '''
    subprocess.run(["osascript", "-e", script])


def add_playlist_to_apple_music(directory_path, playlist_name):
    """ Importa los MP3 del directorio a Music y los agrupa en la playlist (solo macOS). """
    if not APPLE_MUSIC:
        log.info("ℹ️ Sincronización con Apple Music desactivada (--no-apple-music).")
        return
    if sys.platform != "darwin":
        log.info("ℹ️ Sincronización con Apple Music omitida: solo disponible en macOS.")
        return
    if not os.path.isdir(directory_path):
        log.warning(f"❌ No existe el directorio de la playlist: {directory_path}")
        return

    mp3_files = [os.path.join(directory_path, f) for f in os.listdir(directory_path) if f.endswith(".mp3")]
    if not mp3_files:
        log.warning("❌ No se encontraron archivos MP3 en el directorio.")
        return

    create_playlist(playlist_name)
    for mp3_path in mp3_files:
        mp3_path_esc = escapar_applescript(mp3_path)
        script = f'''
        tell application "Music"
            try
                open POSIX file "{mp3_path_esc}"
            on error errorMessage
                log "⚠️ Error con {mp3_path_esc}: " & errorMessage
            end try
            pause
        end tell
        '''
        subprocess.run(["osascript", "-e", script])

    stop_music()
    copy_tracks_with_comment(playlist_name)
    log.info("🎵 Playlist cargada en Apple Music! 🟢 lista para tu iPod")


# ==================== Flujo principal por playlist ====================

def _cancion_dict(t, album):
    """ Construye el dict de una canción a partir del track y su álbum. """
    imagenes = album.get("images") or []
    album_artistas = album.get("artists") or []
    return {
        "titulo": t["name"],
        "artista": t["artists"][0]["name"],
        "album": album.get("name", ""),
        "album_artista": album_artistas[0]["name"] if album_artistas else t["artists"][0]["name"],
        "portada": imagenes[0]["url"] if imagenes else None,
        "uri": t["uri"],
        "duracion": t["duration_ms"] / 1000,
        "numero": t.get("track_number"),
        "total_pistas": album.get("total_tracks"),
        "disco": t.get("disc_number"),
        "anio": (album.get("release_date") or "")[:4],
    }


def obtener_canciones(tipo, rid):
    """ Devuelve (nombre, [dicts de canciones]) para una playlist, álbum o track. """
    canciones = []
    if tipo == "playlist":
        nombre = sp.playlist(rid)["name"]
        resultados = sp.playlist_tracks(rid)
        while resultados:
            for item in resultados["items"]:
                t = item.get("track")
                if t:  # tracks eliminados/no disponibles aparecen como null
                    canciones.append(_cancion_dict(t, t["album"]))
            resultados = sp.next(resultados) if resultados.get("next") else None
    elif tipo == "album":
        album = sp.album(rid)
        nombre = album["name"]
        resultados = sp.album_tracks(rid)
        while resultados:
            for t in resultados["items"]:
                if t:  # los tracks de álbum no traen 'album', se lo pasamos aparte
                    canciones.append(_cancion_dict(t, album))
            resultados = sp.next(resultados) if resultados.get("next") else None
    elif tipo == "track":
        t = sp.track(rid)
        nombre = f'{t["artists"][0]["name"]} - {t["name"]}'
        canciones.append(_cancion_dict(t, t["album"]))
    else:
        raise ValueError(f"Tipo de recurso no soportado: {tipo}")
    return nombre, canciones


def grabar_recurso(tipo, rid):
    try:
        nombre_playlist, canciones = obtener_canciones(tipo, rid)
    except Exception as e:
        log.error(f"❌ ERROR al obtener {tipo} de Spotify: {e}")
        return

    if not canciones:
        log.warning(f"❌ No hay canciones en {tipo} '{rid}'")
        return

    nombre_playlist_dir = sanitizar_nombre(nombre_playlist)
    carpeta_playlist = os.path.join(OUTPUT_DIR, nombre_playlist_dir)
    os.makedirs(carpeta_playlist, exist_ok=True)

    # Limpiamos WAV huérfanos de corridas anteriores que crashearon antes de convertir.
    limpiar_wav_huerfanos(carpeta_playlist)

    total = len(canciones)
    log.info(f"🎶 {total} canciones en la playlist: {nombre_playlist}")

    # La grabación es en tiempo real, así que estimamos el tiempo restante sumando la
    # duración de las pistas que faltan por grabar. OVERHEAD_POR_PISTA cubre el arranque
    # de reproducción y la conversión.
    OVERHEAD_POR_PISTA = 4  # segundos aprox. por pista

    def ruta_mp3(artista, titulo):
        return os.path.join(carpeta_playlist, f"{sanitizar_nombre(artista)} - {sanitizar_nombre(titulo)}.mp3")

    # Índice normalizado de los MP3 ya presentes en la carpeta, para reconocer pistas que
    # ya tienes AUNQUE varíe el formato del nombre (p. ej. descargadas antes desde el iPod).
    existentes_norm = {limpiar_nombre_archivo(os.path.basename(f)[:-4])
                       for f in glob.glob(os.path.join(carpeta_playlist, "*.mp3"))}

    def ya_grabada(c):
        if os.path.exists(ruta_mp3(c["artista"], c["titulo"])):
            return True
        return limpiar_nombre_archivo(f"{c['artista']} - {c['titulo']}") in existentes_norm

    pendientes = [c for c in canciones if not ya_grabada(c)]
    total_nuevas = len(pendientes)
    ya_existen = total - total_nuevas
    segundos_restantes = sum(c["duracion"] + OVERHEAD_POR_PISTA for c in pendientes)

    # Archivo de progreso: se actualiza tras cada pista, para poder retomar tras un corte.
    progreso_path = os.path.join(carpeta_playlist, ".spotipod-progress.json")
    fallidas = []

    def guardar_progreso(ultima=None):
        grabadas = sum(1 for c in canciones if ya_grabada(c))
        try:
            with open(progreso_path, "w", encoding="utf-8") as f:
                json.dump({
                    "tipo": tipo, "id": rid, "nombre": nombre_playlist,
                    "total": total, "grabadas": grabadas, "faltantes": total - grabadas,
                    "fallidas": fallidas, "ultima_pista": ultima,
                    "actualizado": time.strftime("%Y-%m-%d %H:%M:%S"),
                }, f, indent=2, ensure_ascii=False)
        except OSError:
            pass

    # Resumen de reanudación (lee las fallidas de la corrida anterior, si las hubo).
    prev_fallidas = []
    if os.path.exists(progreso_path):
        try:
            with open(progreso_path, encoding="utf-8") as f:
                prev_fallidas = json.load(f).get("fallidas", [])
        except Exception:
            pass

    if ya_existen:
        banner = f"🔄 Reanudando '{nombre_playlist}': {ya_existen}/{total} ya grabadas"
        if prev_fallidas:
            banner += f", {len(prev_fallidas)} fallaron en Spotify la última vez"
        log.info(banner)
    log.info(f"⏱ Por grabar: {total_nuevas} pista(s) nueva(s) — tiempo estimado ~{formatear_duracion(segundos_restantes)}")
    log.info("----------------------------- [Download Playlist] ----------------------------------")

    device_id = obtener_dispositivo_activo()
    if not device_id:
        log.error("❌ No apareció ningún dispositivo Spotify. Abre Spotify y reproduce una canción, "
                  "y déjalo abierto; luego vuelve a ejecutar.")
        return

    guardar_progreso()  # snapshot inicial

    nueva = 0  # cuántas pistas NUEVAS llevamos grabadas (distinto de la posición en la playlist)
    for indice, c in enumerate(canciones, start=1):
        duracion = c["duracion"]
        artista_arch = sanitizar_nombre(c["artista"])
        titulo_arch = sanitizar_nombre(c["titulo"])

        archivo_wav = os.path.join(carpeta_playlist, f"{artista_arch} - {titulo_arch}.wav")
        archivo_mp3 = archivo_wav.replace(".wav", ".mp3")

        if ya_grabada(c):
            log.info(f"[pista {indice}/{total}] ⏭ ya grabada: {artista_arch} - {titulo_arch}")
            continue

        nueva += 1
        etiqueta = f"{c['artista']} - {c['titulo']}"
        log.info(f"[pista {indice}/{total} · nueva {nueva}/{total_nuevas}] 🎧 {etiqueta} "
                 f"({formatear_duracion(duracion)}) — restante ~{formatear_duracion(segundos_restantes)}")

        archivo_wav = grabar_audio(archivo_wav, duracion, device_id, c, nombre_playlist, carpeta_playlist)
        if archivo_wav:
            archivo_jpg = descargar_portada(c["portada"], nombre_playlist, c["artista"], c["titulo"])
            convertir_a_mp3(archivo_wav, archivo_jpg, c, nombre_playlist)
        else:
            fallidas.append(etiqueta)  # Spotify no la reprodujo (se intentó recuperar)

        guardar_progreso(etiqueta)  # persistimos el avance tras cada pista
        segundos_restantes = max(0, segundos_restantes - (duracion + OVERHEAD_POR_PISTA))
        log.info("----------------------------- [Next Track] -----------------------------------------")

    detener_reproduccion(device_id)

    # Respaldamos la playlist en JSON.
    guardar_playlist_json(tipo, rid, nombre_playlist, canciones)

    # Sincronización: iPod directo (sin Music.app) o vía Apple Music.
    if IPOD_MOUNT:
        sincronizar_ipod_directo(carpeta_playlist, nombre_playlist)
    else:
        add_playlist_to_apple_music(os.path.abspath(carpeta_playlist), nombre_playlist)


def _cargar_ipod_module():
    """ Importa tools/ipod_sync y devuelve (módulo, ruta_ipod) o (None, None) con log de error. """
    try:
        sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "tools"))
        import ipod_sync
    except Exception as e:
        log.error(f"❌ No se pudo cargar el módulo de iPod: {e}")
        return None, None
    ipod = ipod_sync.detectar_ipod(None if IPOD_MOUNT in (None, "auto") else IPOD_MOUNT)
    if not ipod:
        log.error("❌ No se encontró un iPod montado. Conéctalo o usa --ipod /Volumes/iPod")
        return ipod_sync, None
    return ipod_sync, ipod


def _ipod_montado_incompatible():
    """
    Chequeo silencioso: (True, motivo) si hay un iPod montado que NO soporta la carga directa;
    (False, "") si no hay iPod montado o es compatible.
    """
    try:
        sys.path.insert(0, TOOLS_DIR)
        import ipod_sync as mod
        ipod = mod.detectar_ipod(None if IPOD_MOUNT in (None, "auto") else IPOD_MOUNT)
        if not ipod:
            return False, ""
        compat, motivo = mod.compatibilidad(ipod)
        return (not compat), motivo
    except Exception:
        return False, ""


def sincronizar_ipod_directo(carpeta_playlist, nombre_playlist):
    """ Carga los MP3 de la carpeta directo a la base de datos del iPod (sin Music.app). """
    mp3s = sorted(glob.glob(os.path.join(carpeta_playlist, "*.mp3")))
    if not mp3s:
        log.warning("❌ No hay MP3 que cargar al iPod.")
        return
    mod, ipod = _cargar_ipod_module()
    if not ipod:
        return
    try:
        mod.sync(ipod, nombre_playlist, mp3s, log=log.info)
    except Exception as e:
        log.error(f"❌ ERROR al cargar al iPod: {e}")


def respaldar_db_ipod(dest):
    """ Respalda la base de datos del iPod a una carpeta local. """
    mod, ipod = _cargar_ipod_module()
    if not ipod:
        return
    try:
        mod.backup_database(ipod, dest, log=log.info)
    except Exception as e:
        log.error(f"❌ ERROR al respaldar la DB del iPod: {e}")


def restaurar_db_ipod(dest):
    """ Restaura la DB del iPod desde uno de los backups en 'dest' (elegido por el usuario). """
    mod, ipod = _cargar_ipod_module()
    if not ipod:
        return
    if not os.path.isdir(dest):
        log.error(f"❌ No existe la carpeta de backups: {dest}")
        return
    backups = sorted(glob.glob(os.path.join(dest, "iTunesDB-backup-*")), reverse=True)
    if not backups:
        log.error(f"❌ No hay backups en {dest} (usa la opción de respaldar primero).")
        return

    print("\nBackups disponibles (más reciente primero):")
    for i, b in enumerate(backups, 1):
        print(f"  {i}) {os.path.basename(b)}")
    sel = input(f"¿Cuál restaurar? [1-{len(backups)}] (Enter = cancelar): ").strip()
    if not sel.isdigit() or not (1 <= int(sel) <= len(backups)):
        print("Cancelado.")
        return
    elegido = backups[int(sel) - 1]
    if input(f"⚠️ Restaurar '{os.path.basename(elegido)}' sobre el iPod? [s/N]: ").strip().lower() != "s":
        print("Cancelado.")
        return
    try:
        mod.restore_database(ipod, elegido, log=log.info)
    except Exception as e:
        log.error(f"❌ ERROR al restaurar la DB del iPod: {e}")


def gestionar_playlists_ipod():
    """ Lista las playlists del iPod y permite borrar una de usuario. """
    mod, ipod = _cargar_ipod_module()
    if not ipod:
        return
    try:
        pls = mod.list_ipod_playlists(ipod)
    except Exception as e:
        log.error(f"❌ ERROR al leer las playlists del iPod: {e}")
        return

    usuarios = [p for p in pls if not p["master"]]
    print(f"\n🎵 Playlists en el iPod ({len(usuarios)} de usuario):")
    for i, p in enumerate(usuarios, 1):
        print(f"  {i}) {p['nombre']}  ({p['items']} pistas)")
    if not usuarios:
        return
    sel = input(f"Nº a BORRAR [1-{len(usuarios)}] (Enter = no borrar): ").strip()
    if not sel.isdigit() or not (1 <= int(sel) <= len(usuarios)):
        return
    nombre = usuarios[int(sel) - 1]["nombre"]
    if input(f"⚠️ Borrar la playlist '{nombre}' del iPod (no borra las canciones)? [s/N]: ").strip().lower() != "s":
        print("Cancelado.")
        return
    try:
        mod.delete_ipod_playlist(ipod, nombre, log=log.info)
    except Exception as e:
        log.error(f"❌ ERROR al borrar la playlist: {e}")


def montar_ipod():
    """ Monta el iPod conectado (útil si lo expulsaste pero sigue enchufado). Solo macOS. """
    if sys.platform != "darwin":
        log.info("ℹ️ Montaje automático solo disponible en macOS (usa tu gestor de archivos).")
        return
    ya = _ipod_montado()
    if ya:
        log.info(f"✅ El iPod ya está montado en {ya}")
        return

    import plistlib
    log.info("🔌 Buscando un iPod conectado...")
    try:
        salida = subprocess.run(["diskutil", "list", "-plist", "external", "physical"],
                                capture_output=True, text=True).stdout
        discos = plistlib.loads(salida.encode()).get("AllDisksAndPartitions", [])
    except Exception as e:
        log.error(f"❌ No se pudieron listar los discos: {e}")
        return

    candidato = None
    for d in discos:
        ident = d.get("DeviceIdentifier")
        if not ident:
            continue
        try:
            info = plistlib.loads(subprocess.run(
                ["diskutil", "info", "-plist", ident], capture_output=True, text=True).stdout.encode())
        except Exception:
            continue
        nombre = f"{info.get('MediaName', '')} {info.get('IORegistryEntryName', '')}"
        if "iPod" in nombre:
            candidato = ident
            break

    if not candidato:
        log.error("❌ No se detectó ningún iPod conectado. Conéctalo y activa el modo disco.")
        return

    log.info(f"🔧 Montando /dev/{candidato}...")
    subprocess.run(["diskutil", "mountDisk", f"/dev/{candidato}"])
    time.sleep(1)
    nuevo = _ipod_montado()
    if nuevo:
        log.info(f"✅ iPod montado en {nuevo}")
    else:
        log.error("❌ No se pudo montar. ¿El iPod está en modo disco?")


def expulsar_ipod():
    """ Expulsa el iPod con seguridad (para que recargue la biblioteca). """
    mod, ipod = _cargar_ipod_module()
    if not ipod:
        return
    log.info(f"⏏️  Expulsando {ipod}...")
    r = subprocess.run(["diskutil", "eject", ipod])
    if r.returncode == 0:
        log.info("✅ iPod expulsado. Ya puedes desconectarlo.")
    else:
        log.error("❌ No se pudo expulsar (¿algún proceso lo está usando?).")


def probar_captura():
    """ Graba unos segundos de BlackHole y reporta el nivel, para validar el enrutamiento. """
    try:
        sys.path.insert(0, TOOLS_DIR)
        import check_level
    except Exception as e:
        log.error(f"❌ No se pudo cargar check_level: {e}")
        return
    check_level.medir_nivel(segundos=3, samplerate=SAMPLE_RATE, log=log.info)


def verificar_grabaciones():
    """ Revisa los MP3 en OUTPUT_DIR y reporta los que están mudos o casi vacíos. """
    mp3s = sorted(glob.glob(os.path.join(OUTPUT_DIR, "**", "*.mp3"), recursive=True))
    if not mp3s:
        log.warning(f"No hay MP3 en '{OUTPUT_DIR}'.")
        return
    log.info(f"🔎 Revisando {len(mp3s)} grabaciones en '{OUTPUT_DIR}'...")
    sospechosos = []
    for m in mp3s:
        try:
            audio = AudioSegment.from_file(m)
            dbfs = audio.max_dBFS
            if dbfs == float("-inf") or dbfs < -50 or len(audio) < 3000:
                sospechosos.append((m, dbfs, len(audio)))
        except Exception as e:
            sospechosos.append((m, None, f"error: {e}"))
    if not sospechosos:
        log.info("✅ Todas las grabaciones tienen audio.")
        return
    log.warning(f"⚠️ {len(sospechosos)} grabación(es) sospechosa(s) (mudas/truncadas):")
    for m, dbfs, dur in sospechosos:
        detalle = f"{dbfs:.0f} dBFS" if isinstance(dbfs, float) else str(dur)
        log.warning(f"   • {os.path.relpath(m, OUTPUT_DIR)}  ({detalle})")

    if sys.stdin.isatty() and input("\n   ¿Borrar las sospechosas para regrabarlas? [s/N]: ").strip().lower() == "s":
        borradas = 0
        for m, _, _ in sospechosos:
            try:
                os.remove(m)
                borradas += 1
            except OSError as e:
                log.error(f"   No se pudo borrar {m}: {e}")
        log.info(f"   🗑️  {borradas} borrada(s). Vuelve a grabar la playlist para regrabarlas (solo faltarán esas).")
    else:
        log.info("   Bórralas y vuelve a grabar la playlist: se regrabarán solo las que falten.")


def configurar_credenciales():
    """ Asistente que pide las credenciales de Spotify y las guarda en .env. """
    print("\nConfigura tus credenciales de Spotify (https://developer.spotify.com/dashboard).")
    cid = input("SPOTIFY_CLIENT_ID: ").strip()
    csec = input("SPOTIFY_CLIENT_SECRET: ").strip()
    if not cid or not csec:
        print("⚠️ Cancelado: ambos valores son obligatorios.")
        return
    redirect = input("Redirect URI [Enter = http://127.0.0.1:8080]: ").strip() or "http://127.0.0.1:8080"
    ruta = ".env"
    if os.path.exists(ruta) and input(f"⚠️ {ruta} ya existe. ¿Sobrescribir? [s/N]: ").strip().lower() != "s":
        print("Cancelado.")
        return
    with open(ruta, "w") as f:
        f.write(f'SPOTIFY_CLIENT_ID={cid}\n')
        f.write(f'SPOTIFY_CLIENT_SECRET={csec}\n')
        f.write(f'SPOTIFY_REDIRECT_URI={redirect}\n')
    print(f"✅ Guardado en {ruta}. Registra '{redirect}' como Redirect URI en el dashboard.")
    # Cargar en el entorno de esta sesión
    os.environ["SPOTIFY_CLIENT_ID"] = cid
    os.environ["SPOTIFY_CLIENT_SECRET"] = csec
    os.environ["SPOTIFY_REDIRECT_URI"] = redirect
    global SPOTIFY_REDIRECT_URI
    SPOTIFY_REDIRECT_URI = redirect


def diagnostico():
    """ Muestra el estado del entorno con un panel: captura de audio, ffmpeg, Spotify e iPod. """
    import shutil as _sh
    tty = sys.stdout.isatty()
    ANCHO = 58

    def col(code, s):
        return f"\033[{code}m{s}\033[0m" if tty else s

    def estado(ok):
        # ok: True (OK), False (falla), None (aviso)
        if ok is True:
            return col("32", "[  OK   ]")
        if ok is False:
            return col("31", "[ FALLA ]")
        return col("33", "[ AVISO ]")

    def fila(label, ok, valor=""):
        print(f"   {estado(ok)}  {label:<16} {col('2', valor)}")

    def seccion(titulo):
        print("\n   " + col("1;36", titulo))

    print()
    print("  ╭" + "─" * ANCHO + "╮")
    titulo = "◆ Diagnóstico SpotiPOD"
    print("  │ " + col("1", titulo) + " " * (ANCHO - len(titulo) - 1) + "│")
    print("  ╰" + "─" * ANCHO + "╯")

    # --- Audio ---
    seccion("Audio")
    virtual = None
    try:
        for d in sd.query_devices():
            if any(k in d["name"] for k in ("BlackHole", "CABLE Input", "Loopback")) and d["max_input_channels"] > 0:
                virtual = d["name"]
                break
    except Exception:
        pass
    fila("Captura virtual", bool(virtual), virtual or "no encontrada (instala BlackHole)")
    ff = _sh.which("ffmpeg")
    fila("ffmpeg", bool(ff), ff or "no instalado")

    # --- Spotify ---
    seccion("Spotify")
    cred = bool(os.getenv("SPOTIFY_CLIENT_ID") and os.getenv("SPOTIFY_CLIENT_SECRET"))
    fila("Credenciales", cred, "configuradas" if cred else "faltan (SPOTIFY_CLIENT_ID/SECRET)")

    # --- iPod (detección silenciosa) ---
    seccion("iPod")
    ipod = None
    try:
        sys.path.insert(0, TOOLS_DIR)
        import ipod_sync as _mod
        ipod = _mod.detectar_ipod(None if IPOD_MOUNT in (None, "auto") else IPOD_MOUNT)
    except Exception:
        _mod = None
    if not ipod:
        fila("Conexión", False, "no montado")
    else:
        fila("Conexión", True, ipod)
        try:
            st = os.statvfs(ipod)
            libre = st.f_bavail * st.f_frsize / 1e9
            total = st.f_blocks * st.f_frsize / 1e9
            usado_pct = 100 * (1 - st.f_bavail / st.f_blocks) if st.f_blocks else 0
            fila("Almacenamiento", None if usado_pct > 90 else True,
                 f"{total:.0f} GB · {libre:.1f} GB libres ({usado_pct:.0f}% usado)")
            stats = _mod.ipod_stats(ipod)
            fila("Biblioteca", True, f"{stats['tracks']} pistas · {stats['playlists']} playlists")
            compat, motivo = _mod.compatibilidad(ipod)
            fila("Carga directa", compat, "compatible" if compat else motivo)
        except Exception as e:
            fila("Base de datos", False, f"no se pudo leer: {e}")
    print()


def guardar_playlist_json(tipo, playlist_id, nombre_playlist, canciones):
    """ Respalda la lista de canciones (con su metadata) en un JSON dentro de la carpeta. """
    nombre_dir = sanitizar_nombre(nombre_playlist)
    archivo_json = os.path.join(OUTPUT_DIR, nombre_dir, nombre_dir + ".json")
    datos = {
        "tipo": tipo,
        "playlist_id": playlist_id,
        "nombre": nombre_playlist,
        "total_canciones": len(canciones),
        "canciones": canciones,
    }
    with open(archivo_json, "w", encoding="utf-8") as archivo:
        json.dump(datos, archivo, indent=4, ensure_ascii=False)
    log.info(f"💾 Playlist '{nombre_playlist}' respaldada en {archivo_json}")


# ==================== Entrada / CLI ====================

def extraer_recurso(entrada):
    """
    Normaliza la entrada a (tipo, id). Acepta playlist, álbum o track, en formato:
      - ID pelado:  3mUUSjvE5q5anEoLNHKfGz            → ('playlist', id)
      - URI:        spotify:album:3mUUSjvE5q5anEoLNHKfGz
      - URL:        https://open.spotify.com/track/3mUUSjvE5q5anEoLNHKfGz?si=...
    """
    entrada = entrada.strip()
    m = re.search(r'(playlist|album|track)[:/]([A-Za-z0-9]+)', entrada)
    if m:
        return m.group(1), m.group(2)
    # ID pelado sin tipo: se asume playlist (compatibilidad con playlist.txt)
    return "playlist", entrada.split("?")[0].rstrip("/").split("/")[-1]


def leer_playlists(playlists_cli):
    """ Usa las entradas de CLI o, si no hay, playlist.txt. Devuelve [(tipo, id)]. """
    if playlists_cli:
        entradas = playlists_cli
    else:
        with open("playlist.txt") as f:
            entradas = [linea.strip() for linea in f if linea.strip()]
    return [extraer_recurso(e) for e in entradas]


def parse_args():
    p = argparse.ArgumentParser(
        prog="spotipod",
        description="SpotiPOD - graba tus playlists de Spotify a MP3 para respaldarlas en tu iPod.",
    )
    p.add_argument("playlists", nargs="*",
                   help="IDs/URIs/URLs de playlist, álbum o track. Si se omite, se lee playlist.txt")
    # Estos usan default=None para distinguir "no pasado" (→ config/defaults) de un valor.
    p.add_argument("--output-dir", default=None, help="Carpeta de salida (por defecto: Playlist)")
    p.add_argument("--bitrate", default=None, help="Bitrate del MP3 (por defecto: 320k)")
    p.add_argument("--sample-rate", type=int, default=None,
                   help="Frecuencia de muestreo (por defecto: auto-detectada del dispositivo)")
    p.add_argument("--silence-threshold", type=int, default=None,
                   help="Pico mínimo (int16) para no considerar la grabación silencio (por defecto: 300)")
    p.add_argument("--retries", type=int, default=None, help="Reintentos de reproducción por pista (por defecto: 3)")
    p.add_argument("--no-apple-music", action="store_true", help="No sincronizar con Apple Music")
    p.add_argument("--ipod", nargs="?", const="auto", default=None, metavar="MONTAJE",
                   help="Cargar la playlist DIRECTO a la base de datos del iPod (sin Music.app). "
                        "Opcional: ruta de montaje (autodetecta si se omite). Solo iPods clásicos sin firma hash.")
    p.add_argument("--backup-db-ipod", nargs="?", const="iPod_DB_Backup", default=None, metavar="DEST",
                   help="Respalda la base de datos del iPod (iTunesDB) a una carpeta local y sale. "
                        "Opcional: carpeta destino (por defecto: iPod_DB_Backup).")
    p.add_argument("-v", "--verbose", action="store_true", help="Log detallado (nivel DEBUG en consola)")
    return p.parse_args()


def cargar_config():
    """
    Carga preferencias desde spotipod.toml (en el directorio actual) o ~/.spotipod.toml.
    Devuelve (dict, ruta|None). Requiere Python 3.11+ (tomllib).
    """
    for ruta in ("spotipod.toml", os.path.expanduser("~/.spotipod.toml")):
        if os.path.isfile(ruta):
            try:
                import tomllib
            except ModuleNotFoundError:
                log.warning("⚠️ El archivo de config .toml requiere Python 3.11+; se ignora.")
                return {}, None
            try:
                with open(ruta, "rb") as f:
                    return tomllib.load(f), ruta
            except Exception as e:
                log.warning(f"⚠️ No se pudo leer {ruta}: {e}")
                return {}, None
    return {}, None


def main():
    global OUTPUT_DIR, SAMPLE_RATE, BITRATE, REINTENTOS, UMBRAL_SILENCIO, APPLE_MUSIC
    global sp, DISPOSITIVO_ID, IPOD_MOUNT

    args = parse_args()
    configurar_logging(args.verbose)

    cfg, cfg_ruta = cargar_config()
    if cfg_ruta:
        log.info(f"⚙️  Config cargada: {cfg_ruta}")

    def opt(cli, clave, defecto):
        # Precedencia: argumento CLI > config file > valor por defecto.
        return cli if cli is not None else cfg.get(clave, defecto)

    OUTPUT_DIR = opt(args.output_dir, "output_dir", os.getenv("SPOTIPOD_OUTPUT_DIR", "Playlist"))
    BITRATE = opt(args.bitrate, "bitrate", "320k")
    SAMPLE_RATE = opt(args.sample_rate, "sample_rate", None)   # None → auto-detectar
    REINTENTOS = opt(args.retries, "retries", 3)
    UMBRAL_SILENCIO = opt(args.silence_threshold, "silence_threshold", 300)
    APPLE_MUSIC = False if args.no_apple_music else cfg.get("apple_music", True)
    if args.ipod is not None:
        IPOD_MOUNT = args.ipod
    else:
        ci = cfg.get("ipod")
        IPOD_MOUNT = "auto" if ci is True else (ci if isinstance(ci, str) else None)

    log.info(BANNER)

    # Acción independiente: respaldar la DB del iPod y salir (no requiere Spotify ni BlackHole).
    if args.backup_db_ipod is not None:
        respaldar_db_ipod(args.backup_db_ipod)
        return

    # Sin argumentos y en terminal interactiva → menú.
    if len(sys.argv) == 1 and sys.stdin.isatty():
        menu_interactivo()
        return

    ejecutar_grabacion(args.playlists)


def ejecutar_grabacion(entradas):
    """ Prepara Spotify/dispositivo (si hace falta) y graba las playlists dadas. """
    global sp, DISPOSITIVO_ID, SAMPLE_RATE
    os.makedirs(OUTPUT_DIR, exist_ok=True)
    if sp is None:
        sp = crear_cliente_spotify()
    if DISPOSITIVO_ID is None:
        DISPOSITIVO_ID = obtener_dispositivo_virtual()
    if SAMPLE_RATE is None:
        SAMPLE_RATE = sample_rate_dispositivo(DISPOSITIVO_ID)
        log.info(f"🎚 Sample rate auto-detectado del dispositivo: {SAMPLE_RATE} Hz")

    recursos = leer_playlists(entradas)
    if not recursos:
        log.warning("❌ No hay nada que procesar (pásalo como argumento o en playlist.txt).")
        return

    for tipo, rid in recursos:
        grabar_recurso(tipo, rid)

    log.info("✅ Todo ha sido procesado.")
    log.info("----------------------------- [STOP SPOTI POD] -------------------------------------")


def _carpetas_grabadas():
    """ Carpetas de OUTPUT_DIR que contienen MP3 (playlists grabadas). """
    return [d for d in sorted(glob.glob(os.path.join(OUTPUT_DIR, "*")))
            if os.path.isdir(d) and glob.glob(os.path.join(d, "*.mp3"))]


def _elegir(carpetas, accion="usar"):
    """ Muestra una lista numerada de carpetas y devuelve la elegida (o None). """
    print(f"\n   {_color('1;36', 'Playlists grabadas')}:")
    for i, d in enumerate(carpetas, 1):
        n = len(glob.glob(os.path.join(d, "*.mp3")))
        print(f"     {_color('1;32', str(i).rjust(2))}  {os.path.basename(d)}  {_color('2', f'({n} MP3)')}")
    sel = input(f"\n   Nº a {accion} [1-{len(carpetas)}] (Enter = cancelar): ").strip()
    if sel.isdigit() and 1 <= int(sel) <= len(carpetas):
        return carpetas[int(sel) - 1]
    return None


def _asegurar_spotify():
    """ Garantiza credenciales + cliente Spotify inicializado. Devuelve True si está listo. """
    global sp
    if not (os.getenv("SPOTIFY_CLIENT_ID") and os.getenv("SPOTIFY_CLIENT_SECRET")):
        print(_color("31", "   ⚠️  Configura tus credenciales primero (opción 15)."))
        return False
    if sp is None:
        try:
            sp = crear_cliente_spotify()
        except SystemExit:
            return False
    return True


def _elegir_destino_y_grabar(entradas):
    """ Pregunta destino (Apple Music / iPod / local) y lanza la grabación. """
    global IPOD_MOUNT, APPLE_MUSIC
    d = input("   Destino: [1] Apple Music  [2] iPod directo  [3] solo local  (Enter=1): ").strip()
    if d == "2":
        IPOD_MOUNT, APPLE_MUSIC = "auto", False
    elif d == "3":
        IPOD_MOUNT, APPLE_MUSIC = None, False
    else:
        IPOD_MOUNT, APPLE_MUSIC = None, True
    ejecutar_grabacion(entradas)


def buscar_spotify():
    """ Busca en Spotify por nombre (playlist/álbum/track), elige un resultado y graba. """
    if not _asegurar_spotify():
        return
    q = input("   Buscar en Spotify: ").strip()
    if not q:
        return
    tipo = (input("   Tipo [playlist/album/track] (Enter=playlist): ").strip().lower() or "playlist")
    if tipo not in ("playlist", "album", "track"):
        tipo = "playlist"
    try:
        res = sp.search(q, type=tipo, limit=20)
        items = [it for it in res[tipo + "s"]["items"] if it]
    except Exception as e:
        log.error(f"❌ Error en la búsqueda: {e}")
        return
    if not items:
        print("   Sin resultados.")
        return

    print(f"\n   {_color('1;36', 'Resultados')}:")
    for i, it in enumerate(items, 1):
        if tipo == "playlist":
            ctx = (it.get("owner") or {}).get("display_name", "")
        else:
            ctx = ", ".join(a["name"] for a in it.get("artists", []))
        print(f"     {_color('1;32', str(i).rjust(2))}  {it['name']}  {_color('2', ctx)}")
    sel = input(f"\n   Nº a grabar [1-{len(items)}] (Enter = cancelar): ").strip()
    if not sel.isdigit() or not (1 <= int(sel) <= len(items)):
        return
    _elegir_destino_y_grabar([items[int(sel) - 1]["uri"]])


def actualizar_respaldo():
    """ Regraba SOLO las pistas nuevas de una playlist ya respaldada (lee su id del JSON). """
    if not _asegurar_spotify():
        return
    respaldos = []
    for d in sorted(glob.glob(os.path.join(OUTPUT_DIR, "*"))):
        j = os.path.join(d, os.path.basename(d) + ".json")
        if os.path.isdir(d) and os.path.isfile(j):
            respaldos.append((d, j))
    if not respaldos:
        print(_color("2", f"   No hay respaldos con metadata en '{OUTPUT_DIR}'."))
        return

    print(f"\n   {_color('1;36', 'Respaldos a actualizar')}:")
    for i, (d, _) in enumerate(respaldos, 1):
        n = len(glob.glob(os.path.join(d, "*.mp3")))
        print(f"     {_color('1;32', str(i).rjust(2))}  {os.path.basename(d)}  {_color('2', f'({n} MP3)')}")
    sel = input(f"\n   Nº a actualizar [1-{len(respaldos)}] (Enter = cancelar): ").strip()
    if not sel.isdigit() or not (1 <= int(sel) <= len(respaldos)):
        return

    _, j = respaldos[int(sel) - 1]
    try:
        with open(j, encoding="utf-8") as f:
            data = json.load(f)
    except Exception as e:
        log.error(f"❌ No se pudo leer {j}: {e}")
        return
    rid = data.get("playlist_id")
    if not rid:
        print("   Este respaldo no tiene ID de Spotify; no se puede actualizar.")
        return
    tipo = data.get("tipo", "playlist")
    print(f"   Actualizando '{data.get('nombre')}' — solo se grabarán las pistas nuevas.")
    _elegir_destino_y_grabar([f"spotify:{tipo}:{rid}"])


def _escribir_m3u(carpeta):
    """ Genera un .m3u (nombres relativos + #EXTINF) para la carpeta. Devuelve (ruta, nº). """
    from mutagen.easyid3 import EasyID3
    mp3s = sorted(glob.glob(os.path.join(carpeta, "*.mp3")))
    ruta = os.path.join(carpeta, os.path.basename(carpeta) + ".m3u")
    with open(ruta, "w", encoding="utf-8") as f:
        f.write("#EXTM3U\n")
        for m in mp3s:
            try:
                dur = int(MP3(m).info.length)
                e = EasyID3(m)
                titulo = (e.get("title") or [""])[0]
                artista = (e.get("artist") or [""])[0]
            except Exception:
                dur, titulo, artista = -1, os.path.splitext(os.path.basename(m))[0], ""
            f.write(f"#EXTINF:{dur},{artista} - {titulo}\n")
            f.write(os.path.basename(m) + "\n")
    return ruta, len(mp3s)


def exportar_m3u():
    """ Genera un .m3u por playlist grabada, para reproducirla en cualquier player. """
    carpetas = _carpetas_grabadas()
    if not carpetas:
        print(_color("2", f"   No hay playlists grabadas en '{OUTPUT_DIR}'."))
        return
    print(f"\n   {_color('1;36', 'Playlists grabadas')}:")
    for i, d in enumerate(carpetas, 1):
        n = len(glob.glob(os.path.join(d, "*.mp3")))
        print(f"     {_color('1;32', str(i).rjust(2))}  {os.path.basename(d)}  {_color('2', f'({n} MP3)')}")
    sel = input(f"\n   Nº a exportar [1-{len(carpetas)}], 'a' = todas (Enter = cancelar): ").strip().lower()
    if sel in ("a", "all", "todas"):
        elegidas = carpetas
    elif sel.isdigit() and 1 <= int(sel) <= len(carpetas):
        elegidas = [carpetas[int(sel) - 1]]
    else:
        return
    for c in elegidas:
        ruta, n = _escribir_m3u(c)
        print(f"   ✅ {os.path.relpath(ruta, OUTPUT_DIR)}  ({n} pistas)")


def gestionar_grabaciones_locales():
    """ Lista las carpetas grabadas con tamaño y permite borrar una. """
    import shutil
    carpetas = _carpetas_grabadas()
    if not carpetas:
        print(_color("2", f"   No hay playlists grabadas en '{OUTPUT_DIR}'."))
        return
    print(f"\n   {_color('1;36', 'Grabaciones locales')}:")
    for i, d in enumerate(carpetas, 1):
        n = len(glob.glob(os.path.join(d, "*.mp3")))
        tam = sum(os.path.getsize(os.path.join(dp, f))
                  for dp, _, fs in os.walk(d) for f in fs) / 1e6
        print(f"     {_color('1;32', str(i).rjust(2))}  {os.path.basename(d)}  {_color('2', f'({n} MP3 · {tam:.0f} MB)')}")
    sel = input(f"\n   Nº a BORRAR [1-{len(carpetas)}] (Enter = no borrar): ").strip()
    if not sel.isdigit() or not (1 <= int(sel) <= len(carpetas)):
        return
    carpeta = carpetas[int(sel) - 1]
    if input(f"   ⚠️ Borrar '{os.path.basename(carpeta)}' y sus MP3 del disco? [s/N]: ").strip().lower() != "s":
        print("   Cancelado.")
        return
    shutil.rmtree(carpeta)
    print(f"   🗑️  Borrado: {carpeta}")


def descargar_playlist_del_ipod():
    """ Lista las playlists del iPod y descarga la elegida a la carpeta local Playlist/. """
    mod, ipod = _cargar_ipod_module()
    if not ipod:
        return
    try:
        pls = [p for p in mod.list_ipod_playlists(ipod) if not p["master"]]
    except Exception as e:
        log.error(f"❌ No se pudieron leer las playlists del iPod: {e}")
        return
    if not pls:
        log.warning("El iPod no tiene playlists de usuario.")
        return

    print(f"\n   {_color('1;36', 'Playlists en el iPod')}:")
    for i, p in enumerate(pls, 1):
        detalle = _color("2", f"({p['items']} pistas)")
        print(f"     {_color('1;32', str(i).rjust(2))}  {p['nombre']}  {detalle}")
    sel = input(f"\n   Nº a descargar [1-{len(pls)}] (Enter = cancelar): ").strip()
    if not sel.isdigit() or not (1 <= int(sel) <= len(pls)):
        return
    nombre = pls[int(sel) - 1]["nombre"]
    try:
        mod.descargar_playlist(ipod, nombre, OUTPUT_DIR, log=log.info)
    except Exception as e:
        log.error(f"❌ ERROR al descargar la playlist: {e}")


def descargar_todas_del_ipod():
    """ Descarga todas las playlists del iPod a la carpeta local Playlist/. """
    mod, ipod = _cargar_ipod_module()
    if not ipod:
        return
    if input(f"   ¿Descargar TODAS las playlists del iPod a '{OUTPUT_DIR}/'? [s/N]: ").strip().lower() != "s":
        return
    try:
        mod.descargar_todas_playlists(ipod, OUTPUT_DIR, log=log.info)
    except Exception as e:
        log.error(f"❌ ERROR al descargar las playlists: {e}")


def cargar_grabada_al_ipod():
    """
    Lista las playlists grabadas localmente en OUTPUT_DIR y carga la elegida al iPod,
    indicando el nombre de la playlist a crear/sincronizar (por defecto, el de la carpeta).
    """
    incompat, motivo = _ipod_montado_incompatible()
    if incompat:
        print(_color("31", f"   ⚠️  Este iPod no soporta carga directa: {motivo}"))
        print("      Los MP3 están en Playlist/; impórtalos con Music.app para cargarlos.")
        return

    carpetas = [d for d in sorted(glob.glob(os.path.join(OUTPUT_DIR, "*")))
                if os.path.isdir(d) and glob.glob(os.path.join(d, "*.mp3"))]
    if not carpetas:
        print(_color("2", f"   No hay playlists grabadas en '{OUTPUT_DIR}'. Graba una primero (opción 3)."))
        return

    print(f"\n   {_color('1;36', 'Playlists grabadas localmente')}:")
    for i, d in enumerate(carpetas, 1):
        n = len(glob.glob(os.path.join(d, "*.mp3")))
        print(f"     {_color('1;32', str(i).rjust(2))}  {os.path.basename(d)}  {_color('2', f'({n} MP3)')}")

    sel = input(f"\n   Nº a cargar [1-{len(carpetas)}] (Enter = cancelar): ").strip()
    if not sel.isdigit() or not (1 <= int(sel) <= len(carpetas)):
        return
    carpeta = carpetas[int(sel) - 1]
    defecto = os.path.basename(carpeta)
    nombre = input(f"   Nombre de la playlist en el iPod [Enter = '{defecto}']: ").strip() or defecto
    subprocess.run([sys.executable, os.path.join(TOOLS_DIR, "ipod_sync.py"), nombre, "--dir", carpeta])


def menu_spotify():
    """ Lista las playlists de tu cuenta de Spotify y graba la(s) que elijas. """
    global sp, IPOD_MOUNT
    if not _asegurar_spotify():
        return

    try:
        playlists = []
        res = sp.current_user_playlists(limit=50)
        while res:
            playlists.extend(p for p in res["items"] if p)
            res = sp.next(res) if res.get("next") else None
    except Exception as e:
        log.error(f"❌ No se pudieron listar tus playlists: {e}")
        return

    if not playlists:
        log.warning("No se encontraron playlists en tu cuenta.")
        return

    print(f"\n   {_color('1;36', 'Tus playlists de Spotify')} ({len(playlists)}):")
    for i, pl in enumerate(playlists, 1):
        total = pl.get("tracks", {}).get("total", "?")
        due = "" if pl.get("public") else _color("2", " · privada")
        print(f"     {_color('1;32', str(i).rjust(3))}  {pl['name']}  {_color('2', f'({total} pistas)')}{due}")

    sel = input("\n   Elige nº (coma para varias), 'a' = todas, Enter = cancelar: ").strip().lower()
    if not sel:
        return
    if sel in ("a", "all", "todas"):
        elegidas = playlists
    else:
        idxs = [int(p) - 1 for p in sel.split(",") if p.strip().isdigit() and 1 <= int(p) <= len(playlists)]
        elegidas = [playlists[i] for i in idxs]
    if not elegidas:
        print("   Nada seleccionado.")
        return

    print("\n   Seleccionadas: " + ", ".join(pl["name"] for pl in elegidas))
    al_ipod = input("   ¿Cargar directo al iPod (sin Music.app)? [s/N]: ").strip().lower() == "s"
    IPOD_MOUNT = "auto" if al_ipod else None
    ejecutar_grabacion([pl["uri"] for pl in elegidas])


TOOLS_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "tools")

_MENU_SECCIONES = [
    ("SPOTIFY", [
        ("S", "Explorar y grabar mis playlists", "listar / elegir"),
        ("B", "Buscar en Spotify y grabar", "por nombre"),
    ]),
    ("GRABAR", [
        ("1", "Playlist / álbum / track", "→ Apple Music"),
        ("2", "Playlist / álbum / track", "→ iPod directo"),
        ("3", "Playlist / álbum / track", "→ solo local (sin sincronizar)"),
        ("4", "Actualizar un respaldo", "solo pistas nuevas"),
    ]),
    ("iPod", [
        ("5", "Cargar una playlist grabada al iPod", "local → iPod"),
        ("6", "Descargar una playlist del iPod", "iPod → local"),
        ("7", "Descargar TODAS las playlists del iPod", "iPod → local"),
        ("8", "Respaldar la base de datos", ""),
        ("9", "Restaurar la base de datos", "desde backup"),
        ("10", "Gestionar playlists", "listar / borrar"),
        ("11", "Respaldar la música del iPod", "→ Mac"),
        ("12", "Montar el iPod", ""),
        ("13", "Expulsar el iPod", ""),
    ]),
    ("GRABACIONES LOCALES", [
        ("14", "Exportar M3U", "reproducir en cualquier player"),
        ("15", "Verificar grabaciones", "detectar mudas"),
        ("16", "Gestionar grabaciones", "listar / borrar"),
    ]),
    ("UTILIDADES", [
        ("17", "Probar captura de audio", ""),
        ("18", "Configurar credenciales", ".env"),
        ("19", "Diagnóstico / info", ""),
    ]),
]


def _ipod_montado():
    for c in glob.glob("/Volumes/*/iPod_Control"):
        return os.path.dirname(c)
    return None


def _render_menu():
    ancho = 50
    barra = "─" * ancho
    print()
    print("  " + _color("36", "╭" + barra + "╮"))
    titulo, ver = "S P O T I P O D", f"v{VERSION}"
    hueco = ancho - len(titulo) - len(ver) - 2
    print("  " + _color("36", "│") + " " + _color("1", titulo)
          + " " * hueco + _color("2", ver) + " " + _color("36", "│"))
    print("  " + _color("36", "╰" + barra + "╯"))

    # Línea de estado en vivo
    mont = _ipod_montado()
    ipod = f"{_color('32', '●')} conectado" if mont else f"{_color('31', '○')} no conectado"
    cred_ok = bool(os.getenv("SPOTIFY_CLIENT_ID") and os.getenv("SPOTIFY_CLIENT_SECRET"))
    cred = f"{_color('32', '●')} listas" if cred_ok else f"{_color('31', '○')} faltan"
    print(f"   iPod {ipod}     Spotify {cred}")

    for seccion, items in _MENU_SECCIONES:
        print("\n   " + _color("1;36", seccion))
        for num, label, extra in items:
            sufijo = "  " + _color("2", extra) if extra else ""
            print(f"     {_color('1;32', num.rjust(2))}  {label}{sufijo}")
    print(f"\n     {_color('1;32', ' 0')}  Salir")


def _grabar_desde_menu(destino):
    """ destino: 'apple' (Music), 'ipod' (directo) o 'local' (solo guardar). """
    global IPOD_MOUNT, APPLE_MUSIC
    if destino == "ipod":
        incompat, motivo = _ipod_montado_incompatible()
        if incompat:
            print(_color("31", f"   ⚠️  El iPod conectado no soporta carga directa: {motivo}"))
            if input("   ¿Grabar igual y cargarlo luego con Music.app? [s/N]: ").strip().lower() != "s":
                return
            destino = "apple"  # graba y sincroniza vía Music.app

    entrada = input("URL/ID (playlist, álbum o track; Enter = playlist.txt): ").strip()
    if destino == "ipod":
        IPOD_MOUNT, APPLE_MUSIC = "auto", False
    elif destino == "local":
        IPOD_MOUNT, APPLE_MUSIC = None, False
    else:
        IPOD_MOUNT, APPLE_MUSIC = None, True
    ejecutar_grabacion([entrada] if entrada else [])


def menu_interactivo():
    """ Menú interactivo para elegir la acción sin recordar los flags. """
    while True:
        if sys.stdout.isatty():
            print("\033[2J\033[H", end="")  # limpiar pantalla para un menú limpio
        _render_menu()
        try:
            op = input(_color("1", "\n   ➤ Opción: ")).strip()
        except EOFError:
            break

        if op.lower() == "s":
            menu_spotify()
        elif op.lower() == "b":
            buscar_spotify()
        elif op == "1":
            _grabar_desde_menu("apple")
        elif op == "2":
            _grabar_desde_menu("ipod")
        elif op == "3":
            _grabar_desde_menu("local")
        elif op == "4":
            actualizar_respaldo()
        elif op == "5":
            cargar_grabada_al_ipod()
        elif op == "6":
            descargar_playlist_del_ipod()
        elif op == "7":
            descargar_todas_del_ipod()
        elif op == "8":
            dest = input("Carpeta destino (Enter = iPod_DB_Backup): ").strip() or "iPod_DB_Backup"
            respaldar_db_ipod(dest)
        elif op == "9":
            dest = input("Carpeta de backups (Enter = iPod_DB_Backup): ").strip() or "iPod_DB_Backup"
            restaurar_db_ipod(dest)
        elif op == "10":
            gestionar_playlists_ipod()
        elif op == "11":
            dest = input("Carpeta destino (Enter = iPod_Backup): ").strip() or "iPod_Backup"
            subprocess.run([sys.executable, os.path.join(TOOLS_DIR, "ipod_backup.py"), "--dest", dest])
        elif op == "12":
            montar_ipod()
        elif op == "13":
            expulsar_ipod()
        elif op == "14":
            exportar_m3u()
        elif op == "15":
            verificar_grabaciones()
        elif op == "16":
            gestionar_grabaciones_locales()
        elif op == "17":
            probar_captura()
        elif op == "18":
            configurar_credenciales()
        elif op == "19":
            diagnostico()
        elif op in ("0", "q", "salir", "exit"):
            print("\n   👋 Hasta luego.\n")
            break
        else:
            print(_color("31", f"   ⚠️  '{op}' no es una opción válida."))
            continue  # remuestra el menú sin pausa

        try:
            input(_color("2", "\n   ↩︎  Enter para volver al menú..."))
        except EOFError:
            break


def cli():
    """ Punto de entrada (usado por el comando de consola 'spotipod' y por __main__). """
    try:
        main()
    except KeyboardInterrupt:
        log.warning("\n⛔ Interrumpido por el usuario. Deteniendo reproducción…")
        detener_reproduccion()
        sys.exit(130)


if __name__ == "__main__":
    cli()
