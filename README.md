# OVO electricity history

Download half-hourly electricity readings from your UK OVO account and plot
your overnight demand. You need half-hourly electricity data available in My OVO.

## Run

From the project directory, install the locked dependencies and run the script.

```sh
uv sync --locked
uv run --locked ovo_data.py --start 2026-10-01 --end 2026-10-31
```

Set `OVO_USERNAME` to your My OVO email and `OVO_PASSWORD` to your password
to run without prompts. The script prompts for each unset or empty value and
hides the password as you type. If your login has multiple accounts, use `--account-id` with the
account number. Dates are inclusive and use UK local time. With no dates, the
script requests the 30 days ending yesterday. It makes one request per UK calendar day,
so large ranges take longer.

The script writes a CSV and a PNG to `output/`. The CSV has UTC and UK
timestamps and electricity use in kWh per half hour. To plot it again offline:

```sh
uv run --locked ovo_data.py --csv output/electricity_2026-10-01_2026-10-31.csv
```

The chart uses the median half-hour average power from midnight to 05:00 UK time
for each night. For example, 0.10 kWh in half an hour is 200 W on average.
The dashed line is the median of the complete nights. Short spikes have less
influence on this estimate, while sustained overnight loads can raise it.
The chart leaves gaps for incomplete nights and accounts for clock changes.

Add `--seasonal` to a download or offline plot to compare complete nights
across meteorological seasons. The seasonal CSV includes results by
season and year and across all years. December counts toward the following
year's winter.

Add `--yearly` to compare complete nights by calendar year. The yearly CSV
includes the number of nights in the selected date range for each year.

The script uses the community-maintained [ovoenergy client](https://pypi.org/project/ovoenergy/).
It depends on the account interface that OVO uses for My OVO.
