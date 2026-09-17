#!/usr/bin/env python3

"""
Convert TYPWiz .typ.prj files to mkgmap TYP compiler .txt files.

Usage:
    python3 conv.py input.typ.prj output.txt

The converter handles:

    [Project]
    [POI]
    [POLYLINE]
    [LINE]
    [POLYGON]

TYPWiz bitmap example:

    Color=0,0xffffff
    Color=1,0x000000
    Line=00111100
    Line=01100010

is converted to mkgmap XPM.

TYPWiz uses the characters in Line= as pixel indexes.

A literal space in a TYPWiz bitmap means background/transparent.
mkgmap requires that transparent pixels have a declared XPM colour,
so spaces are converted to '.' with:

    ". c none"
"""

import argparse
import re
import sys


SECTION_MAP = {
    "POI": "_point",
    "POINT": "_point",
    "POLYLINE": "_line",
    "LINE": "_line",
    "POLYGON": "_polygon",
}


# ----------------------------------------------------------------------
# Basic helpers
# ----------------------------------------------------------------------

def parse_int(value):
    value = value.strip()
    return int(value, 0)


def rgb(value):
    """
    Convert a TYPWiz RGB value to mkgmap #RRGGBB.

    Examples:

        0xffffff -> #FFFFFF
        0x242929 -> #242929
        none     -> none
    """
    value = value.strip()

    if value.lower() in ("none", "transparent"):
        return "none"

    n = int(value, 0)

    return "#{:06X}".format(n & 0xFFFFFF)


def split_key_value(line):
    if "=" not in line:
        return line.strip(), ""

    key, value = line.split("=", 1)

    return key.strip(), value.strip()


# ----------------------------------------------------------------------
# Strings
# ----------------------------------------------------------------------

def parse_string(value):
    """
    Convert:

        String=4,sea

    to:

        String=0x04,sea
    """

    if "," not in value:
        return value

    lang, text = value.split(",", 1)

    lang = lang.strip()

    try:
        n = int(lang, 0)

        return "0x{:02x},{}".format(n, text)

    except ValueError:
        return value


# ----------------------------------------------------------------------
# XPM
# ----------------------------------------------------------------------

def safe_xpm_characters(n):
    """
    Return printable one-character XPM symbols.

    We deliberately avoid:

        "
        \\

    because they would need escaping in the generated text file.

    '.' is also reserved for our transparent pixel.
    """

    chars = []

    for c in (
        "0123456789"
        "abcdefghijklmnopqrstuvwxyz"
        "ABCDEFGHIJKLMNOPQRSTUVWXYZ"
        "!#$%&()*+,-/:;<=>?@[]^_{}|~"
    ):
        if c in ('"', '\\', '.'):
            continue

        chars.append(c)

    if n > len(chars):
        raise ValueError(
            "Too many colours ({}) for one-character XPM".format(n)
        )

    return chars[:n]


# ----------------------------------------------------------------------
# Colour parsing
# ----------------------------------------------------------------------

def parse_colors(properties):
    """
    Parse TYPWiz Color= entries.

    Example:

        Color=0,0xffffff
        Color=1,0x393000
        Color=2,0x7b6500

    Returns:

        [
            (0, "#FFFFFF"),
            (1, "#393000"),
            (2, "#7B6500"),
        ]

    The list is sorted by numeric colour index.
    """

    colors = []

    for key, value in properties:

        kl = key.strip().lower()

        if kl != "color":
            continue

        if "," not in value:
            continue

        index_text, colour_text = value.split(",", 1)

        index_text = index_text.strip()
        colour_text = colour_text.strip()

        try:
            index = int(index_text, 0)
        except ValueError:
            continue

        try:
            colour = rgb(colour_text)
        except ValueError:
            raise ValueError(
                "Invalid Color value: {!r}".format(value)
            )

        colors.append((index, colour))

    # Remove duplicate indexes while retaining the last definition.
    by_index = {}

    for index, colour in colors:
        by_index[index] = colour

    colors = sorted(by_index.items())

    return colors


def get_lines(properties):
    """
    Get all Line= bitmap rows.
    """

    return [
        value
        for key, value in properties
        if key.strip().lower() == "line"
    ]


def bitmap_indexes(lines):
    """
    Return the set of pixel characters actually used by the bitmap.

    Spaces are deliberately excluded because TYPWiz uses them as
    transparent/background pixels.
    """

    used = set()

    for row in lines:
        for pixel in row:
            if pixel != " ":
                used.add(pixel)

    return used


def make_xpm(lines, colors, point=False):
    """
    Convert a TYPWiz bitmap into mkgmap XPM.

    TYPWiz:

        Color=0,...
        Color=1,...
        Color=2,...

        Line=001122
        Line=011220

    becomes something like:

        6 2 4 1
        "0 c #FFFFFF"
        "1 c #393000"
        "2 c #7B6500"
        ". c none"
        "001122"
        "011220"

    The '.' pixel is transparent.
    """

    if not lines:
        return None

    # --------------------------------------------------------------
    # Normalize bitmap rows.
    # --------------------------------------------------------------

    height = len(lines)

    width = max(len(row) for row in lines)

    rows = [
        row.ljust(width)
        for row in lines
    ]

    # --------------------------------------------------------------
    # Build colour-index lookup.
    # --------------------------------------------------------------

    colour_by_index = {}

    for index, colour in colors:
        colour_by_index[str(index)] = colour

    # --------------------------------------------------------------
    # Find actual bitmap indexes.
    # --------------------------------------------------------------

    used = bitmap_indexes(rows)

    defined = set(colour_by_index.keys())

    missing = sorted(
        used - defined,
        key=lambda x: (
            0,
            int(x)
        ) if x.isdigit() else (
            1,
            x
        )
    )

    if missing:
        raise ValueError(
            "Bitmap contains colour index(es) with no Color= "
            "definition: {}. Defined Color= indexes: {}. "
            "Bitmap indexes: {}".format(
                ", ".join(repr(x) for x in missing),
                ", ".join(
                    str(index)
                    for index, colour in colors
                ) or "(none)",
                ", ".join(
                    repr(x)
                    for x in sorted(used)
                ) or "(none)",
            )
        )

    # --------------------------------------------------------------
    # Assign XPM symbols.
    #
    # Keep the TYPWiz colour indexes associated with their colour,
    # but XPM itself uses arbitrary one-character symbols.
    # --------------------------------------------------------------

    symbols = safe_xpm_characters(len(colors))

    colour_map = {}

    for symbol, (index, colour) in zip(symbols, colors):
        colour_map[str(index)] = symbol

    # '.' is reserved for transparency.
    transparent = "."

    # --------------------------------------------------------------
    # Convert bitmap rows.
    # --------------------------------------------------------------

    converted_rows = []

    for row in rows:

        out = []

        for pixel in row:

            # TYPWiz literal space = transparent/background.
            if pixel == " ":
                out.append(transparent)

            elif pixel in colour_map:
                out.append(colour_map[pixel])

            else:
                # This should already have been caught above.
                raise ValueError(
                    "Bitmap contains unknown colour index: {!r}".format(
                        pixel
                    )
                )

        converted_rows.append("".join(out))

    # --------------------------------------------------------------
    # XPM header.
    #
    # Add one colour for transparency.
    # --------------------------------------------------------------

    colour_count = len(colors) + 1

    header = "{} {} {} 1".format(
        width,
        height,
        colour_count,
    )

    result = [header]

    # --------------------------------------------------------------
    # Normal colours.
    # --------------------------------------------------------------

    for symbol, (index, colour) in zip(symbols, colors):

        result.append(
            '"{} c {}"'.format(
                symbol,
                colour,
            )
        )

    # --------------------------------------------------------------
    # Transparent colour.
    #
    # mkgmap documents 'none' as the transparent colour.
    # --------------------------------------------------------------

    result.append(
        '". c none"'
    )

    # --------------------------------------------------------------
    # Bitmap rows.
    # --------------------------------------------------------------

    for row in converted_rows:

        result.append(
            '"{}"'.format(row)
        )

    return result


# ----------------------------------------------------------------------
# Ordinary property translation
# ----------------------------------------------------------------------

def translate_property(key, value, section):
    """
    Translate ordinary TYPWiz fields to mkgmap fields.
    """

    kl = key.strip().lower()

    # --------------------------------------------------------------
    # Type
    # --------------------------------------------------------------

    if kl == "type":

        return "Type={}".format(
            value
        )

    # --------------------------------------------------------------
    # String
    # --------------------------------------------------------------

    if kl == "string":

        return "String={}".format(
            parse_string(value)
        )

    # String1, String2, String3, ...
    if kl.startswith("string") and kl[6:].isdigit():

        return "String{}={}".format(
            key[6:],
            parse_string(value),
        )

    # --------------------------------------------------------------
    # TextSize
    # --------------------------------------------------------------

    if kl == "textsize":

        mapping = {
            "0": "NoLabel",
            "1": "SmallFont",
            "2": "NormalFont",
            "3": "LargeFont",
        }

        return "FontStyle={}".format(
            mapping.get(
                value.strip(),
                "SmallFont",
            )
        )

    # --------------------------------------------------------------
    # TextColor
    # --------------------------------------------------------------

    if kl == "textcolor":

        return "DayCustomColor={}".format(
            rgb(value)
        )

    # --------------------------------------------------------------
    # LineWidth
    # --------------------------------------------------------------

    if kl == "linewidth":

        return "LineWidth={}".format(
            value
        )

    # --------------------------------------------------------------
    # BorderWidth
    # --------------------------------------------------------------

    if kl == "borderwidth":

        return "BorderWidth={}".format(
            value
        )

    # --------------------------------------------------------------
    # UseOrientation
    # --------------------------------------------------------------

    if kl == "useorientation":

        return "UseOrientation={}".format(
            value
        )

    # Unsupported property.
    return None


# ----------------------------------------------------------------------
# Project parser
# ----------------------------------------------------------------------

def parse_project(lines):
    """
    Parse the TYPWiz project.

    Returns:

        [
            ("Project", [(key, value), ...]),
            ("POI",     [(key, value), ...]),
            ...
        ]
    """

    sections = []

    current_name = None
    current_properties = None

    for raw in lines:

        line = raw.rstrip("\r\n")

        stripped = line.strip()

        # Empty line.
        if not stripped:
            continue

        # Comment.
        if stripped.startswith("#"):
            continue

        # ----------------------------------------------------------
        # [END]
        # ----------------------------------------------------------

        if stripped.upper() == "[END]":

            if current_name is not None:

                sections.append(
                    (
                        current_name,
                        current_properties,
                    )
                )

                current_name = None
                current_properties = None

            continue

        # ----------------------------------------------------------
        # [Section]
        # ----------------------------------------------------------

        match = re.match(
            r"^\[([^\]]+)\]$",
            stripped,
        )

        if match:

            current_name = match.group(1)
            current_properties = []

            continue

        # Ignore anything before the first section.
        if current_name is None:
            continue

        # ----------------------------------------------------------
        # key=value
        # ----------------------------------------------------------

        key, value = split_key_value(line)

        if key:

            current_properties.append(
                (
                    key,
                    value,
                )
            )

    # Be tolerant of files without a final [END].
    if current_name is not None:

        sections.append(
            (
                current_name,
                current_properties,
            )
        )

    return sections


# ----------------------------------------------------------------------
# Project output
# ----------------------------------------------------------------------

def emit_project(properties, out):

    family = None
    product = None
    codepage = 1252

    for key, value in properties:

        kl = key.strip().lower()

        if kl == "familyid":

            family = parse_int(value)

        elif kl == "productcode":

            product = parse_int(value)

        elif kl == "codepage":

            codepage = parse_int(value)

    # TYPWiz projects commonly omit ProductCode.
    if product is None:
        product = 1

    if family is not None:

        out.append("[_id]")

        out.append(
            "FID={}".format(family)
        )

        out.append(
            "ProductCode={}".format(product)
        )

        out.append(
            "CodePage={}".format(codepage)
        )

        out.append("[end]")

        out.append("")


# ----------------------------------------------------------------------
# Element output
# ----------------------------------------------------------------------

def emit_element(name, properties, out):

    section = SECTION_MAP.get(
        name.upper()
    )

    if section is None:
        return

    # --------------------------------------------------------------
    # Read colours and bitmap.
    # --------------------------------------------------------------

    colors = parse_colors(properties)

    lines = get_lines(properties)

    # --------------------------------------------------------------
    # Start section.
    # --------------------------------------------------------------

    out.append(
        "[{}]".format(section)
    )

    # --------------------------------------------------------------
    # Ordinary properties.
    # --------------------------------------------------------------

    for key, value in properties:

        kl = key.strip().lower()

        # These are handled separately as bitmap data.
        if kl in (
            "color",
            "line",
        ):
            continue

        translated = translate_property(
            key,
            value,
            section,
        )

        if translated:

            out.append(
                translated
            )

    # --------------------------------------------------------------
    # TYPWiz bitmap -> mkgmap XPM.
    # --------------------------------------------------------------

    if lines:

        xpm = make_xpm(
            lines,
            colors,
            point=(
                section == "_point"
            ),
        )

        if xpm:

            if section == "_point":

                out.append(
                    'DayXpm="{}"'.format(
                        xpm[0]
                    )
                )

            else:

                out.append(
                    'Xpm="{}"'.format(
                        xpm[0]
                    )
                )

            out.extend(
                xpm[1:]
            )

    # --------------------------------------------------------------
    # End section.
    # --------------------------------------------------------------

    out.append(
        "[end]"
    )

    out.append("")


# ----------------------------------------------------------------------
# Conversion
# ----------------------------------------------------------------------

def convert(filename):

    with open(
        filename,
        "r",
        encoding="latin-1",
    ) as f:

        sections = parse_project(
            f.readlines()
        )

    output = []

    for name, properties in sections:

        # ----------------------------------------------------------
        # Project
        # ----------------------------------------------------------

        if name.lower() == "project":

            emit_project(
                properties,
                output,
            )

        # ----------------------------------------------------------
        # Elements
        # ----------------------------------------------------------

        elif name.upper() in SECTION_MAP:

            emit_element(
                name,
                properties,
                output,
            )

        # ----------------------------------------------------------
        # Unsupported section
        # ----------------------------------------------------------

        else:

            print(
                "warning: ignoring unsupported section [{}]".format(
                    name
                ),
                file=sys.stderr,
            )

    return "\n".join(output)


# ----------------------------------------------------------------------
# Main
# ----------------------------------------------------------------------

def main():

    parser = argparse.ArgumentParser(
        description=(
            "Convert TYPWiz .typ.prj "
            "to mkgmap TYP compiler text."
        )
    )

    parser.add_argument(
        "input",
        help="TYPWiz .typ.prj file",
    )

    parser.add_argument(
        "output",
        help="mkgmap TYP .txt file",
    )

    args = parser.parse_args()

    try:

        result = convert(
            args.input
        )

        with open(
            args.output,
            "w",
            encoding="utf-8",
            newline="\n",
        ) as f:

            f.write(result)

    except Exception as e:

        print(
            "error: {}".format(e),
            file=sys.stderr,
        )

        sys.exit(1)


if __name__ == "__main__":
    main()
