"""Enforce the consumer's declared project graph and source boundaries."""

import ast
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Iterable, Mapping

from jellyfin_plugin_toolkit.context import Context


def check_graph(graph: Mapping[str, Iterable[str]]) -> None:
    active = set()
    complete = set()

    def visit(node: str) -> None:
        if node in active:
            raise ValueError("Project dependency cycle: " + node)
        if node in complete:
            return
        active.add(node)
        for dependency in graph.get(node, set()):
            visit(dependency)
        active.remove(node)
        complete.add(node)

    for node in graph:
        visit(node)


def toolkit_graph(root: Path) -> dict[str, set[str]]:
    """Include imports inside functions; omit annotation-only TYPE_CHECKING imports."""
    modules = {
        ".".join(path.relative_to(root).with_suffix("").parts).removesuffix(
            ".__init__"
        ): path
        for path in (root / "jellyfin_plugin_toolkit").rglob("*.py")
    }
    graph = {}
    for name, path in modules.items():
        dependencies: set[str] = set()
        tree = ast.parse(path.read_text())
        annotation_imports = {
            id(child)
            for block in ast.walk(tree)
            if isinstance(block, ast.If)
            and (
                isinstance(block.test, ast.Name)
                and block.test.id == "TYPE_CHECKING"
                or isinstance(block.test, ast.Attribute)
                and isinstance(block.test.value, ast.Name)
                and block.test.value.id == "typing"
                and block.test.attr == "TYPE_CHECKING"
            )
            for statement in block.body
            for child in ast.walk(statement)
        }
        for node in ast.walk(tree):
            if id(node) in annotation_imports:
                continue
            if isinstance(node, ast.Import):
                dependencies.update(
                    alias.name for alias in node.names if alias.name in modules
                )
            elif isinstance(node, ast.ImportFrom) and node.module:
                if node.module in modules:
                    dependencies.add(node.module)
                dependencies.update(
                    node.module + "." + alias.name
                    for alias in node.names
                    if node.module + "." + alias.name in modules
                )
        graph[name] = dependencies - {name}
    check_graph(graph)
    return graph


def check_projects(root: Path) -> dict[str, set[str]]:
    ctx = Context.load(root)
    declared = ctx.config.architecture
    edges = declared.edges
    graph = {}
    for name, dependencies in edges.items():
        path = ctx.path(name)
        tree = ET.parse(path)
        actual = {
            (path.parent / item.attrib["Include"])
            .resolve()
            .relative_to(root.resolve())
            .as_posix()
            for item in tree.findall(".//ProjectReference")
        }
        if actual != set(dependencies):
            raise ValueError("Unexpected project dependencies: " + name)
        graph[name] = actual
    check_graph(graph)
    for directory, forbidden in declared.forbidden.items():
        for source in ctx.path(directory).rglob("*.cs"):
            if {"bin", "obj"}.intersection(source.parts):
                continue
            for token in forbidden:
                if token in source.read_text():
                    raise ValueError(
                        "Forbidden dependency "
                        + token
                        + ": "
                        + str(source.relative_to(root))
                    )
    return graph
