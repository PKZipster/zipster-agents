const axios = require('axios');
const { SHOPIFY_API_KEY, SHOPIFY_STORE } = require('./config');

const BASE_URL = `https://${SHOPIFY_STORE}.myshopify.com/admin/api/2024-01`;

async function apiGet(endpoint) {
  const res = await axios.get(`${BASE_URL}/${endpoint}`, {
    headers: { 'X-Shopify-Access-Token': SHOPIFY_API_KEY },
    timeout: 15000,
  });
  return res.data;
}

async function apiGetPaginated(endpoint, resourceKey) {
  const items = [];
  let url = `${BASE_URL}/${endpoint}`;

  while (url) {
    const res = await axios.get(url, {
      headers: { 'X-Shopify-Access-Token': SHOPIFY_API_KEY },
      timeout: 30000,
    });
    items.push(...(res.data[resourceKey] || []));

    // Parse Link header for next page
    const link = res.headers.link || '';
    const nextMatch = link.match(/<([^>]+)>;\s*rel="next"/);
    url = nextMatch ? nextMatch[1] : null;
  }
  return items;
}

function pctChange(current, previous) {
  if (previous === 0) return current > 0 ? '+100.0%' : '0.0%';
  const change = ((current - previous) / previous) * 100;
  return `${change >= 0 ? '+' : ''}${change.toFixed(1)}%`;
}

async function getRevenueContext() {
  if (!SHOPIFY_API_KEY) {
    return 'SHOPIFY API NOT CONNECTED.\nNeed SHOPIFY_API_KEY in secrets.env with read_orders scope.';
  }

  const sevenDaysAgo = new Date(Date.now() - 7 * 86400000).toISOString().replace(/\.\d+Z$/, 'Z');
  const orders = await apiGetPaginated(
    `orders.json?status=any&created_at_min=${sevenDaysAgo}&limit=250`, 'orders'
  );

  if (!orders.length) return 'No orders found in the last 7 days.';

  const currency = orders[0].currency || 'EUR';
  const daily = {};
  for (const o of orders) {
    const day = o.created_at.slice(0, 10);
    if (!daily[day]) daily[day] = { revenue: 0, orders: 0 };
    daily[day].revenue += parseFloat(o.total_price || 0);
    daily[day].orders += 1;
  }

  const now = new Date();
  const todayStr = now.toISOString().slice(0, 10);
  const yesterdayStr = new Date(now - 86400000).toISOString().slice(0, 10);
  const today = daily[todayStr] || { revenue: 0, orders: 0 };
  const yesterday = daily[yesterdayStr] || { revenue: 0, orders: 0 };
  const todayAov = today.orders > 0 ? today.revenue / today.orders : 0;
  const yesterdayAov = yesterday.orders > 0 ? yesterday.revenue / yesterday.orders : 0;
  const currentTime = now.toTimeString().slice(0, 5);

  const totalRevenue = orders.reduce((s, o) => s + parseFloat(o.total_price || 0), 0);

  const lines = [
    `=== TODAY vs YESTERDAY (as of ${currentTime}) ===`,
    `TODAY (${todayStr}):`,
    `  Revenue: ${currency} ${today.revenue.toLocaleString('en', { minimumFractionDigits: 2 })} (${pctChange(today.revenue, yesterday.revenue)} vs yesterday)`,
    `  Orders: ${today.orders} (${pctChange(today.orders, yesterday.orders)} vs yesterday)`,
    `  AOV: ${currency} ${todayAov.toFixed(2)} (${pctChange(todayAov, yesterdayAov)} vs yesterday)`,
    `YESTERDAY (${yesterdayStr}):`,
    `  Revenue: ${currency} ${yesterday.revenue.toLocaleString('en', { minimumFractionDigits: 2 })}`,
    `  Orders: ${yesterday.orders}`,
    `  AOV: ${currency} ${yesterdayAov.toFixed(2)}`,
    '',
    `=== LAST 7 DAYS TOTAL ===`,
    `Total Revenue: ${currency} ${totalRevenue.toLocaleString('en', { minimumFractionDigits: 2 })}`,
    `Total Orders: ${orders.length}`,
    `7-day AOV: ${currency} ${(totalRevenue / orders.length).toFixed(2)}`,
    '',
    'DAILY BREAKDOWN:',
  ];

  for (const day of Object.keys(daily).sort().reverse()) {
    const d = daily[day];
    const aov = d.orders > 0 ? d.revenue / d.orders : 0;
    lines.push(`  ${day}: ${currency} ${d.revenue.toLocaleString('en', { minimumFractionDigits: 2 })} (${d.orders} orders, AOV ${currency} ${aov.toFixed(2)})`);
  }

  lines.push('\nAD SPEND DATA: Not yet connected. To calculate MER, need Meta Ads and Google Ads API integration.');
  return lines.join('\n');
}

async function getProductsContext() {
  if (!SHOPIFY_API_KEY) {
    return 'SHOPIFY API NOT CONNECTED.\nNeed SHOPIFY_API_KEY in secrets.env with read_products scope.';
  }

  const products = await apiGetPaginated('products.json?limit=250', 'products');
  if (!products.length) return 'No products found.';

  const total = products.length;
  const active = products.filter(p => p.status === 'active').length;
  const draft = products.filter(p => p.status === 'draft').length;
  const archived = products.filter(p => p.status === 'archived').length;

  const missingDesc = [];
  const missingTags = [];
  const missingImages = [];
  const lowInventory = [];

  for (const p of products) {
    const name = p.title || 'Unknown';
    if (!p.body_html || p.body_html.length < 20) missingDesc.push(name);
    if (!p.tags) missingTags.push(name);
    if (!p.images || !p.images.length) missingImages.push(name);
    for (const v of (p.variants || [])) {
      if (v.inventory_quantity > 0 && v.inventory_quantity <= 5 && p.status === 'active') {
        lowInventory.push(`${name} (${v.title || 'Default'}): ${v.inventory_quantity} left`);
      }
    }
  }

  const lines = [
    `=== SHOPIFY PRODUCT CATALOG ===`,
    `Total Products: ${total}`,
    `  Active: ${active}`,
    `  Draft: ${draft}`,
    `  Archived: ${archived}`,
    '',
    'ISSUES FOUND:',
    `  Missing/short description: ${missingDesc.length}`,
    `  Missing tags: ${missingTags.length}`,
    `  Missing images: ${missingImages.length}`,
    `  Low inventory (<=5 units): ${lowInventory.length}`,
  ];

  if (missingDesc.length) {
    lines.push('\nTOP PRODUCTS NEEDING DESCRIPTIONS:');
    missingDesc.slice(0, 10).forEach(n => lines.push(`  * ${n}`));
  }
  if (missingTags.length) {
    lines.push('\nTOP PRODUCTS NEEDING TAGS:');
    missingTags.slice(0, 10).forEach(n => lines.push(`  * ${n}`));
  }
  if (lowInventory.length) {
    lines.push('\nLOW INVENTORY ALERTS:');
    lowInventory.slice(0, 10).forEach(n => lines.push(`  * ${n}`));
  }

  return lines.join('\n');
}

module.exports = { apiGet, apiGetPaginated, getRevenueContext, getProductsContext };
