import os
import json
import requests
import yt_dlp
import spotipy
from spotipy.oauth2 import SpotifyOAuth
from mutagen.mp3 import MP3
from mutagen.id3 import ID3, APIC, TIT2, TPE1, TALB

# Configuración de credenciales Spotify
SPOTIFY_CLIENT_ID = os.getenv("SPOTIFY_CLIENT_ID")
SPOTIFY_CLIENT_SECRET = os.getenv("SPOTIFY_CLIENT_SECRET")
SPOTIFY_REDIRECT_URI = "http://localhost:8080"

# Verificar credenciales
if not SPOTIFY_CLIENT_ID or not SPOTIFY_CLIENT_SECRET:
    print("❌ ERROR: Configura las credenciales de Spotify.")
    exit(1)

# Autenticación con Spotify
sp = spotipy.Spotify(auth_manager=SpotifyOAuth(client_id=SPOTIFY_CLIENT_ID,
                                               client_secret=SPOTIFY_CLIENT_SECRET,
                                               redirect_uri=SPOTIFY_REDIRECT_URI,
                                               scope="playlist-read-private"))

OUTPUT_DIR = "PlaylistYoutube"
os.makedirs(OUTPUT_DIR, exist_ok=True)

# 📡 Obtener canciones de la playlist
def obtener_canciones(playlist_id):
    try:
        playlist_info = sp.playlist(playlist_id)
        nombre_playlist = playlist_info["name"]
        canciones = []

        for t in sp.playlist_tracks(playlist_id)["items"]:
            track = t["track"]
            nombre = track["name"]
            artista = track["artists"][0]["name"]
            album = track["album"]["name"]
            portada_url = track["album"]["images"][0]["url"] if track["album"]["images"] else None

            canciones.append({
                "nombre": nombre,
                "artista": artista,
                "album": album,
                "portada_url": portada_url
            })

        return nombre_playlist, canciones
    except Exception as e:
        print(f"❌ ERROR al obtener la playlist: {e}")
        return None, []

# 🎵 Descargar una canción desde YouTube
def descargar_cancion(nombre, artista, output_folder):
    os.makedirs(output_folder, exist_ok=True)
    query = f"{artista} - {nombre} audio"

    opciones = {
        'format': 'bestaudio/best',
        'outtmpl': f"{output_folder}/%(title)s.%(ext)s",
        'postprocessors': [{'key': 'FFmpegExtractAudio', 'preferredcodec': 'mp3', 'preferredquality': '192'}],
        'noplaylist': True,
        'default_search': 'ytsearch',
        'quiet': False,
    }

    with yt_dlp.YoutubeDL(opciones) as ydl:
        ydl.download([query])

# 🖼️ Descargar la portada
def descargar_portada(url, output_folder, nombre):
    if not url:
        return None

    os.makedirs(output_folder, exist_ok=True)
    ruta_destino = os.path.join(output_folder, f"{nombre}.jpg")

    try:
        response = requests.get(url, stream=True, timeout=10)
        if response.status_code == 200:
            with open(ruta_destino, 'wb') as file:
                for chunk in response.iter_content(1024):
                    file.write(chunk)
            return ruta_destino
    except requests.RequestException:
        return None
    return None

# 🎵 Añadir metadatos y portada
def incrustar_metadatos(archivo_mp3, archivo_jpg, titulo, artista, album):
    try:
        audio = MP3(archivo_mp3, ID3=ID3)
        if not audio.tags:
            audio.tags = ID3()

        if archivo_jpg:
            with open(archivo_jpg, "rb") as img:
                audio.tags.add(APIC(encoding=3, mime="image/jpeg", type=3, desc="Cover", data=img.read()))

        audio.tags.add(TIT2(encoding=3, text=titulo))
        audio.tags.add(TPE1(encoding=3, text=artista))
        audio.tags.add(TALB(encoding=3, text=album))

        audio.save()
    except Exception as e:
        print(f"❌ ERROR al incrustar metadatos en {archivo_mp3}: {e}")

# 🔥 Descargar toda la playlist
def descargar_playlist(playlist_id):
    nombre_playlist, canciones = obtener_canciones(playlist_id)
    if not canciones:
        print("❌ No hay canciones para descargar.")
        return

    carpeta_playlist = os.path.join(OUTPUT_DIR, nombre_playlist)
    os.makedirs(carpeta_playlist, exist_ok=True)

    for cancion in canciones:
        nombre = cancion["nombre"]
        artista = cancion["artista"]
        album = cancion["album"]
        portada_url = cancion["portada_url"]

        archivo_mp3 = os.path.join(carpeta_playlist, f"{artista} - {nombre}.mp3")
        if os.path.exists(archivo_mp3):
            print(f"✅ '{archivo_mp3}' ya existe. Saltando...")
            continue

        print(f"🎶 Descargando: {artista} - {nombre}")
        descargar_cancion(nombre, artista, carpeta_playlist)

        # Buscar el archivo descargado
        for archivo in os.listdir(carpeta_playlist):
            if archivo.endswith(".mp3"):
                ruta_mp3 = os.path.join(carpeta_playlist, archivo)
                archivo_jpg = descargar_portada(portada_url, carpeta_playlist, nombre)
                incrustar_metadatos(ruta_mp3, archivo_jpg, nombre, artista, album)
                break

# 🏁 Ejecutar
playlist_id = input("🎵 Ingresa el ID de la playlist de Spotify: ")
descargar_playlist(playlist_id)
