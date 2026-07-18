import hmac
import io
import json
import os
import re
import time
import uuid
from dotenv import load_dotenv
load_dotenv()
from calendar import monthrange
from datetime import datetime, timedelta
from flask import Flask, render_template, request, jsonify, redirect, url_for, flash, session, Response, stream_with_context
from flask_migrate import Migrate
from werkzeug.utils import secure_filename
import anthropic
import pandas as pd
import numpy as np
from sqlalchemy.exc import IntegrityError
from sklearn.ensemble import IsolationForest
from sqlalchemy import func, and_, or_

from config import Config
from models import (db, Transaction, LogEntry, AccountBalance, Budget, Holding,
                    ChatMessage, Conversation, InstitutionConnection,
                    FinancialAccount, SyncRun)
from rules import CategoryRules
from finance_sync.repository import SyncRepository
from finance_sync.routes import sync_bp
from finance_sync.scheduler import init_scheduler

_insight_cache = {'text': None, 'expires': 0}


def _md_to_html(text):
    """Convert Claude's markdown to HTML with no external dependencies."""
    if not text:
        return ''

    def esc(s):
        return s.replace('&', '&amp;').replace('<', '&lt;').replace('>', '&gt;')

    def fmt(s):
        s = esc(s)
        s = re.sub(r'\*\*(.+?)\*\*', r'<strong>\1</strong>', s)
        s = re.sub(r'\*(.+?)\*', r'<em>\1</em>', s)
        s = re.sub(r'`(.+?)`', r'<code style="background:#f3f4f6;padding:.1em .3em;border-radius:3px;font-size:.85em">\1</code>', s)
        return s

    lines = text.split('\n')
    parts = []
    i = 0
    while i < len(lines):
        s = lines[i].strip()
        if not s:
            i += 1
            continue
        if re.match(r'^-{3,}$', s) or re.match(r'^\*{3,}$', s):
            parts.append('<hr style="border:none;border-top:1px solid #e5e7eb;margin:.6em 0">')
            i += 1; continue
        if s.startswith('### '):
            parts.append(f'<h3 style="font-weight:600;font-size:.9rem;margin:.75em 0 .2em;color:#1f2937">{fmt(s[4:])}</h3>')
            i += 1; continue
        if s.startswith('## '):
            parts.append(f'<h2 style="font-weight:700;font-size:1rem;margin:.85em 0 .25em;color:#111827">{fmt(s[3:])}</h2>')
            i += 1; continue
        if s.startswith('# '):
            parts.append(f'<h1 style="font-weight:700;font-size:1.1rem;margin:1em 0 .3em;color:#111827">{fmt(s[2:])}</h1>')
            i += 1; continue
        if s.startswith('|'):
            tbl, thead, tbody_rows, hdr_done = [], '', [], False
            while i < len(lines) and lines[i].strip().startswith('|'):
                tbl.append(lines[i].strip()); i += 1
            for row in tbl:
                if re.match(r'^\|[\s\-\:|]+\|$', row):
                    hdr_done = True; continue
                cells = [c.strip() for c in row.split('|')[1:-1]]
                if not hdr_done:
                    thead = '<thead><tr>' + ''.join(
                        f'<th style="font-weight:600;text-align:left;padding:.35em .6em;border:1px solid #ddd6fe;background:#f3f0ff;color:#5b21b6;font-size:.78rem">{fmt(c)}</th>'
                        for c in cells) + '</tr></thead>'
                else:
                    tbody_rows.append('<tr>' + ''.join(
                        f'<td style="padding:.3em .6em;border:1px solid #e5e7eb;font-size:.8rem;vertical-align:top">{fmt(c)}</td>'
                        for c in cells) + '</tr>')
            parts.append(f'<div style="overflow-x:auto;margin:.5em 0"><table style="width:100%;border-collapse:collapse">'
                         f'{thead}<tbody>{"".join(tbody_rows)}</tbody></table></div>')
            continue
        if re.match(r'^[-*] ', s):
            items = []
            while i < len(lines) and re.match(r'^[-*] ', lines[i].strip()):
                items.append(f'<li style="margin:.2em 0">{fmt(lines[i].strip()[2:])}</li>'); i += 1
            parts.append(f'<ul style="margin:.35em 0;padding-left:1.4em;list-style:disc">{"".join(items)}</ul>')
            continue
        if re.match(r'^\d+\. ', s):
            items = []
            while i < len(lines) and re.match(r'^\d+\. ', lines[i].strip()):
                item_text = re.sub(r'^\d+\. ', '', lines[i].strip())
                items.append(f'<li style="margin:.2em 0">{fmt(item_text)}</li>'); i += 1
            parts.append(f'<ol style="margin:.35em 0;padding-left:1.4em;list-style:decimal">{"".join(items)}</ol>')
            continue
        parts.append(f'<p style="margin:.3em 0;line-height:1.6">{fmt(s)}</p>')
        i += 1
    return ''.join(parts)

def create_app(test_config=None):
    app = Flask(__name__)
    app.config.from_object(Config)
    if test_config:
        app.config.update(test_config)
    os.makedirs(app.config['UPLOAD_FOLDER'], exist_ok=True)
    db.init_app(app)
    migrate = Migrate(app, db)
    category_rules = CategoryRules()

    # ---------------------------------------------------------------------------
    # Optional password gate — set APP_PASSWORD in .env to require a login.
    # Required before exposing the app beyond localhost (APP_HOST=0.0.0.0):
    # there are no per-user accounts, so this is the only thing standing
    # between the network and your financial data.
    # ---------------------------------------------------------------------------
    app_password = os.environ.get('APP_PASSWORD')
    if app_password:
        @app.before_request
        def _require_login():
            if session.get('authed'):
                return None
            if request.endpoint in ('login', 'static'):
                return None
            if request.path.startswith('/api/'):
                return jsonify({'error': 'authentication required'}), 401
            return redirect(url_for('login', next=request.path))

        @app.route('/login', methods=['GET', 'POST'])
        def login():
            error = None
            if request.method == 'POST':
                if hmac.compare_digest(request.form.get('password', ''), app_password):
                    session['authed'] = True
                    session.permanent = True
                    nxt = request.args.get('next', '/')
                    if not nxt.startswith('/') or nxt.startswith('//'):
                        nxt = '/'
                    return redirect(nxt)
                error = 'Wrong password.'
            return render_template('login.html', error=error)

        @app.route('/logout', methods=['POST'])
        def logout():
            session.clear()
            return redirect(url_for('login'))

    @app.template_filter('dict_update')
    def dict_update_filter(d, updates):
        """Jinja2 filter: return a copy of dict d merged with the updates dict."""
        result = dict(d)
        result.update(updates)
        return result

    # ---------------------------------------------------------------------------
    # Helpers
    # ---------------------------------------------------------------------------

    def _compute_anomaly_scores():
        """Recompute Isolation Forest anomaly scores for all transactions and persist."""
        transactions = Transaction.query.all()
        if len(transactions) < 10:
            return
        df = pd.DataFrame([{
            'id': t.id,
            'abs_amount': abs(float(t.amount)),
            'day_of_week': pd.to_datetime(t.date).dayofweek,
            'day_of_month': pd.to_datetime(t.date).day,
        } for t in transactions])
        X = df[['abs_amount', 'day_of_week', 'day_of_month']].replace([np.inf, -np.inf], np.nan).dropna()
        model = IsolationForest(contamination='auto', random_state=42)
        scores = model.fit_predict(X)
        for tid, score in zip(df['id'], scores):
            t = Transaction.query.get(tid)
            if t:
                t.anomaly_score = float(score)
        db.session.commit()

    def _build_transaction_query(account_filter, category_filter, start_date_str,
                                  end_date_str, direction_filter, search_query):
        filters = []
        if account_filter and account_filter != 'both':
            filters.append(Transaction.account_name == account_filter)
        if category_filter:
            filters.append(Transaction.category == category_filter)
        if start_date_str:
            filters.append(Transaction.date >= datetime.strptime(start_date_str, '%Y-%m-%d'))
        if end_date_str:
            filters.append(Transaction.date <= datetime.strptime(end_date_str, '%Y-%m-%d'))
        if search_query:
            terms = []
            try:
                terms.append(Transaction.id == int(search_query))
            except ValueError:
                pass
            terms.append(Transaction.description.ilike(f'%{search_query}%'))
            filters.append(or_(*terms))
        if direction_filter == 'inbound':
            filters.append(Transaction.amount > 0)
        elif direction_filter == 'outgo':
            filters.append(Transaction.amount < 0)
        return Transaction.query.filter(and_(*filters)) if filters else Transaction.query

    def _compute_net_worth():
        """Net-worth breakdown from synced accounts + holdings.

        Prefers automatically synchronized balances (finance_sync) and falls
        back to the manually entered AccountBalance rows for account types
        that have never been synced. Adds brokerage/crypto detail on top of
        the original keys.
        """
        return SyncRepository.compute_totals()

    def _detect_recurring_summary():
        """Return a compact list of detected recurring expense items for Claude context."""
        txns = Transaction.query.filter(Transaction.amount < 0).order_by(Transaction.date.asc()).all()
        if not txns:
            return []
        df = pd.DataFrame([{
            'date': pd.to_datetime(t.date),
            'description': t.description,
            'amount': float(t.amount),
            'category': t.category,
        } for t in txns])
        df['desc_norm'] = df['description'].str.replace(r'\d+', '', regex=True).str.strip().str.lower()
        df['amount_bucket'] = df['amount'].round(0)
        result = []
        for (desc_norm, amt_bucket), grp in df.groupby(['desc_norm', 'amount_bucket']):
            if len(grp) < 2:
                continue
            grp = grp.sort_values('date')
            dates = grp['date'].tolist()
            gaps = [(dates[i+1] - dates[i]).days for i in range(len(dates)-1)]
            avg_gap = sum(gaps) / len(gaps)
            if 20 <= avg_gap <= 40:
                result.append({
                    'description': grp['description'].iloc[0],
                    'category': grp['category'].iloc[0],
                    'monthly_amount': abs(float(grp['amount'].iloc[0])),
                    'occurrences': len(grp),
                })
        return result

    def _build_finance_context(months=6):
        """Assemble a full financial snapshot for Claude — spending, net worth, and complete holdings detail."""
        cutoff = datetime.now() - timedelta(days=months * 30)

        # --- Spending by category (last N months) ---
        rows = (db.session.query(Transaction.category, func.sum(Transaction.amount))
                .filter(Transaction.date >= cutoff, Transaction.amount < 0)
                .group_by(Transaction.category)
                .order_by(func.sum(Transaction.amount))
                .all())
        spending = {cat: round(abs(float(amt)), 2) for cat, amt in rows}

        # --- Income (last N months) ---
        income = float(db.session.query(func.sum(Transaction.amount))
                       .filter(Transaction.date >= cutoff, Transaction.amount > 0)
                       .scalar() or 0)

        # --- Monthly income/expense trend (6 months) ---
        monthly_rows = (db.session.query(
            func.strftime('%Y-%m', Transaction.date).label('month'),
            func.sum(Transaction.amount).label('net'),
            func.sum(func.abs(Transaction.amount)).label('gross'),
        ).filter(Transaction.date >= cutoff)
         .group_by('month').order_by('month').all())
        monthly_trend = [{'month': r.month, 'net': round(float(r.net), 2)} for r in monthly_rows]

        # --- Budgets ---
        budget_status = [{'category': b.category, 'monthly_limit': float(b.monthly_limit)}
                         for b in Budget.query.all()]

        # --- Recurring subscriptions ---
        recurring_items = _detect_recurring_summary()

        # --- Net worth ---
        nw = _compute_net_worth()

        # --- Full holdings list with individual positions ---
        all_holdings = Holding.query.order_by(Holding.asset_class, Holding.ticker).all()
        total_invested = float(db.session.query(func.sum(Holding.current_value)).scalar() or 0)
        holdings_list = []
        for h in all_holdings:
            val = float(h.current_value)
            holdings_list.append({
                'ticker': h.ticker,
                'name': h.name,
                'shares': float(h.shares),
                'current_value': round(val, 2),
                'asset_class': h.asset_class,
                'account': h.account_name,
                'pct_of_portfolio': round(val / total_invested * 100, 1) if total_invested > 0 else 0,
            })

        # --- Asset class allocation (investments only, with % breakdown) ---
        asset_alloc = {}
        for h in all_holdings:
            ac = h.asset_class
            val = float(h.current_value)
            asset_alloc[ac] = asset_alloc.get(ac, 0) + val
        asset_alloc_detail = {
            ac: {
                'value': round(v, 2),
                'pct_of_investments': round(v / total_invested * 100, 1) if total_invested > 0 else 0,
            }
            for ac, v in sorted(asset_alloc.items(), key=lambda x: -x[1])
        }

        # --- Cash vs investments ratio ---
        total_nw = nw['net_worth']
        allocation_summary = {
            'cash_checking': nw['checking'],
            'cash_savings': nw['savings'],
            'total_cash': nw['cash'],
            'total_investments': round(total_invested, 2),
            'cash_pct': round(nw['cash'] / total_nw * 100, 1) if total_nw > 0 else 0,
            'investments_pct': round(total_invested / total_nw * 100, 1) if total_nw > 0 else 0,
        }

        return {
            'data_period_months': months,
            'net_worth': nw,
            'allocation_summary': allocation_summary,
            'holdings': holdings_list,
            'asset_class_allocation': asset_alloc_detail,
            'total_income_period': round(income, 2),
            'spending_by_category': spending,
            'monthly_cashflow_trend': monthly_trend,
            'budgets': budget_status,
            'recurring_subscriptions': recurring_items,
        }

    # ---------------------------------------------------------------------------
    # Dashboard
    # ---------------------------------------------------------------------------

    @app.route('/')
    def dashboard():
        start_date_str = request.args.get('start_date') or session.get('start_date')
        end_date_str = request.args.get('end_date') or session.get('end_date')
        account_filter = request.args.get('account') or session.get('account', 'both')

        if not start_date_str or not end_date_str:
            end_date = datetime.now()
            start_date = end_date.replace(day=1)
            start_date_str = start_date.strftime('%Y-%m-%d')
            end_date_str = end_date.strftime('%Y-%m-%d')
        else:
            start_date = datetime.strptime(start_date_str, '%Y-%m-%d')
            end_date = datetime.strptime(end_date_str, '%Y-%m-%d')

        session['start_date'] = start_date_str
        session['end_date'] = end_date_str
        session['account'] = account_filter

        query = Transaction.query.filter(Transaction.date.between(start_date, end_date))
        if account_filter != 'both':
            query = query.filter(Transaction.account_name == account_filter)
        transactions = query.all()

        def is_transfer(t):
            return t.category.lower() in ['transfer', 'transfers']

        total_income = sum(
            float(t.amount) for t in transactions
            if t.amount > 0 and (account_filter != 'both' or not is_transfer(t))
        )
        total_outgo = abs(sum(
            float(t.amount) for t in transactions
            if t.amount < 0 and (account_filter != 'both' or not is_transfer(t))
        ))
        net_cashflow = total_income - total_outgo

        category_stats = {}
        for t in transactions:
            if account_filter == 'both' and is_transfer(t):
                continue
            if t.category not in category_stats:
                category_stats[t.category] = {'inbound': 0, 'outbound': 0}
            if t.amount > 0:
                category_stats[t.category]['inbound'] += float(t.amount)
            else:
                category_stats[t.category]['outbound'] += abs(float(t.amount))

        # Monthly outgo trend
        monthly_outgo_q = db.session.query(
            func.strftime('%Y-%m', Transaction.date).label('month'),
            func.sum(func.abs(Transaction.amount)).label('total')
        ).filter(Transaction.date.between(start_date, end_date), Transaction.amount < 0)
        if account_filter != 'both':
            monthly_outgo_q = monthly_outgo_q.filter(Transaction.account_name == account_filter)
        else:
            monthly_outgo_q = monthly_outgo_q.filter(~func.lower(Transaction.category).in_(['transfer', 'transfers']))
        monthly_outgo_data = [{'month': r.month, 'total': float(r.total)}
                               for r in monthly_outgo_q.group_by('month').order_by('month').all()]

        # Monthly income trend
        monthly_income_q = db.session.query(
            func.strftime('%Y-%m', Transaction.date).label('month'),
            func.sum(Transaction.amount).label('total')
        ).filter(Transaction.date.between(start_date, end_date), Transaction.amount > 0)
        if account_filter != 'both':
            monthly_income_q = monthly_income_q.filter(Transaction.account_name == account_filter)
        else:
            monthly_income_q = monthly_income_q.filter(~func.lower(Transaction.category).in_(['transfer', 'transfers']))
        monthly_income_data = [{'month': r.month, 'total': float(r.total)}
                                for r in monthly_income_q.group_by('month').order_by('month').all()]

        # --- Balance history (running sum ordered by date) ---
        bal_q = Transaction.query.filter(Transaction.date.between(start_date, end_date))
        if account_filter != 'both':
            bal_q = bal_q.filter(Transaction.account_name == account_filter)
        bal_txns = bal_q.order_by(Transaction.date.asc()).all()
        running = 0.0
        balance_history = []
        for t in bal_txns:
            running += float(t.amount)
            balance_history.append({'date': t.date.strftime('%Y-%m-%d'), 'balance': round(running, 2)})

        # --- Month-over-Month comparison ---
        period_days = (end_date - start_date).days + 1
        prev_end = start_date - timedelta(days=1)
        prev_start = prev_end - timedelta(days=period_days - 1)
        prev_q = Transaction.query.filter(Transaction.date.between(prev_start, prev_end))
        if account_filter != 'both':
            prev_q = prev_q.filter(Transaction.account_name == account_filter)
        prev_txns = prev_q.all()
        prev_income = sum(float(t.amount) for t in prev_txns if t.amount > 0 and (account_filter != 'both' or not is_transfer(t)))
        prev_outgo = abs(sum(float(t.amount) for t in prev_txns if t.amount < 0 and (account_filter != 'both' or not is_transfer(t))))
        prev_net = prev_income - prev_outgo
        prev_category_stats = {}
        for t in prev_txns:
            if account_filter == 'both' and is_transfer(t):
                continue
            if t.category not in prev_category_stats:
                prev_category_stats[t.category] = {'inbound': 0, 'outbound': 0}
            if t.amount > 0:
                prev_category_stats[t.category]['inbound'] += float(t.amount)
            else:
                prev_category_stats[t.category]['outbound'] += abs(float(t.amount))

        # --- Budget data ---
        budgets = Budget.query.all()
        budget_map = {}
        for b in budgets:
            if b.account_name == 'both' or b.account_name == account_filter:
                budget_map[b.category] = float(b.monthly_limit)

        # Normalize spend to a monthly average so budget limits are always monthly comparisons.
        # For periods < 1 month we compare raw spend vs limit (no extrapolation).
        period_months = max(1.0, period_days / 30.44)

        # --- Budget alerts (B2) ---
        budget_alerts = []
        for cat, limit in budget_map.items():
            cat_stats = category_stats.get(cat, {})
            spent = max(0, cat_stats.get('outbound', 0) - cat_stats.get('inbound', 0))
            monthly_avg = spent / period_months
            if limit > 0 and spent > 0:
                pct = (monthly_avg / limit) * 100
                if pct >= 100:
                    budget_alerts.append({'category': cat, 'pct': round(pct), 'level': 'over',
                                          'spent': spent, 'monthly_avg': round(monthly_avg, 2), 'limit': limit})
                elif pct >= 80:
                    budget_alerts.append({'category': cat, 'pct': round(pct), 'level': 'warning',
                                          'spent': spent, 'monthly_avg': round(monthly_avg, 2), 'limit': limit})
        budget_alerts.sort(key=lambda x: x['pct'], reverse=True)

        # --- Spending insights (D1) ---
        insights = []
        for cat, stats in sorted(category_stats.items(), key=lambda x: x[1]['outbound'], reverse=True):
            if stats['outbound'] > 0:
                prev = prev_category_stats.get(cat, {}).get('outbound', 0)
                if prev > 0:
                    delta_pct = ((stats['outbound'] - prev) / prev) * 100
                    if abs(delta_pct) >= 20:
                        direction = 'up' if delta_pct > 0 else 'down'
                        positive = delta_pct < 0
                        insights.append({
                            'text': f"{cat} spending {direction} {abs(delta_pct):.0f}% vs. prior period "
                                    f"(${stats['outbound']:.0f} vs. ${prev:.0f})",
                            'positive': positive,
                        })
        if insights:
            insights = insights[:5]
        over_budget = [a for a in budget_alerts if a['level'] == 'over']
        under_budget = [c for c, lim in budget_map.items()
                        if max(0, category_stats.get(c, {}).get('outbound', 0) - category_stats.get(c, {}).get('inbound', 0)) / period_months < lim * 0.5]
        if over_budget:
            insights.insert(0, {'text': f"{len(over_budget)} budget(s) exceeded this period.", 'positive': False})
        if under_budget:
            insights.append({'text': f"Well within budget in: {', '.join(under_budget[:3])}.", 'positive': True})

        # --- Category monthly trend (D2) — same date range as the active filter ---
        trend_start = start_date
        trend_end = end_date
        top_spend_cats = sorted(
            [(c, s['outbound']) for c, s in category_stats.items() if s['outbound'] > 0],
            key=lambda x: x[1], reverse=True
        )[:6]
        top_cat_names = [c for c, _ in top_spend_cats]
        trend_q = db.session.query(
            func.strftime('%Y-%m', Transaction.date).label('month'),
            Transaction.category,
            func.sum(func.abs(Transaction.amount)).label('total')
        ).filter(
            Transaction.date.between(trend_start, trend_end),
            Transaction.amount < 0,
        )
        if account_filter != 'both':
            trend_q = trend_q.filter(Transaction.account_name == account_filter)
        if top_cat_names:
            trend_q = trend_q.filter(Transaction.category.in_(top_cat_names))
        trend_rows = trend_q.group_by('month', Transaction.category).order_by('month').all()
        trend_months = sorted(set(r.month for r in trend_rows))
        category_trend = {'months': trend_months, 'series': {}}
        for cat in top_cat_names:
            category_trend['series'][cat] = [0.0] * len(trend_months)
        for r in trend_rows:
            if r.month in trend_months and r.category in category_trend['series']:
                idx = trend_months.index(r.month)
                category_trend['series'][r.category][idx] = round(float(r.total), 2)

        filter_label = f"{start_date_str} – {end_date_str}"

        nw = _compute_net_worth()

        return render_template('dashboard.html',
                               total_income=total_income,
                               total_outgo=total_outgo,
                               net_cashflow=net_cashflow,
                               category_stats=category_stats,
                               monthly_outgo=monthly_outgo_data,
                               monthly_income=monthly_income_data,
                               balance_history=balance_history,
                               prev_income=prev_income,
                               prev_outgo=prev_outgo,
                               prev_net=prev_net,
                               prev_category_stats=prev_category_stats,
                               prev_start=prev_start.strftime('%Y-%m-%d'),
                               prev_end=prev_end.strftime('%Y-%m-%d'),
                               budget_map=budget_map,
                               budget_alerts=budget_alerts,
                               period_months=period_months,
                               insights=insights,
                               category_trend=category_trend,
                               filter_label=filter_label,
                               start_date=start_date_str,
                               end_date=end_date_str,
                               account_filter=account_filter,
                               nw=nw)

    # ---------------------------------------------------------------------------
    # Upload (smart merge — no truncate)
    # ---------------------------------------------------------------------------

    @app.route('/upload', methods=['GET', 'POST'])
    def upload():
        if request.method == 'POST':
            if 'files[]' not in request.files:
                flash('No file selected', 'error')
                return redirect(request.url)

            files = request.files.getlist('files[]')
            account_name = request.form.get('account_name')

            if not account_name:
                flash('Please select an account', 'error')
                return redirect(request.url)

            total_new = 0
            total_skipped = 0
            batch_id = str(uuid.uuid4())

            for file in files:
                if not file.filename:
                    continue
                print(f"Processing file: {file.filename}")
                filename = secure_filename(file.filename)
                filepath = os.path.join(app.config['UPLOAD_FOLDER'], filename)
                file.save(filepath)

                df = pd.read_csv(filepath)
                df = df.sort_values(by='Transaction Date')
                prev_balance = None

                for idx, row in df.iterrows():
                    desc = str(row['Transaction Description'])
                    date = pd.to_datetime(row['Transaction Date']).date()
                    amount = float(row['Transaction Amount'])
                    balance = float(row['Balance']) if not pd.isnull(row['Balance']) else None

                    signed_amount = amount
                    desc_lower = desc.lower()

                    if 'deposit from 360 checking' in desc_lower or 'deposit from 360 performance savings' in desc_lower:
                        signed_amount = abs(amount)
                    elif 'withdrawal to 360 checking' in desc_lower or 'withdrawal to 360 performance savings' in desc_lower:
                        signed_amount = -abs(amount)
                    elif 'monthly interest paid' in desc_lower:
                        signed_amount = abs(amount)
                    elif 'credit card' in desc_lower or 'credit crd' in desc_lower:
                        signed_amount = -abs(amount)
                    elif 'purchase' in desc_lower:
                        signed_amount = -abs(amount)
                    elif 'deposit' in desc_lower or 'credit' in desc_lower:
                        signed_amount = abs(amount)
                    elif 'withdraw' in desc_lower or 'payment' in desc_lower:
                        signed_amount = -abs(amount)
                    elif prev_balance is not None and balance is not None:
                        if balance < prev_balance:
                            signed_amount = -abs(amount)
                        elif balance > prev_balance:
                            signed_amount = abs(amount)

                    category = category_rules.get_category(desc)
                    transaction = Transaction(
                        account_name=account_name,
                        date=date,
                        description=desc,
                        amount=signed_amount,
                        category=category,
                        import_batch_id=batch_id
                    )
                    try:
                        db.session.add(transaction)
                        db.session.flush()
                        total_new += 1
                    except IntegrityError:
                        db.session.rollback()
                        total_skipped += 1
                    else:
                        db.session.commit()

                    prev_balance = balance

                os.remove(filepath)
                print(f"Finished processing: {file.filename}")

            _compute_anomaly_scores()
            if total_new > 0:
                session['last_batch_id'] = batch_id
                session['last_batch_count'] = total_new
            flash(f'Import complete — {total_new} new transactions added, {total_skipped} duplicates skipped.', 'success')
            return redirect(url_for('upload'))

        return render_template('upload.html',
                               last_batch_id=session.get('last_batch_id'),
                               last_batch_count=session.get('last_batch_count'))

    # ---------------------------------------------------------------------------
    # Transactions (with bulk update + export)
    # ---------------------------------------------------------------------------

    @app.route('/transactions')
    def transactions():
        page = request.args.get('page', 1, type=int)
        per_page = request.args.get('per_page', 50, type=int)
        if per_page not in (25, 50, 100, 250):
            per_page = 50
        sort_by = request.args.get('sort_by', 'date')
        sort_dir = request.args.get('sort_dir', 'desc')

        start_date_str = request.args.get('start_date') or session.get('start_date')
        end_date_str = request.args.get('end_date') or session.get('end_date')
        account_filter = request.args.get('account') or session.get('account', 'both')
        category_filter = request.args.get('category') or session.get('category')
        direction_filter = request.args.get('type') or request.args.get('direction') or session.get('direction')
        search_query = request.args.get('search') or session.get('search')

        if request.args.get('type'):
            session.pop('search', None)
            search_query = None

        session['start_date'] = start_date_str
        session['end_date'] = end_date_str
        session['account'] = account_filter
        session['category'] = category_filter
        session['direction'] = direction_filter
        session['search'] = search_query

        query = _build_transaction_query(account_filter, category_filter, start_date_str,
                                          end_date_str, direction_filter, search_query)

        sort_col_map = {
            'id': Transaction.id,
            'date': Transaction.date,
            'description': Transaction.description,
            'account': Transaction.account_name,
            'category': Transaction.category,
            'amount': Transaction.amount,
        }
        sort_col = sort_col_map.get(sort_by, Transaction.date)
        order = sort_col.asc() if sort_dir == 'asc' else sort_col.desc()

        categories = db.session.query(Transaction.category).distinct().order_by(Transaction.category).all()
        accounts = db.session.query(Transaction.account_name).distinct().all()

        txn_page = query.order_by(order).paginate(
            page=page, per_page=per_page, error_out=False, max_per_page=250
        )

        return render_template('transactions.html',
                               transactions=txn_page,
                               categories=[c[0] for c in categories],
                               accounts=[a[0] for a in accounts],
                               start_date=start_date_str,
                               end_date=end_date_str,
                               account_filter=account_filter,
                               category_filter=category_filter,
                               direction_filter=session['direction'],
                               search_query=search_query,
                               sort_by=sort_by,
                               sort_dir=sort_dir,
                               per_page=per_page)

    @app.route('/clear_filters')
    def clear_filters():
        for key in ['start_date', 'end_date', 'account', 'category', 'direction', 'search']:
            session.pop(key, None)
        flash('Filters cleared.', 'info')
        next_url = request.args.get('next')
        return redirect(next_url if next_url else url_for('transactions'))

    @app.route('/update_category', methods=['POST'])
    def update_category():
        transaction_id = request.form.get('transaction_id')
        new_category = request.form.get('category')
        transaction = Transaction.query.get_or_404(transaction_id)
        transaction.category = new_category
        try:
            db.session.commit()
            return jsonify({'success': True})
        except Exception as e:
            db.session.rollback()
            return jsonify({'success': False, 'error': str(e)})

    @app.route('/update_categories_bulk', methods=['POST'])
    def update_categories_bulk():
        data = request.json
        ids = data.get('ids', [])
        new_category = data.get('category', '')
        if not ids or not new_category:
            return jsonify({'success': False, 'error': 'Missing ids or category'})
        try:
            Transaction.query.filter(Transaction.id.in_(ids)).update(
                {Transaction.category: new_category}, synchronize_session='fetch'
            )
            db.session.commit()
            return jsonify({'success': True, 'updated': len(ids)})
        except Exception as e:
            db.session.rollback()
            return jsonify({'success': False, 'error': str(e)})

    @app.route('/transactions/<int:transaction_id>', methods=['PUT'])
    def edit_transaction(transaction_id):
        t = Transaction.query.get_or_404(transaction_id)
        data = request.json
        if 'date' in data:
            t.date = datetime.strptime(data['date'], '%Y-%m-%d').date()
        if 'description' in data:
            t.description = data['description']
        if 'amount' in data:
            t.amount = float(data['amount'])
        if 'category' in data:
            t.category = data['category']
        if 'account_name' in data:
            t.account_name = data['account_name']
        if 'notes' in data:
            t.notes = data['notes'] or None
        try:
            db.session.commit()
            return jsonify({'success': True, 'transaction': {
                'id': t.id,
                'date': t.date.strftime('%Y-%m-%d'),
                'description': t.description,
                'amount': float(t.amount),
                'category': t.category,
                'account_name': t.account_name,
                'notes': t.notes or '',
            }})
        except Exception as e:
            db.session.rollback()
            return jsonify({'success': False, 'error': str(e)})

    @app.route('/transactions/<int:transaction_id>', methods=['DELETE'])
    def delete_transaction(transaction_id):
        transaction = Transaction.query.get_or_404(transaction_id)
        try:
            db.session.delete(transaction)
            db.session.commit()
            return jsonify({'success': True})
        except Exception as e:
            db.session.rollback()
            return jsonify({'success': False, 'error': str(e)})

    @app.route('/import/<batch_id>/undo', methods=['POST'])
    def undo_import(batch_id):
        try:
            deleted = Transaction.query.filter_by(import_batch_id=batch_id).delete(synchronize_session='fetch')
            db.session.commit()
            session.pop('last_batch_id', None)
            session.pop('last_batch_count', None)
            flash(f'Import undone — {deleted} transaction(s) removed.', 'success')
        except Exception as e:
            db.session.rollback()
            flash(f'Undo failed: {str(e)}', 'error')
        return redirect(url_for('upload'))

    @app.route('/transactions/bulk_delete', methods=['POST'])
    def bulk_delete_transactions():
        data = request.json
        ids = data.get('ids', [])
        if not ids:
            return jsonify({'success': False, 'error': 'No IDs provided'})
        try:
            deleted = Transaction.query.filter(Transaction.id.in_(ids)).delete(synchronize_session='fetch')
            db.session.commit()
            return jsonify({'success': True, 'deleted': deleted})
        except Exception as e:
            db.session.rollback()
            return jsonify({'success': False, 'error': str(e)})

    # ---------------------------------------------------------------------------
    # Export
    # ---------------------------------------------------------------------------

    @app.route('/export')
    def export():
        account_filter = request.args.get('account') or session.get('account', 'both')
        category_filter = request.args.get('category') or session.get('category')
        start_date_str = request.args.get('start_date') or session.get('start_date')
        end_date_str = request.args.get('end_date') or session.get('end_date')
        direction_filter = request.args.get('direction') or session.get('direction')
        search_query = request.args.get('search') or session.get('search')

        query = _build_transaction_query(account_filter, category_filter, start_date_str,
                                          end_date_str, direction_filter, search_query)
        txns = query.order_by(Transaction.date.desc()).all()

        rows = [{
            'ID': t.id,
            'Date': t.date.strftime('%Y-%m-%d'),
            'Description': t.description,
            'Account': t.account_name,
            'Category': t.category,
            'Amount': float(t.amount),
        } for t in txns]

        df = pd.DataFrame(rows)
        output = io.StringIO()
        df.to_csv(output, index=False)
        output.seek(0)

        filename = f"transactions_{datetime.now().strftime('%Y%m%d_%H%M%S')}.csv"
        return Response(
            output.getvalue(),
            mimetype='text/csv',
            headers={'Content-Disposition': f'attachment; filename={filename}'}
        )

    # ---------------------------------------------------------------------------
    # Anomalies (reads from DB column instead of retraining each load)
    # ---------------------------------------------------------------------------

    @app.route('/anomalies/<int:transaction_id>/dismiss', methods=['POST'])
    def dismiss_anomaly(transaction_id):
        t = Transaction.query.get_or_404(transaction_id)
        t.anomaly_reviewed = True
        try:
            db.session.commit()
            return jsonify({'success': True})
        except Exception as e:
            db.session.rollback()
            return jsonify({'success': False, 'error': str(e)})

    @app.route('/anomalies')
    def anomalies():
        page = request.args.get('page', 1, type=int)
        search_id = request.args.get('search_id')
        sort_by = request.args.get('sort', 'date_desc')
        show_reviewed = request.args.get('show_reviewed', '0') == '1'

        col_map = {
            'id': Transaction.id, 'date': Transaction.date,
            'description': Transaction.description, 'amount': Transaction.amount,
            'category': Transaction.category,
        }
        col_key = sort_by.replace('_asc', '').replace('_desc', '')
        sort_col = col_map.get(col_key, Transaction.date)
        order = sort_col.asc() if sort_by.endswith('_asc') else sort_col.desc()

        query = Transaction.query.filter(Transaction.anomaly_score == -1.0)
        if not show_reviewed:
            query = query.filter(Transaction.anomaly_reviewed == False)
        if search_id:
            try:
                query = query.filter(Transaction.id == int(search_id))
            except ValueError:
                pass

        if not query.first() and not Transaction.query.first():
            flash('No transactions found. Please upload some data first.', 'info')
            return redirect(url_for('upload'))

        anomaly_page = query.order_by(order).paginate(page=page, per_page=50, error_out=False)

        return render_template('anomalies.html',
                               anomalies=anomaly_page,
                               sort_by=sort_by,
                               show_reviewed=show_reviewed)

    # ---------------------------------------------------------------------------
    # Recurring transactions
    # ---------------------------------------------------------------------------

    @app.route('/recurring')
    def recurring():
        account_filter = request.args.get('account', 'both')
        txns = Transaction.query
        if account_filter != 'both':
            txns = txns.filter(Transaction.account_name == account_filter)
        txns = txns.order_by(Transaction.date.asc()).all()

        if not txns:
            return render_template('recurring.html', recurring_groups=[], account_filter=account_filter)

        df = pd.DataFrame([{
            'id': t.id,
            'date': pd.to_datetime(t.date),
            'description': t.description,
            'amount': float(t.amount),
            'category': t.category,
            'account_name': t.account_name,
        } for t in txns])

        # Normalize description: strip digits, extra spaces
        df['desc_norm'] = df['description'].str.replace(r'\d+', '', regex=True).str.strip().str.lower()
        df['amount_bucket'] = df['amount'].round(0)

        groups = df.groupby(['desc_norm', 'amount_bucket'])
        recurring_groups = []
        for (desc_norm, amt_bucket), grp in groups:
            if len(grp) < 2:
                continue
            grp = grp.sort_values('date')
            dates = grp['date'].tolist()
            gaps = [(dates[i+1] - dates[i]).days for i in range(len(dates)-1)]
            avg_gap = sum(gaps) / len(gaps)
            if 20 <= avg_gap <= 40:
                last_date = grp['date'].iloc[-1]
                next_date = (last_date + pd.Timedelta(days=round(avg_gap))).strftime('%Y-%m-%d')
                recurring_groups.append({
                    'description': grp['description'].iloc[0],
                    'category': grp['category'].iloc[0],
                    'account_name': grp['account_name'].iloc[0],
                    'amount': grp['amount'].iloc[0],
                    'occurrences': len(grp),
                    'avg_gap_days': round(avg_gap, 1),
                    'first_seen': grp['date'].iloc[0].strftime('%Y-%m-%d'),
                    'last_seen': last_date.strftime('%Y-%m-%d'),
                    'next_expected': next_date,
                })

        recurring_groups.sort(key=lambda x: x['occurrences'], reverse=True)
        accounts = db.session.query(Transaction.account_name).distinct().all()
        return render_template('recurring.html', recurring_groups=recurring_groups,
                               account_filter=account_filter,
                               accounts=[a[0] for a in accounts])

    # ---------------------------------------------------------------------------
    # Budgets
    # ---------------------------------------------------------------------------

    @app.route('/budgets', methods=['GET', 'POST'])
    def budgets():
        categories = [c[0] for c in db.session.query(Transaction.category).distinct().all()]
        accounts = [a[0] for a in db.session.query(Transaction.account_name).distinct().all()]

        if request.method == 'POST':
            action = request.form.get('action')
            if action == 'add':
                category = request.form.get('category', '').strip()
                account_name = request.form.get('account_name', 'both')
                try:
                    monthly_limit = float(request.form.get('monthly_limit', 0))
                except ValueError:
                    flash('Invalid budget amount.', 'error')
                    return redirect(url_for('budgets'))
                existing = Budget.query.filter_by(category=category, account_name=account_name).first()
                if existing:
                    existing.monthly_limit = monthly_limit
                    flash(f'Updated budget for {category}.', 'success')
                else:
                    db.session.add(Budget(category=category, account_name=account_name, monthly_limit=monthly_limit))
                    flash(f'Budget set for {category}.', 'success')
                db.session.commit()
            elif action == 'delete':
                budget_id = request.form.get('budget_id')
                b = Budget.query.get(budget_id)
                if b:
                    db.session.delete(b)
                    db.session.commit()
                    flash('Budget deleted.', 'success')
            return redirect(url_for('budgets'))

        all_budgets = Budget.query.order_by(Budget.category).all()
        return render_template('budgets.html', budgets=all_budgets,
                               categories=categories, accounts=accounts)

    # ---------------------------------------------------------------------------
    # Rules
    # ---------------------------------------------------------------------------

    @app.route('/rules', methods=['GET', 'POST'])
    def rules():
        if request.method == 'POST':
            action = request.form.get('action')
            if action == 'add':
                category = request.form.get('category')
                keyword = request.form.get('keyword')
                category_rules.add_rule(category, keyword)
                for transaction in Transaction.query.filter(Transaction.description.ilike(f'%{keyword}%')).all():
                    transaction.category = category
                try:
                    db.session.commit()
                    flash('Rule added and existing transactions updated successfully', 'success')
                except Exception as e:
                    db.session.rollback()
                    flash(f'Error updating transactions: {str(e)}', 'error')
            elif action == 'remove':
                category = request.form.get('category')
                keyword = request.form.get('keyword')
                category_rules.remove_rule(category, keyword)
                for transaction in Transaction.query.filter(
                    Transaction.description.ilike(f'%{keyword}%'),
                    Transaction.category == category
                ).all():
                    transaction.category = 'Uncategorized'
                try:
                    db.session.commit()
                    flash('Rule removed and affected transactions updated successfully', 'success')
                except Exception as e:
                    db.session.rollback()
                    flash(f'Error updating transactions: {str(e)}', 'error')
            return redirect(url_for('rules'))
        rule_stats = {cat: Transaction.query.filter(Transaction.category == cat).count()
                      for cat in category_rules.get_all_rules()}
        uncategorized_count = Transaction.query.filter_by(category='Uncategorized').count()
        return render_template('rules.html', rules=category_rules.get_all_rules(),
                               rule_stats=rule_stats,
                               uncategorized_count=uncategorized_count)

    @app.route('/rules/test', methods=['POST'])
    def rules_test():
        import re as _re
        keyword = (request.json or {}).get('keyword', '').strip()
        if not keyword:
            return jsonify({'matches': []})
        if keyword.startswith('/') and keyword.endswith('/') and len(keyword) > 2:
            pattern = keyword[1:-1]
            try:
                all_txns = Transaction.query.order_by(Transaction.date.desc()).limit(2000).all()
                matches = [t for t in all_txns if _re.search(pattern, t.description, _re.IGNORECASE)][:10]
            except Exception:
                matches = []
        else:
            matches = Transaction.query.filter(
                Transaction.description.ilike(f'%{keyword}%')
            ).order_by(Transaction.date.desc()).limit(10).all()
        return jsonify({'matches': [
            {'date': str(t.date), 'description': t.description,
             'amount': float(t.amount), 'category': t.category}
            for t in matches
        ]})

    @app.route('/rules/reorder', methods=['POST'])
    def rules_reorder():
        new_order = (request.json or {}).get('order', [])
        all_rules = category_rules.get_all_rules()
        reordered = {cat: all_rules[cat] for cat in new_order if cat in all_rules}
        for cat in all_rules:
            if cat not in reordered:
                reordered[cat] = all_rules[cat]
        category_rules.rules = reordered
        category_rules._save_rules(reordered)
        return jsonify({'success': True})

    @app.route('/rules/ai-suggest', methods=['POST'])
    def rules_ai_suggest():
        """Send uncategorized descriptions to Claude and get rule suggestions."""
        body    = request.get_json(force=True) or {}
        model   = (body.get('model') or 'claude-sonnet-4-6').strip()
        if model not in {'claude-haiku-4-5-20251001', 'claude-sonnet-4-6', 'claude-opus-4-8'}:
            model = 'claude-sonnet-4-6'

        api_key = app.config.get('ANTHROPIC_API_KEY', '')
        if not api_key:
            return jsonify({'error': 'ANTHROPIC_API_KEY not configured'}), 503

        # Unique uncategorized descriptions (cap at 200 to stay within token limits)
        rows = (Transaction.query
                .filter_by(category='Uncategorized')
                .with_entities(Transaction.description)
                .distinct()
                .limit(200)
                .all())
        if not rows:
            return jsonify({'suggestions': [], 'message': 'No uncategorized transactions found.'})

        descriptions     = [r[0] for r in rows]
        existing_cats    = list(category_rules.get_all_rules().keys())

        prompt = f"""You are a personal finance assistant analyzing bank/credit-card transaction descriptions.

Existing categories (reuse these when they fit):
{json.dumps(existing_cats)}

Here are {len(descriptions)} unique transaction descriptions that are currently "Uncategorized":
{json.dumps(descriptions, indent=2)}

Suggest keyword rules to categorize them. Guidelines:
- Group related merchants into ONE rule using a regex pattern /merchant1|merchant2/
- Use concise, standard personal-finance categories (Groceries, Dining, Gas, Utilities, Streaming, Healthcare, Shopping, Travel, Entertainment, Rent, Insurance, etc.)
- Reuse existing categories where they fit; only create new ones when clearly needed
- Patterns are case-insensitive substring matches — keep them specific enough to avoid false positives
- Skip descriptions that are too ambiguous or clearly one-off transfers
- Aim for 5–15 high-quality suggestions, not exhaustive coverage

Respond with ONLY valid JSON (no markdown fences, no commentary):
{{
  "suggestions": [
    {{
      "category": "Category Name",
      "keyword": "keyword or /regex/",
      "reason": "one-sentence explanation"
    }}
  ]
}}"""

        try:
            client   = anthropic.Anthropic(api_key=api_key)
            response = client.messages.create(
                model=model, max_tokens=2000,
                messages=[{'role': 'user', 'content': prompt}]
            )
            text = response.content[0].text.strip()
            # Strip markdown code fences Claude sometimes adds
            text = re.sub(r'^```(?:json)?\s*', '', text, flags=re.MULTILINE)
            text = re.sub(r'\s*```$',          '', text, flags=re.MULTILINE)
            raw_suggestions = json.loads(text.strip()).get('suggestions', [])
        except json.JSONDecodeError:
            return jsonify({'error': 'Claude returned invalid JSON — try again or use a different model.'}), 500
        except anthropic.APIError as e:
            return jsonify({'error': str(e)}), 500
        except Exception as e:
            return jsonify({'error': f'Unexpected error: {e}'}), 500

        # Enrich each suggestion with real match counts and example descriptions
        all_transactions = Transaction.query.all()
        enriched = []
        for s in raw_suggestions[:20]:
            cat    = (s.get('category') or '').strip()
            kw     = (s.get('keyword')  or '').strip()
            reason = (s.get('reason')   or '').strip()
            if not cat or not kw:
                continue

            is_regex = kw.startswith('/') and kw.endswith('/') and len(kw) > 2
            total_count = 0
            uncat_count = 0
            examples    = []
            for t in all_transactions:
                try:
                    hit = (re.search(kw[1:-1], t.description, re.IGNORECASE)
                           if is_regex else kw.upper() in t.description.upper())
                    if hit:
                        total_count += 1
                        if t.category == 'Uncategorized':
                            uncat_count += 1
                        if len(examples) < 3 and t.description not in examples:
                            examples.append(t.description)
                except re.error:
                    pass

            enriched.append({
                'category':    cat,
                'keyword':     kw,
                'reason':      reason,
                'total_count': total_count,
                'uncat_count': uncat_count,
                'examples':    examples,
            })

        return jsonify({'suggestions': enriched})

    @app.route('/rules/ai-apply', methods=['POST'])
    def rules_ai_apply():
        """Accept one AI suggestion: add the rule and recategorize matching transactions."""
        body     = request.get_json(force=True) or {}
        category = (body.get('category') or '').strip()
        keyword  = (body.get('keyword')  or '').strip()
        if not category or not keyword:
            return jsonify({'error': 'Missing category or keyword'}), 400

        # Add rule at the TOP of the priority list so it beats all existing rules.
        category_rules.add_rule_first(category, keyword)

        is_regex = keyword.startswith('/') and keyword.endswith('/') and len(keyword) > 2
        count    = 0
        try:
            if is_regex:
                pattern = keyword[1:-1]
                # Recategorize ALL matching transactions regardless of current category —
                # the AI rule takes priority over whatever was assigned before.
                for t in Transaction.query.all():
                    try:
                        if re.search(pattern, t.description, re.IGNORECASE):
                            t.category = category
                            count += 1
                    except re.error:
                        pass
            else:
                txns = Transaction.query.filter(
                    Transaction.description.ilike(f'%{keyword}%')
                ).all()
                for t in txns:
                    t.category = category
                count = len(txns)
            db.session.commit()
        except Exception as e:
            db.session.rollback()
            return jsonify({'error': str(e)}), 500

        return jsonify({'ok': True, 'applied_count': count})

    # ---------------------------------------------------------------------------
    # Log
    # ---------------------------------------------------------------------------

    # The /log page is retired; its /api/log/* endpoints remain because the
    # Investments page reads and writes account balances through them.

    @app.route('/api/log/entries', methods=['GET'])
    def get_log_entries():
        entries = LogEntry.query.order_by(LogEntry.date.asc()).all()
        return jsonify([entry.to_dict() for entry in entries])

    def _recompute_log_balances(account_type):
        """Recompute and persist snapshot balance fields for all entries of an account."""
        acc_bal = AccountBalance.query.filter_by(account_type=account_type).first()
        sb = float(acc_bal.starting_balance) if acc_bal else 0.0
        all_entries = LogEntry.query.filter_by(account_type=account_type).all()
        cleared_sum = sum(e.amount for e in all_entries if e.cleared)
        pending_sum = sum(e.amount for e in all_entries if not e.cleared)
        for e in all_entries:
            e.starting_balance = sb
            e.cleared_balance = sb + cleared_sum
            e.pending_total = pending_sum
            e.available_balance = sb + cleared_sum + pending_sum

    @app.route('/api/log/entries', methods=['POST'])
    def add_log_entry():
        data = request.json
        account_type = data['account_type']
        amount = float(data['amount'])
        cleared = bool(data.get('cleared', False))

        acc_bal = AccountBalance.query.filter_by(account_type=account_type).first()
        sb = float(acc_bal.starting_balance) if acc_bal else 0.0
        existing = LogEntry.query.filter_by(account_type=account_type).all()

        cleared_sum = sum(e.amount for e in existing if e.cleared) + (amount if cleared else 0)
        pending_sum = sum(e.amount for e in existing if not e.cleared) + (0 if cleared else amount)

        entry = LogEntry(
            account_type=account_type,
            date=datetime.strptime(data['date'], '%Y-%m-%d').date(),
            description=data['description'],
            amount=amount,
            cleared=cleared,
            starting_balance=sb,
            pending_total=pending_sum,
            cleared_balance=sb + cleared_sum,
            available_balance=sb + cleared_sum + pending_sum
        )
        db.session.add(entry)
        try:
            db.session.commit()
            return jsonify(entry.to_dict())
        except Exception as e:
            db.session.rollback()
            return jsonify({'error': str(e)}), 400

    @app.route('/api/log/entries/<int:entry_id>', methods=['PUT'])
    def update_log_entry(entry_id):
        entry = LogEntry.query.get_or_404(entry_id)
        data = request.json
        if 'date' in data:
            entry.date = datetime.strptime(data['date'], '%Y-%m-%d').date()
        if 'description' in data:
            entry.description = data['description']
        if 'amount' in data:
            entry.amount = float(data['amount'])
        if 'cleared' in data:
            entry.cleared = bool(data['cleared'])
        _recompute_log_balances(entry.account_type)
        try:
            db.session.commit()
            return jsonify(entry.to_dict())
        except Exception as e:
            db.session.rollback()
            return jsonify({'error': str(e)}), 400

    @app.route('/api/log/entries/<int:entry_id>', methods=['DELETE'])
    def delete_log_entry(entry_id):
        entry = LogEntry.query.get_or_404(entry_id)
        db.session.delete(entry)
        try:
            db.session.commit()
            return jsonify({'success': True})
        except Exception as e:
            db.session.rollback()
            return jsonify({'error': str(e)}), 400

    @app.route('/api/log/clear', methods=['POST'])
    def clear_log():
        try:
            LogEntry.query.delete()
            db.session.commit()
            return jsonify({'success': True})
        except Exception as e:
            db.session.rollback()
            return jsonify({'error': str(e)}), 400

    @app.route('/api/log/balances', methods=['GET'])
    def get_account_balances():
        return jsonify([b.to_dict() for b in AccountBalance.query.all()])

    @app.route('/api/log/balances/<account_type>', methods=['PUT'])
    def update_account_balance(account_type):
        balance = AccountBalance.query.filter_by(account_type=account_type).first()
        if not balance:
            balance = AccountBalance(account_type=account_type)
            db.session.add(balance)
        balance.starting_balance = float(request.json['starting_balance'])
        try:
            db.session.commit()
            return jsonify(balance.to_dict())
        except Exception as e:
            db.session.rollback()
            return jsonify({'error': str(e)}), 400

    # ---------------------------------------------------------------------------
    # AI Chat
    # ---------------------------------------------------------------------------

    @app.route('/chat')
    def chat():
        return render_template('chat.html')

    @app.route('/api/chat', methods=['POST'])
    def api_chat():
        req = request.get_json(force=True)
        user_message = (req.get('message') or '').strip()
        if not user_message:
            return jsonify({'error': 'No message provided'}), 400
        api_key = app.config.get('ANTHROPIC_API_KEY', '')
        if not api_key:
            return jsonify({'error': 'ANTHROPIC_API_KEY not configured.'}), 503

        context = _build_finance_context()
        system_prompt = (
            "You are a personal finance and investment advisor. The user's full financial data is provided. "
            "Respond ONLY with valid JSON in this exact shape (no markdown code fences, no extra text): "
            '{"analysis": "markdown string", "insights": ["string"], "recommended_actions": ["string"]}. '
            "analysis supports markdown (headers, bold, tables, lists). "
            "insights and recommended_actions are short plain-text strings. Be specific with dollar amounts."
        )
        try:
            client = anthropic.Anthropic(api_key=api_key)
            response = client.messages.create(
                model='claude-sonnet-4-6',
                max_tokens=1500,
                system=system_prompt,
                messages=[{
                    'role': 'user',
                    'content': f"Financial data:\n{json.dumps(_build_finance_context(), indent=2)}\n\nQuestion: {user_message}"
                }]
            )
        except anthropic.APIError as e:
            return jsonify({'error': str(e)}), 502

        raw = response.content[0].text.strip()

        # Strip code fences
        raw = re.sub(r'^```(?:json)?\s*', '', raw)
        raw = re.sub(r'\s*```$', '', raw).strip()

        # Extract outermost JSON object
        m = re.search(r'\{.*\}', raw, re.DOTALL)
        if m:
            raw = m.group()

        # Parse JSON — completely isolated from HTML conversion
        result = {}
        try:
            result = json.loads(raw)
        except (json.JSONDecodeError, ValueError):
            result = {'analysis': raw, 'insights': [], 'recommended_actions': []}

        # Unwrap double-encoded: Claude occasionally nests the full JSON inside analysis
        analysis_text = result.get('analysis', '')
        if isinstance(analysis_text, str) and analysis_text.strip().startswith('{'):
            try:
                inner = json.loads(analysis_text)
                if isinstance(inner.get('analysis'), str):
                    result = inner
                    analysis_text = inner['analysis']
            except Exception:
                pass

        # Convert markdown to HTML — fully isolated, never causes a JSON fallback
        try:
            html = _md_to_html(analysis_text) if analysis_text else ''
        except Exception:
            html = '<pre style="white-space:pre-wrap;font-size:.85rem">' + \
                   analysis_text.replace('&', '&amp;').replace('<', '&lt;') + '</pre>'

        return jsonify({
            'html': html,
            'insights': result.get('insights', []),
            'actions': result.get('recommended_actions', []),
        })

    # ── Conversation management ──────────────────────────────────────────────

    @app.route('/api/conversations', methods=['GET'])
    def api_conversations():
        convs = (Conversation.query
                 .order_by(Conversation.updated_at.desc())
                 .all())
        return jsonify({'conversations': [
            {'id': c.id, 'title': c.title, 'updated_at': c.updated_at.isoformat()}
            for c in convs
        ]})

    @app.route('/api/conversations', methods=['POST'])
    def api_new_conversation():
        conv = Conversation(id=str(uuid.uuid4()), title='New Chat')
        db.session.add(conv)
        db.session.commit()
        return jsonify({'id': conv.id, 'title': conv.title})

    @app.route('/api/conversations/<conv_id>', methods=['DELETE'])
    def api_delete_conversation(conv_id):
        try:
            ChatMessage.query.filter_by(session_id=conv_id).delete()
            Conversation.query.filter_by(id=conv_id).delete()
            db.session.commit()
        except Exception:
            db.session.rollback()
        return jsonify({'ok': True})

    # ── Chat messages ────────────────────────────────────────────────────────

    @app.route('/api/chat_history')
    def api_chat_history():
        conv_id = request.args.get('conv', '').strip()
        if not conv_id:
            return jsonify({'messages': []})
        msgs = (ChatMessage.query
                .filter_by(session_id=conv_id)
                .order_by(ChatMessage.id.asc())   # id is insertion-ordered; created_at ties when saved in one commit
                .all())
        return jsonify({'messages': [
            {'role': m.role, 'content': m.content,
             'created_at': m.created_at.isoformat() + 'Z'}   # mark as UTC so JS Date parses correctly
            for m in msgs
        ]})

    _ALLOWED_MODELS = {
        'claude-haiku-4-5-20251001',
        'claude-sonnet-4-6',
        'claude-opus-4-8',
    }

    @app.route('/api/chat_stream', methods=['POST'])
    def api_chat_stream():
        req = request.get_json(force=True)
        user_message = (req.get('message') or '').strip()
        conv_id       = (req.get('conv_id') or '').strip()
        model         = (req.get('model')   or 'claude-sonnet-4-6').strip()
        if model not in _ALLOWED_MODELS:
            model = 'claude-sonnet-4-6'
        if not user_message:
            return jsonify({'error': 'No message provided'}), 400
        if not conv_id:
            return jsonify({'error': 'No conversation ID'}), 400

        api_key = app.config.get('ANTHROPIC_API_KEY', '')
        if not api_key:
            return jsonify({'error': 'ANTHROPIC_API_KEY not configured.'}), 503

        conv = db.session.get(Conversation, conv_id)
        if not conv:
            return jsonify({'error': 'Conversation not found'}), 404

        # ── Build Claude context from history BEFORE saving the current message ──
        recent = (ChatMessage.query
                  .filter_by(session_id=conv_id)
                  .order_by(ChatMessage.id.desc())
                  .limit(20).all())
        history = [{'role': m.role, 'content': m.content} for m in reversed(recent)]

        # ── Persist user message immediately so it survives navigation / disconnect ──
        user_ts = datetime.utcnow()
        try:
            db.session.add(ChatMessage(session_id=conv_id, role='user',
                                       content=user_message, created_at=user_ts))
            if conv.title == 'New Chat':
                conv.title = user_message[:55] + ('…' if len(user_message) > 55 else '')
            conv.updated_at = user_ts
            db.session.commit()
        except Exception as e:
            app.logger.error('chat_stream pre-save user msg failed: %s', e)
            db.session.rollback()

        fin_ctx = _build_finance_context()
        system_prompt = (
            "You are a personal finance and investment advisor. "
            "Here is the user's current financial snapshot:\n\n"
            f"{json.dumps(fin_ctx, indent=2)}\n\n"
            "Respond in clear, well-formatted markdown. Be specific with dollar amounts. "
            "Use headers, tables, and bullet lists when they add clarity. "
            "Keep responses concise and actionable."
        )
        messages = history + [{'role': 'user', 'content': user_message}]

        def _generate():
            full_response = ''
            stream_done   = False
            try:
                client = anthropic.Anthropic(api_key=api_key)
                with client.messages.stream(
                    model=model,
                    max_tokens=1500,
                    system=system_prompt,
                    messages=messages,
                ) as stream:
                    for text in stream.text_stream:
                        full_response += text
                        yield f'data: {json.dumps({"delta": text})}\n\n'
                stream_done = True
            except anthropic.APIError as e:
                yield f'data: {json.dumps({"error": str(e)})}\n\n'
                return
            except Exception as e:
                app.logger.error('chat_stream API error: %s', e)
                yield f'data: {json.dumps({"error": "Unexpected error during streaming."})}\n\n'
                return
            finally:
                # Runs on normal completion, errors, AND client disconnect (GeneratorExit).
                # Saves whatever response accumulated so nothing is silently lost.
                if full_response:
                    try:
                        asst_ts = datetime.utcnow()
                        db.session.add(ChatMessage(session_id=conv_id, role='assistant',
                                                   content=full_response, created_at=asst_ts))
                        db.session.commit()
                    except Exception as e:
                        app.logger.error('chat_stream save assistant failed: %s', e)
                        db.session.rollback()

            if stream_done:
                yield 'data: [DONE]\n\n'

        return Response(
            stream_with_context(_generate()),
            mimetype='text/event-stream',
            headers={'Cache-Control': 'no-cache', 'X-Accel-Buffering': 'no', 'Connection': 'keep-alive'},
        )

    @app.route('/api/chat_clear', methods=['POST'])
    def api_chat_clear():
        """Clear messages for a conversation without deleting it."""
        req     = request.get_json(force=True) or {}
        conv_id = (req.get('conv_id') or '').strip()
        if conv_id:
            try:
                ChatMessage.query.filter_by(session_id=conv_id).delete()
                conv = db.session.get(Conversation, conv_id)
                if conv:
                    conv.title      = 'New Chat'
                    conv.updated_at = datetime.utcnow()
                db.session.commit()
            except Exception:
                db.session.rollback()
        return jsonify({'ok': True})

    @app.route('/api/dashboard-insight')
    def dashboard_insight():
        now = time.time()
        if _insight_cache['text'] and now < _insight_cache['expires']:
            return jsonify({'insight': _insight_cache['text']})
        api_key = app.config.get('ANTHROPIC_API_KEY', '')
        if not api_key:
            return jsonify({'insight': ''})
        context = _build_finance_context(months=1)
        client = anthropic.Anthropic(api_key=api_key)
        try:
            resp = client.messages.create(
                model='claude-haiku-4-5-20251001',
                max_tokens=200,
                system=(
                    "You are a personal finance advisor. In 2-3 sentences, give the single most "
                    "important financial insight from the data. Be specific with dollar amounts."
                ),
                messages=[{'role': 'user', 'content': json.dumps(context)}]
            )
            text = resp.content[0].text.strip()
            _insight_cache['text'] = text
            _insight_cache['expires'] = now + app.config.get('AI_INSIGHT_CACHE_TTL', 3600)
            return jsonify({'insight': text})
        except Exception:
            return jsonify({'insight': ''})

    # ---------------------------------------------------------------------------
    # Investments / Holdings
    # ---------------------------------------------------------------------------

    @app.route('/investments')
    def investments():
        holdings = Holding.query.order_by(Holding.asset_class, Holding.ticker).all()
        nw = _compute_net_worth()
        # Include cash accounts (checking + savings) in portfolio breakdown
        portfolio_by_class = {'Checking': nw['checking'], 'Savings': nw['savings']} if (nw['checking'] or nw['savings']) else {}
        for h in holdings:
            portfolio_by_class[h.asset_class] = round(
                portfolio_by_class.get(h.asset_class, 0) + float(h.current_value), 2)
        # Remove zero-value buckets
        portfolio_by_class = {k: v for k, v in portfolio_by_class.items() if v > 0}
        asset_classes = ['Stock', 'ETF', 'Mutual Fund', 'Bond', 'Crypto', 'Cash', 'Other']

        # --- synchronization context (finance_sync) ---
        connections = (InstitutionConnection.query
                       .order_by(InstitutionConnection.display_name).all())
        synced_accounts = (FinancialAccount.query.filter_by(is_active=True)
                           .order_by(FinancialAccount.account_type,
                                     FinancialAccount.name).all())
        cash_synced = any(a.account_type in ('checking', 'savings') for a in synced_accounts)
        last_sync = (db.session.query(func.max(InstitutionConnection.last_sync_at))
                     .scalar())
        return render_template('investments.html',
                               holdings=holdings, nw=nw,
                               portfolio_by_class=portfolio_by_class,
                               asset_classes=asset_classes,
                               connections=[c.to_dict() for c in connections],
                               synced_accounts=[a.to_dict() for a in synced_accounts],
                               cash_synced=cash_synced,
                               last_sync=last_sync.strftime('%Y-%m-%d %H:%M') if last_sync else None)

    @app.route('/api/holdings', methods=['POST'])
    def add_holding():
        d = request.get_json(force=True)
        h = Holding(
            ticker=d.get('ticker', '').upper(),
            name=d.get('name', ''),
            shares=d.get('shares', 0),
            current_value=d.get('current_value', 0),
            asset_class=d.get('asset_class', 'Stock'),
            account_name=d.get('account_name', 'Brokerage'),
        )
        db.session.add(h)
        try:
            db.session.commit()
            return jsonify(h.to_dict()), 201
        except Exception as e:
            db.session.rollback()
            return jsonify({'error': str(e)}), 400

    @app.route('/api/holdings/<int:hid>', methods=['PUT', 'DELETE'])
    def holding(hid):
        h = Holding.query.get_or_404(hid)
        if h.source == 'sync':
            return jsonify({'error': 'This holding is synchronized automatically from '
                                     f'{h.account_name} and cannot be edited manually. '
                                     'Manage it from the Connections page.'}), 409
        if request.method == 'DELETE':
            db.session.delete(h)
            try:
                db.session.commit()
                return jsonify({'ok': True})
            except Exception as e:
                db.session.rollback()
                return jsonify({'error': str(e)}), 400
        d = request.get_json(force=True)
        if 'ticker' in d:
            h.ticker = d['ticker'].upper()
        for field in ['name', 'shares', 'current_value', 'asset_class', 'account_name']:
            if field in d:
                setattr(h, field, d[field])
        try:
            db.session.commit()
            return jsonify(h.to_dict())
        except Exception as e:
            db.session.rollback()
            return jsonify({'error': str(e)}), 400

    # Auto-migrate: add new columns / tables to existing SQLite DB if they don't exist yet
    from sqlalchemy import text as _text
    with app.app_context():
        with db.engine.connect() as _conn:
            # Create holdings table if it doesn't exist (fallback for installs
            # without flask db upgrade) BEFORE the column migrations below so a
            # fresh database also receives the finance_sync columns.
            _conn.execute(_text("""
                CREATE TABLE IF NOT EXISTS holdings (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    ticker VARCHAR(20) NOT NULL,
                    name VARCHAR(100) NOT NULL,
                    shares NUMERIC(14,6) NOT NULL DEFAULT 0,
                    current_value NUMERIC(12,2) NOT NULL,
                    asset_class VARCHAR(20) NOT NULL DEFAULT 'Stock',
                    account_name VARCHAR(50) NOT NULL DEFAULT 'Brokerage',
                    updated_at DATETIME
                )
            """))
            _conn.commit()
            for _col_sql in [
                "ALTER TABLE transactions ADD COLUMN notes TEXT",
                "ALTER TABLE transactions ADD COLUMN import_batch_id VARCHAR(36)",
                "ALTER TABLE transactions ADD COLUMN anomaly_reviewed BOOLEAN NOT NULL DEFAULT 0",
                # finance_sync columns
                "ALTER TABLE transactions ADD COLUMN source VARCHAR(10) NOT NULL DEFAULT 'csv'",
                "ALTER TABLE transactions ADD COLUMN account_id INTEGER",
                "ALTER TABLE transactions ADD COLUMN external_id VARCHAR(120)",
                "ALTER TABLE holdings ADD COLUMN source VARCHAR(10) NOT NULL DEFAULT 'manual'",
                "ALTER TABLE holdings ADD COLUMN account_id INTEGER",
                "ALTER TABLE holdings ADD COLUMN external_id VARCHAR(120)",
                "ALTER TABLE holdings ADD COLUMN avg_cost NUMERIC(14,4)",
                "ALTER TABLE holdings ADD COLUMN current_price NUMERIC(14,4)",
                "ALTER TABLE holdings ADD COLUMN last_synced_at DATETIME",
                "ALTER TABLE connected_accounts ADD COLUMN item_id VARCHAR(80)",
            ]:
                try:
                    _conn.execute(_text(_col_sql))
                    _conn.commit()
                except Exception:
                    pass  # column already exists

            # connected_accounts originally had UNIQUE(institution) alone, which
            # blocks aggregator adapters (Plaid) from linking more than one
            # institution. Rebuild the table under the new UNIQUE(institution,
            # item_id) constraint if the old single-column constraint is still
            # in place. SQLite can't ALTER a constraint directly.
            try:
                _unique_single_institution = False
                for _idx in _conn.execute(_text(
                        "PRAGMA index_list('connected_accounts')")).fetchall():
                    if not _idx[2]:  # not unique
                        continue
                    _cols = [r[2] for r in _conn.execute(
                        _text(f"PRAGMA index_info('{_idx[1]}')")).fetchall()]
                    if _cols == ['institution']:
                        _unique_single_institution = True
                        break
                if _unique_single_institution:
                    _conn.execute(_text(
                        "ALTER TABLE connected_accounts RENAME TO connected_accounts_old"))
                    _conn.commit()
                    db.create_all()  # recreates connected_accounts with the new schema
                    _conn.execute(_text(
                        "INSERT INTO connected_accounts (id, institution, item_id, "
                        "display_name, status, auth_blob, token_expires_at, last_sync_at, "
                        "last_sync_status, last_error, created_at, updated_at) "
                        "SELECT id, institution, NULL, display_name, status, auth_blob, "
                        "token_expires_at, last_sync_at, last_sync_status, last_error, "
                        "created_at, updated_at FROM connected_accounts_old"))
                    _conn.execute(_text("DROP TABLE connected_accounts_old"))
                    _conn.commit()
            except Exception:
                pass  # table doesn't exist yet (fresh install) — db.create_all() below handles it

    # Create any new tables (e.g. chat_messages, finance_sync tables) without
    # touching existing data, plus dedupe indexes on pre-existing tables.
    with app.app_context():
        db.create_all()
        with db.engine.connect() as _conn:
            for _idx_sql in [
                "CREATE UNIQUE INDEX IF NOT EXISTS idx_transaction_external_unique "
                "ON transactions (account_id, external_id)",
                "CREATE UNIQUE INDEX IF NOT EXISTS idx_holding_sync_unique "
                "ON holdings (account_id, ticker)",
            ]:
                try:
                    _conn.execute(_text(_idx_sql))
                    _conn.commit()
                except Exception:
                    pass  # index already exists

    # ---------------------------------------------------------------------------
    # Financial institution synchronization (finance_sync)
    # ---------------------------------------------------------------------------
    app.register_blueprint(sync_bp)

    if app.config.get('SYNC_AUTO_ENABLED', True) and not app.config.get('TESTING'):
        # Start the background scheduler lazily on the first request so it only
        # runs in the serving process (never in the werkzeug reloader parent).
        @app.before_request
        def _ensure_sync_scheduler():
            init_scheduler(app, interval_hours=app.config.get('SYNC_INTERVAL_HOURS', 12))
    else:
        # Tests still need a scheduler object for the manual-refresh API,
        # but without the periodic background thread.
        init_scheduler(app, interval_hours=app.config.get('SYNC_INTERVAL_HOURS', 12),
                       autostart=False)

    return app

def _ensure_dev_cert(base_dir):
    """Create (once) and return a self-signed localhost certificate pair.

    Plaid requires https for OAuth redirect URIs even in sandbox, so local
    testing needs TLS. The cert covers localhost and this machine's LAN IP;
    browsers will show a one-time "not trusted" warning — that's expected
    for a self-signed cert and fine for development.
    """
    import ipaddress
    import socket
    from datetime import timezone

    cert_path = os.path.join(base_dir, '.dev-cert.pem')
    key_path = os.path.join(base_dir, '.dev-key.pem')
    if os.path.exists(cert_path) and os.path.exists(key_path):
        return cert_path, key_path

    from cryptography import x509
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import rsa
    from cryptography.x509.oid import NameOID

    # Best-effort LAN IP so other devices on the network can use https too.
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(('8.8.8.8', 80))
        lan_ip = s.getsockname()[0]
        s.close()
    except OSError:
        lan_ip = None

    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, 'localhost')])
    sans = [x509.DNSName('localhost'), x509.IPAddress(ipaddress.ip_address('127.0.0.1'))]
    if lan_ip:
        sans.append(x509.IPAddress(ipaddress.ip_address(lan_ip)))
    now = datetime.now(timezone.utc)
    cert = (
        x509.CertificateBuilder()
        .subject_name(name).issuer_name(name)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now)
        .not_valid_after(now + timedelta(days=825))
        .add_extension(x509.SubjectAlternativeName(sans), critical=False)
        .sign(key, hashes.SHA256())
    )
    with open(key_path, 'wb') as fh:
        fh.write(key.private_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PrivateFormat.TraditionalOpenSSL,
            encryption_algorithm=serialization.NoEncryption()))
    with open(cert_path, 'wb') as fh:
        fh.write(cert.public_bytes(serialization.Encoding.PEM))
    return cert_path, key_path


def _truthy(name, default=''):
    return os.environ.get(name, default).lower() in ('1', 'true', 'yes')


if __name__ == '__main__':
    app = create_app()
    ssl_context = None
    if _truthy('APP_HTTPS'):
        ssl_context = _ensure_dev_cert(os.path.dirname(os.path.abspath(__file__)))

    # Default to loopback only. Exposing to the LAN (APP_HOST=0.0.0.0) is
    # refused without APP_PASSWORD — the app has no accounts, so the password
    # gate is the only access control over your financial data.
    host = os.environ.get('APP_HOST', '127.0.0.1')
    if host != '127.0.0.1' and not os.environ.get('APP_PASSWORD'):
        raise SystemExit(
            f"Refusing to bind to {host} without a password: set APP_PASSWORD "
            "in .env (or remove APP_HOST to serve localhost only).")
    app.run(host=host, port=int(os.environ.get('APP_PORT', '5000')),
            debug=_truthy('APP_DEBUG', '1'), ssl_context=ssl_context)
