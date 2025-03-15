import yt_dlp
import os

# Archivo de texto con la lista de canciones
archivo_canciones = "canciones.txt"

# Carpeta de descargas
output_folder = "mp3_songs"
os.makedirs(output_folder, exist_ok=True)

# Función para descargar una canción en MP3
def descargar_mp3(nombre_cancion):
    opciones = {
        'format': 'bestaudio/best',
        'outtmpl': f"{output_folder}/%(title)s.%(ext)s",
        'postprocessors': [{
            'key': 'FFmpegExtractAudio',
            'preferredcodec': 'mp3',
            'preferredquality': '192',
        }],
        'noplaylist': True,
        'default_search': 'ytsearch',
        'quiet': False,
    }

    with yt_dlp.YoutubeDL(opciones) as ydl:
        ydl.download([nombre_cancion])

# Leer el archivo de canciones y procesarlas
with open(archivo_canciones, "r", encoding="utf-8") as file:
    canciones = [line.strip() for line in file if line.strip()]

for cancion in canciones:
    print(f"Descargando: {cancion}...")
    descargar_mp3(cancion)

print("Descargas completadas.")
