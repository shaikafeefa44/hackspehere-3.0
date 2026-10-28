"""generate_dataset.py
Generate a realistic hourly microgrid telemetry dataset.

- 2 years of hourly data (~17,520 rows)
- Columns:
    timestamp (ISO string), temperature (°C), humidity (%), holiday (bool),
    hour (0-23), day_of_week (0=Mon..6=Sun), previous_demand (kW),
    renewable_generation (kW), battery_soc (percent), target_demand_kwh (kW)
- Basic cleaning and feature engineering.
- Train/test split (80/20) saved as CSV files.
"""
import os
import random
from datetime import datetime, timedelta
import pandas as pd
import numpy as np

from backend.config import Config


def is_holiday(date: datetime) -> bool:
    """Very simple holiday detection: weekends + a few fixed dates.
    In a real system this would use a holiday library.
    """
    if date.weekday() >= 5:
        return True
    fixed = {"01-01", "12-25", "07-04"}
    return date.strftime("%m-%d") in fixed


def generate_data(start: datetime, periods: int, freq: str = "H") -> pd.DataFrame:
    """Generate synthetic telemetry.
    Returns a DataFrame with the required columns.
    """
    rng = pd.date_range(start, periods=periods, freq=freq)
    df = pd.DataFrame({"timestamp": rng})
    day_of_year = df["timestamp"].dt.dayofyear
    hour = df["timestamp"].dt.hour
    seasonal = 10 * np.cos(2 * np.pi * (day_of_year - 180) / 365)
    daily = 5 * np.cos(2 * np.pi * (hour - 14) / 24)
    temperature = 20 + seasonal + daily + np.random.normal(0, 1.5, size=len(df))
    df["temperature"] = temperature.round(1)
    humidity = 70 - 0.5 * (temperature - 20) + np.random.normal(0, 5, size=len(df))
    df["humidity"] = humidity.clip(20, 100).round(1)
    df["holiday"] = df["timestamp"].apply(is_holiday)
    df["hour"] = hour
    df["day_of_week"] = df["timestamp"].dt.weekday
    base_demand = (
        100
        + 30 * ((hour >= 8) & (hour <= 20))
        + 20 * ((hour >= 18) & (hour <= 22))
        + np.random.normal(0, 10, size=len(df))
    )
    base_demand = np.where(df["holiday"], base_demand * 0.7, base_demand)
    df["target_demand_kwh"] = base_demand.clip(30, 300).round(2)
    solar = np.where((hour >= 6) & (hour <= 18), 0.5 * (hour - 6), 0)
    solar = np.where(solar < 0, 0, solar)
    renewable = solar * Config.SOLAR_KW_PER_WM2 * random.uniform(0.8, 1.2)
    df["renewable_generation"] = renewable.round(2)
    df["previous_demand"] = df["target_demand_kwh"].shift(1).fillna(df["target_demand_kwh"]).round(2)
    soc = Config.INITIAL_SOC_PCT
    soc_series = []
    capacity = Config.BATTERY_CAPACITY_KWH
    for _, row in df.iterrows():
        demand = row["target_demand_kwh"]
        renewable_val = row["renewable_generation"]
        net = renewable_val - demand
        if net > 0:
            charge = min(net, Config.MAX_CHARGE_KW) * Config.BATTERY_EFFICIENCY
            soc += (charge / capacity) * 100
        else:
            discharge = min(-net, Config.MAX_DISCHARGE_KW) / Config.BATTERY_EFFICIENCY
            soc -= (discharge / capacity) * 100
        soc = max(Config.SOC_MIN_PCT, min(Config.SOC_MAX_PCT, soc))
        soc_series.append(round(soc, 1))
    df["battery_soc"] = soc_series
    return df


def main():
    os.makedirs(Config.DATA_DIR, exist_ok=True)
    start = datetime.now() - timedelta(days=365 * 2)
    df = generate_data(start=start, periods=24 * 365 * 2)
    full_path = Config.DATA_DIR / "synthetic.csv"
    df.to_csv(full_path, index=False)
    split_idx = int(len(df) * 0.8)
    df.iloc[:split_idx].to_csv(Config.DATA_DIR / "train.csv", index=False)
    df.iloc[split_idx:].to_csv(Config.DATA_DIR / "test.csv", index=False)
    print(f"Generated dataset: {full_path}\nTrain: {Config.DATA_DIR / 'train.csv'}\nTest: {Config.DATA_DIR / 'test.csv'}")

if __name__ == "__main__":
    main()
