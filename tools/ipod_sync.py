#!/usr/bin/env python3
"""
Carga MP3s directamente a un iPod clásico (modo disco) SIN pasar por Music.app:
copia los archivos a iPod_Control/Music/F## y añade una playlist a la base de datos
iTunesDB (con backup previo).

Uso directo:
    python tools/ipod_sync.py "Mi Playlist" cancion1.mp3 cancion2.mp3
    python tools/ipod_sync.py --dir Playlist/MiPlaylist "Mi Playlist"
    python tools/ipod_sync.py --ipod /Volumes/iPod --dry-run "Test" *.mp3

Solo funciona en iPods cuya base de datos NO requiere firma: los click-wheel antiguos
(iPod 1G-5.5G/Video, Photo, Mini, Nano 1G-2G). Los que sí la requieren —iPod Classic
(6G/7G), Nano 3G-5G (hash58) y Nano 6G+/Touch (hashAB)— deben usar Music.app.
"""
import argparse
import glob
import os
import random
import re
import shutil
import string
import sys
from datetime import datetime

from mutagen.mp3 import MP3
from mutagen.id3 import ID3

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import itunesdb as db


def detectar_ipod(mount=None):
    """ Devuelve la ruta raíz del iPod (que contiene iPod_Control). """
    if mount:
        if os.path.isdir(os.path.join(mount, "iPod_Control")):
            return mount
        return None
    for control in glob.glob("/Volumes/*/iPod_Control"):
        return os.path.dirname(control)
    return None


def _tag(easy, clave, defecto=""):
    val = easy.get(clave)
    return str(val[0]).strip() if val else defecto


def leer_meta_mp3(ruta, comentario):
    """ Extrae la metadata necesaria para el mhit desde un MP3. """
    audio = MP3(ruta)
    easy = MP3(ruta, ID3=None)
    try:
        tags = easy.tags
        get = (lambda k: (str(tags.get(k)[0]).strip() if tags and tags.get(k) else "")) if tags else (lambda k: "")
    except Exception:
        get = lambda k: ""

    # títulos con EasyID3
    from mutagen.easyid3 import EasyID3
    try:
        e = EasyID3(ruta)
    except Exception:
        e = {}
    titulo = _tag(e, "title") or os.path.splitext(os.path.basename(ruta))[0]
    artista = _tag(e, "artist") or "Artista Desconocido"
    album = _tag(e, "album") or "Album Desconocido"
    tracknum = 0
    tn = _tag(e, "tracknumber")
    if tn:
        try:
            tracknum = int(tn.split("/")[0])
        except ValueError:
            tracknum = 0

    info = audio.info
    return {
        "titulo": titulo,
        "artista": artista,
        "album": album,
        "comentario": comentario,
        "size": os.path.getsize(ruta),
        "length_ms": int(round(info.length * 1000)),
        "bitrate": int(info.bitrate / 1000),
        "samplerate": int(info.sample_rate),
        "track_number": tracknum,
    }


def carpetas_musica(ipod):
    dirs = sorted(glob.glob(os.path.join(ipod, "iPod_Control", "Music", "F[0-9][0-9]")))
    return dirs


def nombre_libre(carpeta, usados):
    """ Genera un nombre de 4 letras mayúsculas único en la carpeta. """
    while True:
        nombre = "".join(random.choice(string.ascii_uppercase) for _ in range(4)) + ".mp3"
        ruta = os.path.join(carpeta, nombre)
        if nombre not in usados and not os.path.exists(ruta):
            usados.add(nombre)
            return nombre


def existe_track(root, meta):
    """ Evita duplicados: True si ya hay un mhit con mismo título+artista+álbum. """
    import struct
    def u32(b, o): return struct.unpack_from("<I", b, o)[0]
    def mhod_str(ch):
        b = ch.raw
        L = u32(b, 4)
        return b[16:16 + L].decode("utf-16-le", "replace")
    objetivo = (meta["titulo"].lower(), meta["artista"].lower(), meta["album"].lower())
    for mhit in db.iter_chunks(root, b"mhit"):
        campos = {}
        for c in mhit.children:
            if c.type == b"mhod":
                mt = u32(c.header, 0x0c)
                if mt in (1, 3, 4):
                    campos[mt] = mhod_str(c).lower()
        if (campos.get(1, ""), campos.get(4, ""), campos.get(3, "")) == objetivo:
            return True
    return False


def backup_database(ipod, dest, log=print):
    """
    Respalda la base de datos del iPod (toda la carpeta iPod_Control/iTunes, que contiene
    iTunesDB y preferencias) a 'dest/iTunesDB-backup-<timestamp>/'. Devuelve la ruta.
    """
    src = os.path.join(ipod, "iPod_Control", "iTunes")
    if not os.path.isdir(src):
        raise RuntimeError(f"No se encontró la carpeta de la DB: {src}")

    ts = datetime.now().strftime("%Y%m%d-%H%M%S")
    destino = os.path.join(dest, f"iTunesDB-backup-{ts}")
    log(f"📀 iPod: {ipod}")
    log(f"🗃  Respaldando base de datos → {destino}")
    shutil.copytree(src, destino)

    # Verificar el iTunesDB copiado y reportar contenido
    itdb = os.path.join(destino, "iTunesDB")
    resumen = ""
    if os.path.isfile(itdb):
        try:
            root, _, _ = db.parse_file(itdb)
            n_t = sum(1 for _ in db.iter_chunks(root, b"mhit"))
            n_p = sum(1 for _ in db.iter_chunks(root, b"mhyp"))
            resumen = f"{n_t} tracks, {n_p} playlists"
        except Exception as e:
            resumen = f"⚠️ no se pudo verificar el iTunesDB: {e}"

    total = sum(os.path.getsize(os.path.join(dp, f))
                for dp, _, fs in os.walk(destino) for f in fs)
    log(f"✅ Base de datos respaldada ({resumen}) — {total / 1024 / 1024:.1f} MB")
    log(f"   Ruta: {destino}")
    return destino


def _itdb_path(ipod):
    return os.path.join(ipod, "iPod_Control", "iTunes", "iTunesDB")


def _cargar_verificado(itdb):
    """ Parsea el iTunesDB y exige round-trip idéntico antes de tocar nada. """
    root, original, _ = db.parse_file(itdb)
    if root.serialize() != original:
        raise RuntimeError("El iTunesDB no re-serializa idéntico; abortando por seguridad.")
    return root


def _escribir_db(root, itdb, log):
    """ Backup + escritura atómica con validación (re-parseo antes de reemplazar). """
    backup = itdb + ".spotipod-" + datetime.now().strftime("%Y%m%d-%H%M%S") + ".bak"
    shutil.copy2(itdb, backup)
    log(f"🛟 Backup del iTunesDB: {backup}")
    tmp = itdb + ".tmp"
    with open(tmp, "wb") as f:
        f.write(root.serialize())
    db.parse_file(tmp)  # lanza si quedó inválida
    os.replace(tmp, itdb)


def list_ipod_playlists(ipod):
    """ Devuelve la lista de playlists del iPod (dicts con nombre/items/master). """
    return db.list_playlists(_cargar_verificado(_itdb_path(ipod)))


def ipod_stats(ipod):
    """
    Devuelve {'tracks', 'playlists'} con conteos reales: pistas = nº de mhit; playlists =
    nº de nombres únicos de usuario (el iPod duplica las listas internamente).
    """
    root = _cargar_verificado(_itdb_path(ipod))
    tracks = sum(1 for _ in db.iter_chunks(root, b"mhit"))
    nombres = set()
    for mhyp in db.iter_chunks(root, b"mhyp"):
        if db._u32(mhyp.header, 0x14) != 1:
            nombres.add(db._titulo_playlist(mhyp))
    return {"tracks": tracks, "playlists": len(nombres)}


def delete_ipod_playlist(ipod, nombre, log=print):
    """ Borra una playlist de usuario del iPod (con backup). Devuelve cuántas se borraron. """
    itdb = _itdb_path(ipod)
    root = _cargar_verificado(itdb)
    n = db.remove_playlist(root, nombre)
    if not n:
        log(f"⚠️ No se encontró la playlist '{nombre}' (o es la biblioteca principal).")
        return 0
    _escribir_db(root, itdb, log)
    log(f"✅ Playlist '{nombre}' eliminada del iPod ({n}).")
    return n


def restore_database(ipod, backup_dir, log=print):
    """
    Restaura la base de datos del iPod desde una carpeta de backup (creada por
    backup_database). Copia el iTunesDB (y demás archivos del backup) de vuelta, tras
    guardar el estado actual por si acaso.
    """
    src_db = os.path.join(backup_dir, "iTunesDB")
    if not os.path.isfile(src_db):
        raise RuntimeError(f"El backup no contiene un iTunesDB: {backup_dir}")

    # El backup debe parsear (evita restaurar algo corrupto)
    db.parse_file(src_db)

    itunes = os.path.join(ipod, "iPod_Control", "iTunes")
    actual = os.path.join(itunes, "iTunesDB")
    ts = datetime.now().strftime("%Y%m%d-%H%M%S")
    if os.path.isfile(actual):
        pre = actual + f".pre-restore-{ts}.bak"
        shutil.copy2(actual, pre)
        log(f"🛟 Estado actual guardado en: {pre}")

    copiados = 0
    for f in os.listdir(backup_dir):
        s = os.path.join(backup_dir, f)
        if os.path.isfile(s):
            shutil.copy2(s, os.path.join(itunes, f))
            copiados += 1
    log(f"✅ Base de datos restaurada desde {backup_dir} ({copiados} archivos).")
    log("   Expulsa el iPod para que recargue la biblioteca.")


def _sanitizar(nombre):
    nombre = re.sub(r'[\/:*?"<>|]', '-', nombre)
    return nombre.replace("\n", " ").replace("\t", " ").strip() or "sin_nombre"


def descargar_playlist(ipod, nombre, dest, log=print):
    """
    Copia los MP3 de una playlist del iPod a 'dest/<nombre>/' con nombres 'Artista - Título.mp3'.
    Idempotente (salta los que ya existen). Devuelve el número de archivos copiados.
    """
    root = _cargar_verificado(_itdb_path(ipod))
    tracks = db.tracks_de_playlist(root, nombre)
    if not tracks:
        log(f"⚠️ La playlist '{nombre}' no existe en el iPod o no tiene pistas.")
        return 0

    carpeta = os.path.join(dest, _sanitizar(nombre))
    os.makedirs(carpeta, exist_ok=True)
    log(f"⬇️  Descargando '{nombre}' ({len(tracks)} pistas) → {carpeta}")

    copiados = omitidos = faltantes = 0
    for i, t in enumerate(tracks, 1):
        loc = t["location"]  # ':iPod_Control:Music:F49:TOQN.mp3'
        origen = os.path.join(ipod, loc.strip(":").replace(":", "/")) if loc else ""
        destino = os.path.join(carpeta, _sanitizar(f"{t['artista']} - {t['titulo']}") + ".mp3")

        if not origen or not os.path.isfile(origen):
            faltantes += 1
            log(f"[{i}/{len(tracks)}] ⚠️ No se encontró el archivo: {t['artista']} - {t['titulo']}")
            continue
        if os.path.exists(destino):
            omitidos += 1
            continue
        shutil.copy2(origen, destino)
        copiados += 1
        log(f"[{i}/{len(tracks)}] ⬇️  {t['artista']} - {t['titulo']}")

    resumen = f"✅ '{nombre}': {copiados} copiadas, {omitidos} ya estaban"
    if faltantes:
        resumen += f", {faltantes} sin archivo"
    log(resumen + f" → {carpeta}")
    return copiados


def descargar_todas_playlists(ipod, dest, log=print):
    """ Descarga TODAS las playlists de usuario del iPod a dest/. Devuelve (nº playlists, copiados). """
    root = _cargar_verificado(_itdb_path(ipod))
    nombres = []
    for p in db.list_playlists(root):
        if not p["master"] and p["nombre"] and p["nombre"] not in nombres:
            nombres.append(p["nombre"])
    if not nombres:
        log("El iPod no tiene playlists de usuario.")
        return 0, 0
    log(f"⬇️  Descargando {len(nombres)} playlist(s) del iPod → {dest}")
    total = 0
    for n in nombres:
        total += descargar_playlist(ipod, n, dest, log=log)
    log(f"✅ Listo: {len(nombres)} playlist(s), {total} archivos nuevos en {dest}")
    return len(nombres), total


def compatibilidad(ipod):
    """
    Determina si el iPod conectado soporta la carga directa a la base de datos.
    Devuelve (compatible_bool, motivo).
    """
    itdb = _itdb_path(ipod)
    if not os.path.isfile(itdb):
        return False, "no se encontró iTunesDB (¿modo disco activado?)"
    try:
        root, _, _ = db.parse_file(itdb)
    except Exception as e:
        return False, f"no se pudo leer el iTunesDB ({e})"
    if db.requiere_firma(root):
        return False, "el iPod firma su base de datos (Classic 6G/7G o Nano 3G+) → usa Music.app"
    return True, "compatible (base de datos sin firma)"


def sync(ipod, nombre_playlist, mp3s, dry_run=False, log=print):
    itdb = os.path.join(ipod, "iPod_Control", "iTunes", "iTunesDB")
    if not os.path.isfile(itdb):
        raise RuntimeError(f"No se encontró iTunesDB en {itdb}")

    log(f"📀 iPod: {ipod}")
    log(f"🗃  Parseando iTunesDB ({os.path.getsize(itdb)} bytes)...")
    root, original, _ = db.parse_file(itdb)

    # Seguridad: la DB debe re-serializar idéntica antes de tocar nada.
    if root.serialize() != original:
        raise RuntimeError("El iTunesDB no re-serializa idéntico; abortando por seguridad.")
    log("✅ iTunesDB verificado (round-trip idéntico).")

    # Compatibilidad: si el iPod firma su DB, no podemos reproducir la firma → abortar.
    if db.requiere_firma(root):
        raise RuntimeError(
            "Este iPod firma su base de datos (iPod Classic 6G/7G o Nano 3G+); la carga directa "
            "no es compatible. Los MP3 ya están grabados: cárgalos con Music.app (menú opción 1).")

    dirs = carpetas_musica(ipod)
    if not dirs:
        raise RuntimeError("No hay carpetas iPod_Control/Music/F##.")

    # Chequeo de espacio libre: no empezar a copiar si no cabe todo (con 100 MB de margen).
    necesario = sum(os.path.getsize(m) for m in mp3s if os.path.isfile(m))
    st = os.statvfs(ipod)
    libre = st.f_bavail * st.f_frsize
    if necesario + 100 * 1024 * 1024 > libre:
        raise RuntimeError(
            f"Espacio insuficiente en el iPod: se necesitan {necesario / 1e6:.0f} MB "
            f"y solo hay {libre / 1e6:.0f} MB libres.")
    log(f"💾 Espacio: {necesario / 1e6:.0f} MB a copiar, {libre / 1e6:.0f} MB libres.")

    usados = set()

    metas = []
    copiados = []
    for i, mp3 in enumerate(mp3s, 1):
        if not os.path.isfile(mp3):
            log(f"⚠️ No existe, se omite: {mp3}")
            continue
        meta = leer_meta_mp3(mp3, nombre_playlist)

        if existe_track(root, meta):
            log(f"[{i}/{len(mp3s)}] ⏭ Ya en el iPod: {meta['artista']} - {meta['titulo']}")
            continue

        carpeta = random.choice(dirs)
        nombre = nombre_libre(carpeta, usados)
        f_folder = os.path.basename(carpeta)
        meta["location"] = f":iPod_Control:Music:{f_folder}:{nombre}"
        destino = os.path.join(carpeta, nombre)

        log(f"[{i}/{len(mp3s)}] ⬇️  {meta['artista']} - {meta['titulo']}  →  {f_folder}/{nombre}")
        if not dry_run:
            shutil.copy2(mp3, destino)
            copiados.append(destino)
        metas.append(meta)

    if not metas:
        log("ℹ️ Nada nuevo que añadir.")
        return 0

    if dry_run:
        log(f"\n🧪 dry-run: se añadirían {len(metas)} tracks a la playlist '{nombre_playlist}'. No se escribió nada.")
        return len(metas)

    # Añadir a la DB en memoria
    db.add_playlist(root, nombre_playlist, metas)

    # Backup del iTunesDB antes de escribir
    backup = itdb + ".spotipod-" + datetime.now().strftime("%Y%m%d-%H%M%S") + ".bak"
    shutil.copy2(itdb, backup)
    log(f"🛟 Backup del iTunesDB: {backup}")

    # Escribir la DB nueva
    nueva = root.serialize()
    # Validación final: debe re-parsear
    tmp = itdb + ".tmp"
    with open(tmp, "wb") as f:
        f.write(nueva)
    db.parse_file(tmp)  # lanza si es inválida
    os.replace(tmp, itdb)

    log(f"\n✅ {len(metas)} tracks añadidos y playlist '{nombre_playlist}' creada en el iPod:")
    for n, meta in enumerate(metas, 1):
        log(f"   🆕 {n}. {meta['artista']} - {meta['titulo']}")
    log("   Expulsa el iPod con seguridad antes de desconectarlo.")
    return len(metas)


def main():
    p = argparse.ArgumentParser(description="Carga MP3s a un iPod clásico sin Music.app.")
    p.add_argument("playlist", help="Nombre de la playlist a crear en el iPod")
    p.add_argument("mp3s", nargs="*", help="Archivos MP3 (o usa --dir)")
    p.add_argument("--dir", help="Carpeta con MP3s (se toman todos los *.mp3)")
    p.add_argument("--ipod", help="Ruta del iPod (autodetecta si se omite)")
    p.add_argument("--dry-run", action="store_true", help="No copia ni escribe; solo muestra")
    args = p.parse_args()

    ipod = detectar_ipod(args.ipod)
    if not ipod:
        print("❌ No se encontró un iPod montado. Conéctalo o usa --ipod /Volumes/iPod")
        sys.exit(1)

    mp3s = list(args.mp3s)
    if args.dir:
        mp3s += sorted(glob.glob(os.path.join(args.dir, "*.mp3")))
    if not mp3s:
        print("❌ No se indicaron MP3s (pásalos como argumento o con --dir).")
        sys.exit(1)

    try:
        sync(ipod, args.playlist, mp3s, dry_run=args.dry_run)
    except Exception as e:
        print(f"❌ ERROR: {e}")
        sys.exit(1)


if __name__ == "__main__":
    main()
