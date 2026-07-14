#!/usr/bin/env python3
"""
Respalda la música de un iPod (modo disco: clásico / Nano / Shuffle) al Mac.

El iPod guarda los archivos en 'iPod_Control/Music/F00..F49/' con nombres codificados
(XXXX.mp3), pero conservan sus tags ID3. Esta herramienta los lee y reconstruye una
biblioteca ordenada como  <dest>/Artista/Álbum/NN Título.mp3.

Uso:
    python tools/ipod_backup.py                      # autodetecta el iPod, copia a ./iPod_Backup
    python tools/ipod_backup.py --dest ~/Music/ipod  # carpeta destino
    python tools/ipod_backup.py --dry-run            # muestra qué haría, sin copiar
    python tools/ipod_backup.py --source /Volumes/iPod -v
"""
import argparse
import glob
import os
import re
import shutil
import sys

from mutagen import File as MutagenFile

DESCONOCIDO_ARTISTA = "Artista Desconocido"
DESCONOCIDO_ALBUM = "Album Desconocido"


def sanitizar(nombre):
    """ Normaliza un nombre para usarlo como archivo/carpeta. """
    nombre = re.sub(r'[\/:*?"<>|]', '-', nombre)
    nombre = nombre.replace("\n", " ").replace("\t", " ").strip().strip(".")
    return nombre or "sin_nombre"


def autodetectar_ipod():
    """ Busca un iPod montado en modo disco en /Volumes. Devuelve la carpeta Music o None. """
    for control in glob.glob("/Volumes/*/iPod_Control/Music"):
        if os.path.isdir(control):
            return control
    return None


def leer_tags(ruta):
    """ Devuelve (artista, album, titulo, track) desde los tags del archivo, con fallbacks. """
    try:
        audio = MutagenFile(ruta, easy=True)
    except Exception:
        audio = None

    def primero(claves):
        if not audio:
            return None
        for k in claves:
            val = audio.get(k)
            if val:
                return str(val[0]).strip() or None
        return None

    artista = primero(["albumartist", "artist"]) or DESCONOCIDO_ARTISTA
    album = primero(["album"]) or DESCONOCIDO_ALBUM
    titulo = primero(["title"]) or os.path.splitext(os.path.basename(ruta))[0]

    track_raw = primero(["tracknumber"])
    track = None
    if track_raw:
        m = re.match(r"\s*(\d+)", track_raw)
        if m:
            track = int(m.group(1))

    return artista, album, titulo, track


def ruta_destino(base, artista, album, titulo, track):
    """ Construye la ruta destino Artista/Álbum/NN Título.mp3 (saneada). """
    carpeta = os.path.join(base, sanitizar(artista), sanitizar(album))
    if track:
        nombre = f"{track:02d} {sanitizar(titulo)}.mp3"
    else:
        nombre = f"{sanitizar(titulo)}.mp3"
    return carpeta, nombre


def destino_sin_colision(carpeta, nombre, tam_origen):
    """
    Devuelve una ruta libre en 'carpeta'. Si ya existe un archivo con el mismo nombre y
    tamaño, devuelve None (ya respaldado). Si existe con distinto tamaño, añade sufijo.
    """
    ruta = os.path.join(carpeta, nombre)
    if not os.path.exists(ruta):
        return ruta
    if os.path.getsize(ruta) == tam_origen:
        return None  # ya respaldado (mismo tamaño)

    base, ext = os.path.splitext(nombre)
    contador = 2
    while True:
        candidato = os.path.join(carpeta, f"{base} ({contador}){ext}")
        if not os.path.exists(candidato):
            return candidato
        if os.path.getsize(candidato) == tam_origen:
            return None
        contador += 1


def main():
    p = argparse.ArgumentParser(description="Respalda la música de un iPod (modo disco) al Mac.")
    p.add_argument("--source", help="Carpeta Music del iPod o raíz del volumen (autodetecta si se omite)")
    p.add_argument("--dest", default="iPod_Backup", help="Carpeta destino (por defecto: ./iPod_Backup)")
    p.add_argument("--dry-run", action="store_true", help="Muestra qué haría sin copiar nada")
    p.add_argument("-v", "--verbose", action="store_true", help="Muestra cada archivo")
    args = p.parse_args()

    # Resolver la carpeta de música del iPod
    source = args.source
    if source and os.path.isdir(os.path.join(source, "iPod_Control", "Music")):
        source = os.path.join(source, "iPod_Control", "Music")
    if not source:
        source = autodetectar_ipod()
    if not source or not os.path.isdir(source):
        print("❌ No se encontró un iPod montado en modo disco.")
        print("   Conéctalo, asegúrate de que aparezca en /Volumes, o pásalo con --source /Volumes/iPod")
        sys.exit(1)

    print(f"📀 Origen: {source}")
    print(f"💾 Destino: {os.path.abspath(args.dest)}")
    if args.dry_run:
        print("🧪 Modo dry-run: no se copiará nada.")

    archivos = sorted(glob.glob(os.path.join(source, "**", "*.mp3"), recursive=True))
    archivos += sorted(glob.glob(os.path.join(source, "**", "*.MP3"), recursive=True))
    total = len(archivos)
    if not total:
        print("❌ No se encontraron archivos MP3 en el iPod.")
        sys.exit(1)

    print(f"🎵 {total} pistas encontradas.\n")

    copiados = omitidos = errores = 0
    for i, origen in enumerate(archivos, start=1):
        try:
            artista, album, titulo, track = leer_tags(origen)
            carpeta, nombre = ruta_destino(args.dest, artista, album, titulo, track)
            tam = os.path.getsize(origen)

            if not args.dry_run:
                os.makedirs(carpeta, exist_ok=True)
            destino = destino_sin_colision(carpeta, nombre, tam)

            if destino is None:
                omitidos += 1
                if args.verbose:
                    print(f"[{i}/{total}] ⏭ Ya existe: {artista} - {titulo}")
            else:
                if args.verbose or args.dry_run:
                    print(f"[{i}/{total}] ⬇️  {artista} - {titulo}  →  {os.path.relpath(destino, args.dest)}")
                if not args.dry_run:
                    shutil.copy2(origen, destino)
                copiados += 1

            if not args.verbose and not args.dry_run and sys.stdout.isatty():
                sys.stdout.write(f"\r   [{i}/{total}] copiados: {copiados}  omitidos: {omitidos}  errores: {errores}")
                sys.stdout.flush()

        except Exception as e:
            errores += 1
            print(f"\n[{i}/{total}] ❌ Error con {origen}: {e}")

    if not args.verbose and not args.dry_run and sys.stdout.isatty():
        sys.stdout.write("\n")

    print("\n──────────────────────────────")
    print(f"✅ Copiados: {copiados}")
    print(f"⏭ Omitidos (ya existían): {omitidos}")
    if errores:
        print(f"❌ Errores: {errores}")
    print(f"💾 Biblioteca respaldada en: {os.path.abspath(args.dest)}")


if __name__ == "__main__":
    main()
