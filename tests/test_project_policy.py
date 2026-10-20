"""Project graph and forbidden dependency enforcement use synthetic projects."""

from consumer_fixture import ConsumerTestCase

from jellyfin_plugin_toolkit.checks.architecture import check_graph, check_projects
from jellyfin_plugin_toolkit.scaffold import toml


class ProjectPolicyTests(ConsumerTestCase):
    def test_cycle_outside_entrypoint_is_rejected(self):
        with self.assertRaisesRegex(ValueError, "cycle"):
            check_graph({"entry": set(), "domain": {"effects"}, "effects": {"domain"}})
        check_graph(
            {"entry": {"effects", "domain"}, "effects": {"domain"}, "domain": set()}
        )

    def test_unexpected_project_reference_is_rejected(self):
        project = self.root / "server/Entry.csproj"
        project.parent.mkdir()
        project.write_text("<Project />")
        self.ctx.config.backend = {
            "entry": "server/Entry.csproj",
            "projects": {"Host": "server/Entry.csproj"},
        }
        self.ctx.config.architecture = {"edges": {"server/Entry.csproj": []}}
        (self.root / "plugin.toml").write_text(toml(self.ctx.config))
        check_projects(self.root)
        project.write_text(
            '<Project><ItemGroup><ProjectReference Include="../domain/Domain.csproj" /></ItemGroup></Project>'
        )
        with self.assertRaisesRegex(ValueError, "Unexpected project dependencies"):
            check_projects(self.root)
