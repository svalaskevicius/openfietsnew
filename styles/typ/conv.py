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

TEXT_ENCODING = "latin-1"
OUTPUT_ENCODING = "utf-8"

DEBUG = True


# ============================================================================
# Diagnostics
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
# Binary input
# ============================================================================

def strip_binary_newline(line):
    if line.endswith(b"\r\n"):
        return line[:-2]

    if line.endswith(b"\n"):
        return line[:-1]

    if line.endswith(b"\r"):
        return line[:-1]

    return line


def split_key_value_binary(line):
    if b"=" not in line:
        return line.strip(), b""

    key, value = line.split(b"=", 1)

    return key.strip(), value.strip()


def is_section_line(line):
    stripped = line.strip()

    return (
        len(stripped) >= 3
        and stripped.startswith(b"[")
        and stripped.endswith(b"]")
    )


def section_name(line):
    stripped = line.strip()

    return stripped[1:-1].decode(TEXT_ENCODING)


def is_comment(line):
    stripped = line.lstrip()

    return stripped.startswith(b";") or stripped.startswith(b"#")


def decode_text(value):
    return value.decode(TEXT_ENCODING)


# ============================================================================
# General parsing
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
    Convert a Garmin/TYP RGB value into XPM's #RRGGBB form.

    Input examples:

        0xffffff
        0x123456
        ffffff

    Output:

        #ffffff
        #123456
    """
    value = value.strip()

    if not value:
        return "#000000"

    try:
        if value.lower().startswith("0x"):
            number = int(value, 16)
        else:
            number = int(value, 16)
    except ValueError as exc:
        raise ConversionError(
            f"Invalid RGB value {value!r}: {exc}"
        )

    number &= 0xFFFFFF

    return f"#{number:06x}"


def parse_string(value):
    value = value.strip()

    if "," not in value:
        return None, value

    language, text = value.split(",", 1)

    language = language.strip()
    text = text.strip()

    try:
        language_value = int(language, 0)
    except ValueError:
        language_value = language

    return language_value, text


# ============================================================================
# XPM character generation
# ============================================================================

def xpm_character_set():
    """
    Safe printable ASCII characters for XPM tokens.

    XPM supports arbitrary character strings as colour identifiers.

    We avoid:

        space
        "
        \\

    because they complicate quoted XPM strings.

    We deliberately do NOT use the old one-character TYPWiz scheme here.

    With cpp=2, 87 characters gives:

        87 * 87 = 7569

    possible colour tokens.

    That is vastly more than the maximum needed by this converter.
    """
    chars = []

    for code in range(0x21, 0x7F):
        char = chr(code)

        if char in {" ", '"', "\\"}:
            continue

        chars.append(char)

    return chars


XPM_CHARS = xpm_character_set()


def generate_xpm_tokens(count, cpp=2):
    """
    Generate unique ASCII XPM colour tokens.

    For cpp=2 this produces:

        !!
        !#
        !$
        ...
        ~}

    etc., excluding unsafe characters.

    The generated tokens are deterministic.
    """
    if cpp != 2:
        raise ValueError("This converter currently generates cpp=2 XPM.")

    tokens = []

    for first in XPM_CHARS:
        for second in XPM_CHARS:
            tokens.append(first + second)

            if len(tokens) >= count:
                return tokens

    raise ConversionError(
        f"Unable to create {count} XPM tokens with cpp={cpp}"
    )


# ============================================================================
# Bitmap conversion
# ============================================================================

def build_source_palette(colours):
    """
    Build:

        source Color key byte -> RGB

    from:

        [(key_byte, rgb_string), ...]
    """
    palette = {}

    for key_byte, colour_rgb in colours:
        if key_byte in palette:
            warn(
                f"Duplicate Color= key 0x{key_byte:02x}; "
                f"later definition replaces earlier definition"
            )

        palette[key_byte] = colour_rgb

    return palette


def convert_bitmap_to_xpm(
    rows,
    colours,
    *,
    filename,
    section,
    element_index,
):
    """
    Convert the Garmin/TYPWiz bitmap into standard mkgmap XPM.

    IMPORTANT SOURCE FORMAT:

        ASCII '0' (0x30) = transparent

        bitmap byte = Color key byte + 1

    Examples:

        Color=0,... -> bitmap '1'
        Color=1,... -> bitmap '2'
        Color=2,... -> bitmap '3'

    Thus:

        b'33333331000013333333'

    means:

        3 -> Color key 2
        1 -> Color key 0
        0 -> transparent

    OUTPUT FORMAT:

        Xpm="width height colours cpp"

        "token c #RRGGBB"
        "token c none"

        "tokentokentoken..."
    """

    info(
        f"  Converting bitmap: "
        f"rows={len(rows)}, colors={len(colours)}"
    )

    if not rows:
        fail(
            "Bitmap contains no rows",
            filename=filename,
            section=section,
        )

    width = len(rows[0])

    if width == 0:
        fail(
            "Bitmap contains an empty first row",
            filename=filename,
            section=section,
        )

    # ------------------------------------------------------------------------
    # Verify that every row has exactly the same number of source pixels.
    #
    # DO NOT pad or truncate anything.
    # ------------------------------------------------------------------------
    for row_index, row in enumerate(rows):
        if len(row) != width:
            fail(
                (
                    "Bitmap row width mismatch: "
                    f"expected={width}, "
                    f"actual={len(row)}, "
                    f"row={row_index}"
                ),
                filename=filename,
                section=section,
                key="Line",
                raw=row,
            )

    source_palette = build_source_palette(colours)

    # ------------------------------------------------------------------------
    # Determine which source colours actually occur in the bitmap.
    #
    # This is useful because some TYPWiz files may contain Color= definitions
    # which aren't referenced by this particular bitmap.
    # ------------------------------------------------------------------------
    referenced_keys = set()
    has_transparency = False

    for row_index, row in enumerate(rows):
        for column_index, pixel_byte in enumerate(row):

            # ASCII '0' = transparent.
            if pixel_byte == ord("0"):
                has_transparency = True
                continue

            # Bitmap byte is Color key + 1.
            source_key = (pixel_byte - 1) & 0xFF

            if source_key not in source_palette:
                fail(
                    (
                        "Bitmap references an undefined Color= entry. "
                        f"pixel_byte=0x{pixel_byte:02x}, "
                        f"derived_color_key=0x{source_key:02x}, "
                        f"row={row_index}, "
                        f"column={column_index}, "
                        f"row_length={len(row)}, "
                        f"defined_colours={len(source_palette)}"
                    ),
                    filename=filename,
                    section=section,
                    key="Line",
                    raw=row,
                )

            referenced_keys.add(source_key)

    # ------------------------------------------------------------------------
    # Preserve Color= ordering from the original file.
    #
    # This makes the output easier to compare against the source.
    # ------------------------------------------------------------------------
    ordered_keys = []

    for key_byte, _ in colours:
        if key_byte in referenced_keys and key_byte not in ordered_keys:
            ordered_keys.append(key_byte)

    # ------------------------------------------------------------------------
    # Number of XPM colours.
    #
    # Transparent is a real XPM colour entry using:
    #
    #     c none
    #
    # when transparency exists.
    # ------------------------------------------------------------------------
    colour_count = len(ordered_keys)

    if has_transparency:
        colour_count += 1

    # cpp=2 is enough for 108 colours by a huge margin.
    cpp = 2

    tokens = generate_xpm_tokens(colour_count, cpp=cpp)

    token_index = 0
    token_for_source_key = {}

    # Reserve first token for transparency if necessary.
    transparent_token = None

    if has_transparency:
        transparent_token = tokens[token_index]
        token_index += 1

    # Assign one token to every actual source colour.
    for source_key in ordered_keys:
        token_for_source_key[source_key] = tokens[token_index]
        token_index += 1

    # ------------------------------------------------------------------------
    # Build XPM colour definitions.
    # ------------------------------------------------------------------------
    colour_lines = []

    if has_transparency:
        colour_lines.append(
            f'"{transparent_token} c none"'
        )

    for source_key in ordered_keys:
        token = token_for_source_key[source_key]
        colour_rgb = source_palette[source_key]

        colour_lines.append(
            f'"{token} c {colour_rgb}"'
        )

    # ------------------------------------------------------------------------
    # Convert each source bitmap pixel to a two-character XPM token.
    # ------------------------------------------------------------------------
    bitmap_lines = []

    for row_index, row in enumerate(rows):
        output_row = []

        for column_index, pixel_byte in enumerate(row):

            # ------------------------------------------------------------
            # CRITICAL:
            #
            # ASCII '0' is transparent.
            #
            # Do NOT subtract one from it.
            # ------------------------------------------------------------
            if pixel_byte == ord("0"):
                if transparent_token is None:
                    fail(
                        (
                            "Internal error: transparent pixel found "
                            "without transparent XPM token"
                        ),
                        filename=filename,
                        section=section,
                        key="Line",
                        raw=row,
                    )

                output_row.append(transparent_token)
                continue

            source_key = (pixel_byte - 1) & 0xFF

            token = token_for_source_key.get(source_key)

            if token is None:
                fail(
                    (
                        "Bitmap references a colour which was not assigned "
                        f"an XPM token: "
                        f"pixel_byte=0x{pixel_byte:02x}, "
                        f"source_color_key=0x{source_key:02x}, "
                        f"row={row_index}, "
                        f"column={column_index}"
                    ),
                    filename=filename,
                    section=section,
                    key="Line",
                    raw=row,
                )

            output_row.append(token)

        converted = "".join(output_row)

        expected_length = width * cpp

        if len(converted) != expected_length:
            fail(
                (
                    "Internal XPM row width mismatch: "
                    f"expected={expected_length}, "
                    f"actual={len(converted)}, "
                    f"source_width={width}, "
                    f"cpp={cpp}, "
                    f"row={row_index}"
                ),
                filename=filename,
                section=section,
                key="Line",
                raw=row,
            )

        bitmap_lines.append(
            f'"{converted}"'
        )

    # ------------------------------------------------------------------------
    # Assemble XPM.
    # ------------------------------------------------------------------------
    xpm = []

    xpm.append(
        f'Xpm="{width} {len(rows)} {colour_count} {cpp}"'
    )

    xpm.extend(colour_lines)
    xpm.extend(bitmap_lines)

    info(
        f"  XPM: width={width}, height={len(rows)}, "
        f"colors={colour_count}, cpp={cpp}"
    )

    if has_transparency:
        info(
            f"  XPM: transparency token={transparent_token!r}"
        )

    return xpm


# ============================================================================
# Property translation
# ============================================================================

def translate_property(key, value):
    """
    Translate normal TYPWiz properties.

    Color= and Line= are deliberately excluded because they are converted
    into standard XPM.
    """
    key_text = decode_text(key).strip()
    value_text = decode_text(value).strip()

    if key_text in {"Color", "Line"}:
        return None

    if key_text == "String":
        language, text = parse_string(value_text)

        if language is None:
            return f"String={text}"

        return f"String={language},{text}"

    return f"{key_text}={value_text}"


# ============================================================================
# Element parser
# ============================================================================

def parse_element(
    lines,
    start_index,
    section,
    *,
    filename,
    element_index,
):
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

        # --------------------------------------------------------------------
        # Raw Color= entry.
        # --------------------------------------------------------------------
        if key == b"Color":

            # Empty Color= entries occur in the source.
            if not value:
                if DEBUG:
                    info(
                        f"  Ignoring empty Color= "
                        f"in element {element_index}"
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
                        "Color= key must contain exactly one raw byte: "
                        f"length={len(color_key)}"
                    ),
                    filename=filename,
                    section=section,
                    key="Color",
                    raw=value,
                )

            try:
                colour_rgb = rgb(
                    decode_text(color_value)
                )
            except Exception as exc:
                fail(
                    f"Invalid Color= RGB value: {exc}",
                    filename=filename,
                    section=section,
                    key="Color",
                    raw=value,
                )

            colours.append(
                (color_key[0], colour_rgb)
            )

        # --------------------------------------------------------------------
        # Raw Line= bitmap row.
        # --------------------------------------------------------------------
        elif key == b"Line":
            bitmap_rows.append(value)

        # --------------------------------------------------------------------
        # Ordinary property.
        # --------------------------------------------------------------------
        else:
            translated = translate_property(key, value)

            if translated is not None:
                properties.append(translated)

        i += 1

    return {
        "section": section,
        "properties": properties,
        "colours": colours,
        "bitmap_rows": bitmap_rows,
    }, i


# ============================================================================
# Project parser
# ============================================================================

def parse_project(data, *, filename):
    lines = data.splitlines(keepends=True)

    info(
        f"Input contains {len(lines)} binary lines"
    )

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

        # ====================================================================
        # Project
        # ====================================================================
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

                    translated = translate_property(
                        key,
                        value,
                    )

                    if translated is not None:
                        project_properties.append(
                            translated
                        )

                i += 1

            continue

        # ====================================================================
        # Graphical element
        # ====================================================================
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

    output.append(
        f"[{output_section}]"
    )

    # ------------------------------------------------------------------------
    # Normal properties.
    # ------------------------------------------------------------------------
    for prop in element["properties"]:
        output.append(prop)

    # ------------------------------------------------------------------------
    # Bitmap -> XPM.
    # ------------------------------------------------------------------------
    bitmap_rows = element["bitmap_rows"]

    if bitmap_rows:

        xpm = convert_bitmap_to_xpm(
            bitmap_rows,
            element["colours"],
            filename=filename,
            section=section,
            element_index=element_index,
        )

        # mkgmap's standard syntax uses:
        #
        #   DayXpm
        #
        # for POIs.
        #
        # For lines/polygons it uses:
        #
        #   Xpm
        #
        if output_section == "_point":
            output.append(xpm[0].replace("Xpm=", "DayXpm=", 1))
        else:
            output.append(xpm[0])

        output.extend(xpm[1:])

    # ------------------------------------------------------------------------
    # No bitmap.
    #
    # Retain colours only as diagnostics/comments would be misleading;
    # Color=/Line= are part of the TYPWiz representation, not standard
    # mkgmap element properties.
    # ------------------------------------------------------------------------

    output.append("[_end]")

    return output


# ============================================================================
# Conversion
# ============================================================================

def convert(input_path, output_path):
    info("=" * 70)
    info("TYP conversion started")
    info(f"Input : {input_path}")
    info(f"Output: {output_path}")
    info("Input : BINARY")
    info(f"Text  : {TEXT_ENCODING.upper()}")
    info("XPM   : ASCII, cpp=2")
    info("=" * 70)

    # ------------------------------------------------------------------------
    # BINARY read.
    # ------------------------------------------------------------------------
    info(
        f"Reading binary input: {input_path}"
    )

    input_file = Path(input_path)

    data = input_file.read_bytes()

    info(
        f"Read {len(data)} bytes"
    )

    # ------------------------------------------------------------------------
    # Parse.
    # ------------------------------------------------------------------------
    project_properties, elements = parse_project(
        data,
        filename=str(input_path),
    )

    info(
        f"Parsed {len(elements)} graphical elements"
    )

    # ------------------------------------------------------------------------
    # Emit.
    # ------------------------------------------------------------------------
    output_lines = []

    output_lines.extend(
        emit_project(project_properties)
    )

    total = len(elements)

    for index, element in enumerate(
        elements,
        start=1,
    ):
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

    output_text = (
        "\n".join(output_lines)
        + "\n"
    )

    # ------------------------------------------------------------------------
    # UTF-8 output.
    # ------------------------------------------------------------------------
    info(
        f"Writing UTF-8 output: {output_path}"
    )

    Path(output_path).write_text(
        output_text,
        encoding=OUTPUT_ENCODING,
        newline="\n",
    )

    info("=" * 70)
    info("Conversion completed successfully")
    info(
        "Output size: "
        f"{len(output_text.encode(OUTPUT_ENCODING))} bytes"
    )
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
        convert(
            input_path,
            output_path,
        )

    except ConversionError as exc:
        print(
            str(exc),
            file=sys.stderr,
        )
        sys.exit(1)

    except Exception as exc:
        print(
            (
                "ERROR | unexpected exception: "
                f"{type(exc).__name__}: {exc}"
            ),
            file=sys.stderr,
        )
        sys.exit(1)


if __name__ == "__main__":
    main()
