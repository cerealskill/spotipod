import os
from mutagen.id3 import ID3, COMM, ID3NoHeaderError

# Configuración
folder_path = "Playlist/Fortnite"  # Reemplázalo con la ruta de tu carpeta
comment_value = "Fortnite"  # Ajusta el comentario

def actualizar_comentario_mp3(folder, comment_value):
    """
    Recorre todos los archivos MP3 en la carpeta dada y actualiza el tag 'comment' (COMM).
    Si el archivo no tiene metadatos ID3, lo omite.
    """
    for file in os.listdir(folder):
        if file.lower().endswith(".mp3"):
            file_path = os.path.join(folder, file)
            try:
                # Cargar etiquetas ID3
                try:
                    audio = ID3(file_path)
                except ID3NoHeaderError:
                    print("⚠ El archivo '{}' no tiene metadatos ID3, se omitirá.".format(file))
                    continue  # Omitimos archivos sin etiquetas.

                # Eliminar comentarios previos (evita duplicados)
                audio.delall("COMM")

                # Agregar el nuevo comentario en inglés (estándar)
                audio.add(COMM(encoding=3, lang="eng", desc="", text=comment_value))
                
                # Guardar cambios
                audio.save()
                print("✔ Comentario actualizado en: {}".format(file))

            except Exception as e:
                print("❌ Error con '{}': {}".format(file, e))

# Ejecutar la función
if __name__ == "__main__":
    if os.path.exists(folder_path):
        actualizar_comentario_mp3(folder_path, comment_value)
    else:
        print("❌ Error: La carpeta especificada no existe.")