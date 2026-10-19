"""Enforce the consumer's declared project graph and source boundaries."""

import xml.etree.ElementTree as ET

from jellyfin_plugin_toolkit.context import Context


def check_graph(graph):
    active = set()
    complete = set()

    def visit(node):
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


def check_projects(root):
    ctx = Context.load(root)
    declared = ctx.config.get("architecture", {})
    edges = declared.get("edges", {})
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
    for directory, forbidden in declared.get("forbidden", {}).items():
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


def main():
    check_projects(Context.load().root)
    print("PASS: declared project graph and host-independent boundaries")


if __name__ == "__main__":
    main()
