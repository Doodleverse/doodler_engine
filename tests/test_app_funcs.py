from doodler_engine.app_funcs import get_asset_files


def test_get_asset_files():
    assert get_asset_files() == []

