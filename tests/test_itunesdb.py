"""
Tests del parser/escritor del iTunesDB (tools/itunesdb.py).

Se construye un iTunesDB sintético mínimo (sin datos personales) y se verifica:
  - round-trip byte-idéntico
  - fix_sizes idempotente
  - añadir playlist nueva / anexar a existente
  - borrar playlist (sin borrar tracks)

Si hay un iPod montado (o IPOD_DB apunta a un iTunesDB real), se añade un test de
round-trip contra esa DB real.
"""
import glob
import os
import struct
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "tools"))
import itunesdb as db


# ---------- Constructor de un iTunesDB sintético mínimo ----------

def _hdr(typ, hlen, campos=None):
    h = bytearray(hlen)
    h[0:4] = typ
    struct.pack_into("<I", h, 4, hlen)
    for off, val in (campos or {}).items():
        struct.pack_into("<I", h, off, val)
    return bytes(h)


def _mhit(tid, titulo, artista, album, loc):
    mhods = [
        db.make_string_mhod(1, titulo),
        db.make_string_mhod(4, artista),
        db.make_string_mhod(3, album),
        db.make_string_mhod(6, "MPEG audio file"),
        db.make_string_mhod(8, ""),
        db.make_string_mhod(2, loc),
    ]
    h = _hdr(b"mhit", 0xf4, {0x10: tid, 0x24: 1000, 0x28: 60000, 0x38: 320, 0x3c: 44100 << 16})
    return db.Chunk(b"mhit", h, mhods)


def _mhip(grp, tid):
    return db.Chunk(b"mhip", _hdr(b"mhip", 0x4c, {0x14: grp, 0x18: tid}), [])


def _mhyp(titulo, master, plid, items):
    hijos = [db.make_string_mhod(1, titulo)] + [_mhip(g, t) for g, t in items]
    h = _hdr(b"mhyp", 0xb8, {0x14: 1 if master else 0,
                             0x18: plid & 0xffffffff, 0x1c: plid >> 32})
    return db.Chunk(b"mhyp", h, hijos)


def _mhsd(tipo, hijo):
    return db.Chunk(b"mhsd", _hdr(b"mhsd", 0x60, {0x0c: tipo}), [hijo])


def make_db():
    t1 = _mhit(1, "Song A", "Artist", "Album", ":iPod_Control:Music:F00:AAAA.mp3")
    t2 = _mhit(2, "Song B", "Artist", "Album", ":iPod_Control:Music:F00:BBBB.mp3")
    tracklist = _mhsd(1, db.Chunk(b"mhlt", _hdr(b"mhlt", 0x5c), [t1, t2]))

    master = _mhyp("Library", True, 0x1111, [(101, 1), (102, 2)])
    user = _mhyp("Mix", False, 0x2222, [(201, 1)])
    pllist = _mhsd(2, db.Chunk(b"mhlp", _hdr(b"mhlp", 0x5c), [master, user]))

    root = db.Chunk(b"mhbd", _hdr(b"mhbd", 0x68, {0x10: 117}), [tracklist, pllist])
    db.fix_sizes(root)
    return root


def _meta(i, playlist):
    return dict(titulo=f"New {i}", artista="Nuevo", album="Backup",
                comentario=playlist, location=f":iPod_Control:Music:F00:N{i:03d}.mp3",
                size=2000, length_ms=120000, bitrate=320, samplerate=44100, track_number=i)


def _reparse(root):
    data = root.serialize()
    rp, end = db.parse(data, 0)
    assert end == len(data)
    return rp


# ---------- Tests con el fixture sintético ----------

def test_roundtrip():
    root = make_db()
    data = root.serialize()
    rp, end = db.parse(data, 0)
    assert end == len(data)
    assert rp.serialize() == data


def test_fix_sizes_idempotente():
    root = make_db()
    data = root.serialize()
    db.fix_sizes(root)
    assert root.serialize() == data


def test_conteos_iniciales():
    root = make_db()
    assert sum(1 for _ in db.iter_chunks(root, b"mhit")) == 2
    pls = db.list_playlists(root)
    assert {p["nombre"] for p in pls} == {"Library", "Mix"}
    assert [p for p in pls if p["master"]][0]["items"] == 2


def test_add_playlist_nueva():
    root = make_db()
    db.add_playlist(root, "Recorded", [_meta(1, "Recorded"), _meta(2, "Recorded")])
    rp = _reparse(root)
    assert sum(1 for _ in db.iter_chunks(rp, b"mhit")) == 4  # 2 + 2
    porpl = {p["nombre"]: p["items"] for p in db.list_playlists(rp) if not p["master"]}
    assert porpl.get("Recorded") == 2
    # los nuevos tracks entraron a la biblioteca (master)
    assert [p for p in db.list_playlists(rp) if p["master"]][0]["items"] == 4


def test_add_playlist_anexar():
    root = make_db()
    db.add_playlist(root, "Mix", [_meta(9, "Mix")])  # 'Mix' ya existe
    rp = _reparse(root)
    mix = [p for p in db.list_playlists(rp) if p["nombre"] == "Mix" and not p["master"]]
    assert len(mix) == 1          # no creó una nueva
    assert mix[0]["items"] == 2   # 1 + 1


def test_remove_playlist():
    root = make_db()
    n = db.remove_playlist(root, "Mix")
    assert n == 1
    rp = _reparse(root)
    assert "Mix" not in [p["nombre"] for p in db.list_playlists(rp)]
    # borrar la playlist NO borra los tracks
    assert sum(1 for _ in db.iter_chunks(rp, b"mhit")) == 2


def test_remove_playlist_inexistente():
    root = make_db()
    assert db.remove_playlist(root, "NoExiste") == 0


def test_no_borra_master():
    root = make_db()
    assert db.remove_playlist(root, "Library") == 0  # master no se borra por nombre


def test_unicode_en_tags():
    root = make_db()
    db.add_playlist(root, "ñ💿", [_meta(1, "ñ💿")])
    rp = _reparse(root)
    assert "ñ💿" in [p["nombre"] for p in db.list_playlists(rp)]


# ---------- Test opcional contra un iTunesDB real ----------

def _db_real():
    if os.getenv("IPOD_DB") and os.path.isfile(os.getenv("IPOD_DB")):
        return os.getenv("IPOD_DB")
    for m in glob.glob("/Volumes/*/iPod_Control/iTunes/iTunesDB"):
        return m
    return None


@pytest.mark.skipif(_db_real() is None, reason="no hay iTunesDB real montado")
def test_roundtrip_db_real():
    root, original, end = db.parse_file(_db_real())
    assert end == len(original)
    assert root.serialize() == original       # round-trip byte-idéntico
    db.fix_sizes(root)
    assert root.serialize() == original       # fix_sizes no altera una DB válida
