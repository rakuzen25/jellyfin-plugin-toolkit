"""Documentation links are checked locally, including on documentation-only CI runs."""

import tempfile
import unittest
from functools import partial
from pathlib import Path

from jellyfin_plugin_toolkit.checks.docs import check


class DocumentationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.check = partial(check, dependency_root=Path(__file__).resolve().parents[1])
        self.write("README.md", "# Project\n\n[Guide](docs/guide.md)\n")
        self.write("docs/guide.md", "# Guide\n")

    def write(self, name: str, text: str) -> None:
        path = self.root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text)

    def test_root_markdown_links_are_checked_without_requiring_readme_navigation(
        self,
    ) -> None:
        self.write("AGENTS.md", "# Instructions\n[Home](README.md)\n")
        self.write("CONTRIBUTING.md", "# Contribute\n[Missing](absent.md)\n")
        errors = self.check(self.root)
        self.assertEqual(1, len(errors))
        self.assertIn("CONTRIBUTING.md", errors[0])
        self.assertIn("Cannot find file", errors[0])
        self.write("CONTRIBUTING.md", "# Contribute\n[Home](README.md)\n")
        self.assertEqual([], self.check(self.root))

    def test_relative_links_images_directories_queries_and_encoded_paths(self) -> None:
        self.write("assets/cover image.svg", "<svg/>")
        self.write(
            "docs/guide.md",
            '# Guide\n[Home](../README.md?view=1#project "Project")\n'
            "[Assets](../assets/)\n![Cover](<../assets/cover image.svg>)\n"
            "[![Cover](../assets/cover%20image.svg)](../README.md)\n",
        )
        self.assertEqual([], self.check(self.root))
        (self.root / "assets/cover image.svg").unlink()
        self.assertEqual(
            2, sum("Cannot find file" in error for error in self.check(self.root))
        )

    def test_nested_list_links_are_checked(self) -> None:
        self.write("docs/guide.md", "# Guide\n- Section\n    - [Missing](missing.md)\n")
        self.assertTrue(any("Cannot find file" in e for e in self.check(self.root)))

    def test_generated_changelog_links_are_checked_without_formatting(self) -> None:
        self.write("CHANGELOG.md", "# Releases\n\n\n* [Missing](absent.md)\n")
        errors = self.check(self.root)
        self.assertEqual(1, len(errors))
        self.assertIn("CHANGELOG.md", errors[0])
        self.assertIn("Cannot find file", errors[0])
        self.write("CHANGELOG.md", "# Releases\n\n\n* [Home](README.md)\n")
        self.assertEqual([], self.check(self.root))

    def test_linked_reference_image_uses_the_enclosing_definitions(self) -> None:
        self.write("cover.svg", "<svg/>")
        self.write(
            "docs/guide.md",
            "# Guide\n[![Cover][image]](../README.md)\n\n[image]: ../cover.svg\n",
        )
        self.assertEqual([], self.check(self.root))
        (self.root / "cover.svg").unlink()
        self.assertTrue(any("Cannot find file" in e for e in self.check(self.root)))

    def test_renamed_target_requires_updating_the_link(self) -> None:
        (self.root / "docs/guide.md").rename(self.root / "docs/renamed.md")
        self.assertTrue(
            any(
                "Cannot find file" in e and "docs/guide.md" in e
                for e in self.check(self.root)
            )
        )
        self.write("README.md", "# Project\n[Guide](docs/renamed.md)\n")
        self.assertEqual([], self.check(self.root))

    def test_duplicate_headings_have_distinct_anchors(self) -> None:
        self.write(
            "docs/guide.md",
            "# Guide\n## Save\n## Save\n## Save-1\n## Save\n"
            "[Second](#save-1) [Third](#save-1-1) [Fourth](#save-2)\n",
        )
        self.assertEqual([], self.check(self.root))
        self.write("README.md", "# Project\n[Guide](docs/guide.md#save-3)\n")
        self.assertTrue(any("Cannot find heading" in e for e in self.check(self.root)))

    def test_heading_markup_unicode_setext_and_explicit_anchor(self) -> None:
        self.write(
            "docs/guide.md",
            '# **Café** and `code`!\n\nSecond heading\n---\n\n<a id="custom"></a>\n',
        )
        self.write(
            "README.md",
            "# Project\n[Guide](docs/guide.md#café-and-code) [Second](docs/guide.md#second-heading) [Custom](docs/guide.md#custom)\n",
        )
        self.assertEqual([], self.check(self.root))

    def test_reference_links_and_images_resolve_without_case_sensitivity(self) -> None:
        self.write("cover.svg", "<svg/>")
        self.write(
            "docs/guide.md",
            "# Guide\n[Home][parent] [Parent][] [Parent] ![Cover][image]\n\n"
            '[parent]: ../README.md#project "Project"\n\n[image]: ../cover.svg\n',
        )
        self.assertEqual([], self.check(self.root))

    def test_undefined_explicit_reference_reports_the_document_line(self) -> None:
        self.write("docs/guide.md", "# Guide\n\n[Home][missing]\n")
        errors = self.check(self.root)
        self.assertEqual(1, len(errors))
        self.assertIn("docs/guide.md:3:", errors[0])
        self.assertIn("undefined definition", errors[0])

    def test_fenced_inline_and_commented_examples_are_ignored(self) -> None:
        self.write(
            "docs/guide.md",
            "# Guide\n```markdown\n[Broken](missing.md)\n## Hidden\n````\n"
            "~~~md\n[Broken][missing]\n~~~\n"
            "`[Broken](missing.md)` and `` `[Broken](missing.md)` ``\n"
            "<!-- [Broken](missing.md) -->\n",
        )
        self.assertEqual([], self.check(self.root))

    def test_short_fence_does_not_end_a_longer_fence(self) -> None:
        self.write(
            "docs/guide.md",
            "# Guide\n````md\n```\n[Broken](missing.md)\n````\n[Home](../README.md)\n",
        )
        self.assertEqual([], self.check(self.root))

    def test_nested_parentheses_and_escaped_destinations(self) -> None:
        self.write("docs/track (live).md", "# Track\n")
        self.write("docs/guide.md", "# Guide\n[Track](track%20(live).md)\n")
        self.assertEqual([], self.check(self.root))

    def test_only_readme_reachable_pages_pass_even_if_orphans_link_each_other(
        self,
    ) -> None:
        self.write("docs/a.md", "# A\n[B](b.md)\n")
        self.write("docs/b.md", "# B\n[A](a.md)\n")
        self.assertEqual(
            2, sum("unreachable" in error for error in self.check(self.root))
        )
        self.write("docs/guide.md", "# Guide\n[A](a.md)\n")
        self.assertEqual([], self.check(self.root))

    def test_external_urls_need_no_network_but_local_links_cannot_escape_root(
        self,
    ) -> None:
        self.write(
            "docs/guide.md",
            "# Guide\n[Site](https://example.test/missing#part) [Mail](mailto:help@example.test)\n"
            "[Outside](../../outside.md) [Absolute](/README.md)\n",
        )
        self.assertEqual(
            2, sum("repository-relative" in error for error in self.check(self.root))
        )

    def test_missing_readme_is_an_error_and_release_history_is_not_an_orphan(
        self,
    ) -> None:
        self.write("CHANGELOG.md", "# Releases\n")
        self.assertEqual([], self.check(self.root))
        (self.root / "README.md").unlink()
        self.assertIn(
            "README.md: missing documentation entrypoint", self.check(self.root)
        )

    def test_ci_checks_docs_once_in_quality_without_node_in_planning(self) -> None:
        repository = Path(__file__).resolve().parents[1]
        workflow = (repository / ".github/workflows/validate.yml").read_text()
        impact = workflow.split("  impact:\n", 1)[1].split("  quality:\n", 1)[0]
        self.assertNotIn("setup-node", impact)
        self.assertNotIn("check docs", impact)
        quality = workflow.split("  quality:\n", 1)[1].split("  frontend:\n", 1)[0]
        self.assertEqual(1, quality.count("jpt check docs"))
        self.assertIn("Check documentation", quality)


if __name__ == "__main__":
    unittest.main()
