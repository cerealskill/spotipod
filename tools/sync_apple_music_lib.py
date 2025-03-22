import subprocess
import os

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


def add_directory_to_playlist(directory_path, playlist_name):
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
    #sync_ipod()


# Parámetros
directory_path = "/Users/bashs/Developer/spotimy/Playlist/2025"  # Reemplaza con la ruta real
playlist_name = "2025"  # Cambia el nombre de la playlist

# Agregar MP3 del directorio a la biblioteca y moverlos a la playlist
add_directory_to_playlist(directory_path, playlist_name)
