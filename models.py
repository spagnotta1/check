from datetime import datetime
from flask_sqlalchemy import SQLAlchemy
from sqlalchemy import Index

db = SQLAlchemy()


class AppUser(db.Model):
    """Login credentials for the app owner (created via first-run setup)."""
    __tablename__ = 'app_users'

    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(80), nullable=False, unique=True)
    password_hash = db.Column(db.String(255), nullable=False)
    created_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)


class Transaction(db.Model):
    __tablename__ = 'transactions'

    id = db.Column(db.Integer, primary_key=True)
    account_name = db.Column(db.String(50), nullable=False)
    date = db.Column(db.Date, nullable=False)
    description = db.Column(db.String(255), nullable=False)
    amount = db.Column(db.Numeric(10, 2), nullable=False)
    category = db.Column(db.String(50), nullable=False, default='Uncategorized')
    imported_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)
    anomaly_score = db.Column(db.Float, nullable=True)
    notes = db.Column(db.Text, nullable=True)
    import_batch_id = db.Column(db.String(36), nullable=True, index=True)
    anomaly_reviewed = db.Column(db.Boolean, nullable=False, default=False, server_default='0')

    # --- synchronization fields (populated by finance_sync) ---
    source = db.Column(db.String(10), nullable=False, default='csv', server_default='csv')  # 'csv' | 'sync' | 'manual'
    account_id = db.Column(db.Integer, db.ForeignKey('financial_accounts.id'), nullable=True, index=True)
    external_id = db.Column(db.String(120), nullable=True)

    # Unique indexes prevent duplicates for both CSV imports (by content)
    # and synced imports (by provider transaction ID).
    __table_args__ = (
        Index('idx_transaction_unique',
              'account_name', 'date', 'description', 'amount',
              unique=True),
        Index('idx_transaction_external_unique', 'account_id', 'external_id', unique=True),
    )

    def __repr__(self):
        return f'<Transaction {self.id}: {self.date} {self.description} {self.amount}>'


class Budget(db.Model):
    __tablename__ = 'budgets'

    id = db.Column(db.Integer, primary_key=True)
    category = db.Column(db.String(50), nullable=False)
    account_name = db.Column(db.String(50), nullable=False, default='both')
    monthly_limit = db.Column(db.Numeric(10, 2), nullable=False)

    __table_args__ = (
        Index('idx_budget_unique', 'category', 'account_name', unique=True),
    )

    def to_dict(self):
        return {
            'id': self.id,
            'category': self.category,
            'account_name': self.account_name,
            'monthly_limit': float(self.monthly_limit),
        } 

class LogEntry(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    account_type = db.Column(db.String(50), nullable=False)  # 'checking' or 'savings'
    date = db.Column(db.Date, nullable=False)
    description = db.Column(db.String(200), nullable=False)
    amount = db.Column(db.Float, nullable=False)
    cleared = db.Column(db.Boolean, default=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    
    # Balance fields
    starting_balance = db.Column(db.Float, nullable=False)
    pending_total = db.Column(db.Float, nullable=False)
    cleared_balance = db.Column(db.Float, nullable=False)
    available_balance = db.Column(db.Float, nullable=False)

    def to_dict(self):
        return {
            'id': str(self.id),
            'account_type': self.account_type,
            'date': self.date.strftime('%Y-%m-%d'),
            'description': self.description,
            'amount': self.amount,
            'cleared': self.cleared,
            'starting_balance': self.starting_balance,
            'pending_total': self.pending_total,
            'cleared_balance': self.cleared_balance,
            'available_balance': self.available_balance
        }

class AccountBalance(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    account_type = db.Column(db.String(50), nullable=False, unique=True)  # 'checking' or 'savings'
    starting_balance = db.Column(db.Float, nullable=False, default=0)
    last_updated = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    def to_dict(self):
        return {
            'account_type': self.account_type,
            'starting_balance': self.starting_balance,
            'last_updated': self.last_updated.strftime('%Y-%m-%d %H:%M:%S')
        }


class Conversation(db.Model):
    __tablename__ = 'conversations'

    id         = db.Column(db.String(36), primary_key=True)
    title      = db.Column(db.String(80), nullable=False, default='New Chat')
    created_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)


class ChatMessage(db.Model):
    __tablename__ = 'chat_messages'

    id         = db.Column(db.Integer, primary_key=True)
    session_id = db.Column(db.String(36), nullable=False, index=True)
    role       = db.Column(db.String(20), nullable=False)   # 'user' | 'assistant'
    content    = db.Column(db.Text, nullable=False)
    created_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)


class RecurringDismissal(db.Model):
    """A detected recurring bill/subscription the user has marked as not
    actually recurring. Matched against detected groups by normalized
    description (see recurring.py), so it survives re-detection."""
    __tablename__ = 'recurring_dismissals'

    id          = db.Column(db.Integer, primary_key=True)
    desc_key    = db.Column(db.String(255), nullable=False, unique=True)  # normalized description
    description = db.Column(db.String(255), nullable=False)  # as displayed when dismissed
    kind        = db.Column(db.String(20), nullable=False, default='subscription')  # 'bill' | 'subscription'
    created_at  = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)


class Holding(db.Model):
    __tablename__ = 'holdings'

    id            = db.Column(db.Integer, primary_key=True)
    ticker        = db.Column(db.String(20), nullable=False)
    name          = db.Column(db.String(100), nullable=False)
    shares        = db.Column(db.Numeric(14, 6), nullable=False, default=0)
    current_value = db.Column(db.Numeric(12, 2), nullable=False)
    asset_class   = db.Column(db.String(20), nullable=False, default='Stock')
    account_name  = db.Column(db.String(50), nullable=False, default='Brokerage')
    updated_at    = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    # --- synchronization fields (populated by finance_sync) ---
    source         = db.Column(db.String(10), nullable=False, default='manual', server_default='manual')  # 'manual' | 'sync'
    account_id     = db.Column(db.Integer, db.ForeignKey('financial_accounts.id'), nullable=True, index=True)
    external_id    = db.Column(db.String(120), nullable=True)
    avg_cost       = db.Column(db.Numeric(14, 4), nullable=True)   # per-share cost basis
    current_price  = db.Column(db.Numeric(14, 4), nullable=True)
    last_synced_at = db.Column(db.DateTime, nullable=True)

    __table_args__ = (
        Index('idx_holding_sync_unique', 'account_id', 'ticker', unique=True),
    )

    @property
    def cost_basis(self):
        if self.avg_cost is None or self.shares is None:
            return None
        return round(float(self.avg_cost) * float(self.shares), 2)

    @property
    def gain_loss(self):
        basis = self.cost_basis
        if basis is None:
            return None
        return round(float(self.current_value) - basis, 2)

    @property
    def gain_pct(self):
        basis = self.cost_basis
        if not basis:
            return None
        return round((float(self.current_value) - basis) / basis * 100, 2)

    def to_dict(self):
        return {
            'id': self.id,
            'ticker': self.ticker,
            'name': self.name,
            'shares': float(self.shares),
            'current_value': float(self.current_value),
            'asset_class': self.asset_class,
            'account_name': self.account_name,
            'updated_at': self.updated_at.strftime('%Y-%m-%d %H:%M') if self.updated_at else None,
            'source': self.source,
            'account_id': self.account_id,
            'avg_cost': float(self.avg_cost) if self.avg_cost is not None else None,
            'current_price': float(self.current_price) if self.current_price is not None else None,
            'cost_basis': self.cost_basis,
            'gain_loss': self.gain_loss,
            'gain_pct': self.gain_pct,
            'last_synced_at': self.last_synced_at.strftime('%Y-%m-%d %H:%M') if self.last_synced_at else None,
        }


# ═══════════════════════════════════════════════════════════════════════════
# Financial institution synchronization (finance_sync)
# ═══════════════════════════════════════════════════════════════════════════

class InstitutionConnection(db.Model):
    """A user's connection to a financial institution.

    Most adapters have exactly one connection per institution slug. Aggregator
    adapters (e.g. Plaid) can have many — one per linked "Item" — distinguished
    by ``item_id``. ``UNIQUE(institution, item_id)`` still lets single-item
    adapters behave as before: SQL treats NULL as distinct from every other
    NULL, so multiple rows sharing an institution with ``item_id IS NULL``
    would technically be permitted at the DB level, but application logic
    (``ConnectionService``) never creates more than one for those adapters.
    """
    __tablename__ = 'connected_accounts'

    id               = db.Column(db.Integer, primary_key=True)
    institution      = db.Column(db.String(40), nullable=False)  # adapter slug
    item_id          = db.Column(db.String(80), nullable=True)  # aggregator item id (Plaid); NULL for single-item adapters
    display_name     = db.Column(db.String(80), nullable=False)
    status           = db.Column(db.String(20), nullable=False, default='connected')  # connected | error | expired | disconnected
    auth_blob        = db.Column(db.Text, nullable=True)  # encrypted OAuth/API tokens (never usernames/passwords)
    token_expires_at = db.Column(db.DateTime, nullable=True)
    last_sync_at     = db.Column(db.DateTime, nullable=True)
    last_sync_status = db.Column(db.String(20), nullable=True)  # success | partial | error
    last_error       = db.Column(db.Text, nullable=True)
    created_at       = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)
    updated_at       = db.Column(db.DateTime, nullable=False, default=datetime.utcnow, onupdate=datetime.utcnow)

    __table_args__ = (
        Index('uq_institution_item', 'institution', 'item_id', unique=True),
    )

    accounts = db.relationship('FinancialAccount', backref='connection',
                               cascade='all, delete-orphan', lazy=True)

    def to_dict(self):
        return {
            'id': self.id,
            'institution': self.institution,
            'item_id': self.item_id,
            'display_name': self.display_name,
            'status': self.status,
            'last_sync_at': self.last_sync_at.strftime('%Y-%m-%d %H:%M:%S') if self.last_sync_at else None,
            'last_sync_status': self.last_sync_status,
            'last_error': self.last_error,
            'created_at': self.created_at.strftime('%Y-%m-%d %H:%M:%S'),
            'account_count': len(self.accounts),
        }


class FinancialAccount(db.Model):
    """A synchronized account (checking, savings, brokerage, crypto) at an institution."""
    __tablename__ = 'financial_accounts'

    id                = db.Column(db.Integer, primary_key=True)
    connection_id     = db.Column(db.Integer, db.ForeignKey('connected_accounts.id'), nullable=False, index=True)
    external_id       = db.Column(db.String(120), nullable=False)
    name              = db.Column(db.String(120), nullable=False)
    account_type      = db.Column(db.String(20), nullable=False)  # checking | savings | brokerage | crypto | credit | other
    currency          = db.Column(db.String(10), nullable=False, default='USD')
    mask              = db.Column(db.String(10), nullable=True)
    balance           = db.Column(db.Numeric(14, 2), nullable=False, default=0)
    available_balance = db.Column(db.Numeric(14, 2), nullable=True)
    is_active         = db.Column(db.Boolean, nullable=False, default=True)
    last_synced_at    = db.Column(db.DateTime, nullable=True)
    created_at        = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)
    updated_at        = db.Column(db.DateTime, nullable=False, default=datetime.utcnow, onupdate=datetime.utcnow)

    __table_args__ = (
        Index('idx_finacct_unique', 'connection_id', 'external_id', unique=True),
    )

    def to_dict(self):
        return {
            'id': self.id,
            'connection_id': self.connection_id,
            'institution': self.connection.institution if self.connection else None,
            'name': self.name,
            'account_type': self.account_type,
            'currency': self.currency,
            'mask': self.mask,
            'balance': float(self.balance),
            'available_balance': float(self.available_balance) if self.available_balance is not None else None,
            'is_active': self.is_active,
            'last_synced_at': self.last_synced_at.strftime('%Y-%m-%d %H:%M:%S') if self.last_synced_at else None,
        }


class SyncRun(db.Model):
    """One synchronization attempt (per connection, or engine-wide)."""
    __tablename__ = 'sync_history'

    id                   = db.Column(db.Integer, primary_key=True)
    connection_id        = db.Column(db.Integer, db.ForeignKey('connected_accounts.id'), nullable=True, index=True)
    institution          = db.Column(db.String(40), nullable=True)  # denormalized for display after disconnect
    trigger              = db.Column(db.String(20), nullable=False, default='manual')  # manual | scheduled | connect
    status               = db.Column(db.String(20), nullable=False, default='running')  # running | success | partial | error
    started_at           = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)
    finished_at          = db.Column(db.DateTime, nullable=True)
    accounts_synced      = db.Column(db.Integer, nullable=False, default=0)
    balances_updated     = db.Column(db.Integer, nullable=False, default=0)
    holdings_synced      = db.Column(db.Integer, nullable=False, default=0)
    transactions_added   = db.Column(db.Integer, nullable=False, default=0)
    transactions_skipped = db.Column(db.Integer, nullable=False, default=0)
    error_message        = db.Column(db.Text, nullable=True)

    errors = db.relationship('SyncErrorLog', backref='run', cascade='all, delete-orphan', lazy=True)

    def to_dict(self):
        return {
            'id': self.id,
            'connection_id': self.connection_id,
            'institution': self.institution,
            'trigger': self.trigger,
            'status': self.status,
            'started_at': self.started_at.strftime('%Y-%m-%d %H:%M:%S'),
            'finished_at': self.finished_at.strftime('%Y-%m-%d %H:%M:%S') if self.finished_at else None,
            'accounts_synced': self.accounts_synced,
            'balances_updated': self.balances_updated,
            'holdings_synced': self.holdings_synced,
            'transactions_added': self.transactions_added,
            'transactions_skipped': self.transactions_skipped,
            'error_message': self.error_message,
            'errors': [e.to_dict() for e in self.errors],
        }


class SyncErrorLog(db.Model):
    """Individual errors captured during a sync run."""
    __tablename__ = 'sync_errors'

    id            = db.Column(db.Integer, primary_key=True)
    run_id        = db.Column(db.Integer, db.ForeignKey('sync_history.id'), nullable=True, index=True)
    connection_id = db.Column(db.Integer, nullable=True)
    institution   = db.Column(db.String(40), nullable=True)
    error_type    = db.Column(db.String(40), nullable=False, default='sync_error')
    message       = db.Column(db.Text, nullable=False)
    is_transient  = db.Column(db.Boolean, nullable=False, default=False)
    attempt       = db.Column(db.Integer, nullable=False, default=1)
    created_at    = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)

    def to_dict(self):
        return {
            'id': self.id,
            'institution': self.institution,
            'error_type': self.error_type,
            'message': self.message,
            'is_transient': self.is_transient,
            'attempt': self.attempt,
            'created_at': self.created_at.strftime('%Y-%m-%d %H:%M:%S'),
        }


class PortfolioSnapshotRow(db.Model):
    """Daily net-worth snapshot written after each sync (one row per day)."""
    __tablename__ = 'portfolio_snapshots'

    id                = db.Column(db.Integer, primary_key=True)
    snapshot_date     = db.Column(db.Date, nullable=False, unique=True)
    checking          = db.Column(db.Numeric(14, 2), nullable=False, default=0)
    savings           = db.Column(db.Numeric(14, 2), nullable=False, default=0)
    total_cash        = db.Column(db.Numeric(14, 2), nullable=False, default=0)
    brokerage         = db.Column(db.Numeric(14, 2), nullable=False, default=0)
    crypto            = db.Column(db.Numeric(14, 2), nullable=False, default=0)
    total_investments = db.Column(db.Numeric(14, 2), nullable=False, default=0)
    net_worth         = db.Column(db.Numeric(14, 2), nullable=False, default=0)
    created_at        = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)

    def to_dict(self):
        return {
            'date': self.snapshot_date.strftime('%Y-%m-%d'),
            'checking': float(self.checking),
            'savings': float(self.savings),
            'total_cash': float(self.total_cash),
            'brokerage': float(self.brokerage),
            'crypto': float(self.crypto),
            'total_investments': float(self.total_investments),
            'net_worth': float(self.net_worth),
        }


class MarketPrice(db.Model):
    """Latest known market price per symbol (updated on every sync)."""
    __tablename__ = 'market_prices'

    id          = db.Column(db.Integer, primary_key=True)
    symbol      = db.Column(db.String(20), nullable=False, unique=True)
    name        = db.Column(db.String(100), nullable=True)
    price       = db.Column(db.Numeric(14, 4), nullable=False)
    currency    = db.Column(db.String(10), nullable=False, default='USD')
    asset_class = db.Column(db.String(20), nullable=True)
    source      = db.Column(db.String(40), nullable=True)  # institution slug that reported it
    as_of       = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)

    def to_dict(self):
        return {
            'symbol': self.symbol,
            'name': self.name,
            'price': float(self.price),
            'currency': self.currency,
            'asset_class': self.asset_class,
            'source': self.source,
            'as_of': self.as_of.strftime('%Y-%m-%d %H:%M:%S'),
        }