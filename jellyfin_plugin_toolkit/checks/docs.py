"""Check repository Markdown links and README reachability without network access."""

import html
import re
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import unquote, urlsplit


@dataclass(frozen=True)
class Link:
    line: int
    target: str | None
    label: str


@dataclass(frozen=True)
class Document:
    anchors: set[str]
    links: list[Link]


def mask_code(text: str, *, inline: bool = True) -> str:
    """Keep offsets/newlines so errors still point to the original document."""
    lines = text.splitlines(keepends=True)
    fence = ""
    for index, line in enumerate(lines):
        marker = re.match(r"^ {0,3}(`{3,}|~{3,})(.*)$", line)
        if fence:
            lines[index] = re.sub(r"[^\n]", " ", line)
            if (
                marker
                and marker[1][0] == fence[0]
                and len(marker[1]) >= len(fence)
                and not marker[2].strip()
            ):
                fence = ""
        elif marker:
            fence = marker[1]
            lines[index] = re.sub(r"[^\n]", " ", line)
    visible = "".join(lines)
    visible = re.sub(
        r"<!--.*?-->",
        lambda match: re.sub(r"[^\n]", " ", match[0]),
        visible,
        flags=re.DOTALL,
    )
    if inline:
        visible = re.sub(
            r"(`+)(?!`)(.+?)(?<!`)\1(?!`)",
            lambda match: re.sub(r"[^\n]", " ", match[0]),
            visible,
            flags=re.DOTALL,
        )
    return visible


def label_key(label: str) -> str:
    return " ".join(label.split()).casefold()


def closing(text: str, start: int, left: str, right: str) -> int:
    depth = 1
    index = start + 1
    while index < len(text):
        if text[index] == "\\":
            index += 2
            continue
        if text[index] == left:
            depth += 1
        elif text[index] == right:
            depth -= 1
            if not depth:
                return index
        index += 1
    return -1


def destination(value: str) -> str:
    value = value.strip()
    if value.startswith("<"):
        return value[1 : value.find(">")]
    depth = 0
    for index, character in enumerate(value):
        if index and value[index - 1] == "\\":
            continue
        if character == "(":
            depth += 1
        elif character == ")":
            depth -= 1
        elif character.isspace() and not depth:
            return value[:index]
    return value


def parse(text: str, inherited: dict[str, str] | None = None) -> Document:
    visible = mask_code(text)
    references = dict(inherited or {})
    definition = re.compile(
        r"^ {0,3}\[([^\]\n]+)\]:[ \t]*(<[^>\n]+>|\S+).*$", re.MULTILINE
    )
    for match in definition.finditer(visible):
        references.setdefault(label_key(match[1]), destination(match[2]))
    visible = definition.sub(lambda match: " " * len(match[0]), visible)
    links: list[Link] = []
    index = 0
    while (start := visible.find("[", index)) != -1:
        index = start + 1
        if start and visible[start - 1] == "\\":
            continue
        end = closing(visible, start, "[", "]")
        if end == -1:
            continue
        label = visible[start + 1 : end]
        after = end + 1
        target: str | None
        if visible[after : after + 1] == "(":
            finish = closing(visible, after, "(", ")")
            if finish == -1:
                continue
            target = destination(visible[after + 1 : finish])
            index = finish + 1
        elif visible[after : after + 1] == "[":
            finish = closing(visible, after, "[", "]")
            if finish == -1:
                continue
            key = visible[after + 1 : finish] or label
            target = references.get(label_key(key))
            index = finish + 1
        elif label_key(label) in references:
            target = references[label_key(label)]
            index = after
        else:
            continue
        links.append(Link(visible.count("\n", 0, start) + 1, target, label))
        # A linked image has its own destination as well as its enclosing link.
        if "![" in label:
            links.extend(
                Link(link.line + visible.count("\n", 0, start), link.target, link.label)
                for link in parse(label, references).links
            )

    anchors: set[str] = set()
    headings = mask_code(text, inline=False).splitlines()
    for index, line in enumerate(headings):
        heading = re.match(r"^ {0,3}#{1,6}\s+(.*?)\s*#*\s*$", line)
        title = heading[1] if heading else ""
        if (not heading and index + 1 < len(headings)) and (
            line.strip() and re.fullmatch(" {0,3}(?:=+|-+)[ \\t]*", headings[index + 1])
        ):
            title = line.strip()
        if title:
            title = re.sub(r"<[^>]*>", "", title)
            title = re.sub(r"!?\[([^\]]+)\]\([^)]*\)", r"\1", title)
            title = re.sub(r"\\([!\"#$%&'()*+,\-./:;<=>?@\[\]^_`{|}~])", r"\1", title)
            slug = re.sub(r"[^\w\- ]", "", html.unescape(title).lower()).replace(
                " ", "-"
            )
            anchor = slug
            suffix = 0
            while anchor in anchors:
                suffix += 1
                anchor = f"{slug}-{suffix}"
            anchors.add(anchor)
    anchors.update(
        re.findall(
            r"<(?:a|h[1-6])\b[^>]*(?:id|name)=[\"\']([^\"\']+)",
            mask_code(text, inline=False),
        )
    )
    return Document(anchors, links)


def documents(root: Path) -> list[Path]:
    return [
        path
        for path in [
            *sorted(root.glob("*.md")),
            *sorted((root / "docs").rglob("*.md")),
            *sorted((root / "vendor").glob("*.md")),
        ]
        if path.is_file()
    ]


def check(root: Path) -> list[str]:
    root = root.resolve()
    parsed = {path: parse(path.read_text()) for path in documents(root)}
    edges: dict[Path, set[Path]] = {path: set() for path in parsed}
    errors: list[str] = []
    for path, document in parsed.items():
        for link in document.links:
            prefix = f"{path.relative_to(root)}:{link.line}"
            if link.target is None:
                errors.append(f"{prefix}: undefined reference [{link.label}]")
                continue
            target = html.unescape(re.sub(r"\\(.)", r"\1", link.target))
            url = urlsplit(target)
            if url.scheme or url.netloc:
                continue
            local = unquote(url.path)
            resolved = (path.parent / local).resolve() if local else path
            if local.startswith("/") or not resolved.is_relative_to(root):
                errors.append(f"{prefix}: use a repository-relative link: {target}")
            elif not resolved.exists():
                errors.append(f"{prefix}: missing target: {target}")
            elif resolved in parsed:
                edges[path].add(resolved)
                if (
                    url.fragment
                    and unquote(url.fragment) not in parsed[resolved].anchors
                ):
                    errors.append(f"{prefix}: missing heading anchor: {target}")
    reached: set[Path] = set()
    pending = [root / "README.md"]
    while pending:
        path = pending.pop()
        if path not in reached:
            reached.add(path)
            pending.extend(edges.get(path, set()))
    for path in parsed:
        # Preserved release history and upstream notices are not navigation pages.
        if path.is_relative_to(root / "docs") and path not in reached:
            errors.append(f"{path.relative_to(root)}: unreachable from README.md")
    if root / "README.md" not in parsed:
        errors.append("README.md: missing documentation entrypoint")
    return errors


def main(ctx) -> None:
    root = ctx.root
    errors = check(root)
    if errors:
        raise SystemExit("\n".join(errors))
    print(
        f"PASS: local links, anchors, and README reachability ({len(documents(root))} Markdown files)."
    )


if __name__ == "__main__":
    from jellyfin_plugin_toolkit.context import Context

    main(Context.load())
