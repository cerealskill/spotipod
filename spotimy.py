import sys
import time
import os
import json
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
from spotipy.oauth2 import SpotifyOAuth
from mutagen.mp3 import MP3
from mutagen.id3 import ID3, APIC, TIT2, TPE1, TALB, COMM
from datetime import datetime

# Configuración de credenciales Spotify API desde variables de entorno
#SPOTIFY_CLIENT_ID = ""
#SPOTIFY_CLIENT_SECRET = ""

SPOTIFY_CLIENT_ID = os.getenv("SPOTIFY_CLIENT_ID")
SPOTIFY_CLIENT_SECRET = os.getenv("SPOTIFY_CLIENT_SECRET")
SPOTIFY_REDIRECT_URI = "http://localhost:8080"

print(f"----------------------------- [START SPOTI MY] -------------------------------------")

# Logo en ASCII
print("""

███████╗██████╗  ██████╗ ████████╗██╗    ███╗   ███╗██╗   ██╗
██╔════╝██╔══██╗██╔═══██╗╚══██╔══╝██║    ████╗ ████║╚██╗ ██╔╝
███████╗██████╔╝██║   ██║   ██║   ██║    ██╔████╔██║ ╚████╔╝ 
╚════██║██╔═══╝ ██║   ██║   ██║   ██║    ██║╚██╔╝██║  ╚██╔╝  
███████║██║     ╚██████╔╝   ██║   ██║    ██║ ╚═╝ ██║   ██║   
╚══════╝╚═╝      ╚═════╝    ╚═╝   ╚═╝    ╚═╝     ╚═╝   ╚═╝   
                                                             
Spotify Playlist Recorder v0.0.3 - Compatible con macOS, Windows y Linux
""")

print("⚠️ AVISO LEGAL: Este software es solo para uso personal. No redistribuir ni compartir grabaciones.")
print(f"------------------------------------------------------------------------------------")

if not SPOTIFY_CLIENT_ID or not SPOTIFY_CLIENT_SECRET:
    print("❌ ERROR: Las credenciales de Spotify no están configuradas. Configura las variables de entorno y vuelve a intentarlo.")
    print(" $ export SPOTIFY_CLIENT_ID='tu_cliente_id' ")
    print(" $ export SPOTIFY_CLIENT_SECRET='tu_secreto' ")
    sys.exit(1)

try:
    scope = "user-read-playback-state user-modify-playback-state playlist-read-private"
    sp = spotipy.Spotify(auth_manager=SpotifyOAuth(client_id=SPOTIFY_CLIENT_ID,
                                                   client_secret=SPOTIFY_CLIENT_SECRET,
                                                   redirect_uri=SPOTIFY_REDIRECT_URI,
                                                   scope=scope))
except Exception as e:
    print(f"❌ ERROR al conectar con Spotify: {e}")
    sys.exit(1)

OUTPUT_DIR = "Playlist"
RECOVERY_TRACKS = "recovery_tracks"

os.makedirs(OUTPUT_DIR, exist_ok=True)

SAMPLE_RATE = 44100
CHANNELS = 2

def obtener_dispositivo_virtual():
    try:
        dispositivos = sd.query_devices()
        for i, dispositivo in enumerate(dispositivos):
            if ("BlackHole" in dispositivo["name"] or "CABLE Input" in dispositivo["name"] or "Loopback" in dispositivo["name"]) and dispositivo["max_input_channels"] > 0:
                print(f"✅ Usando dispositivo virtual en {i}: {dispositivo['name']}")
                return i
    except Exception as e:
        print(f"❌ ERROR al buscar dispositivo de audio virtual: {e}")
    print("❌ No se encontró un dispositivo de grabación virtual compatible. Verifica la configuración de sonido.")
    print("ℹ️ Para instalar uno, sigue estos pasos:")
    print("  - macOS: Instala BlackHole desde https://existential.audio/blackhole/")
    print("  - Windows: Instala VB-Audio Virtual Cable desde https://vb-audio.com/Cable/")
    print("  - Linux: Configura Loopback con PulseAudio o JACK.")
    sys.exit(1)

try:
    DISPOSITIVO_ID = obtener_dispositivo_virtual()
except RuntimeError as e:
    print(e)
    sys.exit(1)

# 📥 Guardar la playlist en un archivo JSON
def guardar_playlist_json(playlist_id, nombre_playlist, canciones):
    datos_playlist = {
        "playlist_id": playlist_id,
        "nombre": nombre_playlist,
        "total_canciones": len(canciones),
        "canciones": canciones
    }

    archivo_json = os.path.join(OUTPUT_DIR, nombre_playlist, nombre_playlist+".json")

    with open(archivo_json, "w", encoding="utf-8") as archivo:
        json.dump(datos_playlist, archivo, indent=4, ensure_ascii=False)

    print(f"💾 Playlist '{nombre_playlist}' respaldada en {archivo_json}")

# 📡 Obtener dispositivo activo en Spotify
def obtener_dispositivo_activo():
    try:
        devices = sp.devices()
        return devices["devices"][0]["id"] if devices["devices"] else None
    except Exception as e:
        print(f"❌ ERROR al obtener dispositivos activos de Spotify: {e}")
        return None


def limpiar_nombre_archivo(nombre):
    """ Reemplaza caracteres inválidos y normaliza el texto para comparación de archivos. """
    return "".join(c if c.isalnum() or c in " _-" else "_" for c in nombre).strip().lower()

def existe_cancion(nombre_cancion, output_folder):
    """ 
    Busca si una canción con un nombre similar ya existe en la carpeta.
    Se compara el término de búsqueda y el título real de la canción.
    """
    nombre_cancion_limpio = limpiar_nombre_archivo(nombre_cancion)
    
    archivos_en_carpeta = glob.glob(os.path.join(output_folder, "*.mp3"))  # Busca solo MP3

    for archivo in archivos_en_carpeta:
        nombre_archivo_limpio = limpiar_nombre_archivo(os.path.basename(archivo).replace(".mp3", ""))

        # Si el nombre buscado está contenido en el archivo existente
        if nombre_cancion_limpio in nombre_archivo_limpio:
            return True, archivo  # Retorna True y el archivo encontrado

    return False, None

def descargar_mp3(nombre_cancion, output_folder):
    """ Descarga una canción de YouTube en formato MP3 si aún no existe en la carpeta de destino. """

    # Crear carpeta si no existe
    os.makedirs(output_folder, exist_ok=True)

    # Verificar si la canción ya existe (búsqueda por coincidencia parcial)
    existe, archivo_existente = existe_cancion(nombre_cancion, output_folder)
    if existe:
        print(f"✅ La canción ya existe en la carpeta: '{archivo_existente}'. No se descarga nuevamente.")
        return

    opciones_info = {
        'format': 'bestaudio/best',
        'default_search': 'ytsearch',
        'noplaylist': True,
        'quiet': True,
        'skip_download': True,  # Solo obtiene información, no descarga
    }

    try:
        with yt_dlp.YoutubeDL(opciones_info) as ydl:
            info = ydl.extract_info(nombre_cancion, download=False)
            
            if 'title' in info:
                titulo_real = limpiar_nombre_archivo(info['title'])
                archivo_mp3 = os.path.join(output_folder, f"{titulo_real}.mp3")

                # Segunda verificación con el título real obtenido de YouTube
                existe, archivo_existente = existe_cancion(titulo_real, output_folder)
                if existe:
                    print(f"✅ La canción '{titulo_real}' ya ha sido descargada como '{archivo_existente}'.")
                    return
                
                print(f"🎵 Descargando: {titulo_real}...")

                # Configuración para la descarga con un nombre de archivo forzado
                opciones_descarga = {
                    'format': 'bestaudio/best',
                    'outtmpl': os.path.join(output_folder, f"{titulo_real}.%(ext)s"),  # Se fuerza el nombre
                    'postprocessors': [{
                        'key': 'FFmpegExtractAudio',
                        'preferredcodec': 'mp3',
                        'preferredquality': '192',
                    }],
                    'noplaylist': True,
                    'default_search': 'ytsearch',
                    'quiet': False,
                }

                with yt_dlp.YoutubeDL(opciones_descarga) as ydl_descarga:
                    ydl_descarga.download([nombre_cancion])

                print(f"✅ Descarga completa: {archivo_mp3}")

            else:
                print("⚠️ No se encontró información de la canción.")

    except Exception as e:
        print(f"❌ Error al descargar la canción: {e}")




# 🎙️ Grabar audio con BlackHole
def grabar_audio(archivo_wav, duracion, nombre, artista):
    if duracion <= 0:
        print("❌ ERROR: Duración de grabación inválida.")
        return None
    try:
        
        # 📂 Asegurar que la carpeta donde se guardará el archivo existe
        os.makedirs(os.path.dirname(archivo_wav), exist_ok=True)

        # Verificar si hay reproducción en Spotify antes de empezar
        estado = sp.current_playback()
        if not estado or not estado["is_playing"]:
            
            print("❌ No hay reproducción en Spotify. No se grabará nada.") 
            
            # Obtener la fecha y hora actual
            timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            nombre_cancion = f"{artista} - {nombre}"
            
            # Guardar en archivo debug.txt
            with open("debug.txt", "a", encoding="utf-8") as debug_file:
                debug_file.write(f"[{timestamp}] Pista no encontrada en spotify: {artista} - {nombre}\n")
            
            # Buscar el track en youtube para poder ser descargado
            descargar_mp3(nombre_cancion, RECOVERY_TRACKS)

            return None
        
        print(f"🎙 Iniciando grabación por {duracion} segundos: {archivo_wav}")
        
        grabacion = sd.rec(int(SAMPLE_RATE * duracion), samplerate=SAMPLE_RATE, channels=CHANNELS, dtype="int16", device=DISPOSITIVO_ID)
        
        time.sleep(duracion)
        
        sd.stop()
        
        with wave.open(archivo_wav, "wb") as wf:
            wf.setnchannels(CHANNELS)
            wf.setsampwidth(2)
            wf.setframerate(SAMPLE_RATE)
            wf.writeframes(grabacion.tobytes())
        
        print(f"✅ Grabación guardada en: {archivo_wav}")
        return archivo_wav
    
    except Exception as e:
        print(f"❌ ERROR en la grabación de audio: {e}")
        return None


# 🔄 Convertir WAV a MP3 con portada
def convertir_a_mp3(archivo_wav, archivo_jpg, titulo, artista, album, nombre_playlist):
    if not os.path.exists(archivo_wav):
        print("❌ ERROR: No se encontró el archivo WAV para convertir.")
        return None

    archivo_mp3 = archivo_wav.replace(".wav", ".mp3")

    try:
        audio = AudioSegment.from_wav(archivo_wav)
        audio.export(archivo_mp3, format="mp3", bitrate="320k")
        os.remove(archivo_wav)
        print(f"🎵 Convertido a MP3: {archivo_mp3}")
        
        if os.path.exists(archivo_jpg):
            incrustar_portada_mp3(archivo_mp3, archivo_jpg, titulo, artista, album, nombre_playlist)
        else:
            print("⚠️ No se encontró la portada, MP3 sin imagen.")

        print(f"----------------------------- [Next Track] -----------------------------------------")

        return archivo_mp3
    except Exception as e:
        print(f"❌ ERROR al convertir a MP3: {e}")
        return None

# 🖼️ Descargar la portada en 'playlist/NOMBRE_PLAYLIST/cover/'
def descargar_portada(url, nombre_playlist, artista, titulo):
    if not url:
        print("⚠️ Advertencia: No se proporcionó una URL de portada.")
        return None
    carpeta_cover = os.path.join(OUTPUT_DIR, nombre_playlist.replace("/", "-").replace(":", "-"), "cover")
    os.makedirs(carpeta_cover, exist_ok=True)

    # Sanitizar el nombre del archivo
    nombre_archivo = f"{artista} - {titulo}.jpg"
    nombre_archivo = nombre_archivo.translate(str.maketrans({
        "/": "-", "\\": "-", ":": "-", "?": "", "|": "", "*": "",
        "\"": "", "<": "", ">": "", "\n": "", "\t": ""
    }))
    ruta_destino = os.path.join(carpeta_cover, nombre_archivo)
    
    try:
        response = requests.get(url, stream=True, timeout=10)
        if response.status_code == 200:
            with open(ruta_destino, 'wb') as file:
                for chunk in response.iter_content(1024):
                    file.write(chunk)
            print(f"🖼️ Portada guardada en: {ruta_destino}")
            return ruta_destino
        else:
            print(f"❌ ERROR al descargar la portada: Código {response.status_code}")
    except requests.RequestException as e:
        print(f"❌ ERROR al descargar la portada: {e}")
    return None


# 🎵 Incrustar portada en MP3
def incrustar_portada_mp3(archivo_mp3, archivo_jpg, titulo, artista, album, nombre_playlist):
    try:
        audio = MP3(archivo_mp3, ID3=ID3)

        if not audio.tags:
            audio.tags = ID3()

        with open(archivo_jpg, "rb") as img:
            audio.tags.add(APIC(encoding=3, mime="image/jpeg", type=3, desc="Cover", data=img.read()))
        
        audio.tags.add(TIT2(encoding=3, text=titulo))
        audio.tags.add(TPE1(encoding=3, text=artista))
        audio.tags.add(TALB(encoding=3, text=album))
        # Se agrega el comentario con el nombre de la playlist para luego en Music poder filtrar y crear la playlist para
        # Cargar en el IPOD
        audio.tags.add(COMM(encoding=3, lang="eng", desc="", text=nombre_playlist))

        audio.save()
        print(f"✅ Portada incrustada en {archivo_mp3}")

    except Exception as e:
        print(f"❌ ERROR al incrustar la portada en {archivo_mp3}: {e}")

# 📂 Organizar archivos por playlist
def organizar_archivo(nombre_playlist, artista, nombre):
    carpeta_playlist = os.path.join(OUTPUT_DIR, re.sub(r'[\/:*?"<>|]', '-', nombre_playlist))
    os.makedirs(carpeta_playlist, exist_ok=True)
    nombre_archivo = f"{artista} - {nombre}.wav"
    nombre_archivo = re.sub(r'[\/:*?"<>|]', '-', nombre_archivo)

    ruta_final = os.path.join(carpeta_playlist, nombre_archivo)

    return ruta_final

# Agrega el mp3 generado a la libreria de Music en MACOSX
def create_playlist(playlist_name):
    """
    Crea una playlist en Music si no existe.
    """
    script = f'''
    tell application "Music"
        -- Verificar si la playlist ya existe
        set playlistExists to false
        repeat with p in playlists
            if name of p is "{playlist_name}" then
                set playlistExists to true
                exit repeat
            end if
        end repeat

        -- Si no existe, crearla
        if playlistExists is false then
            make new playlist with properties {{name:"{playlist_name}"}}
        end if
    end tell
    '''
    subprocess.run(["osascript", "-e", script])


def delete_playlist(playlist_name):
    """
    Elimina una playlist en Music si existe.
    """
    script = f'''
    tell application "Music"
        -- Verificar si la playlist existe
        set playlistExists to false
        repeat with p in playlists
            if name of p is "{playlist_name}" then
                set playlistExists to true
                exit repeat
            end if
        end repeat

        -- Si la playlist existe, eliminarla
        if playlistExists is true then
            delete playlist "{playlist_name}"
        else
            log "⚠️ La playlist '{playlist_name}' no existe."
        end if
    end tell
    '''
    subprocess.run(["osascript", "-e", script])

def stop_music():
    """
    Detiene la reproducción de Music para evitar que cada MP3 importado se reproduzca automáticamente.
    """
    script = '''
    tell application "Music"
        pause
    end tell
    '''
    subprocess.run(["osascript", "-e", script])

def copy_tracks_with_comment(playlist_name):
    """
    Copia canciones con un comentario específico a la playlist sin duplicarlas.
    """
    create_playlist(playlist_name)  # Asegurar que la playlist exista

    script = f'''
    tell application "Music"
        set targetPlaylist to playlist "{playlist_name}"
        
        -- Obtener los nombres y artistas de las canciones ya en la playlist
        set existingTracks to {{}}
        repeat with t in tracks of targetPlaylist
            set trackName to name of t
            set trackArtist to artist of t
            set existingTracks to existingTracks & {{trackName & " - " & trackArtist}}
        end repeat

        -- Buscar canciones en la biblioteca con el comentario correspondiente
        repeat with t in tracks of library playlist 1
            try
                if comment of t is not missing value and comment of t contains "{playlist_name}" then
                    set trackName to name of t
                    set trackArtist to artist of t
                    set trackKey to trackName & " - " & trackArtist
                    
                    -- Agregar solo si no está en la playlist
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
    """
    Abre todos los archivos MP3 de un directorio en Music para agregarlos a la biblioteca,
    los coloca en una playlist (creándola si no existe) y detiene la reproducción después de cada archivo.
    """
    # Obtener la lista de archivos MP3 en el directorio
    mp3_files = [os.path.join(directory_path, f) for f in os.listdir(directory_path) if f.endswith(".mp3")]

    if not mp3_files:
        print("❌ No se encontraron archivos MP3 en el directorio.")
        return

    create_playlist(playlist_name)
    # Agregar cada MP3 a la biblioteca y moverlo a la playlist
    for mp3_path in mp3_files:
        # Extraer solo el nombre del archivo sin la ruta
        mp3_filename = os.path.basename(mp3_path)

        script = f'''
        tell application "Music"
            try
                -- Abrir el archivo en Music (esto lo agrega a la biblioteca)
                open POSIX file "{mp3_path}"
            on error errorMessage
                log "⚠️ Error con {mp3_path}: " & errorMessage
            end try
            pause
        end tell
        '''
        subprocess.run(["osascript", "-e", script])
    
    stop_music()
    copy_tracks_with_comment(playlist_name)
    print("🎵 Playlist cargada en Apple Music! 🟢 lista para ser usada en tu iPod")
    #sync_ipod()

# 🔥 Grabar una playlist completa
def grabar_playlist(playlist_id):
    try:
        nombre_playlist = sp.playlist(playlist_id)["name"]
        canciones = [(t["track"]["name"], t["track"]["artists"][0]["name"], t["track"]["album"]["name"], t["track"]["album"]["images"][0]["url"], t["track"]["uri"], t["track"]["duration_ms"] / 1000) for t in sp.playlist_tracks(playlist_id)["items"]]
    except Exception as e:
        print(f"❌ ERROR al obtener playlist de Spotify: {e}")
        return
    
    if not canciones:
        print(f"❌ No hay canciones en la playlist '{nombre_playlist}'")
        return

    print(f"🎶 {len(canciones)} canciones en la playlist: {nombre_playlist}")
    print(f"----------------------------- [Download Playlist] ----------------------------------")
    device_id = obtener_dispositivo_activo()
    if not device_id:
        print("❌ No hay dispositivos Spotify activos.")
        return

    for nombre, artista, album, portada_url, uri, duracion in canciones:
        
        nombre = nombre.translate(str.maketrans({
            "/": "-", "\\": "-", ":": "-", "?": "", "|": "", "*": "",
            "\"": "", "<": "", ">": "", "\n": "", "\t": ""
        }))
        
        artista = artista.translate(str.maketrans({
            "/": "-", "\\": "-", ":": "-", "?": "", "|": "", "*": "",
            "\"": "", "<": "", ">": "", "\n": "", "\t": ""
        }))

        archivo_wav = os.path.join(OUTPUT_DIR, nombre_playlist, f"{artista} - {nombre}.wav")
        
        archivo_mp3 = archivo_wav.replace(".wav", ".mp3")
               
        if os.path.exists(archivo_mp3):
            print(f"⏭ '{archivo_mp3}' ya existe. Saltando...")
            continue
        
        sp.start_playback(device_id=device_id, uris=[uri])
        
        time.sleep(2)
        
        archivo_wav = grabar_audio(archivo_wav, duracion, nombre, artista)
        
        if archivo_wav:
            archivo_jpg = descargar_portada(portada_url, nombre_playlist, artista, nombre)
            convertir_a_mp3(archivo_wav, archivo_jpg, nombre, artista, album, nombre_playlist)

    # respaldamos la playlist en un archivo JSON
    guardar_playlist_json(playlist_id, nombre_playlist, canciones)

    # Sincronizamos la playlist con Apple Music (IPOD)
    current_directory = os.getcwd()
    directory_path = current_directory+"/"+OUTPUT_DIR+"/"+nombre_playlist
    
    #print("📂 Directorio Playlist:", directory_path)
    add_playlist_to_apple_music(directory_path, nombre_playlist)

# 🏁 Leer playlists desde archivo o argumento
def leer_playlists():
    return [sys.argv[1]] if len(sys.argv) > 1 else open("playlist.txt").read().strip().split("\n")

# 🏁 Ejecutar el script
for playlist_id in leer_playlists():
    grabar_playlist(playlist_id)

print("✅ Todas las playlists han sido procesadas.")
print(f"----------------------------- [STOP SPOTI MY] -------------------------------------")