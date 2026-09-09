import assert from 'node:assert/strict';
import {runInNewContext} from 'node:vm';
import {JSDOM} from 'jsdom';
import {ledgerControlsJs} from './ledger-controls.mjs';
import {dashboardPage,dashboardJs,fields} from './dashboard.mjs';
class FixedDate extends Date{constructor(...args){super(...(args.length?args:['2026-09-09T12:00:00Z']));}static now(){return Date.parse('2026-09-09T12:00:00Z');}}
const helpers=runInNewContext(ledgerControlsJs+'\n({ledgerValue,ledgerFields})',{fields,Date:FixedDate});
const cases=[['2026-10-03','Open','Due in 24d'],['2026-09-10','Open','Due in 1d'],['2026-09-09','Open','Due today'],['2026-09-08','Open','Overdue'],['2026-09-08','Paid','Paid'],['2026-09-08','VOID','Void'],['','Open','No due date']];
const rows=cases.map(([due_date,status,expected],i)=>({id:String(i),kind:'invoices',version:1,updated_by:'Synthetic',data:{date:'2026-09-01',invoice_number:'SYN-'+i,due_date,status,amount:100,balance_due:status==='Open'?100:0,received_date:status==='Paid'?'2026-09-08':''},expected}));
for(const row of rows)assert.equal(helpers.ledgerValue(row,'due_status'),row.expected);
assert.equal(helpers.ledgerValue({data:{status:'Open',balance_due:0,due_date:'2026-09-10'}},'due_status'),'Due in 1d');
const keys=Object.keys(helpers.ledgerFields('invoices'));assert.equal(keys.indexOf('received_date'),keys.indexOf('due_status')+1);
for(const role of ['read','write']){
 const dom=new JSDOM(dashboardPage({email:'synthetic@example.test',role,csrf:'test'}),{url:'https://example.test/#invoices',runScripts:'outside-only'}),w=dom.window;
 w.Date=FixedDate;w.scrollTo=()=>{};w.fetch=async p=>({ok:true,json:async()=>p==='/api/records'?rows:[]});
 try{w.eval(dashboardJs);await new Promise(r=>setTimeout(r,30));
  const badges=[...w.document.querySelectorAll('.invoice-due-badge')];assert.equal(badges.length,rows.length);for(const row of rows)assert.ok(badges.some(b=>b.textContent===row.expected));
  assert.ok(w.document.querySelector('.invoice-due-paid'));assert.ok(w.document.querySelector('.invoice-due-overdue'));assert.ok(w.document.querySelector('.invoice-due-due'));
  const heads=[...w.document.querySelectorAll('.sort-column')].map(b=>b.textContent);assert.equal(heads.findIndex(t=>t.startsWith('Received Date')),heads.findIndex(t=>t.startsWith('Due Status'))+1);
  const filter=w.document.querySelector('[aria-label="Filter Due Status"]');filter.click();const options=[...w.document.querySelectorAll('.filter-values input')].map(i=>i.value);assert.ok(options.includes('Due in 24d'));assert.ok(options.includes('Paid'));assert.ok(!options.includes('Closed'));assert.ok(!options.includes('Upcoming'));
  w.closeTransactionActions();
  const exported=w.ledgerExportRows('invoices',w.ledgerState('invoices'));assert.equal(Object.keys(exported[0].data).indexOf('received_date'),Object.keys(exported[0].data).indexOf('due_status')+1);
  if(role==='write'){w.document.querySelector('[aria-label="Add transaction"]').click();const draft=w.document.querySelector('.invoice-draft');assert.equal(draft.cells[keys.indexOf('due_status')].textContent,'Calculated on save');assert.equal(draft.cells[keys.indexOf('received_date')].querySelector('input').name,'received_date');}
 }finally{w.close();}
}
console.log('PASS: countdown/Paid badges, overdue/today/void/no-date handling, adjacent received date, value filters, exports and aligned invoice draft');
