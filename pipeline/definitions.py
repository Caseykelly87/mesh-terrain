"""The Dagster code location: dbt assets and the dbt CLI resource, nothing else yet.

The dbt manifest is produced at load time rather than baked into the image. The
project has no models yet and parsing it takes about two seconds; a manifest
generated at build time would go stale the moment warehouse-eng edits a model and
the failure would surface as a confusing lineage mismatch in the webserver instead
of a parse error at startup. Package installation (dbt deps) runs only when the
packages directory is missing, so a container or a developer machine that already
has packages does not need network access to load the code location.
"""

from collections.abc import Iterator

from dagster import AssetExecutionContext, Definitions
from dagster_dbt import DbtCliResource, DbtProject, dbt_assets

from pipeline.common.logging import configure_logging, get_logger
from pipeline.common.settings import get_settings

settings = get_settings()
configure_logging(settings.log_format)
log = get_logger("pipeline.definitions")

dbt_project = DbtProject(project_dir=settings.dbt_project_dir)


# Phase 2 candidate: replace this with DbtProject.prepare_if_dev plus a manifest
# built in the image, once models exist and parse time matters.
def prepare_dbt_project(project: DbtProject) -> None:
    """Install dbt packages when absent, then parse the project into a manifest."""
    dbt = DbtCliResource(project_dir=project)
    if project.has_uninstalled_deps:
        log.info("dbt_deps_start", project_dir=str(project.project_dir))
        dbt.cli(["deps", "--quiet"], target_path=project.target_path).wait()
        log.info("dbt_deps_end")
    log.info("dbt_parse_start", project_dir=str(project.project_dir))
    dbt.cli(["parse", "--quiet"], target_path=project.target_path).wait()
    log.info("dbt_parse_end", manifest_path=str(project.manifest_path))


prepare_dbt_project(dbt_project)


@dbt_assets(manifest=dbt_project.manifest_path)
def mesh_terrain_dbt_assets(
    context: AssetExecutionContext, dbt: DbtCliResource
) -> Iterator[object]:
    """Every dbt model, seed, snapshot and test in the mesh_terrain project."""
    yield from dbt.cli(["build"], context=context).stream()


defs = Definitions(
    assets=[mesh_terrain_dbt_assets],
    resources={"dbt": DbtCliResource(project_dir=dbt_project)},
)
