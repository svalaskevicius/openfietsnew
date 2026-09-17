#!/usr/bin/env python3

import sys
import traceback
from pathlib import Path


SECTION_MAP = {
    "POI": "_point",
    "POINT": "_point",
    "POLYLINE": "_line",
    "LINE": "_line",
    "POLYGON": "_polygon",
}

DEBUG = True
TEXT_ENCODING = "latin-1"


# ============================================================
# Diagnostics
# ============================================================

class ConversionError(Exception):
    def __init__(
        self,
        message,
        *,
        filename=None,
        line_no=None,
        section=None,
        key=None,
        raw=None,
        offset=None,
    ):
        parts = ["ERROR"]

        if filename is not None:
            parts.append(f"file={filename}")

        if line_no is not None:
            parts.append(f"line={line_no}")

        if offset is not None:
            parts.append(f"offset={offset}")

        if section is not None:
            parts.append(f"section={section}")

        if key is not None:
            parts.append(f"key={key}")

        parts.append(message)

        if raw is not None:
            parts.append(f"raw={raw!r}")

        super().__init__(" | ".join(parts))


def info(message):
    if DEBUG:
        print(f"[INFO] {message}", file=sys.stderr)


def warn(message):
    print(f"[WARN] {message}", file=sys.stderr)


def fail(
    message,
    *,
    filename=None,
    line_no=None,
    section=None,
    key=None,
    raw=None,
    offset=None,
):
    raise ConversionError(
        message,
        filename=filename,
        line_no=line_no,
        section=section,
        key=key,
        raw=raw,
        offset=offset,
    )


# ============================================================
# Binary helpers
# ============================================================

def strip_binary_newline(data):
    if data.endswith(b"\r\n"):
        return data[:-2]

    if data.endswith(b"\n"):
        return data[:-1]

    if data.endswith(b"\r"):
        return data[:-1]

    return data


def split_key_value_binary(line):
    pos = line.find(b"=")

    if pos < 0:
        return None, None

    return line[:pos], line[pos + 1:]


def is_section_line(line):
    return (
        len(line) >= 2
        and line.startswith(b"[")
        and line.endswith(b"]")
    )


def is_comment(line):
    return line.startswith(b"#")


# ============================================================
# Text decoding
# ============================================================

def decode_text(
    data,
    *,
    filename,
    line_no,
    section,
    key,
    raw=None,
):
    """
    Ordinary TYP text is decoded as Latin-1.

    Latin-1 maps every byte 0x00-0xFF directly to a Unicode
    codepoint, so raw bytes such as 0xfc do not cause decoding
    failures.
    """

    try:
        return data.decode(TEXT_ENCODING)

    except Exception as e:
        fail(
            (
                f"{TEXT_ENCODING} text decode failed: "
                f"{type(e).__name__}: {e}"
            ),
            filename=filename,
            line_no=line_no,
            section=section,
            key=key,
            raw=raw if raw is not None else data,
        )


def decode_ascii(
    data,
    *,
    filename,
    line_no,
    section,
    key,
):
    try:
        return data.decode("ascii")

    except UnicodeDecodeError as e:
        bad = data[e.start] if e.start < len(data) else None

        fail(
            (
                "ASCII decode failed. "
                f"bad_byte="
                f"{('0x%02x' % bad) if bad is not None else 'EOF'}"
            ),
            filename=filename,
            line_no=line_no,
            section=section,
            key=key,
            raw=data,
        )


def section_name(
    line,
    filename,
    line_no,
):
    raw = line[1:-1]

    try:
        return raw.decode("ascii")

    except UnicodeDecodeError as e:
        bad = raw[e.start]

        fail(
            (
                "Section name contains a non-ASCII byte. "
                f"bad_byte=0x{bad:02x}"
            ),
            filename=filename,
            line_no=line_no,
            raw=line,
        )


# ============================================================
# Numeric helpers
# ============================================================

def parse_int(
    value,
    *,
    filename,
    line_no,
    section,
    key,
    raw,
):
    value = value.strip()

    text = decode_ascii(
        value,
        filename=filename,
        line_no=line_no,
        section=section,
        key=key,
    )

    try:
        return int(text, 0)

    except ValueError:
        fail(
            f"invalid integer value {text!r}",
            filename=filename,
            line_no=line_no,
            section=section,
            key=key,
            raw=raw,
        )


def rgb(
    value,
    *,
    filename,
    line_no,
    section,
    key,
    raw,
):
    value = value.strip()

    text = decode_ascii(
        value,
        filename=filename,
        line_no=line_no,
        section=section,
        key=key,
    )

    try:
        number = int(text, 0)

    except ValueError:
        fail(
            f"invalid RGB value {text!r}",
            filename=filename,
            line_no=line_no,
            section=section,
            key=key,
            raw=raw,
        )

    number &= 0xFFFFFF

    return f"0x{number:06x}"


# ============================================================
# String properties
# ============================================================

def parse_string(
    value,
    *,
    filename,
    line_no,
    section,
    key,
    raw,
):
    comma = value.find(b",")

    if comma < 0:
        fail(
            (
                "String property has no comma. "
                "Expected format such as String=4,sea"
            ),
            filename=filename,
            line_no=line_no,
            section=section,
            key=key,
            raw=raw,
        )

    number_part = value[:comma]
    text_part = value[comma + 1:]

    number = parse_int(
        number_part,
        filename=filename,
        line_no=line_no,
        section=section,
        key=key,
        raw=raw,
    )

    text = decode_text(
        text_part,
        filename=filename,
        line_no=line_no,
        section=section,
        key=key,
        raw=raw,
    )

    return number, text


# ============================================================
# XPM palette
# ============================================================

def palette_characters():
    """
    Printable ASCII characters.

    Space is reserved for transparent pixels.
    Quote is avoided because XPM uses quoted strings.
    """

    chars = []

    for number in range(33, 127):
        char = chr(number)

        if char == '"':
            continue

        chars.append(char)

    return chars


XPM_CHARS = palette_characters()


def make_xpm_palette(raw_color_keys):

    if len(raw_color_keys) > len(XPM_CHARS):
        fail(
            (
                "Too many colours for one-character ASCII XPM. "
                f"defined={len(raw_color_keys)}, "
                f"available={len(XPM_CHARS)}"
            )
        )

    mapping = {}

    for raw_key, xpm_char in zip(
        raw_color_keys,
        XPM_CHARS,
    ):
        mapping[raw_key] = xpm_char

    return mapping


# ============================================================
# Bitmap conversion
# ============================================================

def convert_bitmap(
    rows,
    color_keys,
    *,
    filename,
    section,
):
    """
    Source relationship:

        Color=N
        bitmap pixel=N+1

    Pixel 0 is transparent.

    Bitmap rows are not padded or truncated.
    """

    palette = make_xpm_palette(
        color_keys
    )

    converted = []

    for row_index, row in enumerate(
        rows,
        1,
    ):

        output = []

        for column, pixel in enumerate(
            row,
            1,
        ):

            if pixel == 0:
                output.append(" ")
                continue

            color_key = (
                pixel - 1
            ) & 0xFF

            if color_key not in palette:
                fail(
                    (
                        "Bitmap references an undefined "
                        "Color= entry. "
                        f"pixel_byte=0x{pixel:02x}, "
                        f"derived_color_key=0x{color_key:02x}, "
                        f"row={row_index}, "
                        f"column={column}, "
                        f"row_length={len(row)}, "
                        f"defined_colours={len(color_keys)}"
                    ),
                    filename=filename,
                    section=section,
                    key="Line",
                    raw=row,
                )

            output.append(
                palette[color_key]
            )

        converted.append(
            "".join(output)
        )

    return converted, palette


# ============================================================
# Property translation
# ============================================================

def translate_property(
    key,
    value,
    *,
    filename,
    line_no,
    section,
    raw,
):
    key_text = decode_ascii(
        key,
        filename=filename,
        line_no=line_no,
        section=section,
        key="property-name",
    )

    if key_text == "String":

        number, text = parse_string(
            value,
            filename=filename,
            line_no=line_no,
            section=section,
            key=key_text,
            raw=raw,
        )

        return f"String={number},{text}"

    if key_text in {
        "TextColor",
        "BorderColor",
        "BackgroundColor",
    }:

        value_text = rgb(
            value,
            filename=filename,
            line_no=line_no,
            section=section,
            key=key_text,
            raw=raw,
        )

        return f"{key_text}={value_text}"

    if key_text in {
        "Type",
        "TextSize",
        "FontSize",
    }:

        value_text = decode_ascii(
            value.strip(),
            filename=filename,
            line_no=line_no,
            section=section,
            key=key_text,
        )

        return f"{key_text}={value_text}"

    # Everything else is ordinary Latin-1 text.
    value_text = decode_text(
        value,
        filename=filename,
        line_no=line_no,
        section=section,
        key=key_text,
        raw=raw,
    )

    return f"{key_text}={value_text}"


# ============================================================
# Element parser
# ============================================================

def parse_element(
    lines,
    start_index,
    *,
    filename,
    section,
):
    element = {
        "section": section,
        "properties": [],
        "colors": [],
        "bitmap": [],
        "start_line": (
            lines[start_index - 1][0]
            if start_index > 0
            else 1
        ),
    }

    i = start_index

    while i < len(lines):

        line_no, raw = lines[i]

        # Blank
        if not raw:
            i += 1
            continue

        # Comment
        if is_comment(raw):
            info(
                f"Skipping comment at "
                f"{filename}:{line_no}: {raw!r}"
            )

            i += 1
            continue

        # End
        if raw == b"[END]":
            return element, i + 1

        # Property
        key, value = split_key_value_binary(raw)

        if key is None:
            fail(
                (
                    "Expected key=value, comment, blank line, "
                    "or [END]"
                ),
                filename=filename,
                line_no=line_no,
                section=section,
                raw=raw,
            )

        # ----------------------------------------------------
        # Color
        # ----------------------------------------------------

        if key == b"Color":

            if not value:
                warn(
                    f"{filename}:{line_no}: "
                    f"section={section}: "
                    "empty Color= ignored"
                )

                i += 1
                continue

            color_key = value[0]
            color_value = value[1:]

            if color_value.startswith(b","):
                color_value = color_value[1:]

            color_rgb = rgb(
                color_value,
                filename=filename,
                line_no=line_no,
                section=section,
                key="Color",
                raw=raw,
            )

            element["colors"].append(
                {
                    "key": color_key,
                    "rgb": color_rgb,
                    "line_no": line_no,
                    "raw": raw,
                }
            )

            info(
                f"  Color line {line_no}: "
                f"key=0x{color_key:02x}, "
                f"rgb={color_rgb}"
            )

            i += 1
            continue

        # ----------------------------------------------------
        # Line
        # ----------------------------------------------------

        if key == b"Line":

            element["bitmap"].append(
                {
                    "data": value,
                    "line_no": line_no,
                    "raw": raw,
                }
            )

            info(
                f"  Bitmap line {line_no}: "
                f"{len(value)} raw pixels"
            )

            i += 1
            continue

        # ----------------------------------------------------
        # Normal property
        # ----------------------------------------------------

        translated = translate_property(
            key,
            value,
            filename=filename,
            line_no=line_no,
            section=section,
            raw=raw,
        )

        element["properties"].append(
            {
                "text": translated,
                "line_no": line_no,
                "raw": raw,
            }
        )

        i += 1

    fail(
        "Reached end of file without finding [END]",
        filename=filename,
        section=section,
    )


# ============================================================
# Project parser
# ============================================================

def parse_project(filename):

    path = Path(filename)

    if not path.exists():
        fail(
            "input file does not exist",
            filename=filename,
        )

    info(
        f"Reading binary input: {path}"
    )

    try:
        data = path.read_bytes()

    except Exception as e:
        fail(
            (
                "failed to read input: "
                f"{type(e).__name__}: {e}"
            ),
            filename=filename,
        )

    info(
        f"Read {len(data)} bytes"
    )

    raw_lines = data.splitlines()

    info(
        f"Input contains {len(raw_lines)} binary lines"
    )

    lines = []

    for line_no, raw in enumerate(
        raw_lines,
        1,
    ):
        lines.append(
            (
                line_no,
                strip_binary_newline(raw),
            )
        )

    project = []

    i = 0

    while i < len(lines):

        line_no, raw = lines[i]

        # Blank
        if not raw:
            i += 1
            continue

        # Comment
        if is_comment(raw):
            info(
                f"Skipping top-level comment at "
                f"line {line_no}: {raw!r}"
            )

            i += 1
            continue

        # Project
        if raw == b"[Project]":

            info(
                f"Found [Project] at line {line_no}"
            )

            element, next_i = parse_element(
                lines,
                i + 1,
                filename=filename,
                section="Project",
            )

            project.append(element)

            i = next_i
            continue

        # Section
        if is_section_line(raw):

            section = section_name(
                raw,
                filename,
                line_no,
            )

            if section == "END":
                fail(
                    "Unexpected [END] at top level",
                    filename=filename,
                    line_no=line_no,
                    raw=raw,
                )

            if section not in SECTION_MAP:
                warn(
                    f"{filename}:{line_no}: "
                    f"unknown section [{section}]"
                )

            info(
                f"Found [{section}] at line {line_no}"
            )

            element, next_i = parse_element(
                lines,
                i + 1,
                filename=filename,
                section=section,
            )

            project.append(element)

            i = next_i
            continue

        fail(
            (
                "Unexpected top-level line. "
                "Expected comment, blank line, [Project], "
                "or an element section."
            ),
            filename=filename,
            line_no=line_no,
            raw=raw,
        )

    info(
        f"Successfully parsed {len(project)} elements"
    )

    return project


# ============================================================
# Output
# ============================================================

def emit_project(
    project,
    input_filename,
):
    output = []

    for element_number, element in enumerate(
        project,
        1,
    ):

        section = element["section"]

        info(
            f"Emitting element "
            f"{element_number}/{len(project)} "
            f"[{section}]"
        )

        # Section
        if section == "Project":
            output.append("[Project]")
        else:
            output.append(
                f"[{SECTION_MAP.get(section, section.lower())}]"
            )

        # Normal properties
        for property_item in element["properties"]:
            output.append(
                property_item["text"]
            )

        # Bitmap
        if element["bitmap"]:

            color_keys = [
                entry["key"]
                for entry in element["colors"]
            ]

            bitmap_rows = [
                entry["data"]
                for entry in element["bitmap"]
            ]

            info(
                f"  Converting bitmap: "
                f"rows={len(bitmap_rows)}, "
                f"colors={len(color_keys)}"
            )

            converted_rows, palette = convert_bitmap(
                bitmap_rows,
                color_keys,
                filename=input_filename,
                section=section,
            )

            # Color definitions
            for color_entry in element["colors"]:

                raw_key = color_entry["key"]

                xpm_char = palette[raw_key]

                output.append(
                    f"Color={xpm_char},"
                    f"{color_entry['rgb']}"
                )

            # Bitmap rows
            for bitmap_entry, row in zip(
                element["bitmap"],
                converted_rows,
            ):

                source_line = bitmap_entry["line_no"]

                info(
                    f"  Output Line from input line "
                    f"{source_line}: "
                    f"{len(row)} pixels"
                )

                output.append(
                    f"Line={row}"
                )

        output.append("[END]")
        output.append("")

    return "\n".join(output)


# ============================================================
# Conversion
# ============================================================

def convert(
    input_file,
    output_file,
):

    info("=" * 70)
    info("TYP conversion started")
    info(f"Input : {input_file}")
    info(f"Output: {output_file}")
    info("Input : BINARY")
    info("Text  : LATIN-1")
    info("XPM   : ASCII")
    info("=" * 70)

    project = parse_project(
        input_file
    )

    info(
        "Parsing complete"
    )

    info(
        "Starting output generation"
    )

    text = emit_project(
        project,
        input_file,
    )

    output_path = Path(
        output_file
    )

    try:
        output_path.write_text(
            text,
            encoding="utf-8",
            newline="\n",
        )

    except Exception as e:
        fail(
            (
                "failed to write UTF-8 output: "
                f"{type(e).__name__}: {e}"
            ),
            filename=output_file,
        )

    output_bytes = len(
        text.encode("utf-8")
    )

    info(
        f"Output written successfully: "
        f"{output_bytes} UTF-8 bytes"
    )

    info("=" * 70)
    info("CONVERSION COMPLETE")
    info("=" * 70)


# ============================================================
# Main
# ============================================================

def main():

    if len(sys.argv) != 3:

        print(
            "Usage:",
            file=sys.stderr,
        )

        print(
            "  python conv.py "
            "input.typ.prj output.typ.txt",
            file=sys.stderr,
        )

        sys.exit(2)

    input_file = sys.argv[1]
    output_file = sys.argv[2]

    try:

        convert(
            input_file,
            output_file,
        )

    except ConversionError as e:

        print(
            file=sys.stderr
        )

        print(
            str(e),
            file=sys.stderr
        )

        print(
            file=sys.stderr
        )

        print(
            "Conversion stopped.",
            file=sys.stderr,
        )

        print(
            "The output file must not be considered valid.",
            file=sys.stderr,
        )

        sys.exit(1)

    except Exception as e:

        print(
            file=sys.stderr
        )

        print(
            "ERROR | unexpected exception",
            file=sys.stderr,
        )

        print(
            f"ERROR | type={type(e).__name__}",
            file=sys.stderr,
        )

        print(
            f"ERROR | message={e}",
            file=sys.stderr,
        )

        print(
            file=sys.stderr
        )

        traceback.print_exc()

        sys.exit(1)


if __name__ == "__main__":
    main()
