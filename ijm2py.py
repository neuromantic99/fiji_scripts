"""
ijm2py.py — Convert ImageJ/Fiji macro (.ijm) into a Fiji Jython script (.py)

What it does (best-effort, practical):
- Converts: run("Command", "key=val key2=[val with spaces] flag3")
  into:
    IJ.run("Command",
           "key=val\n"
           "key2=[val with spaces]\n"
           "flag3")
- Splits long argument strings into newline-separated tokens.
- Keeps bracketed values intact: path=[D:/10 mW/subsampled]
- Preserves // comments as # comments.
- Leaves unrecognized lines as Python comments (so you don’t silently lose logic).

Notes:
- Output is for Fiji's **Python (Jython)** runtime, so it uses: from ij import IJ
- This is not a full macro-language transpiler; it targets the common “recorded macro” pattern.
"""

from __future__ import annotations

import argparse
import re
from pathlib import Path


RUN_CALL_RE = re.compile(r"\brun\s*\(", re.IGNORECASE)


def _unescape_ijm_string(s: str) -> str:
    """Unescape common IJM string escapes."""
    # IJM uses backslash escapes similar to Java in recorded macros.
    return (
        s.replace(r"\\", "\\")
        .replace(r"\"", '"')
        .replace(r"\n", "\n")
        .replace(r"\t", "\t")
    )


def _escape_py_string_fragment(s: str) -> str:
    """Escape a fragment for inclusion inside a Python double-quoted string literal."""
    return s.replace("\\", "\\\\").replace('"', r"\"")


def _read_quoted_string(text: str, i: int) -> tuple[str, int]:
    """
    Read a double-quoted string starting at text[i] == '"'.
    Returns (raw_string_contents, next_index_after_closing_quote).
    Supports backslash-escaped quotes.
    """
    assert text[i] == '"'
    i += 1
    out = []
    while i < len(text):
        ch = text[i]
        if ch == "\\" and i + 1 < len(text):
            out.append(ch)
            out.append(text[i + 1])
            i += 2
            continue
        if ch == '"':
            i += 1
            break
        out.append(ch)
        i += 1
    return "".join(out), i


def _parse_run_call(line: str) -> tuple[str, str] | None:
    """
    Parse run("Command", "Args"); from a single line.
    Returns (command, args) or None if not parseable.
    """
    m = RUN_CALL_RE.search(line)
    if not m:
        return None

    i = m.end()
    # Skip whitespace
    while i < len(line) and line[i].isspace():
        i += 1

    if i >= len(line) or line[i] != '"':
        return None

    cmd_raw, i = _read_quoted_string(line, i)
    # Skip whitespace
    while i < len(line) and line[i].isspace():
        i += 1

    if i >= len(line) or line[i] != ",":
        return None
    i += 1

    while i < len(line) and line[i].isspace():
        i += 1

    if i >= len(line) or line[i] != '"':
        return None

    args_raw, i = _read_quoted_string(line, i)

    cmd = _unescape_ijm_string(cmd_raw)
    args = _unescape_ijm_string(args_raw)
    return cmd, args


def _split_args_tokens(args: str) -> list[str]:
    """
    Split recorded-macro arg string into tokens on spaces,
    but do NOT split inside:
      - [...] (common for values with spaces)
      - {...} (sometimes used for arrays)
      - (...) (rare but possible)
    Also preserves empty/leading/trailing spaces by ignoring them.
    """
    tokens = []
    buf = []
    depth_square = depth_curly = depth_paren = 0

    def flush():
        if buf:
            token = "".join(buf).strip()
            if token:
                tokens.append(token)
            buf.clear()

    for ch in args:
        if ch == "[":
            depth_square += 1
        elif ch == "]" and depth_square > 0:
            depth_square -= 1
        elif ch == "{":
            depth_curly += 1
        elif ch == "}" and depth_curly > 0:
            depth_curly -= 1
        elif ch == "(":
            depth_paren += 1
        elif ch == ")" and depth_paren > 0:
            depth_paren -= 1

        # Split on whitespace only at top level
        if ch.isspace() and depth_square == 0 and depth_curly == 0 and depth_paren == 0:
            flush()
        else:
            buf.append(ch)

    flush()
    return tokens


def _format_args_as_multiline(tokens: list[str], indent: str = "    ") -> str:
    """
    Build a Python expression string like:
      "a=X\n"
      "b=Y\n"
      "flag"
    """
    if not tokens:
        return '""'

    lines = []
    for idx, tok in enumerate(tokens):
        tok_escaped = _escape_py_string_fragment(tok)
        # Add \n to all but last token (makes paste-friendly arg string)
        suffix = r"\n " if idx < len(tokens) - 1 else ""
        lines.append(f'"{tok_escaped}{suffix}"')

    if len(lines) == 1:
        return lines[0]

    joined = ("\n" + indent).join(lines)
    return f"(\n{indent}{joined}\n)"


def convert_ijm_to_py(ijm_text: str) -> str:
    out = []
    out.append("#@String \n")
    out.append("from ij import IJ\n\n")

    for raw_line in ijm_text.splitlines():
        line = raw_line.rstrip("\n")

        # Convert IJM comment style
        stripped = line.lstrip()
        if stripped.startswith("//"):
            out.append("# " + stripped[2:].lstrip() + "\n")
            continue

        parsed = _parse_run_call(line)
        if parsed is None:
            # Keep non-run lines as comments so you notice them.
            if stripped:
                out.append("# [unconverted] " + line + "\n")
            else:
                out.append("\n")
            continue

        cmd, args = parsed
        tokens = _split_args_tokens(args)
        args_expr = _format_args_as_multiline(tokens, indent=" " * 8)

        out.append(f'IJ.run("{_escape_py_string_fragment(cmd)}", {args_expr})\n')

    return "".join(out)


def main() -> int:
    ap = argparse.ArgumentParser(
        description="Convert ImageJ macro (.ijm) to Fiji Jython script (.py)"
    )
    ap.add_argument("input", type=Path, help="Path to input .ijm file")
    ap.add_argument(
        "-o",
        "--output",
        type=Path,
        default=None,
        help="Path to output .py file (default: <input>.py)",
    )
    args = ap.parse_args()

    inp: Path = args.input
    outp: Path = args.output if args.output else inp.with_suffix(".py")

    ijm_text = inp.read_text(encoding="utf-8", errors="replace")
    py_text = convert_ijm_to_py(ijm_text)
    outp.write_text(py_text, encoding="utf-8")

    print(f"Wrote: {outp}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
