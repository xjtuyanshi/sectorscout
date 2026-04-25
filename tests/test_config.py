from pathlib import Path

from sectorscout.config import config_hash, load_config


def test_config_hash_is_stable_for_same_content_with_different_key_order(tmp_path: Path) -> None:
    first = tmp_path / "first.yaml"
    second = tmp_path / "second.yaml"
    first.write_text(
        """
project:
  name: SectorScout
market:
  calendar: XNYS
  timezone: America/New_York
database:
  path: data/sectorscout.duckdb
""",
        encoding="utf-8",
    )
    second.write_text(
        """
database:
  path: data/sectorscout.duckdb
market:
  timezone: America/New_York
  calendar: XNYS
project:
  name: SectorScout
""",
        encoding="utf-8",
    )
    assert config_hash(load_config(first)) == config_hash(load_config(second))


def test_config_hash_changes_when_material_value_changes(tmp_path: Path) -> None:
    first = tmp_path / "first.yaml"
    second = tmp_path / "second.yaml"
    first.write_text("market:\n  calendar: XNYS\n", encoding="utf-8")
    second.write_text("market:\n  calendar: XNAS\n", encoding="utf-8")
    assert config_hash(load_config(first)) != config_hash(load_config(second))

