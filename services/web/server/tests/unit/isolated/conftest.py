import json
import os
import random
from pathlib import Path

import pytest
from faker import Faker
from pytest_simcore.helpers.utils_dict import ConfigDict


@pytest.fixture
def dir_with_random_content(tmpdir, faker: Faker) -> Path:
    def make_files_in_dir(dir_path: Path, file_count: int) -> None:
        for _ in range(file_count):
            (dir_path / f"{faker.file_name(extension='bin')}").write_bytes(
                os.urandom(random.randint(1, 10))
            )

    def ensure_dir(path_to_ensure: Path) -> Path:
        path_to_ensure.mkdir(parents=True, exist_ok=True)
        return path_to_ensure

    def make_subdirectory_with_content(subdir_name: Path, max_file_count: int) -> None:
        subdir_name = ensure_dir(subdir_name)
        make_files_in_dir(
            dir_path=subdir_name,
            file_count=random.randint(1, max_file_count),
        )

    def make_subdirectories_with_content(
        subdir_name: Path, max_subdirectories_count: int, max_file_count: int
    ) -> None:
        subdirectories_count = random.randint(1, max_subdirectories_count)
        for _ in range(subdirectories_count):
            make_subdirectory_with_content(
                subdir_name=subdir_name / f"{faker.word()}",
                max_file_count=max_file_count,
            )

    # -----------------------

    temp_dir_path = Path(tmpdir)
    data_container = ensure_dir(temp_dir_path / "study_data")

    make_subdirectories_with_content(
        subdir_name=data_container, max_subdirectories_count=5, max_file_count=5
    )
    make_files_in_dir(dir_path=data_container, file_count=5)

    # creates a good amount of files
    for _ in range(4):
        for subdirectory_path in (
            path for path in data_container.glob("*") if path.is_dir()
        ):
            make_subdirectories_with_content(
                subdir_name=subdirectory_path,
                max_subdirectories_count=3,
                max_file_count=3,
            )

    return temp_dir_path


@pytest.fixture
def app_config_for_production_legacy(test_data_dir: Path) -> ConfigDict:
    app_config = json.loads(
        (test_data_dir / "server_docker_prod_app_config-unit.json").read_text()
    )

    print("app config (legacy) used in production:\n", json.dumps(app_config, indent=1))
    return app_config
