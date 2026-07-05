"""
Replays the CDR parquet as time-ordered batches, simulating live traffic.

Each call to next_batch() advances a cursor by `batch_min` simulated minutes and
returns the records created in that slice — the "new CDR logs" the ambient agent
reacts to.
"""
from __future__ import annotations

import pandas as pd


class CDRStream:
    def __init__(self, parquet_path: str, batch_min: int = 5):
        df = pd.read_parquet(parquet_path)
        df["created_ts"] = pd.to_datetime(df["created_ts"])
        self.df = df.sort_values("created_ts").reset_index(drop=True)
        self.batch_min = batch_min
        self.start = self.df["created_ts"].min()
        self.end = self.df["created_ts"].max()
        self.cursor = self.start

    def has_next(self) -> bool:
        return self.cursor <= self.end

    def next_batch(self) -> pd.DataFrame:
        lo = self.cursor
        hi = lo + pd.Timedelta(minutes=self.batch_min)
        batch = self.df[(self.df["created_ts"] >= lo) & (self.df["created_ts"] < hi)]
        self.cursor = hi
        return batch

    def reset(self) -> None:
        self.cursor = self.start
