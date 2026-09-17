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

    token_index = 0

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

    # Count palette colour definitions and how many are opaque (not "none"). mkgmap rejects a [_line]/[_polygon] whose only colours are transparent, so inject one unused opaque colour to let the section compile. Row data still references only transparency and therefore draws nothing (empty art).
    colour_defs = [l for l in xpm[1:] if len(l.strip().strip('"').split()) == 3]
    opaque_count = sum(1 for l in colour_defs if "none" not in l)

    if colour_defs and opaque_count == 0:
        xpm.insert(1, '"! c #000000"')
        parts = xpm[0][len('Xpm="'):].rstrip('"').split()
        w, h, n, cpp = int(parts[0]), int(parts[1]), int(parts[2]), parts[3]
        xpm[0] = f'Xpm="{w} {h} {n + 1} {cpp}"'

    return xpm


def solid_pattern_xpm(colours):
    """mkgmap's compact empty-pixmap form, used when an element has no bitmap art.

    TypCompiler requires a bare Xpm tag on every [_line] and [_polygon] section, so one
    without any source Line= art is given `Xpm="0 0 N 0"` plus its Colour(s).  For a
    [_line] the geometry comes from the already-emitted LineWidth/BorderWidth tags; for a
    [_polygon] it draws a solid fill (one colour, or day/night with two).  With one
    colour it is `Xpm="0 0 1 0"`; with two colours `Xpm="0 0 2 0"`.  Falls back to black
    if there is nothing else to use.
    """
    first = colours[0][1] if colours and colours[0][1] else "#000000"
    second = colours[1][1] if len(colours) > 1 and colours[1][1] else None

    xpm = []
    if second:
        xpm.append('Xpm="0 0 2 0"')
        xpm.append(f'"a c {first}"')
        xpm.append(f'"b c {second}"')
    else:
        xpm.append('Xpm="0 0 1 0"')
        xpm.append(f'"a c {first}"')

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
# Findings: mkgmap does NOT understand the legacy tags TYPWiz writes.
#
#   * TextSize=N            -> FontStyle=<enum>  (mkgmap has no "TextSize")
#       1 -> NoLabel,2 -> SmallFont   3 -> NormalFont   4 -> LargeFont
#   * TextColor=#RRGGBB     -> DayCustomColor / NightCustomColor
#           lightened toward white so labels stay readable against the device's
#           auto halo.  Verified against committed typ.txt:
#               day   = round(0.30*src + 0.70*255)
#               night = round(0.15*src + 0.85*255)
# ============================================================================

FONT_SIZE_MAP = {
    "1": "NoLabel",
    "2": "SmallFont",
    "3": "NormalFont",
    "4": "LargeFont",
}


def _to_rgb(value):
    """Accept #RRGGBB or 0xRRGGBB -> (r, g, b) ints."""
    value = value.strip()
    if value.startswith("#"):
        value = value[1:]
    elif value[:2].lower() == "0x":
        value = value[2:]
    return tuple(int(value[i:i + 2], 16) for i in (0, 2, 4))


def _rgb_to_hex(rgb):
    return "#%02x%02x%02x" % rgb


def lighten(hex_value, fraction):
    """Blend a colour toward white by `fraction` (0..1)."""
    r, g, b = _to_rgb(hex_value)
    out = lambda c: min(255, round(fraction * c + (1 - fraction) * 255))
    return (_rgb_to_hex((out(r), out(g), out(b))))


def apply_findings(properties):
    """Translate the two unsupported tags into their mkgmap equivalents.

    Returns a flat list of output lines, in original order: one FontStyle line
    replaces TextSize=N; Day/NightCustomColor replace TextColor=#hex.  Every
    other property passes through untouched.
    """
    out = []
    for prop in properties:
        if prop.startswith("TextSize="):
            size = prop.split("=", 1)[1].strip()
            font = FONT_SIZE_MAP.get(size, "SmallFont")
            out.append(f"FontStyle={font}")
        elif prop.startswith("TextColor="):
            hexval = prop.split("=", 1)[1].strip()
            out.append(f"DayCustomColor={lighten(hexval, 0.30)}")
            out.append(f"NightCustomColor={lighten(hexval, 0.15)}")
        else:
            out.append(prop)
    return out


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
    draw_order_level = None  # DrawOrder=N z-level for the [_drawOrder] section

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
        # DrawOrder=N -> mkgmap expects a [_drawOrder] section, not inline here.
        # Capture the level; object_type is resolved from the final Type= property
        # after all keys are collected (so key order does not matter).
        # --------------------------------------------------------------------
        elif key == b"DrawOrder":
            try:
                draw_order_level = int(decode_text(value).strip())
            except ValueError as exc:
                if DEBUG:
                    info(
                        f"  Ignoring non-numeric DrawOrder= "
                        f"in element {element_index}: {exc}"
                    )

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

    element_draw_order = None

    if section.upper() == "POLYGON":
        # Every polygon must appear in [_drawOrder]; default z-level 1 when it defines none.
        level = draw_order_level if draw_order_level is not None else 1
        object_type = next(
            (p.split("=", 1)[1].strip() for p in properties if p.startswith("Type=")),
            None,
        )
        element_draw_order = (object_type, level)

    return {
        "section": section,
        "properties": properties,
        "colours": colours,
        "bitmap_rows": bitmap_rows,
        "draw_order": element_draw_order,
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
        # Graphical element (binary names are Title-Case: POI/Polyline/Polygon)
        # ====================================================================
        canonical = section.upper()
        if canonical in SECTION_MAP:
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

def _family_id(project_properties):
    """Pull the numeric family id out of [Project] (or default 1)."""
    for prop in project_properties:
        if prop.startswith("FamilyID="):
            value = prop.split("=", 1)[1].strip()
            try:
                return int(value, 0)
            except ValueError:
                break
    return 1


def emit_project(project_properties):
    """mkgmap's identity block.

    The binary [Project] carries editor cruft (empty Product=, IMGpath to a
    stale machine...).  We only keep what TypCompiler needs and normalise it to
    the committed [_id] shape: CodePage / FID / ProductCode.
    """
    family_id = _family_id(project_properties)

    # mkgmap identity block. FID is a fixed small number; the project's
    # FamilyID becomes ProductCode (20181 here).  Matches committed typ.txt.
    output = [
        "[_id]",
        "CodePage=1252",
        "FID=1",
        f"ProductCode={family_id}",
        "[_end]",
    ]

    return output


def emit_element(
    element,
    *,
    filename,
    element_index,
):
    section = element["section"]
    # Binary names are Title-Case; SECTION_MAP keys are UPPER.
    canonical = section.upper()
    output_section = SECTION_MAP.get(canonical)
    if output_section is None:
        fail(
            f"Unsupported graphical type {section!r}",
            filename=filename,
            section=section,
        )

    # Apply findings: TextSize->FontStyle and TextColor->Day/Night colors.
    properties = apply_findings(element["properties"])

    output = []

    output.append(
        f"[{output_section}]"
    )

    # Normal (translated) properties.
    for prop in properties:
        output.append(prop)

    # Art handling differs by section type. POIs always carry DayXpm icon art, and
    # TypCompiler requires a bare Xpm tag on every [_line] and [_polygon] section.
    # A [_line]/[_polygon] with source Line= art keeps it verbatim (even a fully
    # transparent pattern: mkgmap simply draws nothing for it). One without any bitmap
    # is given mkgmap's compact empty-pixmap form -- Xpm="0 0 N 0" plus its Colour(s):
    # a solid line for [_line] (geometry from the already-emitted LineWidth/BorderWidth)
    # or a single/two-colour fill for [_polygon], so it always has a valid pattern and
    # never fails "No XPM tag in section".
    bitmap_rows = element["bitmap_rows"]

    if output_section == "_line":
        if bitmap_rows:
            xpm = convert_bitmap_to_xpm(
                bitmap_rows,
                element["colours"],
                filename=filename,
                section=section,
                element_index=element_index,
            )
        else:
            # No source bitmap art: emit mkgmap's compact solid-line form (uses width).
            xpm = solid_pattern_xpm(element["colours"])
    elif output_section == "_polygon":
        if not bitmap_rows:
            # No bitmap; a Colour= gives a solid fill, day/night with two colours.
            xpm = solid_pattern_xpm(element["colours"])
        else:
            xpm = convert_bitmap_to_xpm(
                bitmap_rows,
                element["colours"],
                filename=filename,
                section=section,
                element_index=element_index,
            )
    else:  # point (unchanged behaviour)
        xpm = convert_bitmap_to_xpm(
            bitmap_rows,
            element["colours"],
            filename=filename,
            section=section,
            element_index=element_index,
        )

    header = xpm[0]
    if output_section == "_point":
        header = header.replace("Xpm=", "DayXpm=", 1)
    output.append(header)
    output.extend(xpm[1:])

    output.append("[_end]")

    return output


# ============================================================================
# Draw order (polygon z-levels)
# ============================================================================

def build_draw_order(elements):
    """Collect mkgmap [_drawOrder] entries from parsed polygon elements.

    Each element carrying a captured draw_order yields one `Type=<object_type>,<level>`
    line, sorted by level then object type so higher levels render on top and the list
    is deterministic.  Returns [] when no polygons carry DrawOrder=.
    """
    entries = [e["draw_order"] for e in elements if e.get("draw_order")]

    lines = []

    for object_type, level in sorted(entries, key=lambda item: (item[1], item[0] or "")):
        if not object_type:
            continue
        lines.append(f"Type={object_type},{level}")

    return lines


# ============================================================================
# Conversion
# ============================================================================

def convert(
    input_path,
    output_path,
    *,
    include_line=False,
    include_polygon=False,
):
    """Phase control.

    Default (no flags) = PHASE 1: emit only [_point] sections, reproducing the
    committed typ/openfietsnew.typ.txt structure.

        --lines            also emit [_line] sections from [Polyline] entries
        --polygons         also emit [_polygon] sections from [Polygon] entries
        --all              both of the above (PHASE 2: full mkgmap structure)
    """
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

    # mkgmap requires every polygon type that should render to be listed here with its
    # z-level (DrawOrder).  Emit it before the [_polygon] sections so ordering is clear.
    if include_polygon:
        draw_order_lines = build_draw_order(elements)

        if draw_order_lines:
            output_lines.append("[_drawOrder]")
            output_lines.extend(draw_order_lines)
            output_lines.append("[_end]")

    total = len(elements)

    for index, element in enumerate(
        elements,
        start=1,
    ):
        # Phase control: points always; line/polygon opt-in.
        canonical = element["section"].upper()
        if canonical == "POLYLINE" and not include_line:
            continue
        if canonical == "POLYGON" and not include_polygon:
            continue

        info(
            f"Emitting element {index}/{total} "
            f"[{element['section']}]"
        )

        emitted = emit_element(
            element,
            filename=str(input_path),
            element_index=index,
        )

        if emitted is not None:
            output_lines.extend(emitted)

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
    if len(sys.argv) < 3:
        print(
            f"Usage: {sys.argv[0]} INPUT.typ.prj OUTPUT.typ.txt [--all|--lines|--polygons]",
            file=sys.stderr,
        )
        sys.exit(2)

    input_path = sys.argv[1]
    output_path = sys.argv[2]

    flags = set(sys.argv[3:])
    if "--all" in flags:
        include_line = include_polygon = True
    else:
        include_line = "--lines" in flags
        include_polygon = "--polygons" in flags

    try:
        convert(
            input_path,
            output_path,
            include_line=include_line,
            include_polygon=include_polygon,
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
