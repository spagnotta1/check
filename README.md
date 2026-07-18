# Checkbook App

A local web application for managing and analyzing your Capital One bank transactions. This app allows you to upload CSV exports from your Capital One accounts, automatically categorize transactions, and view detailed breakdowns of your spending.

## Features

- **Automatic account synchronization** — connect via Plaid (any supported
  bank, brokerage, or crypto exchange) or Coinbase directly once; balances,
  holdings, and transactions then sync automatically every 12 hours (plus
  manual Refresh / Refresh All)
- Upload CSV exports from Capital One accounts (still supported)
- Automatic transaction categorization based on keywords
- Customizable categorization rules
- Dashboard with spending breakdowns and charts
- Investments page with synced holdings (shares, avg cost, price, gain/loss)
- Net worth and allocation charts that update automatically after every sync
- Sync History page with per-run stats and error log
- Detailed transaction list with filtering and search
- Local storage using SQLite
- No authentication required (single-user application)

## Account Synchronization (finance_sync)

The `finance_sync/` package implements the Adapter Pattern: every institution
is a single adapter class registered in `finance_sync/adapters/`, and the
institution-agnostic `SyncEngine` runs the same pipeline for each —
refresh token → validate → fetch → normalize to canonical models → validate →
persist to SQLite → log.

- **Sandbox mode (default):** with no API credentials configured, adapters use
  deterministic simulated provider backends (`finance_sync/sandbox.py`) so the
  entire connect → sync → dashboard flow works locally.
- **Live mode:** set the institution's environment variables in `.env` and the
  adapter switches to the real API with no code changes:
  - Plaid (recommended): `PLAID_CLIENT_ID`, `PLAID_SECRET`, `PLAID_ENV`
    (`sandbox` | `development` | `production`, default `sandbox`). Get
    credentials from the Plaid dashboard. Plaid is an aggregator — one
    connection lets you link many real institutions (banks, brokerages,
    crypto exchanges, including Capital One and most others) through an
    embedded widget (Plaid Link), each becoming its own row under "Connected
    Institutions".
  - Coinbase: `COINBASE_CLIENT_ID`, `COINBASE_CLIENT_SECRET` (OAuth2) — a
    direct integration independent of Plaid, useful if you'd rather not route
    crypto through the aggregator.

  Two other adapters (Capital One, E*Trade) and a designed-but-unbuildable
  Vanguard adapter were removed: Capital One has no self-serve public API,
  and E*Trade's developer access requires their partner program — both are
  better covered through Plaid anyway.
- **Tokens** are stored encrypted (Fernet). The key comes from
  `SYNC_ENCRYPTION_KEY` or is generated once into `.sync_encryption_key`.
  Usernames and passwords are never stored.
- **Settings:** `SYNC_INTERVAL_HOURS` (default 12), `SYNC_AUTO_ENABLED`
  (set `0` to disable background sync).
- **Adding an institution** requires exactly one new adapter class decorated
  with `@register_adapter` — no synchronization code changes.

Run the test suite with `python -m pytest tests/`.

## Requirements

- Python 3.11 or higher
- pip (Python package manager)

## Installation

1. Clone this repository:
```bash
git clone <repository-url>
cd checkbook-app
```

2. Create and activate a virtual environment:
```bash
# Windows
python -m venv venv
.\venv\Scripts\activate

# macOS/Linux
python -m venv venv
source venv/bin/activate
```

3. Install dependencies:
```bash
pip install -r requirements.txt
```

4. Initialize the database:
```bash
python init_db.py
```

## Usage

1. Start the application:
```bash
flask --app app run
```

2. Open your web browser and navigate to `http://localhost:5000`

3. Upload your Capital One CSV exports:
   - Go to the Upload page
   - Select the account type (Checking or Savings)
   - Choose your CSV file(s)
   - Click Upload

4. View and manage your transactions:
   - Dashboard: View spending summaries and charts
   - Transactions: Browse and filter your transaction history
   - Rules: Manage automatic categorization rules

## CSV Format

The application expects CSV files exported from Capital One with the following columns:
- Date
- Description
- Amount
- Category
- Balance

## Customizing Categories

You can customize how transactions are automatically categorized:

1. Go to the Rules page
2. Add new rules by specifying:
   - Category name
   - Keyword to match in transaction descriptions
3. Remove existing rules as needed

## Development

- The application uses Flask for the backend
- SQLite with SQLAlchemy for data storage
- Tailwind CSS for styling
- Chart.js for visualizations
- Alpine.js for simple interactivity

## License

This project is licensed under the MIT License - see the LICENSE file for details. 