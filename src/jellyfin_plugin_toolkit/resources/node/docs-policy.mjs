import path from "node:path";
import { dependency } from "./dependencies.mjs";

const { visit } = await dependency("unist-util-visit");
const { fromHtml } = await dependency("hast-util-from-html");

/** Repository navigation policy, applied to remark's parsed syntax tree. */
export default function repositoryPolicy() {
    return (tree, file) => {
        const anchors = [];
        const definitions = new Map();
        const links = [];
        visit(tree, "definition", (node) => definitions.set(node.identifier, node.url));
        visit(tree, (node) => {
            if (node.type === "html") {
                visit(fromHtml(node.value, { fragment: true }), "element", (element) => {
                    const id = element.properties.id || (element.tagName === "a" && element.properties.name);
                    if (id) anchors.push({ type: "paragraph", data: { id: String(id) }, children: [] });
                });
            }
            const target =
                node.type === "link" || node.type === "image"
                    ? node.url
                    : node.type === "linkReference" || node.type === "imageReference"
                      ? definitions.get(node.identifier)
                      : undefined;
            if (target === undefined || /^[a-z][a-z\d+.-]*:|^\/\//i.test(target)) return;
            const url = new URL(target, "file://" + path.resolve(file.cwd, file.path));
            const resolved = decodeURIComponent(url.pathname);
            const relative = path.relative(file.cwd, resolved);
            if (target.startsWith("/") || relative === ".." || relative.startsWith(".." + path.sep))
                file.message("Use a repository-relative link: " + target, node, "toolkit:repository-relative");
            else links.push(resolved);
        });
        tree.children.push(...anchors);
        file.data.toolkitLinks = links;
    };
}
