import assert from 'node:assert/strict';
import {JSDOM} from 'jsdom';
import {dashboardPage,dashboardJs,fields} from './dashboard.mjs';
const script=process.env.HOSTED_CHECK==='1'?await(await fetch('https://datafoldit-test-poc.vamsibh07.workers.dev/dashboard.js')).text():dashboardJs;
for(const kind of Object.keys(fields))for(const role of ['write','read']){
 const data={...Object.fromEntries(Object.entries(fields[kind]).map(([k,t])=>[k,Array.isArray(t)?t[0]:t==='number'?100:t==='date'?'2026-09-01':t==='month'?'2026-09':'Synthetic'])),invoice_number:'INV-000020',status:'Paid',balance_due:0,received_date:'2026-09-02',credit_date:'2026-09-03',paystub_sent:'Y'};
 const original={id:'original',kind,tracking_id:kind==='invoices'?null:'OLD-000001',version:1,updated_by:'original@example.test',data},snapshot=JSON.stringify(original),rows=[original];let writes=0,payload;
 const dom=new JSDOM(dashboardPage({email:'cloner@example.test',role,csrf:'test'}),{url:'https://test/#'+kind,runScripts:'outside-only'}),w=dom.window;w.scrollTo=()=>{};
 w.fetch=async(path,options)=>{if(options?.method==='POST'){writes++;payload=JSON.parse(options.body);return {ok:true,json:async()=>({id:'new'})};}return {ok:true,json:async()=>path==='/api/records'?rows:[{record_id:'original',id:'attachment',name:'original.pdf'}]};};
 try{
  w.eval(script);await new Promise(r=>setTimeout(r,20));const button=[...w.document.querySelectorAll('button')].find(b=>b.getAttribute('aria-label')?.startsWith('Duplicate '));
  if(role==='read'||kind==='invoices'){assert.equal(button,undefined);continue;}
  assert.ok(button);const sourceRow=button.closest('tr');button.click();let draft=w.document.querySelector('.invoice-draft');assert.ok(draft);assert.equal(draft.previousElementSibling.textContent,sourceRow.textContent);assert.equal(writes,0);
  const value=k=>draft.querySelector('[name="'+k+'"]').value;
  assert.equal(value(kind==='payroll'?'month':'date'),kind==='payroll'?'2026-09':'2026-09-01');
  assert.equal(draft.querySelectorAll('a').length,0);assert.equal(draft.querySelector('input[type=file]').files.length,0);assert.ok(draft.textContent.includes('Not saved'));
  if(kind==='invoices'){assert.equal(value('invoice_number'),'INV-000021');assert.equal(value('status'),'Open');assert.equal(value('balance_due'),'100');assert.equal(value('received_date'),'');}
  else assert.ok(draft.firstElementChild.textContent.includes('Assigned on save'));
  if(kind==='payroll'){assert.equal(value('credit_date'),'');assert.equal(value('paystub_sent'),'N');}
  const change=draft.querySelector('[name="'+(kind==='payroll'?'first_name':kind==='invoices'?'customer':kind==='bank'?'detail':'description')+'"]');change.value='Edited clone';
  [...w.document.querySelectorAll('button')].find(b=>b.getAttribute('aria-label')?.startsWith('Duplicate ')).click();assert.equal(change.value,'Edited clone');assert.equal(w.document.querySelectorAll('.invoice-draft').length,1);
  [...draft.querySelectorAll('button')].find(b=>b.textContent==='Cancel').click();assert.equal(w.document.querySelector('.invoice-draft'),null);assert.equal(writes,0);
  [...w.document.querySelectorAll('button')].find(b=>b.getAttribute('aria-label')?.startsWith('Duplicate ')).click();draft=w.document.querySelector('.invoice-draft');const save=[...draft.querySelectorAll('button')].find(b=>b.textContent==='Save');save.click();save.click();await new Promise(r=>setTimeout(r,20));
  assert.equal(writes,1);assert.equal(payload.action,'create');assert.equal(payload.kind,kind);assert.equal(payload.id,undefined);assert.equal(payload.data.tracking_id,undefined);assert.equal(payload.data.updated_by,undefined);assert.equal(payload.data.attachments,undefined);
  const {attachments,...untouched}=original;assert.equal(JSON.stringify(untouched),snapshot,'source is not modified');assert.equal(w.document.querySelector('.invoice-draft'),null);
 }finally{w.close();}
}
console.log('PASS: clone for Bank, Expenses and Payroll only; no invoice clone; copied dates, payroll resets, no attachments or identity copied, cancel, draft preservation, single create and read-only restrictions');
