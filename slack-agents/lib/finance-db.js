const Database = require('better-sqlite3');
const { FINANCE_DB_PATH } = require('./config');
const fs = require('fs');

function getFinanceContext() {
  if (!fs.existsSync(FINANCE_DB_PATH)) return 'Finance database not available yet.';

  const db = new Database(FINANCE_DB_PATH, { readonly: true });
  const today = new Date().toISOString().slice(0, 10);
  const sevenDays = new Date(Date.now() + 7 * 86400000).toISOString().slice(0, 10);
  const thirtyDays = new Date(Date.now() + 30 * 86400000).toISOString().slice(0, 10);
  const sevenDaysAgo = new Date(Date.now() - 7 * 86400000).toISOString().slice(0, 10);

  const where = "supplier != '_not_invoice' AND amount > 0";

  const totalInvoices = db.prepare(
    `SELECT COUNT(*) as c FROM invoices WHERE status NOT IN ('skipped','duplicate','reminder') AND ${where}`
  ).get().c;

  const overdue = db.prepare(
    `SELECT supplier, amount, currency, due_date, invoice_number, reminder_count FROM invoices WHERE status='overdue' AND ${where} GROUP BY invoice_number ORDER BY due_date ASC`
  ).all();

  const unpaid = db.prepare(
    `SELECT supplier, amount, currency, due_date, invoice_number, reminder_count FROM invoices WHERE status='unpaid' AND ${where} GROUP BY invoice_number ORDER BY due_date ASC`
  ).all();

  const dueSoon = db.prepare(
    `SELECT supplier, amount, currency, due_date, invoice_number FROM invoices WHERE status='unpaid' AND due_date !='unknown' AND due_date<=? AND due_date>=? AND ${where} GROUP BY invoice_number`
  ).all(sevenDays, today);

  const due30d = db.prepare(
    `SELECT supplier, amount, currency, due_date, invoice_number FROM invoices WHERE status='unpaid' AND due_date !='unknown' AND due_date<=? AND due_date>=? AND ${where} GROUP BY invoice_number`
  ).all(thirtyDays, today);

  const paidCount = db.prepare("SELECT COUNT(*) as c FROM invoices WHERE status='paid'").get().c;

  const sumByStatus = (status, curr) => db.prepare(
    `SELECT COALESCE(SUM(amount),0) as s FROM invoices WHERE status=? AND currency=? AND ${where}`
  ).get(status, curr).s;

  const overdueEur = sumByStatus('overdue', 'EUR');
  const unpaidEur = sumByStatus('unpaid', 'EUR');
  const overdueUsd = sumByStatus('overdue', 'USD');
  const overdueChf = sumByStatus('overdue', 'CHF');

  const topSuppliers = db.prepare(
    `SELECT supplier, SUM(amount) as total, currency FROM invoices WHERE status IN ('unpaid','overdue') AND ${where} GROUP BY supplier, currency ORDER BY total DESC LIMIT 10`
  ).all();

  const recentPaid = db.prepare(
    `SELECT supplier, amount, currency, invoice_number FROM invoices WHERE status='paid' AND extracted_date>=? AND amount>0 AND supplier !='_not_invoice' GROUP BY invoice_number ORDER BY amount DESC LIMIT 5`
  ).all(sevenDaysAgo);

  db.close();

  const fmt = (n) => Number(n).toLocaleString('en', { minimumFractionDigits: 2, maximumFractionDigits: 2 });

  const lines = [
    `=== FINANCE SNAPSHOT \u2014 ${today} ===`,
    `Total invoices tracked: ${totalInvoices} | Paid: ${paidCount}`,
    '',
    'CASH POSITION \u2014 OUTSTANDING:',
    `  Overdue: \u20ac${fmt(overdueEur)} EUR`,
  ];
  if (overdueUsd > 0) lines.push(`  Overdue: $${fmt(overdueUsd)} USD`);
  if (overdueChf > 0) lines.push(`  Overdue: CHF ${fmt(overdueChf)}`);
  lines.push(`  Unpaid (not yet due): \u20ac${fmt(unpaidEur)} EUR`);
  lines.push(`  Total outstanding EUR: \u20ac${fmt(overdueEur + unpaidEur)}`);
  lines.push(`  Due within 7 days: ${dueSoon.length} invoices`);
  lines.push(`  Due within 30 days: ${due30d.length} invoices`);

  lines.push('\nTOP SUPPLIERS BY OUTSTANDING:');
  for (const { supplier, total, currency } of topSuppliers) {
    lines.push(`  * ${supplier}: ${currency} ${fmt(total)}`);
  }

  if (overdue.length) {
    lines.push(`\nOVERDUE (${overdue.length}):`);
    for (const r of overdue) {
      const rem = r.reminder_count ? ` \u26a0\ufe0f ${r.reminder_count} reminder(s)` : '';
      lines.push(`  #${r.invoice_number} \u2014 ${r.supplier} \u2014 ${r.currency} ${fmt(r.amount)} \u2014 due ${r.due_date}${rem}`);
    }
  }

  if (unpaid.length) {
    lines.push(`\nUNPAID (${unpaid.length}):`);
    for (const r of unpaid) {
      lines.push(`  #${r.invoice_number} \u2014 ${r.supplier} \u2014 ${r.currency} ${fmt(r.amount)} \u2014 due ${r.due_date}`);
    }
  }

  if (recentPaid.length) {
    lines.push('\nRECENTLY PAID (last 7 days):');
    for (const r of recentPaid) {
      lines.push(`  #${r.invoice_number} \u2014 ${r.supplier} \u2014 ${r.currency} ${fmt(r.amount)}`);
    }
  }

  return lines.join('\n');
}

function markInvoicePaid(invoiceNumber) {
  if (!fs.existsSync(FINANCE_DB_PATH)) return 'Finance database not available.';
  const db = new Database(FINANCE_DB_PATH);
  const result = db.prepare(
    "UPDATE invoices SET status='paid' WHERE invoice_number=? AND status IN ('unpaid','overdue')"
  ).run(invoiceNumber);
  db.close();
  if (result.changes > 0) return `Marked invoice #${invoiceNumber} as paid (${result.changes} record(s) updated).`;
  return `No unpaid/overdue invoice found with number #${invoiceNumber}.`;
}

module.exports = { getFinanceContext, markInvoicePaid };
