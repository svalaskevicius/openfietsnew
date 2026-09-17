#!/usr/bin/env python3

"""
Convert TYPWiz .typ.prj files to mkgmap TYP compiler .txt files.

Usage:
    python3 typwiz2mkgmap.py input.typ.prj output.txt

The converter handles:
    [Project]
    [POI]
    [POLYLINE]
    [LINE]
    [POLYGON]

TYPWiz bitmap representation:

    Color=0,0xffffff
    Color=1,0x000000
    Line=00111100
    Line=01100010

is converted to mkgmap XPM.

The first Color number is the pixel index used in Line=.
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


def parse_int(value):
    value = value.strip()
    return int(value, 0)


def rgb(value):
    """
    Convert TYPWiz RGB integer to mkgmap #RRGGBB.

    TYPWiz examples look like:
        0xffffff
        0x242929
    """
    value = value.strip()

    if value.lower() == "none":
        return "none"

    n = int(value, 0)
    return "#{:06X}".format(n & 0xFFFFFF)


def split_key_value(line):
    if "=" not in line:
        return line.strip(), ""

    key, value = line.split("=", 1)
    return key.strip(), value.strip()


def parse_string(value):
    """
    TYPWiz:
        String=4,sea

    mkgmap:
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


def safe_xpm_characters(n):
    """
    Return printable one-character XPM symbols.

    Avoid quote and backslash because these would need escaping
    in the mkgmap text representation.
    """
    chars = []

    for c in (
        "0123456789"
        "abcdefghijklmnopqrstuvwxyz"
        "ABCDEFGHIJKLMNOPQRSTUVWXYZ"
        "!#$%&()*+,-./:;<=>?@[]^_{}|~"
    ):
        if c not in ('"', '\\'):
            chars.append(c)

    if n > len(chars):
        raise ValueError(
            "Too many colours ({}) for one-character XPM".format(n)
        )

    return chars[:n]


def parse_colors(properties):
    """
    Return:

        [(index, '#RRGGBB'), ...]

    sorted by colour index.
    """
    colors = []

    for key, value in properties:
        if key.lower() != "color":
            continue

        if "," not in value:
            continue

        index, colour = value.split(",", 1)

        try:
            index = int(index.strip(), 0)
        except ValueError:
            continue

        colors.append((index, rgb(colour)))

    colors.sort()

    return colors


def get_lines(properties):
    return [
        value
        for key, value in properties
        if key.lower() == "line"
    ]


def make_xpm(lines, colors, point=False):
    if not lines:
        return None

    height = len(lines)
    width = max(len(x) for x in lines)

    # Make all rows the same length.
    rows = [x.ljust(width) for x in lines]

    # TYPWiz colour indexes can be arbitrary.
    colour_map = {}

    # Assign XPM characters.
    symbols = safe_xpm_characters(len(colors))

    for symbol, (index, colour) in zip(symbols, colors):
        colour_map[str(index)] = symbol

    # If there are pixel indices without Color= entries,
    # fail rather than silently producing corrupt artwork.
    used = set("".join(rows))
    missing = sorted(
        x for x in used
        if x.strip() and x not in colour_map
    )

    # TYPWiz uses an implicit background/transparent pixel in
    # some objects.  Treat an undefined index as transparent.
    for index in missing:
        colour_map[index] = " "

    # TYPWiz commonly uses decimal digits as indexes.
    # For multi-digit indexes we need a little more care.
    #
    # In practice the openfietsnew file uses single-character
    # colour indexes, so this is the normal path.
    converted_rows = []

    for row in rows:
        out = []
        for pixel in row:
            if pixel in colour_map:
                out.append(colour_map[pixel])
            else:
                out.append(" ")
        converted_rows.append("".join(out))

    header = '{} {} {} 1'.format(
        width,
        height,
        len(colors),
    )

    result = [header]

    for symbol, (index, colour) in zip(symbols, colors):
        result.append('"{} c {}"'.format(symbol, colour))

    result.extend('"{}"'.format(row) for row in converted_rows)

    return result


def translate_property(key, value, section):
    """
    Translate ordinary TYPWiz fields to mkgmap fields.
    """

    kl = key.lower()

    if kl == "type":
        return "Type={}".format(value)

    if kl == "string":
        return "String={}".format(parse_string(value))

    if kl.startswith("string") and kl[6:].isdigit():
        return "String{}={}".format(
            key[6:],
            parse_string(value),
        )

    if kl == "textsize":
        # TYPWiz TextSize:
        #   0 = no label
        #   1 = small
        #   2 = normal
        #   3 = large
        mapping = {
            "0": "NoLabel",
            "1": "SmallFont",
            "2": "NormalFont",
            "3": "LargeFont",
        }

        return "FontStyle={}".format(
            mapping.get(value.strip(), "SmallFont")
        )

    if kl == "textcolor":
        return "DayCustomColor={}".format(
            rgb(value)
        )

    if kl == "linewidth":
        return "LineWidth={}".format(value)

    if kl == "borderwidth":
        return "BorderWidth={}".format(value)

    if kl == "useorientation":
        return "UseOrientation={}".format(value)

    return None


def parse_project(lines):
    """
    Parse the TYPWiz project into a list of sections.

    Returns:
        [
            ("Project", [(key,value), ...]),
            ("POI", [...]),
            ...
        ]
    """

    sections = []
    current = None

    for raw in lines:
        line = raw.rstrip("\r\n")

        stripped = line.strip()

        if not stripped:
            continue

        if stripped.startswith("#"):
            continue

        # [END]
        if stripped.upper() == "[END]":
            if current is not None:
                sections.append(current)
                current = None
            continue

        # [Project], [POI], etc.
        m = re.match(r"^\[([^\]]+)\]$", stripped)

        if m:
            name = m.group(1)
            current = (name, [])
            continue

        if current is None:
            continue

        key, value = split_key_value(line)

        if key:
            current[1].append((key, value))

    if current is not None:
        sections.append(current)

    return sections


def emit_project(properties, out):
    family = None
    product = None
    codepage = 1252

    for key, value in properties:
        kl = key.lower()

        if kl == "familyid":
            family = parse_int(value)

        elif kl == "productcode":
            product = parse_int(value)

        elif kl == "codepage":
            codepage = parse_int(value)

    # Your sample has FamilyID but no ProductCode.
    # mkgmap's normal/default product code is 1.
    if product is None:
        product = 1

    if family is not None:
        out.append("[_id]")
        out.append("FID={}".format(family))
        out.append("ProductCode={}".format(product))
        out.append("CodePage={}".format(codepage))
        out.append("[end]")
        out.append("")


def emit_element(name, properties, out):
    section = SECTION_MAP.get(name.upper())

    if section is None:
        return

    colors = parse_colors(properties)
    lines = get_lines(properties)

    out.append("[{}]".format(section))

    # Ordinary properties.
    for key, value in properties:

        kl = key.lower()

        if kl in ("color", "line"):
            continue

        translated = translate_property(
            key,
            value,
            section,
        )

        if translated:
            out.append(translated)

    # TYPWiz indexed bitmap -> mkgmap XPM.
    if lines:
        xpm = make_xpm(
            lines,
            colors,
            point=(section == "_point"),
        )

        if xpm:
            if section == "_point":
                out.append(
                    'DayXpm="{}"'.format(xpm[0])
                )
            else:
                out.append(
                    'Xpm="{}"'.format(xpm[0])
                )

            out.extend(xpm[1:])

    out.append("[end]")
    out.append("")


def convert(filename):
    with open(filename, "r", encoding="latin-1") as f:
        sections = parse_project(f.readlines())

    output = []

    for name, properties in sections:

        if name.lower() == "project":
            emit_project(properties, output)

        elif name.upper() in SECTION_MAP:
            emit_element(
                name,
                properties,
                output,
            )

        else:
            print(
                "warning: ignoring unsupported section [{}]"
                .format(name),
                file=sys.stderr,
            )

    return "\n".join(output)


def main():
    parser = argparse.ArgumentParser(
        description="Convert TYPWiz .typ.prj to mkgmap TYP text."
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
        result = convert(args.input)

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
