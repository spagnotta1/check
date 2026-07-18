const formatCurrency = (amount) =>
    new Intl.NumberFormat('en-US', { style: 'currency', currency: 'USD' }).format(amount);

// ── EntryRow ──────────────────────────────────────────────────────────────────

const EntryRow = ({ entry, runningBalance, onToggleCleared, onDelete, onEdit }) => {
    const [editing, setEditing] = React.useState(false);
    const [editData, setEditData] = React.useState({
        date: entry.date,
        description: entry.description,
        amount: Math.abs(entry.amount).toString(),
        isInbound: entry.amount >= 0
    });

    const handleSave = () => {
        const raw = parseFloat(editData.amount);
        if (isNaN(raw) || raw === 0) { alert('Enter a valid amount.'); return; }
        const signed = editData.isInbound ? Math.abs(raw) : -Math.abs(raw);
        onEdit(entry.id, { date: editData.date, description: editData.description, amount: signed });
        setEditing(false);
    };

    const rowBase = `border-b border-gray-200 ${entry.cleared ? 'bg-gray-50' : 'hover:bg-gray-50'}`;

    if (editing) {
        return (
            <tr className="border-b border-blue-200 bg-blue-50">
                <td className="py-2 px-3">
                    <input type="date" value={editData.date}
                        onChange={e => setEditData({ ...editData, date: e.target.value })}
                        className="w-full rounded border-gray-300 text-sm" />
                </td>
                <td className="py-2 px-3">
                    <input type="text" value={editData.description}
                        onChange={e => setEditData({ ...editData, description: e.target.value })}
                        className="w-full rounded border-gray-300 text-sm" />
                </td>
                <td className="py-2 px-3">
                    <div className="flex items-center space-x-1">
                        <input type="number" step="0.01" min="0" value={editData.amount}
                            onChange={e => setEditData({ ...editData, amount: e.target.value })}
                            className="w-24 rounded border-gray-300 text-sm text-right" />
                        <select value={editData.isInbound ? 'in' : 'out'}
                            onChange={e => setEditData({ ...editData, isInbound: e.target.value === 'in' })}
                            className="rounded border-gray-300 text-sm">
                            <option value="out">Payment</option>
                            <option value="in">Deposit</option>
                        </select>
                    </div>
                </td>
                <td className="py-2 px-3 text-center">
                    <input type="checkbox" checked={entry.cleared}
                        onChange={() => onToggleCleared(entry.id)}
                        className="h-4 w-4 text-blue-600" />
                </td>
                <td className="py-2 px-3 text-right text-sm text-gray-500">
                    {formatCurrency(runningBalance)}
                </td>
                <td className="py-2 px-3">
                    <div className="flex space-x-2">
                        <button onClick={handleSave}
                            className="text-green-700 hover:text-green-900 text-sm font-medium">Save</button>
                        <button onClick={() => setEditing(false)}
                            className="text-gray-500 hover:text-gray-700 text-sm">Cancel</button>
                    </div>
                </td>
            </tr>
        );
    }

    return (
        <tr className={rowBase}>
            <td className={`py-2 px-3 text-sm ${entry.cleared ? 'text-gray-400 line-through' : 'text-gray-700'}`}>
                {entry.date}
            </td>
            <td className={`py-2 px-3 text-sm ${entry.cleared ? 'text-gray-400 line-through' : 'text-gray-700'}`}>
                {entry.description}
            </td>
            <td className={`py-2 px-3 text-right text-sm font-medium ${entry.cleared ? 'text-gray-400' : entry.amount < 0 ? 'text-red-600' : 'text-green-600'}`}>
                {formatCurrency(entry.amount)}
            </td>
            <td className="py-2 px-3 text-center">
                <input type="checkbox" checked={entry.cleared}
                    onChange={() => onToggleCleared(entry.id)}
                    className="h-4 w-4 text-blue-600 cursor-pointer" />
            </td>
            <td className={`py-2 px-3 text-right text-sm font-medium ${runningBalance < 0 ? 'text-red-600' : 'text-gray-800'}`}>
                {formatCurrency(runningBalance)}
            </td>
            <td className="py-2 px-3">
                <div className="flex space-x-3">
                    <button onClick={() => setEditing(true)}
                        className="text-blue-600 hover:text-blue-800 text-sm">Edit</button>
                    <button onClick={() => onDelete(entry.id)}
                        className="text-red-500 hover:text-red-700 text-sm">Del</button>
                </div>
            </td>
        </tr>
    );
};

// ── AccountLedger ─────────────────────────────────────────────────────────────

const AccountLedger = ({ title, startingBalance, entries, onAdd, onEdit, onDelete, onToggleCleared, onUpdateStartingBalance }) => {
    const [showAddForm, setShowAddForm] = React.useState(false);
    const [isEditingBalance, setIsEditingBalance] = React.useState(false);
    const [newEntry, setNewEntry] = React.useState({
        date: new Date().toISOString().split('T')[0],
        description: '',
        amount: '',
        isInbound: false
    });

    // Always sort by date ascending so running balance accumulates correctly
    const sorted = [...entries].sort((a, b) => a.date.localeCompare(b.date));

    let running = startingBalance;
    const rows = sorted.map(e => {
        running += e.amount;
        return { ...e, runningBalance: running };
    });

    const clearedBalance = startingBalance + sorted.filter(e => e.cleared).reduce((s, e) => s + e.amount, 0);
    const pendingTotal = sorted.filter(e => !e.cleared).reduce((s, e) => s + e.amount, 0);
    const available = clearedBalance + pendingTotal;

    const handleBalanceSubmit = (e) => {
        e.preventDefault();
        const val = parseFloat(e.target.balance.value);
        if (isNaN(val)) return;
        if (!window.confirm('Changing the starting balance will affect all running totals. Continue?')) return;
        onUpdateStartingBalance(val);
        setIsEditingBalance(false);
    };

    const handleSubmit = (e) => {
        e.preventDefault();
        const raw = parseFloat(newEntry.amount);
        if (isNaN(raw) || raw === 0) { alert('Enter a valid amount.'); return; }
        const signed = newEntry.isInbound ? Math.abs(raw) : -Math.abs(raw);
        onAdd({ ...newEntry, amount: signed, cleared: false });
        setNewEntry({ date: new Date().toISOString().split('T')[0], description: '', amount: '', isInbound: false });
        setShowAddForm(false);
    };

    const statColor = (val) => val < 0 ? 'text-red-600' : 'text-gray-900';

    return (
        <div className="bg-white rounded-lg shadow p-6">
            <h2 className="text-xl font-bold mb-4 text-gray-800">{title}</h2>

            {/* Starting balance row */}
            <div className="mb-4 flex items-center space-x-3">
                <span className="text-sm text-gray-500">Starting Balance:</span>
                {isEditingBalance ? (
                    <form onSubmit={handleBalanceSubmit} className="flex items-center space-x-2">
                        <input type="number" name="balance" step="0.01" defaultValue={startingBalance}
                            autoFocus
                            className="w-32 rounded border-gray-300 shadow-sm text-sm px-2 py-1" />
                        <button type="submit"
                            className="px-3 py-1 text-sm bg-blue-600 text-white rounded hover:bg-blue-700">Save</button>
                        <button type="button" onClick={() => setIsEditingBalance(false)}
                            className="px-3 py-1 text-sm border border-gray-300 rounded text-gray-600 hover:bg-gray-50">Cancel</button>
                    </form>
                ) : (
                    <>
                        <span className={`font-semibold ${statColor(startingBalance)}`}>{formatCurrency(startingBalance)}</span>
                        <button onClick={() => setIsEditingBalance(true)}
                            className="text-blue-600 hover:text-blue-800 text-xs">Edit</button>
                    </>
                )}
            </div>

            {/* Ledger table */}
            <div className="overflow-x-auto">
                <table className="min-w-full divide-y divide-gray-200">
                    <thead className="bg-gray-50">
                        <tr>
                            <th className="px-3 py-2 text-left text-xs font-medium text-gray-500 uppercase">Date</th>
                            <th className="px-3 py-2 text-left text-xs font-medium text-gray-500 uppercase">Description</th>
                            <th className="px-3 py-2 text-right text-xs font-medium text-gray-500 uppercase">Amount</th>
                            <th className="px-3 py-2 text-center text-xs font-medium text-gray-500 uppercase">Cleared</th>
                            <th className="px-3 py-2 text-right text-xs font-medium text-gray-500 uppercase">Balance</th>
                            <th className="px-3 py-2 text-center text-xs font-medium text-gray-500 uppercase">Actions</th>
                        </tr>
                    </thead>
                    <tbody className="bg-white">
                        {rows.length === 0 ? (
                            <tr>
                                <td colSpan="6" className="py-10 text-center text-gray-400 text-sm">No entries yet</td>
                            </tr>
                        ) : rows.map(entry => (
                            <EntryRow
                                key={entry.id}
                                entry={entry}
                                runningBalance={entry.runningBalance}
                                onToggleCleared={onToggleCleared}
                                onDelete={onDelete}
                                onEdit={onEdit}
                            />
                        ))}
                    </tbody>
                </table>
            </div>

            {/* Add entry */}
            {showAddForm ? (
                <form onSubmit={handleSubmit} className="mt-4 p-4 border border-gray-200 rounded-lg space-y-3 bg-gray-50">
                    <p className="text-sm font-medium text-gray-700">New Entry</p>
                    <div className="grid grid-cols-1 sm:grid-cols-2 gap-3">
                        <div>
                            <label className="block text-xs text-gray-500 mb-1">Date</label>
                            <input type="date" value={newEntry.date}
                                onChange={e => setNewEntry({ ...newEntry, date: e.target.value })}
                                className="block w-full rounded border-gray-300 shadow-sm text-sm px-2 py-1" required />
                        </div>
                        <div>
                            <label className="block text-xs text-gray-500 mb-1">Amount</label>
                            <div className="flex space-x-2">
                                <input type="number" step="0.01" min="0" placeholder="0.00" value={newEntry.amount}
                                    onChange={e => setNewEntry({ ...newEntry, amount: e.target.value })}
                                    className="block w-full rounded border-gray-300 shadow-sm text-sm px-2 py-1" required />
                                <select value={newEntry.isInbound ? 'in' : 'out'}
                                    onChange={e => setNewEntry({ ...newEntry, isInbound: e.target.value === 'in' })}
                                    className="rounded border-gray-300 shadow-sm text-sm px-2 py-1">
                                    <option value="out">Payment</option>
                                    <option value="in">Deposit</option>
                                </select>
                            </div>
                        </div>
                    </div>
                    <div>
                        <label className="block text-xs text-gray-500 mb-1">Description</label>
                        <input type="text" value={newEntry.description}
                            onChange={e => setNewEntry({ ...newEntry, description: e.target.value })}
                            className="block w-full rounded border-gray-300 shadow-sm text-sm px-2 py-1" required />
                    </div>
                    <div className="flex space-x-2">
                        <button type="submit"
                            className="px-4 py-2 text-sm font-medium text-white bg-blue-600 rounded hover:bg-blue-700">
                            {newEntry.isInbound ? '+ Add Deposit' : '+ Add Payment'}
                        </button>
                        <button type="button" onClick={() => setShowAddForm(false)}
                            className="px-4 py-2 text-sm font-medium text-gray-600 border border-gray-300 rounded hover:bg-gray-100">
                            Cancel
                        </button>
                    </div>
                </form>
            ) : (
                <button onClick={() => setShowAddForm(true)}
                    className="mt-4 px-4 py-2 text-sm font-medium text-white bg-blue-600 rounded hover:bg-blue-700">
                    + Add Entry
                </button>
            )}

            {/* Balance summary */}
            <div className="mt-6 pt-4 border-t grid grid-cols-3 gap-4 text-center">
                <div>
                    <p className="text-xs font-medium text-gray-500 uppercase tracking-wide">Cleared Balance</p>
                    <p className={`text-lg font-semibold mt-1 ${statColor(clearedBalance)}`}>{formatCurrency(clearedBalance)}</p>
                </div>
                <div>
                    <p className="text-xs font-medium text-gray-500 uppercase tracking-wide">Pending</p>
                    <p className={`text-lg font-semibold mt-1 ${statColor(pendingTotal)}`}>{formatCurrency(pendingTotal)}</p>
                </div>
                <div>
                    <p className="text-xs font-medium text-gray-500 uppercase tracking-wide">Available</p>
                    <p className={`text-lg font-semibold mt-1 ${statColor(available)}`}>{formatCurrency(available)}</p>
                </div>
            </div>
        </div>
    );
};

// ── LogApp ────────────────────────────────────────────────────────────────────

const LogApp = () => {
    const [checking, setChecking] = React.useState({ startingBalance: 0, entries: [] });
    const [savings, setSavings] = React.useState({ startingBalance: 0, entries: [] });
    const [loading, setLoading] = React.useState(true);
    const [error, setError] = React.useState(null);

    const accountState = (type) => type === 'checking' ? checking : savings;
    const setAccountState = (type, updater) =>
        type === 'checking' ? setChecking(updater) : setSavings(updater);

    const loadAll = () => {
        setLoading(true);
        Promise.all([
            fetch('/api/log/balances').then(r => r.json()),
            fetch('/api/log/entries').then(r => r.json())
        ])
        .then(([balances, entries]) => {
            const balMap = {};
            balances.forEach(b => { balMap[b.account_type] = b.starting_balance; });

            const mapEntry = e => ({ id: e.id, date: e.date, description: e.description, amount: e.amount, cleared: e.cleared });

            setChecking({
                startingBalance: balMap['checking'] || 0,
                entries: entries.filter(e => e.account_type === 'checking').map(mapEntry)
            });
            setSavings({
                startingBalance: balMap['savings'] || 0,
                entries: entries.filter(e => e.account_type === 'savings').map(mapEntry)
            });
            setLoading(false);
        })
        .catch(() => {
            setError('Failed to load log data. Please refresh the page.');
            setLoading(false);
        });
    };

    React.useEffect(() => { loadAll(); }, []);

    const updateStartingBalance = (accountType, newBalance) => {
        fetch(`/api/log/balances/${accountType}`, {
            method: 'PUT',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ starting_balance: newBalance })
        })
        .then(r => { if (!r.ok) throw r; return r.json(); })
        .then(saved => {
            setAccountState(accountType, prev => ({ ...prev, startingBalance: saved.starting_balance }));
        })
        .catch(() => alert('Failed to save balance. Please try again.'));
    };

    const addEntry = (accountType, entry) => {
        fetch('/api/log/entries', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
                account_type: accountType,
                date: entry.date,
                description: entry.description,
                amount: entry.amount,
                cleared: entry.cleared
            })
        })
        .then(r => { if (!r.ok) throw r; return r.json(); })
        .then(saved => {
            setAccountState(accountType, prev => ({
                ...prev,
                entries: [...prev.entries, { id: saved.id, date: saved.date, description: saved.description, amount: saved.amount, cleared: saved.cleared }]
            }));
        })
        .catch(() => alert('Failed to add entry. Please try again.'));
    };

    const editEntry = (accountType, entryId, updates) => {
        fetch(`/api/log/entries/${entryId}`, {
            method: 'PUT',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(updates)
        })
        .then(r => { if (!r.ok) throw r; return r.json(); })
        .then(saved => {
            setAccountState(accountType, prev => ({
                ...prev,
                entries: prev.entries.map(e =>
                    e.id === entryId
                        ? { id: saved.id, date: saved.date, description: saved.description, amount: saved.amount, cleared: saved.cleared }
                        : e
                )
            }));
        })
        .catch(() => alert('Failed to update entry. Please try again.'));
    };

    const toggleCleared = (accountType, entryId) => {
        const entry = accountState(accountType).entries.find(e => e.id === entryId);
        if (!entry) return;
        editEntry(accountType, entryId, { cleared: !entry.cleared });
    };

    const deleteEntry = (accountType, entryId) => {
        if (!window.confirm('Delete this entry?')) return;
        fetch(`/api/log/entries/${entryId}`, { method: 'DELETE' })
        .then(r => { if (!r.ok) throw r; return r.json(); })
        .then(() => {
            setAccountState(accountType, prev => ({
                ...prev,
                entries: prev.entries.filter(e => e.id !== entryId)
            }));
        })
        .catch(() => alert('Failed to delete entry. Please try again.'));
    };

    const clearAllEntries = () => {
        if (!window.confirm('Permanently delete ALL log entries from the database? Starting balances will be kept. This cannot be undone.')) return;
        fetch('/api/log/clear', { method: 'POST' })
        .then(r => { if (!r.ok) throw r; return r.json(); })
        .then(() => {
            setChecking(prev => ({ ...prev, entries: [] }));
            setSavings(prev => ({ ...prev, entries: [] }));
        })
        .catch(() => alert('Failed to clear entries. Please try again.'));
    };

    if (loading) {
        return (
            <div className="flex items-center justify-center py-20">
                <p className="text-gray-400 text-sm">Loading log data…</p>
            </div>
        );
    }

    if (error) {
        return (
            <div className="flex items-center justify-center py-20">
                <p className="text-red-500 text-sm">{error}</p>
            </div>
        );
    }

    return (
        <div className="space-y-8">
            <div className="flex justify-end">
                <button onClick={clearAllEntries}
                    className="px-4 py-2 text-sm font-medium text-white bg-red-600 rounded hover:bg-red-700 focus:outline-none focus:ring-2 focus:ring-offset-2 focus:ring-red-500">
                    Clear All Entries
                </button>
            </div>

            <AccountLedger
                title="Checking Account"
                startingBalance={checking.startingBalance}
                entries={checking.entries}
                onAdd={(e) => addEntry('checking', e)}
                onEdit={(id, updates) => editEntry('checking', id, updates)}
                onDelete={(id) => deleteEntry('checking', id)}
                onToggleCleared={(id) => toggleCleared('checking', id)}
                onUpdateStartingBalance={(bal) => updateStartingBalance('checking', bal)}
            />

            <AccountLedger
                title="Savings Account"
                startingBalance={savings.startingBalance}
                entries={savings.entries}
                onAdd={(e) => addEntry('savings', e)}
                onEdit={(id, updates) => editEntry('savings', id, updates)}
                onDelete={(id) => deleteEntry('savings', id)}
                onToggleCleared={(id) => toggleCleared('savings', id)}
                onUpdateStartingBalance={(bal) => updateStartingBalance('savings', bal)}
            />
        </div>
    );
};

ReactDOM.render(<LogApp />, document.getElementById('log-app'));
