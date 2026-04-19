# Allure TC Stats

Tiny Python CLI that reads one or more Allure report URLs and produces a CSV per
report with per-test-case statistics: **TC**, **Total Runs**, **Failed**
(`AF` / `SKP` / number), and **Success Rate**.

## Requirements

- Python 3.9 or newer
- `pip`
- Internet access to the Allure report host

## Install
```bash
# 1. Clone the repo (HTTPS)
git clone https://github.com/BarbateSparrow/sp-allure-tc-stats.git
cd allure-tc-stats

# 2. Create a virtual environment
#    Windows (PowerShell):
python -m venv .venv
.\.venv\Scripts\Activate.ps1

#    macOS / Linux:
python3 -m venv .venv
source .venv/bin/activate

# 3. Install the single dependency
pip install -r requirements.txt
```

## Run

Single report:
```bash
python allure_tc_stats.py \
    "http://host:4446/allure-docker-service/projects/regression-office-dashboard/reports/20/index.html#"
```

Multiple reports:
```bash
python allure_tc_stats.py URL1 URL2 URL3 --out ./reports
```

Many URLs from a file (one URL per line, `#` for comments):
```bash
python allure_tc_stats.py --urls-file urls.txt --out ./reports
```

CSVs are written to `./reports/<project>_report-<n>.csv`.

## Update to the latest version
```bash
cd allure-tc-stats
git pull
# Re-activate the virtualenv if needed, then:
pip install -r requirements.txt   # only if dependencies changed
```

## Options

| Flag          | Default     | Description                           |
|---------------|-------------|---------------------------------------|
| `--out`       | `./reports` | Output directory for CSV files        |
| `--urls-file` | –           | Text file with one URL per line       |
| `--workers`   | `16`        | Parallel HTTP workers per report      |
| `--timeout`   | `30`        | Per-request timeout in seconds        |