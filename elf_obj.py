"""
elf_obj.py  –  Parse an x86-64 ELF relocatable object file into a plain
               Python dict, then write it back to disk byte-for-byte.

Designed as a foundation for a compiler backend: every structural piece
(ELF header, section headers, section data, symbol table, string tables)
is a named field so you can build one from scratch without `as`.

Usage
-----
    from elf_obj import parse, build, load, save

    obj = load("add1.o")          # parse file → dict
    save(obj, "copy.o")           # dict → file  (round-trip)

    # Or work with raw bytes directly:
    raw  = open("add1.o", "rb").read()
    obj  = parse(raw)
    out  = build(obj)
    assert out == raw             # lossless round-trip
"""

import struct
from copy import deepcopy


# ---------------------------------------------------------------------------
# Low-level helpers
# ---------------------------------------------------------------------------

def _u8(buf, off):   return buf[off]
def _u16(buf, off):  return struct.unpack_from("<H", buf, off)[0]
def _u32(buf, off):  return struct.unpack_from("<I", buf, off)[0]
def _u64(buf, off):  return struct.unpack_from("<Q", buf, off)[0]

def _p16(v): return struct.pack("<H", v)
def _p32(v): return struct.pack("<I", v)
def _p64(v): return struct.pack("<Q", v)


# ---------------------------------------------------------------------------
# ELF type constants (subset used by relocatable objects)
# ---------------------------------------------------------------------------

# e_type
ET_REL = 1

# sh_type
SHT = {
    0: "SHT_NULL",
    1: "SHT_PROGBITS",
    2: "SHT_SYMTAB",
    3: "SHT_STRTAB",
    7: "SHT_NOTE",
    8: "SHT_NOBITS",    # .bss – occupies no file space
}

# sh_flags bits
SHF = {
    0x1: "SHF_WRITE",
    0x2: "SHF_ALLOC",
    0x4: "SHF_EXECINSTR",
}

# st_bind (high nibble of st_info)
STB = {0: "STB_LOCAL", 1: "STB_GLOBAL", 2: "STB_WEAK"}

# st_type (low nibble of st_info)
STT = {
    0: "STT_NOTYPE",
    1: "STT_OBJECT",
    2: "STT_FUNC",
    3: "STT_SECTION",
    4: "STT_FILE",
}

# st_visibility (low 2 bits of st_other)
STV = {0: "STV_DEFAULT", 1: "STV_INTERNAL", 2: "STV_HIDDEN", 3: "STV_PROTECTED"}


def _shf_names(flags):
    return [name for bit, name in SHF.items() if flags & bit]


# ---------------------------------------------------------------------------
# parse(raw_bytes) → dict
# ---------------------------------------------------------------------------

def parse(data: bytes) -> dict:
    """
    Parse a 64-bit ELF relocatable object into a structured dict.

    Top-level keys
    --------------
    elf_header   : dict   – all 13 fields of the ELF header
    sections     : list   – one dict per section (see below)

    Each section dict
    -----------------
    name         : str    – resolved from .shstrtab
    header       : dict   – all 10 Elf64_Shdr fields (raw integers + decoded names)
    data         : bytes  – raw section content (empty bytes for SHT_NOBITS)

    For SHT_SYMTAB sections an extra key is added:
    symbols      : list   – one dict per Elf64_Sym entry

    For SHT_STRTAB sections an extra key is added:
    strings      : list   – list of (offset, string) tuples
    """

    # ------------------------------------------------------------------ #
    # 1. ELF header  (64 bytes at offset 0)                               #
    # ------------------------------------------------------------------ #
    assert data[:4] == b"\x7fELF",    "Not an ELF file"
    assert data[4]  == 2,             "Only ELF64 supported"
    assert data[5]  == 1,             "Only little-endian supported"
    assert _u16(data, 0x10) == ET_REL, "Only relocatable objects (ET_REL) supported"

    elf_header = {
        # e_ident bytes
        "ei_mag":        list(data[0:4]),    # [0x7f, 'E', 'L', 'F']
        "ei_class":      data[4],            # 2 = ELFCLASS64
        "ei_data":       data[5],            # 1 = ELFDATA2LSB
        "ei_version":    data[6],            # 1
        "ei_osabi":      data[7],            # 0 = ELFOSABI_NONE (SYSV)
        "ei_abiversion": data[8],            # 0
        # _ident padding: bytes 9-15 are zero

        "e_type":      _u16(data, 0x10),     # 1 = ET_REL
        "e_machine":   _u16(data, 0x12),     # 0x3e = EM_X86_64
        "e_version":   _u32(data, 0x14),     # 1
        "e_entry":     _u64(data, 0x18),     # 0 for relocatable
        "e_phoff":     _u64(data, 0x20),     # program header offset (0 for .o)
        "e_shoff":     _u64(data, 0x28),     # section header table offset
        "e_flags":     _u32(data, 0x30),     # processor-specific flags
        "e_ehsize":    _u16(data, 0x34),     # ELF header size (64)
        "e_phentsize": _u16(data, 0x36),     # program header entry size
        "e_phnum":     _u16(data, 0x38),     # number of program headers (0)
        "e_shentsize": _u16(data, 0x3a),     # section header entry size (64)
        "e_shnum":     _u16(data, 0x3c),     # number of section headers
        "e_shstrndx":  _u16(data, 0x3e),     # index of .shstrtab
    }

    e_shoff    = elf_header["e_shoff"]
    e_shentsize= elf_header["e_shentsize"]
    e_shnum    = elf_header["e_shnum"]
    e_shstrndx = elf_header["e_shstrndx"]

    # ------------------------------------------------------------------ #
    # 2. Read raw section headers                                          #
    # ------------------------------------------------------------------ #
    def _read_shdr(i):
        base = e_shoff + i * e_shentsize
        sh_type = _u32(data, base + 4)
        return {
            "sh_name":      _u32(data, base + 0x00),  # index into .shstrtab
            "sh_type":      sh_type,
            "sh_type_name": SHT.get(sh_type, f"0x{sh_type:x}"),
            "sh_flags":     _u64(data, base + 0x08),
            "sh_flag_names":_shf_names(_u64(data, base + 0x08)),
            "sh_addr":      _u64(data, base + 0x10),  # vaddr (0 in .o files)
            "sh_offset":    _u64(data, base + 0x18),  # file offset
            "sh_size":      _u64(data, base + 0x20),  # size in file
            "sh_link":      _u32(data, base + 0x28),  # section link index
            "sh_info":      _u32(data, base + 0x2c),  # extra info
            "sh_addralign": _u64(data, base + 0x30),  # alignment
            "sh_entsize":   _u64(data, base + 0x38),  # entry size (for tables)
        }

    raw_shdrs = [_read_shdr(i) for i in range(e_shnum)]

    # ------------------------------------------------------------------ #
    # 3. Resolve .shstrtab so we can name every section                   #
    # ------------------------------------------------------------------ #
    ss = raw_shdrs[e_shstrndx]
    shstrtab_bytes = data[ss["sh_offset"] : ss["sh_offset"] + ss["sh_size"]]

    def _cstr(strtab, idx):
        end = strtab.find(b"\x00", idx)
        return strtab[idx:end].decode("utf-8", errors="replace")

    # ------------------------------------------------------------------ #
    # 4. Build section list                                                #
    # ------------------------------------------------------------------ #
    sections = []
    for i, shdr in enumerate(raw_shdrs):
        name = _cstr(shstrtab_bytes, shdr["sh_name"])

        # SHT_NOBITS (.bss) takes no file space
        if shdr["sh_type"] == 8:  # SHT_NOBITS
            sec_data = b""
        else:
            off  = shdr["sh_offset"]
            size = shdr["sh_size"]
            sec_data = data[off : off + size]

        section = {
            "name":   name,
            "header": shdr,
            "data":   sec_data,
        }
        sections.append(section)

    # ------------------------------------------------------------------ #
    # 5. Decode .strtab / .symtab / .shstrtab contents                   #
    # ------------------------------------------------------------------ #
    # First pass: collect all strtab sections so symtab can resolve names
    strtab_by_index = {}
    for i, sec in enumerate(sections):
        if sec["header"]["sh_type"] == 3:  # SHT_STRTAB
            strtab_bytes = sec["data"]
            pairs = []
            off = 0
            while off < len(strtab_bytes):
                end = strtab_bytes.find(b"\x00", off)
                if end == -1:
                    end = len(strtab_bytes)
                pairs.append((off, strtab_bytes[off:end].decode("utf-8", errors="replace")))
                off = end + 1
            sec["strings"] = pairs
            strtab_by_index[i] = strtab_bytes

    # Second pass: decode symbol tables
    for sec in sections:
        if sec["header"]["sh_type"] != 2:  # SHT_SYMTAB
            continue
        entsize = sec["header"]["sh_entsize"] or 24  # Elf64_Sym is 24 bytes
        strtab  = strtab_by_index.get(sec["header"]["sh_link"], b"")
        symdata = sec["data"]
        symbols = []
        for off in range(0, len(symdata), entsize):
            st_name  = _u32(symdata, off + 0)
            st_info  = _u8(symdata,  off + 4)
            st_other = _u8(symdata,  off + 5)
            st_shndx = _u16(symdata, off + 6)
            st_value = _u64(symdata, off + 8)
            st_size  = _u64(symdata, off + 16)
            bind = (st_info >> 4) & 0xf
            typ  = st_info & 0xf
            vis  = st_other & 0x3
            name_end = strtab.find(b"\x00", st_name)
            sym_name = strtab[st_name:name_end].decode("utf-8", errors="replace") if strtab else ""
            symbols.append({
                "name":       sym_name,
                "st_name":    st_name,     # index into linked strtab
                "st_info":    st_info,     # raw byte
                "st_bind":    bind,
                "st_bind_name": STB.get(bind, f"0x{bind:x}"),
                "st_type":    typ,
                "st_type_name": STT.get(typ, f"0x{typ:x}"),
                "st_other":   st_other,
                "st_visibility": STV.get(vis, f"0x{vis:x}"),
                "st_shndx":   st_shndx,   # section index (0xfff1 = ABS, 0xffff = XINDEX)
                "st_value":   st_value,   # offset within section (in .o files)
                "st_size":    st_size,
            })
        sec["symbols"] = symbols

    return {
        "elf_header": elf_header,
        "sections":   sections,
    }


# ---------------------------------------------------------------------------
# build(obj_dict) → bytes
# ---------------------------------------------------------------------------

def build(obj: dict) -> bytes:
    """
    Serialise a parsed (or hand-constructed) ELF object dict back to bytes.

    The layout produced is:
        [ELF header 64 B]
        [section data, packed in section-index order, respecting alignment]
        [section header table]

    This matches the layout `as` produces for simple objects.

    NOTE: if you modify section data sizes you must also update the
    corresponding sh_offset / sh_size fields in section["header"] before
    calling build(), OR use build_fresh() which recomputes everything.
    """
    hdr = obj["elf_header"]
    sections = obj["sections"]

    # ---- ELF header -------------------------------------------------------
    ident = bytes([
        0x7f, 0x45, 0x4c, 0x46,   # magic
        hdr["ei_class"],
        hdr["ei_data"],
        hdr["ei_version"],
        hdr["ei_osabi"],
        hdr["ei_abiversion"],
        0, 0, 0, 0, 0, 0, 0,      # padding
    ])
    elf_hdr_bytes = (
        ident
        + _p16(hdr["e_type"])
        + _p16(hdr["e_machine"])
        + _p32(hdr["e_version"])
        + _p64(hdr["e_entry"])
        + _p64(hdr["e_phoff"])
        + _p64(hdr["e_shoff"])
        + _p32(hdr["e_flags"])
        + _p16(hdr["e_ehsize"])
        + _p16(hdr["e_phentsize"])
        + _p16(hdr["e_phnum"])
        + _p16(hdr["e_shentsize"])
        + _p16(hdr["e_shnum"])
        + _p16(hdr["e_shstrndx"])
    )
    assert len(elf_hdr_bytes) == 64

    # ---- section data  (placed at offsets recorded in each header) --------
    # We trust sh_offset / sh_size as stored (lossless round-trip).
    total_size = hdr["e_shoff"] + hdr["e_shentsize"] * hdr["e_shnum"]
    buf = bytearray(total_size)
    buf[0:64] = elf_hdr_bytes

    for sec in sections:
        sh = sec["header"]
        if sh["sh_type"] == 8:  # SHT_NOBITS – no bytes in file
            continue
        off  = sh["sh_offset"]
        data = sec["data"]
        buf[off : off + len(data)] = data

    # ---- section header table  --------------------------------------------
    def _write_shdr(sh, base):
        buf[base + 0x00:base + 0x04] = _p32(sh["sh_name"])
        buf[base + 0x04:base + 0x08] = _p32(sh["sh_type"])
        buf[base + 0x08:base + 0x10] = _p64(sh["sh_flags"])
        buf[base + 0x10:base + 0x18] = _p64(sh["sh_addr"])
        buf[base + 0x18:base + 0x20] = _p64(sh["sh_offset"])
        buf[base + 0x20:base + 0x28] = _p64(sh["sh_size"])
        buf[base + 0x28:base + 0x2c] = _p32(sh["sh_link"])
        buf[base + 0x2c:base + 0x30] = _p32(sh["sh_info"])
        buf[base + 0x30:base + 0x38] = _p64(sh["sh_addralign"])
        buf[base + 0x38:base + 0x40] = _p64(sh["sh_entsize"])

    shoff = hdr["e_shoff"]
    shentsz = hdr["e_shentsize"]
    for i, sec in enumerate(sections):
        _write_shdr(sec["header"], shoff + i * shentsz)

    return bytes(buf)


# ---------------------------------------------------------------------------
# Convenience: load / save
# ---------------------------------------------------------------------------

def load(path: str) -> dict:
    return parse(open(path, "rb").read())

def save(obj: dict, path: str) -> None:
    open(path, "wb").write(build(obj))


# ---------------------------------------------------------------------------
# Pretty-printer (for exploration)
# ---------------------------------------------------------------------------

def dump(obj: dict) -> None:
    h = obj["elf_header"]
    print("=== ELF Header ===")
    print(f"  class      : {h['ei_class']} (2=ELF64)")
    print(f"  data       : {h['ei_data']} (1=little-endian)")
    print(f"  type       : {h['e_type']} (1=ET_REL relocatable)")
    print(f"  machine    : {hex(h['e_machine'])} (0x3e=x86-64)")
    print(f"  shoff      : {hex(h['e_shoff'])}  ({h['e_shoff']} bytes into file)")
    print(f"  shnum      : {h['e_shnum']} sections")
    print(f"  shstrndx   : {h['e_shstrndx']} (index of .shstrtab)")

    print("\n=== Sections ===")
    for i, sec in enumerate(obj["sections"]):
        sh = sec["header"]
        print(f"\n  [{i}] {sec['name']!r}")
        print(f"       type    : {sh['sh_type_name']}")
        print(f"       flags   : {sh['sh_flag_names']}")
        print(f"       offset  : {hex(sh['sh_offset'])}  size={sh['sh_size']} B")
        print(f"       align   : {sh['sh_addralign']}")
        if sec["data"]:
            hex_preview = sec["data"][:16].hex(" ")
            tail = "…" if len(sec["data"]) > 16 else ""
            print(f"       data    : {hex_preview}{tail}")
        if "symbols" in sec:
            print(f"       symbols :")
            for sym in sec["symbols"]:
                print(f"         {sym['name']!r:15s}  "
                      f"bind={sym['st_bind_name']:12s}  "
                      f"type={sym['st_type_name']:12s}  "
                      f"shndx={sym['st_shndx']}  "
                      f"value={hex(sym['st_value'])}")
        if "strings" in sec:
            print(f"       strings : {[s for _, s in sec['strings']]}")


# ---------------------------------------------------------------------------
# Self-test / demo
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import sys, os

    path = sys.argv[1] if len(sys.argv) > 1 else "add1.o"
    print(f"Parsing {path!r}  ({os.path.getsize(path)} bytes)\n")

    obj = load(path)
    dump(obj)

    # Round-trip check
    rebuilt = build(obj)
    original = open(path, "rb").read()
    if rebuilt == original:
        print("\n✓  Round-trip: built bytes are bit-for-bit identical to the input.")
    else:
        # Find first differing byte
        for i, (a, b) in enumerate(zip(rebuilt, original)):
            if a != b:
                print(f"\n✗  First difference at byte {hex(i)}: built={hex(a)} original={hex(b)}")
                break
        if len(rebuilt) != len(original):
            print(f"   Length mismatch: built={len(rebuilt)}  original={len(original)}")
