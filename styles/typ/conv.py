#!/usr/bin/env python3

import sys
from pathlib import Path


# ============================================================================
# Configuration
# ============================================================================

SECTION_MAP = {
    "POI": "_point",
    "POINT": "_point",
    "POLYLINE": "_line",
    "LINE": "_line",
    "POLYGON": "_polygon",
}

DEBUG = True

# The .typ.prj file is fundamentally a byte-oriented file.
# Ordinary textual fields are decoded with Latin-1 because the file contains
# bytes which are not valid UTF-8.
TEXT_ENCODING = "latin-1"

# Output .typ.txt is UTF-8, while generated XPM colour/pixel characters are
# restricted to ASCII.
OUTPUT_ENCODING = "utf-8"


# ============================================================================
# Exceptions / diagnostics
# ============================================================================

class ConversionError(Exception):
    pass


def info(message):
    print(f"[INFO] {message}")


def warn(message):
    print(f"[WARN] {message}")


def fail(
    message,
    *,
    filename=None,
    section=None,
    key=None,
    raw=None,
):
    parts = ["ERROR"]

    if filename:
        parts.append(f"file={filename}")
    if section:
        parts.append(f"section={section}")
    if key:
        parts.append(f"key={key}")

    text = " | ".join(parts) + " | " + message

    if raw is not None:
        text += f" | raw={raw!r}"

    raise ConversionError(text)


# ============================================================================
# Binary input helpers
# ============================================================================

def strip_binary_newline(line):
    """
    Remove only the physical line ending.

    The rest of the bytes are preserved exactly.
    """
    if line.endswith(b"\r\n"):
        return line[:-2]
    if line.endswith(b"\n") or line.endswith(b"\r"):
        return line[:-1]
    return line


def split_key_value_binary(line):
    """
    Split a binary line at the first '='.

    Returns:
        (key_bytes, value_bytes)

    Both are still raw bytes.
    """
    if b"=" not in line:
        return line.strip(), b""

    key, value = line.split(b"=", 1)
    return key.strip(), value.strip()


def is_section_line(line):
    stripped = line.strip()

    return (
        stripped.startswith(b"[")
        and stripped.endswith(b"]")
        and len(stripped) >= 3
    )


def section_name(line):
    stripped = line.strip()
    return stripped[1:-1].decode(TEXT_ENCODING)


def is_comment(line):
    stripped = line.lstrip()
    return stripped.startswith(b";") or stripped.startswith(b"#")


def decode_text(value):
    """
    Decode ordinary TYP text as Latin-1.

    Latin-1 is deliberate: every byte 0x00..0xFF maps to exactly one Unicode
    code point, so no byte sequence can be lost or rejected.
    """
    return value.decode(TEXT_ENCODING)


def decode_ascii(value, *, filename, section, key):
    """
    Decode data which we expect to be ASCII after conversion.
    """
    try:
        return value.decode("ascii")
    except UnicodeDecodeError as exc:
        fail(
            f"Expected ASCII data but found byte 0x{value[exc.start]:02x}",
            filename=filename,
            section=section,
            key=key,
            raw=value,
        )


# ============================================================================
# Numeric helpers
# ============================================================================

def parse_int(value, default=0):
    value = value.strip()

    if not value:
        return default

    try:
        return int(value, 0)
    except ValueError:
        try:
            return int(value, 10)
        except ValueError:
            return default


def rgb(value):
    """
    Keep RGB values in the form expected by mkgmap.

    Examples:
        0xffffff
        0x123456
    """
    value = value.strip()

    if not value:
        return "0x000000"

    if value.lower().startswith("0x"):
        number = int(value, 16)
    else:
        number = int(value, 0)

    number &= 0xFFFFFF

    return f"0x{number:06x}"


# ============================================================================
# TYP property parsing
# ============================================================================

def parse_string(value):
    """
    Parse:

        String=4,sea

    into:

        (language, text)

    If no comma exists, preserve the whole value as text.
    """
    value = value.strip()

    if "," not in value:
        return None, value

    lang, text = value.split(",", 1)

    lang = lang.strip()
    text = text.strip()

    try:
        language = int(lang, 0)
    except ValueError:
        language = lang

    return language, text


# ============================================================================
# XPM character handling
# ============================================================================

def safe_xpm_characters():
    """
    Return printable ASCII characters suitable for generated XPM data.

    We deliberately exclude:

        space       - previously caused mkgmap "Tag ' '" errors
        double quote - awkward inside quoted XPM lines
        backslash   - awkward in escaped text
        0           - reserved by the TYP/mkgmap representation for
                      transparency

    More importantly, the generated Color key and its following bitmap
    character must both be safe ASCII characters.

    mkgmap's TYP bitmap convention observed in the source files is:

        Color=<key>,...
        bitmap=<key + 1>

    Therefore a colour key may only be selected if key+1 is also a safe
    printable ASCII character.
    """
    result = []

    for code in range(0x21, 0x7F):
        char = chr(code)

        # Reserved / inconvenient characters.
        if char in {" ", '"', "\\"}:
            continue

        # Never use ASCII '0' as a colour key because it is the transparent
        # bitmap pixel.
        if char == "0":
            continue

        next_code = code + 1

        # The bitmap character must also be printable ASCII and safe.
        if next_code > 0x7E:
            continue

        next_char = chr(next_code)

        if next_char in {" ", '"', "\\"}:
            continue

        if next_char == "0":
            continue

        result.append(char)

    return result


XPM_PALETTE_CHARS = safe_xpm_characters()


def make_xpm_palette(colours, *, filename, section, element_index):
    """
    Build an ASCII palette for the output XPM.

    `colours` is a list of:

        (source_color_key_byte, rgb_string)

    The generated Color key is ASCII.

    IMPORTANT:

        output Color=C,rgb

    corresponds to bitmap pixel:

        chr(ord(C) + 1)

    because that is the convention used by the Garmin/mkgmap TYP format.

    Transparency remains bitmap character '0' and is not represented as a
    source Color entry.
    """
    if len(colours) > len(XPM_PALETTE_CHARS):
        fail(
            (
                f"Too many colours ({len(colours)}) for the available "
                f"ASCII one-character XPM palette ({len(XPM_PALETTE_CHARS)}). "
                f"This element needs a multi-character XPM representation."
            ),
            filename=filename,
            section=section,
        )

    palette = {}

    for index, (source_key, colour_value) in enumerate(colours):
        output_key = XPM_PALETTE_CHARS[index]

        palette[source_key] = {
            "key": output_key,
            "pixel": chr(ord(output_key) + 1),
            "rgb": colour_value,
        }

    return palette


# ============================================================================
# Bitmap conversion
# ============================================================================

def convert_bitmap(
    rows,
    colours,
    *,
    filename,
    section,
    element_index,
):
    """
    Convert raw Garmin bitmap rows into ASCII mkgmap XPM rows.

    SOURCE CONVENTION
    -----------------

    The source uses:

        bitmap '0' = transparent

    and for colour pixels:

        bitmap byte = Color-key byte + 1

    Examples:

        Color=0,...  -> bitmap '1'
        Color=1,...  -> bitmap '2'
        Color=2,...  -> bitmap '3'

    Thus:

        b'33333331000013333333'

    means:

        '3' -> Color key '2'
        '1' -> Color key '0'
        '0' -> transparent

    The crucial point is that ASCII '0' is byte 0x30, NOT numeric byte 0.
    """
    info(
        f"  Converting bitmap: rows={len(rows)}, colors={len(colours)}"
    )

    palette = make_xpm_palette(
        colours,
        filename=filename,
        section=section,
        element_index=element_index,
    )

    # Map raw source Color-key byte -> generated ASCII bitmap character.
    source_to_output_pixel = {}

    for source_key, entry in palette.items():
        source_to_output_pixel[source_key] = entry["pixel"]

    output_rows = []

    for row_index, row in enumerate(rows):
        output = []

        for column_index, pixel_byte in enumerate(row):

            # ================================================================
            # IMPORTANT:
            #
            # ASCII '0' (0x30) means transparent.
            #
            # Do this BEFORE subtracting one.
            # ================================================================
            if pixel_byte == ord("0"):
                output.append("0")
                continue

            # Every non-transparent bitmap pixel is Color-key + 1.
            source_color_key = (pixel_byte - 1) & 0xFF

            if source_color_key not in source_to_output_pixel:
                fail(
                    (
                        "Bitmap references an undefined Color= entry. "
                        f"pixel_byte=0x{pixel_byte:02x}, "
                        f"derived_color_key=0x{source_color_key:02x}, "
                        f"row={row_index}, "
                        f"column={column_index}, "
                        f"row_length={len(row)}, "
                        f"defined_colours={len(colours)}"
                    ),
                    filename=filename,
                    section=section,
                    key="Line",
                    raw=row,
                )

            output.append(source_to_output_pixel[source_color_key])

        output_row = "".join(output)

        # Never silently pad or truncate bitmap rows.
        if len(output_row) != len(row):
            fail(
                (
                    "Internal bitmap conversion changed row length: "
                    f"source={len(row)}, output={len(output_row)}, "
                    f"row={row_index}"
                ),
                filename=filename,
                section=section,
                key="Line",
                raw=row,
            )

        output_rows.append(output_row)

    return palette, output_rows


# ============================================================================
# Property translation
# ============================================================================

def translate_property(key, value):
    """
    Translate ordinary TYPWiz properties to mkgmap syntax.

    Bitmap-specific Color= and Line= handling is performed separately.
    """
    key_text = decode_text(key).strip()
    value_text = decode_text(value).strip()

    # These are handled by the bitmap converter.
    if key_text in {"Color", "Line"}:
        return None

    # Keep String= values as text.
    if key_text == "String":
        language, text = parse_string(value_text)

        if language is None:
            return f"String={text}"

        return f"String={language},{text}"

    # Numeric properties which mkgmap accepts directly.
    return f"{key_text}={value_text}"


# ============================================================================
# Element parsing
# ============================================================================

def parse_element(
    lines,
    start_index,
    section,
    *,
    filename,
    element_index,
):
    """
    Parse one [POI], [POLYLINE], [POLYGON], etc. element.

    Returns:
        (element_dict, next_index)
    """
    properties = []
    colours = []
    bitmap_rows = []

    i = start_index

    while i < len(lines):
        raw_line = strip_binary_newline(lines[i])

        if is_section_line(raw_line):
            name = section_name(raw_line)

            if name == "END":
                i += 1
                break

        if not raw_line.strip():
            i += 1
            continue

        if is_comment(raw_line):
            i += 1
            continue

        key, value = split_key_value_binary(raw_line)

        if key == b"Color":
            # Empty Color= lines exist in the source and should not create
            # bogus palette entries.
            if not value:
                if DEBUG:
                    info(
                        f"  Ignoring empty Color= in element {element_index}"
                    )
                i += 1
                continue

            if b"," not in value:
                fail(
                    "Color= entry has no comma separator",
                    filename=filename,
                    section=section,
                    key="Color",
                    raw=value,
                )

            color_key, color_value = value.split(b",", 1)

            color_key = color_key.strip()
            color_value = color_value.strip()

            if len(color_key) != 1:
                fail(
                    (
                        "Color= key is not exactly one raw byte: "
                        f"length={len(color_key)}"
                    ),
                    filename=filename,
                    section=section,
                    key="Color",
                    raw=value,
                )

            try:
                color_rgb = rgb(decode_text(color_value))
            except Exception as exc:
                fail(
                    f"Invalid Color RGB value: {exc}",
                    filename=filename,
                    section=section,
                    key="Color",
                    raw=value,
                )

            colours.append((color_key[0], color_rgb))

        elif key == b"Line":
            # Keep bitmap rows completely raw.
            bitmap_rows.append(value)

        else:
            translated = translate_property(key, value)

            if translated is not None:
                properties.append(translated)

        i += 1

    element = {
        "section": section,
        "properties": properties,
        "colours": colours,
        "bitmap_rows": bitmap_rows,
    }

    return element, i


# ============================================================================
# Project parsing
# ============================================================================

def parse_project(data, *, filename):
    """
    Parse the entire .prj file from raw bytes.

    Returns:
        project properties
        elements
    """
    lines = data.splitlines(keepends=True)

    info(f"Input contains {len(lines)} binary lines")

    project_properties = []
    elements = []

    i = 0

    while i < len(lines):
        raw_line = strip_binary_newline(lines[i])

        if not raw_line.strip():
            i += 1
            continue

        if is_comment(raw_line):
            i += 1
            continue

        if not is_section_line(raw_line):
            i += 1
            continue

        section = section_name(raw_line)

        # ------------------------------------------------------------
        # Project section
        # ------------------------------------------------------------
        if section == "Project":
            i += 1

            while i < len(lines):
                raw = strip_binary_newline(lines[i])

                if is_section_line(raw):
                    name = section_name(raw)

                    if name == "END":
                        i += 1
                        break

                if raw.strip() and not is_comment(raw):
                    key, value = split_key_value_binary(raw)

                    translated = translate_property(key, value)

                    if translated is not None:
                        project_properties.append(translated)

                i += 1

            continue

        # ------------------------------------------------------------
        # Elements
        # ------------------------------------------------------------
        if section in SECTION_MAP:
            element, next_i = parse_element(
                lines,
                i + 1,
                section,
                filename=filename,
                element_index=len(elements) + 1,
            )

            elements.append(element)
            i = next_i
            continue

        i += 1

    return project_properties, elements


# ============================================================================
# Output
# ============================================================================

def emit_project(project_properties):
    output = []

    output.append("[_project]")

    for prop in project_properties:
        output.append(prop)

    output.append("[_end]")

    return output


def emit_element(
    element,
    *,
    filename,
    element_index,
):
    section = element["section"]
    output_section = SECTION_MAP[section]

    output = []

    output.append(f"[{output_section}]")

    for prop in element["properties"]:
        output.append(prop)

    colours = element["colours"]
    bitmap_rows = element["bitmap_rows"]

    if bitmap_rows:
        palette, converted_rows = convert_bitmap(
            bitmap_rows,
            colours,
            filename=filename,
            section=section,
            element_index=element_index,
        )

        # ------------------------------------------------------------
        # Emit Color= entries.
        #
        # The generated key is one character before the bitmap pixel.
        #
        # Example:
        #
        #     Color=A,0xffffff
        #
        # bitmap pixel:
        #
        #     B
        #
        # Transparency is always bitmap character '0'.
        # ------------------------------------------------------------
        for source_key, entry in palette.items():
            output.append(
                f"Color={entry['key']},{entry['rgb']}"
            )

        for row in converted_rows:
            output.append(f"Line={row}")

    else:
        # Elements without bitmap rows still retain their Color= definitions.
        #
        # Since there is no bitmap, these are emitted using the same safe
        # ASCII colour-key convention.
        if colours:
            palette = make_xpm_palette(
                colours,
                filename=filename,
                section=section,
                element_index=element_index,
            )

            for source_key, entry in palette.items():
                output.append(
                    f"Color={entry['key']},{entry['rgb']}"
                )

    output.append("[_end]")

    return output


# ============================================================================
# Main conversion
# ============================================================================

def convert(input_path, output_path):
    info("=" * 70)
    info("TYP conversion started")
    info(f"Input : {input_path}")
    info(f"Output: {output_path}")
    info("Input : BINARY")
    info(f"Text  : {TEXT_ENCODING.upper()}")
    info("XPM   : ASCII")
    info("=" * 70)

    # ------------------------------------------------------------------------
    # Read BINARY.
    #
    # Do NOT use read_text() and do NOT decode the whole file as UTF-8.
    # ------------------------------------------------------------------------
    info(f"Reading binary input: {input_path}")

    path = Path(input_path)
    data = path.read_bytes()

    info(f"Read {len(data)} bytes")

    # ------------------------------------------------------------------------
    # Parse.
    # ------------------------------------------------------------------------
    project_properties, elements = parse_project(
        data,
        filename=str(input_path),
    )

    info(f"Parsed {len(elements)} graphical elements")

    # ------------------------------------------------------------------------
    # Emit.
    # ------------------------------------------------------------------------
    output_lines = []

    output_lines.extend(
        emit_project(project_properties)
    )

    total = len(elements)

    for index, element in enumerate(elements, start=1):
        info(
            f"Emitting element {index}/{total} "
            f"[{element['section']}]"
        )

        output_lines.extend(
            emit_element(
                element,
                filename=str(input_path),
                element_index=index,
            )
        )

    output_text = "\n".join(output_lines) + "\n"

    # ------------------------------------------------------------------------
    # Write UTF-8 output.
    # ------------------------------------------------------------------------
    info(f"Writing UTF-8 output: {output_path}")

    Path(output_path).write_text(
        output_text,
        encoding=OUTPUT_ENCODING,
        newline="\n",
    )

    info("=" * 70)
    info("Conversion completed successfully")
    info(f"Output size: {len(output_text.encode(OUTPUT_ENCODING))} bytes")
    info("=" * 70)


# ============================================================================
# CLI
# ============================================================================

def main():
    if len(sys.argv) != 3:
        print(
            f"Usage: {sys.argv[0]} INPUT.typ.prj OUTPUT.typ.txt",
            file=sys.stderr,
        )
        sys.exit(2)

    input_path = sys.argv[1]
    output_path = sys.argv[2]

    try:
        convert(input_path, output_path)

    except ConversionError as exc:
        print(str(exc), file=sys.stderr)
        sys.exit(1)

    except Exception as exc:
        print(
            f"ERROR | unexpected exception: {type(exc).__name__}: {exc}",
            file=sys.stderr,
        )
        sys.exit(1)


if __name__ == "__main__":
    main()
