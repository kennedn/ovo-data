"""Download UK OVO electricity readings and plot overnight demand."""

import argparse
import asyncio
import csv
import math
import os
from datetime import UTC, date, datetime, time, timedelta
from getpass import getpass
from pathlib import Path
from statistics import median
from zoneinfo import ZoneInfo

import aiohttp
import matplotlib
from ovoenergy import OVOEnergy
from ovoenergy.const import USAGE_HALF_HOURLY_URL
from ovoenergy.exceptions import OVOEnergyAPINotAuthorized

matplotlib.use("Agg")
from matplotlib import pyplot as plt

LONDON = ZoneInfo("Europe/London")
DAY = timedelta(days=1)
SLOT = timedelta(minutes=30)
SEASONS = ("Winter", "Spring", "Summer", "Autumn")


class OVOEnergyUTC(OVOEnergy):
    async def _request(self, url, method, **kwargs):
        # Despite its name, this endpoint returns UTC for each UK calendar day.
        if url.startswith(f"{USAGE_HALF_HOURLY_URL}/"):
            url = url.replace(USAGE_HALF_HOURLY_URL, f"{USAGE_HALF_HOURLY_URL}-local", 1)
        return await super()._request(url, method, **kwargs)


def days(start, end):
    for offset in range((end - start).days + 1):
        yield start + offset * DAY


async def download_day(client, day):
    readings = {}
    try:
        usage = await client.get_half_hourly_usage(day.isoformat())
    except OVOEnergyAPINotAuthorized:
        raise
    except Exception as exc:
        # Client errors can contain private request details.
        raise ValueError(f"OVO request failed for {day} ({type(exc).__name__}).") from exc
    for reading in usage.electricity or []:
        if reading.consumption is None:
            continue
        stamp = reading.interval.start
        finish = reading.interval.end
        if stamp.tzinfo is None:
            stamp = stamp.replace(tzinfo=UTC)
        if finish.tzinfo is None:
            finish = finish.replace(tzinfo=UTC)
        stamp = stamp.astimezone(UTC)
        if stamp.astimezone(LONDON).date() != day:
            continue
        duration = finish.astimezone(UTC) - stamp
        # OVO can report the final millisecond as an inclusive end.
        if reading.unit.lower() != "kwh" or duration not in (
            SLOT, SLOT - timedelta(milliseconds=1)
        ):
            raise ValueError(f"OVO returned an unexpected unit or interval for {day}.")
        kwh = float(reading.consumption)
        if math.isfinite(kwh):
            readings[stamp] = kwh
    return readings


async def download(start, end, account_id):
    username = os.environ.get("OVO_USERNAME", "").strip() or input("OVO email: ").strip()
    password = os.environ.get("OVO_PASSWORD") or getpass("OVO password: ")
    readings = {}

    async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=30)) as session:
        client = OVOEnergyUTC(session)
        if not await client.authenticate(username, password):
            raise ValueError("OVO login failed. Check your account credentials.")
        if account_id is None:
            accounts = client.account_ids or (await client.bootstrap_accounts()).account_ids
            accounts = sorted({int(account) for account in accounts})
            if len(accounts) != 1:
                raise ValueError(f"Select your account with --account-id. Accounts: {accounts}")
            account_id = accounts[0]
        client.custom_account_id = account_id

        requested = list(days(start, end))
        for offset in range(0, len(requested), 4):
            batch = requested[offset:offset + 4]
            print(f"Fetching {batch[0]} to {batch[-1]} (UK dates)", flush=True)
            results = await asyncio.gather(
                *(download_day(client, day) for day in batch), return_exceptions=True
            )
            if any(isinstance(result, OVOEnergyAPINotAuthorized) for result in results):
                if not await client.get_token() and not await client.authenticate(username, password):
                    raise ValueError("OVO session expired and could not be renewed.")
                results = await asyncio.gather(*(download_day(client, day) for day in batch))
            for result in results:
                if isinstance(result, BaseException):
                    raise result
                readings.update(result)
            await asyncio.sleep(0.25)

    if not readings:
        raise ValueError("OVO returned no electricity readings. Check the dates and half-hourly usage in My OVO.")
    return readings


def export_csv(path, readings):
    with path.open("w", newline="") as file:
        writer = csv.writer(file)
        writer.writerow(["start_utc", "start_uk", "electricity_kwh"])
        for stamp, kwh in sorted(readings.items()):
            writer.writerow([stamp.isoformat(), stamp.astimezone(LONDON).isoformat(), kwh])


def read_csv(path):
    readings = {}
    with path.open(newline="") as file:
        for row in csv.DictReader(file):
            stamp = datetime.fromisoformat(row["start_utc"])
            kwh = float(row["electricity_kwh"])
            if stamp.tzinfo is None or not math.isfinite(kwh):
                raise ValueError("CSV readings need an offset-aware timestamp and finite kWh.")
            readings[stamp.astimezone(UTC)] = kwh
    return readings


def overnight_baselines(readings, start, end):
    for day in days(start, end):
        first = datetime.combine(day, time(0), LONDON).astimezone(UTC)
        last = datetime.combine(day, time(5), LONDON).astimezone(UTC)
        slots = [first + index * SLOT for index in range((last - first) // SLOT)]
        watts = [readings[stamp] * 2000 for stamp in slots if stamp in readings]
        yield day, median(watts) if len(watts) == len(slots) else math.nan


def plot_overnight(nights, output):
    dates, baselines = zip(*nights)
    complete = [value for value in baselines if math.isfinite(value)]
    if not complete:
        print("No complete 00:00–05:00 windows. Overnight chart skipped.")
        return

    typical = median(complete)
    fig, ax = plt.subplots(figsize=(10, 4), layout="constrained")
    ax.plot(dates, baselines, "o-", markersize=3, label="Nightly median")
    ax.axhline(typical, linestyle="--", color="tab:orange", label=f"Typical baseline: {typical:.0f} W")
    ax.set(title="Overnight electricity demand · 00:00–05:00 UK time",
           xlabel="Date", ylabel="Half-hour average power (W)")
    ax.legend()
    ax.grid(alpha=0.25)
    fig.autofmt_xdate()
    fig.savefig(output, dpi=150)
    plt.close(fig)
    print(f"Typical overnight baseline: {typical:.0f} W across {len(complete)}/{len(dates)} complete nights.")
    print(f"Chart: {output}")


def plot_seasons(nights, output, suffix):
    by_year = {}
    pooled = {season: [] for season in SEASONS}
    for day, watts in nights:
        season = SEASONS[(day.month % 12) // 3]
        year = day.year + (day.month == 12)
        values = by_year.setdefault((year, season), [])
        if math.isfinite(watts):
            values.append(watts)
            pooled[season].append(watts)

    csv_path = output / f"seasonal_{suffix}.csv"
    with csv_path.open("w", newline="") as file:
        writer = csv.writer(file)
        writer.writerow(["season", "year", "complete_nights", "median_w"])
        for (year, season), values in by_year.items():
            writer.writerow([season, year, len(values), median(values) if values else ""])
        for season, values in pooled.items():
            writer.writerow([season, "all", len(values), median(values) if values else ""])
    print(f"Seasonal summary: {csv_path}")

    available = [(season, values) for season, values in pooled.items() if values]
    if available:
        fig, ax = plt.subplots(figsize=(8, 4), layout="constrained")
        ax.boxplot([values for _, values in available], showfliers=False, showmeans=False)
        ax.set_xticks(
            range(1, len(available) + 1),
            [f"{season}\n{median(values):.0f} W · {len(values)} nights" for season, values in available],
        )
        ax.set(title="Overnight demand by season · 00:00–05:00 UK time",
               ylabel="Nightly median power (W)")
        ax.grid(axis="y", alpha=0.25)
        chart = output / f"seasonal_{suffix}.png"
        fig.savefig(chart, dpi=150)
        plt.close(fig)
        print(f"Seasonal chart: {chart}")


def plot_years(nights, output, suffix):
    by_year = {}
    for day, watts in nights:
        by_year.setdefault(day.year, []).append(watts)

    csv_path = output / f"yearly_{suffix}.csv"
    values_by_year, labels = [], []
    with csv_path.open("w", newline="") as file:
        writer = csv.writer(file)
        writer.writerow(["year", "complete_nights", "nights_in_range", "median_w"])
        for year, watts in by_year.items():
            complete = [value for value in watts if math.isfinite(value)]
            typical = median(complete) if complete else None
            writer.writerow([year, len(complete), len(watts), typical])
            if complete:
                values_by_year.append(complete)
                labels.append(f"{year}\n{typical:.0f} W · {len(complete)} nights")
    print(f"Yearly summary: {csv_path}")

    if values_by_year:
        fig, ax = plt.subplots(figsize=(9, 4), layout="constrained")
        ax.boxplot(values_by_year, showfliers=False)
        ax.set_xticks(range(1, len(labels) + 1), labels)
        ax.set(title="Overnight demand by year · 00:00–05:00 UK time",
               ylabel="Nightly median power (W)")
        ax.grid(axis="y", alpha=0.25)
        chart = output / f"yearly_{suffix}.png"
        fig.savefig(chart, dpi=150)
        plt.close(fig)
        print(f"Yearly chart: {chart}")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--start", type=date.fromisoformat, help="First UK date (YYYY-MM-DD)")
    parser.add_argument("--end", type=date.fromisoformat, help="Last UK date, inclusive")
    parser.add_argument("--account-id", type=int, help="OVO account number for downloads")
    parser.add_argument("--csv", type=Path, help="Plot a previously exported CSV without logging in")
    parser.add_argument("--seasonal", action="store_true", help="Compare overnight demand by season")
    parser.add_argument("--yearly", action="store_true", help="Compare overnight demand by calendar year")
    parser.add_argument("--output", type=Path, default=Path("output"))
    args = parser.parse_args()
    try:
        if args.csv:
            readings = read_csv(args.csv)
            if not readings:
                raise ValueError("The CSV has no readings.")
            start = args.start or min(readings).astimezone(LONDON).date()
            end = args.end or max(readings).astimezone(LONDON).date()
        else:
            end = args.end or datetime.now(LONDON).date() - DAY
            start = args.start or end - 29 * DAY
        if start > end:
            raise ValueError("--start must be on or before --end.")
        if not args.csv and end >= datetime.now(LONDON).date():
            raise ValueError("Choose an end date before today for a download.")

        args.output.mkdir(parents=True, exist_ok=True)
        if not args.csv:
            readings = asyncio.run(download(start, end, args.account_id))
            path = args.output / f"electricity_{start}_{end}.csv"
            export_csv(path, readings)
            print(f"Saved {len(readings)} half-hourly readings to {path}")
        nights = list(overnight_baselines(readings, start, end))
        suffix = f"{start}_{end}"
        plot_overnight(nights, args.output / f"overnight_{suffix}.png")
        if args.seasonal:
            plot_seasons(nights, args.output, suffix)
        if args.yearly:
            plot_years(nights, args.output, suffix)
    except (ValueError, OSError, KeyError) as exc:
        parser.exit(1, f"Error: {exc}\n")
    except Exception as exc:
        parser.exit(1, f"Error: {type(exc).__name__}. Check your connection and whether My OVO is working.\n")


if __name__ == "__main__":
    main()
