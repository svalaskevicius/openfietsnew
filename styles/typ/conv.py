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
# Binary input helpers
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
    Convert a TYP RGB value to #RRGGBB.
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
# XPM character handling
# ============================================================================

def xpm_character_set():
    """
    Safe printable ASCII characters.

    We exclude:
        space
        "
        \

    because they are inconvenient inside quoted XPM strings.

    '0' is also excluded because the source TYP format uses ASCII '0'
    as the transparent bitmap pixel.
    """
    chars = []

    for code in range(0x21, 0x7F):
        char = chr(code)

        if char in {" ", '"', "\\", "."}:
            continue

        chars.append(char)

    return chars


XPM_CHARS = xpm_character_set()


def available_xpm_tokens(cpp):
    """
    Number of possible tokens for the requested characters-per-pixel.
    """
    return len(XPM_CHARS) ** cpp


def generate_xpm_tokens(count, cpp, has_transparency):
    """
    Generate deterministic ASCII XPM tokens.

    cpp=1:
        !
        #
        $
        ...

    cpp=2:
        !!
        !#
        !$
        ...

    cpp=2 is used only when cpp=1 cannot represent all colours.
    """
    if cpp == 1:
        if count > len(XPM_CHARS):
            raise ConversionError(
                f"Cannot generate {count} one-character XPM tokens"
            )

        tokens = XPM_CHARS[:count]
        if has_transparency:
            tokens.append(".")
        return tokens

    if cpp == 2:
        tokens = []

        for first in XPM_CHARS:
            for second in XPM_CHARS:
                tokens.append(first + second)

                if len(tokens) >= count:
                    if has_transparency:
                        tokens.append("..")
                    return tokens

        raise ConversionError(
            f"Cannot generate {count} two-character XPM tokens"
        )

    raise ConversionError(
        f"Unsupported XPM cpp={cpp}"
    )


def choose_cpp(colour_count):
    """
    Use one character whenever possible.

    Only switch to two characters when necessary.
    """
    if colour_count <= available_xpm_tokens(1):
        return 1

    if colour_count <= available_xpm_tokens(2):
        return 2

    raise ConversionError(
        (
            f"Too many colours ({colour_count}) for XPM: "
            f"maximum with cpp=1 is {available_xpm_tokens(1)}, "
            f"maximum with cpp=2 is {available_xpm_tokens(2)}"
        )
    )


# ============================================================================
# Bitmap conversion
# ============================================================================

def build_source_palette(colours):
    """
    Convert the source Color= list into:

        raw colour-key byte -> RGB
    """
    palette = {}

    for key_byte, colour_rgb in colours:
        if key_byte in palette:
            warn(
                (
                    f"Duplicate Color= key 0x{key_byte:02x}; "
                    f"later definition replaces earlier definition"
                )
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
    Convert the binary TYPWiz bitmap into XPM.
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
    # Verify every source row has exactly the same width.
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
    # Find the actual colours referenced by the bitmap.
    # ------------------------------------------------------------------------
    referenced_keys = set()
    has_transparency = False

    for row_index, row in enumerate(rows):
        for column_index, pixel_byte in enumerate(row):

            source_key = pixel_byte & 0xFF

            if source_key not in source_palette:
                has_transparency = True

            referenced_keys.add(source_key)

    # ------------------------------------------------------------------------
    # Preserve the source Color= order.
    # ------------------------------------------------------------------------
    ordered_keys = []

    for key_byte, _ in colours:
        if (
            key_byte in referenced_keys
            and key_byte not in ordered_keys
        ):
            ordered_keys.append(key_byte)

    # ------------------------------------------------------------------------
    # Transparency is one XPM colour.
    # ------------------------------------------------------------------------
    vis_colour_count = len(ordered_keys)
    colour_count = vis_colour_count

    if has_transparency:
        colour_count += 1

    # ------------------------------------------------------------------------
    # IMPORTANT:
    #
    # cpp=1 unless it is actually necessary to use cpp=2.
    # ------------------------------------------------------------------------
    cpp = choose_cpp(colour_count)

    info(
        f"  XPM palette: colors={colour_count}, cpp={cpp}"
    )

    if cpp == 1:
        info(
            "  XPM palette fits in one character; "
            "using cpp=1"
        )
    else:
        info(
            "  XPM palette requires more than one character; "
            "using cpp=2"
        )

    # ------------------------------------------------------------------------
    # Generate palette tokens.
    # ------------------------------------------------------------------------
    tokens = generate_xpm_tokens(
        vis_colour_count,
        cpp,
        has_transparency
    )
    if has_transparency:
        transparent_token = tokens[vis_colour_count]

    print(tokens)
    token_index = 0
    print(colour_count)

    token_for_source_key = {}

    for source_key in ordered_keys:
        token_for_source_key[source_key] = tokens[token_index]
        token_index += 1

    # ------------------------------------------------------------------------
    # XPM colour definitions.
    # ------------------------------------------------------------------------
    colour_lines = []

    for source_key in ordered_keys:
        token = token_for_source_key[source_key]
        colour_rgb = source_palette[source_key]

        colour_lines.append(
            f'"{token} c {colour_rgb}"'
        )

    if has_transparency:
        colour_lines.append(
            f'"{transparent_token} c none"'
        )


    print(colour_lines)
    # ------------------------------------------------------------------------
    # Bitmap.
    # ------------------------------------------------------------------------
    bitmap_lines = []

    for row_index, row in enumerate(rows):
        output_row = []

        for column_index, pixel_byte in enumerate(row):
            source_key = pixel_byte & 0xFF

            token = token_for_source_key.get(source_key)

            if token is None:
                token = transparent_token

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
    # XPM header.
    # ------------------------------------------------------------------------
    xpm = []

    xpm.append(
        f'Xpm="{width} {len(rows)} {colour_count} {cpp}"'
    )

    xpm.extend(colour_lines)
    xpm.extend(bitmap_lines)

    return xpm


# ============================================================================
# Property translation
# ============================================================================

def translate_property(key, value):
    key_text = decode_text(key).strip()
    value_text = decode_text(value).strip()

    # These are converted into XPM separately.
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
        # Color=
        # --------------------------------------------------------------------
        if key == b"Color":

            # Empty Color= entries exist in the source.
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
        # Line=
        # --------------------------------------------------------------------
        elif key == b"Line":
            # Keep this completely binary.
            bitmap_rows.append(value)

        # --------------------------------------------------------------------
        # Ordinary property.
        # --------------------------------------------------------------------
        else:
            translated = translate_property(
                key,
                value,
            )

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
        # [Project]
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

    # Normal properties.
    for prop in element["properties"]:
        output.append(prop)

    # Bitmap.
    bitmap_rows = element["bitmap_rows"]

    if bitmap_rows:
        xpm = convert_bitmap_to_xpm(
            bitmap_rows,
            element["colours"],
            filename=filename,
            section=section,
            element_index=element_index,
        )

        # POI uses DayXpm.
        if output_section == "_point":
            output.append(
                xpm[0].replace(
                    "Xpm=",
                    "DayXpm=",
                    1,
                )
            )
        else:
            output.append(xpm[0])

        output.extend(xpm[1:])

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
    info("XPM   : ASCII")
    info("XPM   : cpp=1 unless cpp=2 is required")
    info("=" * 70)

    # ------------------------------------------------------------------------
    # Read as binary.
    # ------------------------------------------------------------------------
    info(
        f"Reading binary input: {input_path}"
    )

    data = Path(input_path).read_bytes()

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
    # Write UTF-8.
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
