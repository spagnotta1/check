"""Recurring-payment detection.

Splits recurring outflows into two kinds:

* **Bills & payments** — debt service and obligatory monthly payments
  (student loans, auto loans, credit card bills, insurance, rent, utilities).
  Identified by category: the user's own categorization rules are treated as
  the primary evidence, so no payment cadence is required — amounts and
  timing may vary (a twice-monthly card payment, an irregular bursar
  payment). Two or more recent payments to the same payee qualify.

* **Subscriptions** — genuine subscription services. Anything the user has
  categorized as ``Subscriptions`` qualifies on a monthly cadence. For every
  other category (where a monthly-ish restaurant habit would otherwise slip
  through) the bar is much higher: the charge must repeat for the *exact same
  amount* at a tight monthly interval at least three times — the signature of
  an automated billing system, not a human ordering "the usual".

Groups whose most recent hit is older than ``RECENCY_DAYS`` (measured against
the newest transaction in the data, not the wall clock) are treated as
cancelled/paid off and excluded. Groups the user has manually dismissed
(their normalized description matches a ``dismissed_keys`` entry) are also
excluded — human-in-the-loop wins over any heuristic.
"""

import re
from datetime import timedelta

BILL_CATEGORIES = {'Student Loan', 'Auto Loan', 'Credit Card', 'Insurance Payment',
                   'Utilities', 'Rent', 'Mortgage'}
SUBSCRIPTION_CATEGORIES = {'Subscriptions'}
# Money movement, not spending — never a bill or subscription.
EXCLUDED_CATEGORIES = {'Transfer', 'Income', 'Investments'}

RECENCY_DAYS = 183  # ~6 months

_AVG_MONTH_DAYS = 30.44


def normalize_description(description):
    """Strip digits and collapse whitespace so 'INVOICE 1234' == 'INVOICE 5678'."""
    return re.sub(r'\s+', ' ', re.sub(r'\d+', '', description or '')).strip().lower()


def _related(a, b):
    """Two normalized descriptions refer to the same payee if one contains
    the other (import sources vary the wording, not the payee name)."""
    return a in b or b in a


def _median(values):
    ordered = sorted(values)
    n = len(ordered)
    return ordered[n // 2] if n % 2 else (ordered[n // 2 - 1] + ordered[n // 2]) / 2


def _merge_related(groups):
    """Merge groups whose normalized descriptions substring-match.

    Import sources describe the same payee differently over time (a CSV
    export says 'Withdrawal from FIRSTMARK PAYMENTS', the live sync just
    'FIRSTMARK'), which would otherwise show one loan as two rows.
    Returns (keys, txns) pairs.
    """
    clusters = []  # each: [set_of_keys, txns]
    for key, txns in sorted(groups.items(), key=lambda kv: len(kv[0])):
        for cluster in clusters:
            if any(_related(key, other) for other in cluster[0]):
                cluster[0].add(key)
                cluster[1].extend(txns)
                break
        else:
            clusters.append([{key}, list(txns)])
    return clusters


def _summarize(keys, txns, monthly_amount):
    txns = sorted(txns, key=lambda t: t['date'])
    gaps = [(txns[i + 1]['date'] - txns[i]['date']).days for i in range(len(txns) - 1)]
    gap = _median(gaps)
    latest = txns[-1]
    return {
        'description': latest['description'],
        'desc_keys': sorted(keys),
        'category': latest['category'],
        'account_name': latest.get('account_name'),
        'monthly_amount': round(monthly_amount, 2),
        'occurrences': len(txns),
        'median_gap_days': round(gap, 1),
        'first_seen': txns[0]['date'].strftime('%Y-%m-%d'),
        'last_seen': latest['date'].strftime('%Y-%m-%d'),
        'next_expected': (latest['date'] + timedelta(days=round(gap))).strftime('%Y-%m-%d'),
    }


def detect_recurring(txns, dismissed_keys=()):
    """Detect recurring outflows in a list of transaction dicts.

    Each dict needs ``date`` (date or datetime), ``description``, ``amount``
    (float, negative = expense), ``category``, and optionally
    ``account_name``. ``dismissed_keys`` are normalized descriptions the user
    has manually marked as not recurring; any matching group is suppressed.
    Returns ``{'bills': [...], 'subscriptions': [...]}`` sorted by monthly
    amount descending.
    """
    dismissed_keys = [k for k in dismissed_keys if k]

    expenses = [t for t in txns
                if t['amount'] < 0 and t['category'] not in EXCLUDED_CATEGORIES]
    if not expenses:
        return {'bills': [], 'subscriptions': []}

    max_date = max(t['date'] for t in txns)
    cutoff = max_date - timedelta(days=RECENCY_DAYS)

    def is_dismissed(keys):
        return any(_related(key, d) for key in keys for d in dismissed_keys)

    bill_groups, sub_groups, other_groups = {}, {}, {}
    for t in expenses:
        key = normalize_description(t['description'])
        if not key:
            continue
        if t['category'] in BILL_CATEGORIES:
            bill_groups.setdefault(key, []).append(t)
        elif t['category'] in SUBSCRIPTION_CATEGORIES:
            sub_groups.setdefault(key, []).append(t)
        else:
            # Strict tier: only identical charge amounts can group together.
            other_groups.setdefault((key, round(t['amount'], 2)), []).append(t)

    bills, subscriptions = [], []

    for keys, grp in _merge_related(bill_groups):
        if len(grp) < 2 or is_dismissed(keys):
            continue
        grp.sort(key=lambda t: t['date'])
        if grp[-1]['date'] < cutoff:
            continue
        # The user's category rules already say this is a bill — no cadence
        # requirement, since debt payments can be irregular (extra principal,
        # twice-monthly card payments, semester bursar bills).
        # Show the typical actual payment as it appears in transactions;
        # the median resists one-off extra payments.
        summary = _summarize(keys, grp, _median([t['amount'] for t in grp]))
        # Annual cost follows the real payment frequency, not a flat ×12.
        summary['annual_cost'] = round(
            abs(summary['monthly_amount']) * 365.25 / max(summary['median_gap_days'], 1))
        bills.append(summary)

    for keys, grp in _merge_related(sub_groups):
        if len(grp) < 2 or is_dismissed(keys):
            continue
        grp.sort(key=lambda t: t['date'])
        if grp[-1]['date'] < cutoff:
            continue
        gaps = [(grp[i + 1]['date'] - grp[i]['date']).days for i in range(len(grp) - 1)]
        if not 20 <= _median(gaps) <= 40:
            continue
        # The current price is what renews, so charge = most recent amount.
        subscriptions.append(_summarize(keys, grp, grp[-1]['amount']))

    for (key, amount), grp in other_groups.items():
        if len(grp) < 3 or is_dismissed([key]):
            continue
        grp.sort(key=lambda t: t['date'])
        if grp[-1]['date'] < cutoff:
            continue
        gaps = [(grp[i + 1]['date'] - grp[i]['date']).days for i in range(len(grp) - 1)]
        if not 25 <= _median(gaps) <= 35:
            continue
        if not all(18 <= g <= 45 for g in gaps):
            continue
        subscriptions.append(_summarize({key}, grp, amount))

    bills.sort(key=lambda g: g['monthly_amount'])
    subscriptions.sort(key=lambda g: g['monthly_amount'])
    return {'bills': bills, 'subscriptions': subscriptions}
