"""Write Tracepaper models (SPEC-v0.md §2.8): 3D views as .glb files.

    import tracepaper.model
    tracepaper.model.export(assembly, "views/window.glb", hidden=["Glass", "Steel / Z profile"])

export() writes a CadQuery assembly (anything with CadQuery's Assembly.export) and
marks the named nodes as hidden when a window opens the model. Standard library only;
the CAD package is whatever the caller already uses.
"""
from __future__ import annotations

import json
import struct
from pathlib import Path
from typing import Iterable, Union

GLB_MAGIC, JSON_CHUNK, BIN_CHUNK = 0x46546C67, 0x4E4F534A, 0x004E4942
SEP = " / "


def export(assembly, path: Union[str, Path], hidden: Iterable[str] = ()) -> Path:
    """Write assembly to path as .glb, with the nodes in hidden hidden at first.

    Each entry of hidden is a node's path in the viewer's tree ("Steel / Z profile")
    or just a name, which hides every node of that name. The file is written to a
    temporary name and renamed into place, so the viewer never reads half of it, and
    its folder is made if it isn't there yet.
    """
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    assembly.export(str(tmp), exportType="GLB")
    try:
        hidden = list(hidden)
        if hidden:
            mark_hidden(tmp, hidden)
        tmp.replace(path)
    except BaseException:
        tmp.unlink(missing_ok=True)
        raise
    return path


def read_glb(path: Union[str, Path]) -> tuple:
    """(the glTF JSON as a dict, the binary chunk) of a .glb file."""
    data = Path(path).read_bytes()
    magic, version, _ = struct.unpack_from("<III", data, 0)
    if magic != GLB_MAGIC or version != 2:
        raise ValueError(f"{path} is not a glTF 2.0 binary (.glb) file")
    length, kind = struct.unpack_from("<II", data, 12)
    if kind != JSON_CHUNK:
        raise ValueError(f"{path}: the first chunk is not JSON")
    gltf = json.loads(data[20:20 + length])
    rest = data[20 + length:]
    binary = b""
    if len(rest) >= 8:
        blen, bkind = struct.unpack_from("<II", rest, 0)
        if bkind == BIN_CHUNK:
            binary = rest[8:8 + blen]
    return gltf, binary


def glb_bytes(gltf: dict, binary: bytes) -> bytes:
    """The .glb file of the glTF JSON and its binary chunk."""
    text = json.dumps(gltf, separators=(",", ":")).encode()
    text += b" " * (-len(text) % 4)  # chunks are 4-byte aligned; JSON pads with spaces
    binary += b"\0" * (-len(binary) % 4)
    chunks = struct.pack("<II", len(text), JSON_CHUNK) + text
    if binary:
        chunks += struct.pack("<II", len(binary), BIN_CHUNK) + binary
    return struct.pack("<III", GLB_MAGIC, 2, 12 + len(chunks)) + chunks


def write_glb(path: Union[str, Path], gltf: dict, binary: bytes) -> None:
    Path(path).write_bytes(glb_bytes(gltf, binary))


def tree(gltf: dict) -> list:
    """(path, node index) of the named nodes, as the viewer's tree shows them.

    Unnamed nodes don't count, and a single top node (the assembly itself) is left
    out, so its parts are the top level.
    """
    nodes = gltf.get("nodes", [])
    scene = gltf.get("scenes", [{}])[gltf.get("scene", 0)]

    def under(indices, above):
        out = []
        for i in indices:
            name = nodes[i].get("name")
            if not name:
                out += under(nodes[i].get("children", []), above)
                continue
            key = f"{above}{SEP}{name}" if above else name
            out.append((key, i, under(nodes[i].get("children", []), key)))
        return out

    top = under(scene.get("nodes", []), "")
    if len(top) == 1 and top[0][2]:
        top = under(nodes[top[0][1]].get("children", []), "")

    def flat(items):
        out = []
        for key, i, children in items:
            out.append((key, i))
            out += flat(children)
        return out

    return flat(top)


def mark_hidden(path: Union[str, Path], hidden: Iterable[str]) -> None:
    """Mark nodes of a .glb file hidden at first: extras.tracepaper.hidden = true."""
    gltf, binary = read_glb(path)
    nodes = gltf.get("nodes", [])
    paths = tree(gltf)
    for entry in hidden:
        found = [i for key, i in paths if key == entry or (SEP not in entry and nodes[i].get("name") == entry)]
        if not found:
            raise ValueError(f"no node {entry!r} in {path}; its nodes are: {', '.join(k for k, _ in paths)}")
        for i in found:
            nodes[i].setdefault("extras", {}).setdefault("tracepaper", {})["hidden"] = True
    write_glb(path, gltf, binary)
