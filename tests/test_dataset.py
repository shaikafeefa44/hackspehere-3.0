import pathlib
import pandas as pd

from backend.config import Config


def test_dataset_files_exist():
    """Ensure the dataset generator produced the expected CSV files and columns."""
    data_dir = pathlib.Path(Config.DATA_DIR)
    assert data_dir.exists(), f"Data directory {data_dir} missing"
    for filename in ["synthetic.csv", "train.csv", "test.csv"]:
        path = data_dir / filename
        assert path.is_file(), f"{filename} not found in {data_dir}"
        df = pd.read_csv(path)
        assert not df.empty, f"{filename} is empty"
        expected = {
            "timestamp",
            "temperature",
            "humidity",
            "holiday",
            "hour",
            "day_of_week",
            "previous_demand",
            "renewable_generation",
            "battery_soc",
            "target_demand_kwh",
        }
        assert expected.issubset(set(df.columns)), f"Missing columns in {filename}"
