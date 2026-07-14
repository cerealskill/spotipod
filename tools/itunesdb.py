#!/usr/bin/env python3
"""
Parser/serializador del iTunesDB de iPods clásicos (formato de chunks 'mh..').

Modela la DB como un árbol de Chunks. La regla de oro de seguridad: parsear y volver a
serializar debe producir bytes IDÉNTICOS al original. Solo cuando eso se cumple es seguro
modificar la DB (añadir tracks / playlists).

Reglas estructurales (little-endian):
  - Todo chunk: [0:4]=tipo, [4:8]=longitud de cabecera, [8:12]=campo variable.
  - mhbd: campo@0x14 = nº de hijos (mhsd). Hijos tras la cabecera.
  - mhsd: campo@8 = longitud total (acota su subárbol). Un único hijo tras la cabecera.
  - Cabeceras de lista (mhlt, mhlp, mhla): campo@8 = nº de hijos (contador), NO longitud.
    Los hijos van justo después de la cabecera.
  - Chunks 'item' (mhit, mhyp, mhip, mhia): campo@8 = longitud total; hijos entre la
    cabecera y esa longitud.
  - mhod y desconocidos: hoja; se conservan los bytes [0:total@8] tal cual.
"""
import struct

LIST_COUNT = {b"mhlt", b"mhlp", b"mhla"}          # campo@8 = nº de hijos
ITEM_TOTAL = {b"mhsd", b"mhit", b"mhyp", b"mhip", b"mhia"}  # campo@8 = longitud total
LEAF = {b"mhod"}                                  # hoja con datos crudos


def _u32(buf, off):
    return struct.unpack_from("<I", buf, off)[0]


class Chunk:
    __slots__ = ("type", "header", "children", "raw")

    def __init__(self, type_, header=b"", children=None, raw=None):
        self.type = type_            # bytes de 4
        self.header = header         # bytes de la cabecera (incluye tipo/longitudes)
        self.children = children if children is not None else []
        self.raw = raw               # solo hojas: cuerpo tras la cabecera

    def clone(self):
        return Chunk(self.type, bytes(self.header),
                     [c.clone() for c in self.children],
                     None if self.raw is None else bytes(self.raw))

    # ---- longitudes/campos de la cabecera ----
    def hlen(self):
        return _u32(self.header, 4)

    def field8(self):
        return _u32(self.header, 8)

    def set_field8(self, val):
        self.header = self.header[:8] + struct.pack("<I", val) + self.header[12:]

    def set_field(self, off, val):
        self.header = self.header[:off] + struct.pack("<I", val) + self.header[off + 4:]

    # ---- serialización ----
    def serialize(self):
        if self.raw is not None:
            return self.header + self.raw
        return self.header + b"".join(c.serialize() for c in self.children)

    def total_size(self):
        return len(self.serialize())

    def __repr__(self):
        t = self.type.decode("latin1")
        return f"<Chunk {t} hlen={self.hlen()} f8={self.field8()} hijos={len(self.children)}>"


def parse(buf, off=0):
    """ Parsea un chunk en 'off'. Devuelve (chunk, offset_siguiente). """
    type_ = buf[off:off + 4]
    hlen = _u32(buf, off + 4)
    f8 = _u32(buf, off + 8)
    header = buf[off:off + hlen]

    if type_ == b"mhbd":
        n = _u32(buf, off + 0x14)              # nº de mhsd
        pos = off + hlen
        children = []
        for _ in range(n):
            child, pos = parse(buf, pos)
            children.append(child)
        return Chunk(type_, header, children), pos

    if type_ in LIST_COUNT:
        pos = off + hlen
        children = []
        for _ in range(f8):                    # f8 = nº de hijos
            child, pos = parse(buf, pos)
            children.append(child)
        return Chunk(type_, header, children), pos

    if type_ in ITEM_TOTAL:
        end = off + f8                         # f8 = longitud total
        pos = off + hlen
        children = []
        while pos < end:
            child, pos = parse(buf, pos)
            children.append(child)
        if pos != end:                         # seguridad: no debería sobrar/faltar
            raise ValueError(f"{type_} en {off:#x}: hijos terminan en {pos:#x}, esperado {end:#x}")
        return Chunk(type_, header, children), end

    # hoja (mhod / desconocidos): conservar bytes crudos hasta total@8
    end = off + f8
    return Chunk(type_, header, raw=buf[off + hlen:end]), end


def parse_file(ruta):
    with open(ruta, "rb") as f:
        buf = f.read()
    chunk, end = parse(buf, 0)
    return chunk, buf, end


# ---- utilidades de recorrido ----
def iter_chunks(chunk, tipo=None):
    """ Recorre el árbol en profundidad; si 'tipo' se da (bytes), filtra por tipo. """
    if tipo is None or chunk.type == tipo:
        yield chunk
    for c in chunk.children:
        yield from iter_chunks(c, tipo)


def fix_sizes(chunk):
    """
    Recalcula (bottom-up) todas las longitudes y contadores para que el árbol sea
    consistente tras cualquier modificación:
      - hojas: total@8 = cabecera + datos
      - items (mhit/mhyp/mhip/mhia/mhsd): total@8 = cabecera + hijos; + contadores
      - cabeceras de lista (mhlt/mhlp/mhla): campo@8 = nº de hijos
      - mhbd: total@8 = tamaño total; nº de hijos@0x14

    Sobre un árbol sin modificar debe reproducir los bytes originales (test de seguridad).
    """
    for c in chunk.children:
        fix_sizes(c)

    t = chunk.type
    if chunk.raw is not None:                        # hoja
        chunk.set_field8(len(chunk.header) + len(chunk.raw))
        return

    cuerpo = sum(c.total_size() for c in chunk.children)

    if t == b"mhbd":
        chunk.set_field8(len(chunk.header) + cuerpo)
        chunk.set_field(0x14, len(chunk.children))
    elif t in LIST_COUNT:
        chunk.set_field8(len(chunk.children))        # campo@8 = nº de hijos
    elif t in ITEM_TOTAL:
        chunk.set_field8(len(chunk.header) + cuerpo)
        # contadores específicos
        if t == b"mhit":
            n_mhod = sum(1 for c in chunk.children if c.type == b"mhod")
            chunk.set_field(0x0c, n_mhod)
        elif t == b"mhyp":
            n_mhod = sum(1 for c in chunk.children if c.type == b"mhod")
            n_mhip = sum(1 for c in chunk.children if c.type == b"mhip")
            chunk.set_field(0x0c, n_mhod)
            chunk.set_field(0x10, n_mhip)
        elif t == b"mhip":
            n_mhod = sum(1 for c in chunk.children if c.type == b"mhod")
            chunk.set_field(0x0c, n_mhod)


# ==================== Construcción de chunks (añadir tracks/playlists) ====================
import os
import random
import struct as _struct

MHOD_TITLE, MHOD_LOCATION, MHOD_ALBUM, MHOD_ARTIST = 1, 2, 3, 4
MHOD_FILETYPE, MHOD_COMMENT = 6, 8


def _mhod_string(mtype):
    """ Construye un mhod de cadena (cabecera de 24 bytes). fix_sizes ajustará total@8. """
    header = b"mhod" + _struct.pack("<IIIII", 24, 0, mtype, 0, 0)
    return header


def make_string_mhod(mtype, texto, posicion=1):
    """ Crea un mhod de cadena UTF-16LE del tipo dado. """
    utf16 = (texto or "").encode("utf-16-le")
    body = _struct.pack("<IIII", posicion, len(utf16), 0, 0) + utf16
    ch = Chunk(b"mhod", _mhod_string(mtype), raw=body)
    return ch


def encontrar(root, tipo, pred=None):
    for c in iter_chunks(root, tipo):
        if pred is None or pred(c):
            return c
    return None


def _mhsd_por_tipo(root, tipo_mhsd):
    for m in iter_chunks(root, b"mhsd"):
        if _u32(m.header, 0x0c) == tipo_mhsd:
            return m
    return None


def snapshot_ids(root):
    """ Devuelve (max_track_id, max_grpid, plids_existentes) para asignar IDs nuevos. """
    max_tid = 0
    max_grp = 0
    plids = set()
    for mhit in iter_chunks(root, b"mhit"):
        max_tid = max(max_tid, _u32(mhit.header, 0x10))
    for mhip in iter_chunks(root, b"mhip"):
        max_grp = max(max_grp, _u32(mhip.header, 0x14))
    for mhyp in iter_chunks(root, b"mhyp"):
        plids.add(struct.unpack_from("<Q", mhyp.header, 0x18)[0])
    return max_tid, max_grp, plids


def build_mhit(template_mhit, track_id, dbid, meta):
    """
    Construye un mhit nuevo clonando la plantilla (hereda todos los campos desconocidos)
    y sobrescribiendo id, tamaño, duración, bitrate, sample rate y los mhods de cadena.
    meta: dict con titulo, artista, album, comentario, location, size, length_ms,
          bitrate, samplerate, (opcional) track_number.
    """
    mhit = template_mhit.clone()

    # Índice de mhods de la plantilla por tipo (para conservar pos/flags exactos)
    plantillas = {}
    for c in template_mhit.children:
        if c.type == b"mhod":
            plantillas[_u32(c.header, 0x0c)] = c

    def mhod_de(mtype, texto):
        tpl = plantillas.get(mtype)
        if tpl is None:
            return make_string_mhod(mtype, texto)
        # conservar posicion + flags de la plantilla, cambiar solo la cadena
        utf16 = (texto or "").encode("utf-16-le")
        body = tpl.raw[:4] + _struct.pack("<I", len(utf16)) + tpl.raw[8:16] + utf16
        return Chunk(b"mhod", bytes(tpl.header), raw=body)

    # Reconstruir los mhods en el mismo orden que la plantilla
    nuevos = []
    for c in template_mhit.children:
        if c.type != b"mhod":
            continue
        mt = _u32(c.header, 0x0c)
        if mt == MHOD_TITLE:
            nuevos.append(mhod_de(mt, meta["titulo"]))
        elif mt == MHOD_ARTIST:
            nuevos.append(mhod_de(mt, meta["artista"]))
        elif mt == MHOD_ALBUM:
            nuevos.append(mhod_de(mt, meta["album"]))
        elif mt == MHOD_COMMENT:
            nuevos.append(mhod_de(mt, meta.get("comentario", "")))
        elif mt == MHOD_LOCATION:
            nuevos.append(mhod_de(mt, meta["location"]))
        elif mt == MHOD_FILETYPE:
            nuevos.append(c.clone())  # "Archivo de audio MPEG": reutilizar tal cual
        else:
            nuevos.append(c.clone())
    mhit.children = nuevos

    # Campos numéricos
    mhit.set_field(0x10, track_id)
    mhit.set_field(0x24, meta["size"])
    mhit.set_field(0x28, meta["length_ms"])
    if meta.get("track_number"):
        mhit.set_field(0x2c, meta["track_number"])
    mhit.set_field(0x38, meta.get("bitrate", 0))
    mhit.set_field(0x3c, meta.get("samplerate", 44100) << 16)
    # id persistente de 64 bits (en 0x70 y duplicado en 0xa8)
    mhit.header = (mhit.header[:0x70] + _struct.pack("<Q", dbid) + mhit.header[0x78:])
    mhit.header = (mhit.header[:0xa8] + _struct.pack("<Q", dbid) + mhit.header[0xb0:])
    return mhit


def build_mhip(template_mhip, group_id, track_id):
    """ Crea un mhip 'pelado' (sin mhod) que referencia un track. """
    header = bytearray(template_mhip.header)
    _struct.pack_into("<I", header, 0x0c, 0)         # num_mhod = 0
    _struct.pack_into("<I", header, 0x14, group_id)  # grpid único
    _struct.pack_into("<I", header, 0x18, track_id)  # track referenciado
    return Chunk(b"mhip", bytes(header), children=[])


def build_playlist(template_mhyp, nombre, plid, items):
    """
    Crea una playlist (mhyp) clonando una de usuario: cambia título, id persistente,
    quita el flag master y reemplaza los items. 'items' = lista de (group_id, track_id).
    """
    mhyp = template_mhyp.clone()
    # plantilla de mhip (el primero de la plantilla)
    tpl_mhip = encontrar(template_mhyp, b"mhip")

    # Cambiar título (mhod tipo 1)
    for c in mhyp.children:
        if c.type == b"mhod" and _u32(c.header, 0x0c) == MHOD_TITLE:
            utf16 = nombre.encode("utf-16-le")
            c.raw = c.raw[:4] + _struct.pack("<I", len(utf16)) + c.raw[8:16] + utf16
            break

    # id persistente único y quitar flag master
    mhyp.header = mhyp.header[:0x14] + _struct.pack("<I", 0) + mhyp.header[0x18:]
    mhyp.header = mhyp.header[:0x18] + _struct.pack("<Q", plid) + mhyp.header[0x20:]

    # Conservar solo los mhods (ajustes) y añadir los mhips nuevos
    mhods = [c for c in mhyp.children if c.type == b"mhod"]
    mhips = [build_mhip(tpl_mhip, gid, tid) for gid, tid in items]
    mhyp.children = mhods + mhips
    return mhyp


def add_playlist(root, nombre, tracks, append=True):
    """
    Añade 'tracks' (lista de dicts de meta con 'location' ya en el iPod) como tracks nuevos
    a la biblioteca y los agrupa en la playlist 'nombre'. Modifica 'root' in-place.

    Si append=True y ya existe una playlist de usuario con ese nombre, anexa los tracks a
    ella (a todas sus copias internas); si no, crea una playlist nueva.

    Devuelve el número de tracks añadidos.
    """
    mhlt = encontrar(root, b"mhlt")
    template_mhit = encontrar(root, b"mhit")
    masters = [m for m in iter_chunks(root, b"mhyp") if _u32(m.header, 0x14) == 1]
    tpl_user = encontrar(root, b"mhyp", lambda m: _u32(m.header, 0x14) == 0
                         and any(c.type == b"mhip" for c in m.children))
    tpl_mhip = encontrar(root, b"mhip")
    mhlp_user = None
    for mhlp in iter_chunks(root, b"mhlp"):
        if tpl_user is not None and tpl_user in mhlp.children:
            mhlp_user = mhlp
            break
    if not (mhlt and template_mhit and masters and tpl_user and tpl_mhip and mhlp_user):
        raise RuntimeError("No se pudo localizar la estructura base de la DB (mhlt/mhit/mhyp).")

    max_tid, max_grp, plids = snapshot_ids(root)
    tid = max_tid
    grp = max_grp

    def nuevo_grp():
        nonlocal grp
        grp += 1
        return grp

    nuevos_tids = []
    for meta in tracks:
        tid += 1
        mhlt.children.append(build_mhit(template_mhit, tid, random.getrandbits(64), meta))
        # a la biblioteca (todas las copias de la playlist maestra)
        for master in masters:
            master.children.append(build_mhip(tpl_mhip, nuevo_grp(), tid))
        nuevos_tids.append(tid)

    existentes = [m for m in iter_chunks(root, b"mhyp")
                  if _u32(m.header, 0x14) != 1 and _titulo_playlist(m) == nombre]

    if append and existentes:
        for mhyp in existentes:
            for t in nuevos_tids:
                mhyp.children.append(build_mhip(tpl_mhip, nuevo_grp(), t))
    else:
        while True:
            plid = random.getrandbits(64)
            if plid not in plids:
                break
        items = [(nuevo_grp(), t) for t in nuevos_tids]
        mhlp_user.children.append(build_playlist(tpl_user, nombre, plid, items))

    fix_sizes(root)
    return len(tracks)


def _titulo_playlist(mhyp):
    for c in mhyp.children:
        if c.type == b"mhod" and _u32(c.header, 0x0c) == 1:
            b = c.raw
            L = _u32(b, 4)
            return b[16:16 + L].decode("utf-16-le", "replace")
    return None


def list_playlists(root):
    """ Devuelve [{'nombre', 'items', 'master'}] de todas las playlists. """
    out = []
    for mhyp in iter_chunks(root, b"mhyp"):
        out.append({
            "nombre": _titulo_playlist(mhyp),
            "items": sum(1 for c in mhyp.children if c.type == b"mhip"),
            "master": _u32(mhyp.header, 0x14) == 1,
        })
    return out


def remove_playlist(root, nombre):
    """
    Elimina las playlists de usuario (no master) cuyo título coincide. NO borra los tracks
    de la biblioteca, solo la playlist. Devuelve cuántas se eliminaron.
    """
    removed = 0
    for mhlp in list(iter_chunks(root, b"mhlp")):
        nuevos = []
        for c in mhlp.children:
            if (c.type == b"mhyp" and _u32(c.header, 0x14) != 1
                    and _titulo_playlist(c) == nombre):
                removed += 1
                continue
            nuevos.append(c)
        mhlp.children = nuevos
    if removed:
        fix_sizes(root)
    return removed


def write_db(root, ruta):
    """ Serializa y escribe la DB (se asume que 'ruta' ya tiene backup). """
    with open(ruta, "wb") as f:
        f.write(root.serialize())


if __name__ == "__main__":
    import sys
    ruta = sys.argv[1] if len(sys.argv) > 1 else \
        "/Volumes/iPod/iPod_Control/iTunes/iTunesDB"

    root, original, end = parse_file(ruta)
    print(f"📀 Parseado: {ruta}")
    print(f"   tamaño archivo: {len(original)}  | fin del árbol: {end}")

    # Conteos
    from collections import Counter
    cont = Counter(c.type.decode("latin1") for c in iter_chunks(root))
    for k in ("mhbd", "mhsd", "mhla", "mhlt", "mhit", "mhlp", "mhyp", "mhip", "mhod"):
        if k in cont:
            print(f"   {k}: {cont[k]}")

    # === TEST DE ROUND-TRIP: re-serializar debe dar bytes idénticos ===
    salida = root.serialize()
    if salida == original:
        print("✅ ROUND-TRIP PERFECTO: la DB re-serializada es byte-idéntica al original.")
    else:
        print(f"❌ DIFERENCIA: original={len(original)} vs serializado={len(salida)}")
        # localizar primer byte distinto
        n = min(len(salida), len(original))
        for i in range(n):
            if salida[i] != original[i]:
                print(f"   primer byte distinto en offset {i:#x}")
                break
        sys.exit(1)
