import sys
import json
import os
import spotipy
from spotipy.oauth2 import SpotifyOAuth

# 🔑 CONFIGURACIÓN DE SPOTIFY API
SPOTIFY_CLIENT_ID = "ce811f0e562947a99fb2c6207965014b"
SPOTIFY_CLIENT_SECRET = "42be3eb277e64b0fa4eeaacf2e2f0887"
SPOTIFY_REDIRECT_URI = "http://localhost:8080"

scope = "playlist-read-private"
sp = spotipy.Spotify(auth_manager=SpotifyOAuth(client_id=SPOTIFY_CLIENT_ID,
                                               client_secret=SPOTIFY_CLIENT_SECRET,
                                               redirect_uri=SPOTIFY_REDIRECT_URI,
                                               scope=scope))

# 📂 Obtener el nombre de la playlist
def obtener_nombre_playlist(playlist_id):
    try:
        playlist_info = sp.playlist(playlist_id)
        nombre_playlist = playlist_info["name"].replace("/", "-").replace("\\", "-")
        return nombre_playlist
    except Exception as e:
        print(f"❌ ERROR: No se pudo obtener la playlist. Verifica el ID. {e}")
        exit()

# 🎶 Obtener canciones de una playlist
def obtener_canciones_playlist(playlist_id):
    canciones = []
    try:
        resultados = sp.playlist_tracks(playlist_id)
        for item in resultados["items"]:
            track = item["track"]
            if track:
                cancion_info = {
                    "nombre": track["name"],
                    "artista": [artist["name"] for artist in track["artists"]],
                    "album": track["album"]["name"],
                    "duracion_ms": track["duration_ms"],
                    "url_spotify": track["external_urls"]["spotify"]
                }
                canciones.append(cancion_info)
    except Exception as e:
        print(f"❌ ERROR: No se pudo obtener la playlist. {e}")
        exit()

    return canciones

# 📥 Guardar la playlist en un archivo JSON dentro de una estructura de carpetas
def guardar_playlist_json(playlist_id):
    nombre_playlist = obtener_nombre_playlist(playlist_id)
    canciones = obtener_canciones_playlist(playlist_id)
    
    if not canciones:
        print("❌ No se encontraron canciones en la playlist. Verifica el ID.")
        exit()

    datos_playlist = {
        "playlist_id": playlist_id,
        "nombre": nombre_playlist,
        "total_canciones": len(canciones),
        "canciones": canciones
    }

    # 📂 Crear estructura de carpetas: playlist/NOMBRE_PLAYLIST/
    carpeta_playlist = os.path.join("playlist", nombre_playlist)
    os.makedirs(carpeta_playlist, exist_ok=True)

    # 📄 Ruta del archivo JSON
    archivo_json = os.path.join(carpeta_playlist, "playlist.json")

    # Guardar como JSON
    with open(archivo_json, "w", encoding="utf-8") as archivo:
        json.dump(datos_playlist, archivo, indent=4, ensure_ascii=False)

    print(f"✅ Playlist '{nombre_playlist}' guardada en {archivo_json}")

# 🏁 Obtener ID de la playlist desde la terminal
if len(sys.argv) > 1:
    PLAYLIST_ID = sys.argv[1]
else:
    PLAYLIST_ID = input("🔹 Ingresa el ID de la playlist de Spotify: ")

guardar_playlist_json(PLAYLIST_ID)
