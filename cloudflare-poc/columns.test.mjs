import assert from 'node:assert/strict';
import {JSDOM} from 'jsdom';
import {dashboardPage,dashboardJs,fields} from './dashboard.mjs';
const script=process.env.HOSTED_CHECK==='1'?await(await fetch('https://datafoldit-test-poc.vamsibh07.workers.dev/dashboard.js')).text():dashboardJs;
for(const kind of Object.keys(fields))for(const role of ['write','read']){
 const dom=new JSDOM(dashboardPage({email:'sample@example.test',role,csrf:'test'}),{url:'https://test/#'+kind,runScripts:'outside-only'}),w=dom.window;
 w.scrollTo=()=>{};w.fetch=async path=>({ok:true,json:async()=>path==='/api/records'?[{id:'a',kind,version:1,tracking_id:'TEST-1',updated_by:'Sample',data:{date:'2026-09-09',month:'2026-09',amount:100,vendor:'Synthetic',customer:'Synthetic',source:'Synthetic'}}]:[]});
 try{w.eval(script);await new Promise(r=>setTimeout(r,25));const button=w.document.querySelector('.columns-button');assert.ok(button);button.click();let popup=w.document.querySelector('body>.columns-popup');assert.ok(popup);const locked=popup.querySelector('input:disabled');assert.ok(locked.checked);assert.equal(locked.dataset.column,kind==='invoices'?'invoice_number':'tracking_id');
 const key=kind==='bank'?'source':kind==='invoices'?'customer':'vendor';popup.querySelector('[data-column="'+key+'"]').checked=false;
 popup.querySelector('.columns-list').dispatchEvent(new w.Event('scroll'));assert.equal(popup.hidden,false,'scrolling long checklist must not close it');
 popup.querySelector('.primary').click();const index=w.eval('columnKeys("'+kind+'").indexOf("'+key+'")');let table=w.document.querySelector('.table-wrap table');assert.ok([...table.rows].every(row=>row.cells[index].hidden));assert.equal(w.eval('ledgerExportRows("'+kind+'",ledgerState("'+kind+'"))[0].data["'+key+'"]'),'Synthetic');
 assert.deepEqual(JSON.parse(w.localStorage.getItem('datafoldit-columns-'+kind)),[key]);assert.equal(w.localStorage.getItem('datafoldit-columns-'+(kind==='bank'?'expenses':'bank')),null);
 if(role==='write'){w.document.querySelector('.ledger-heading button').click();table=w.document.querySelector('.table-wrap table');assert.ok([...table.rows].every(row=>!row.cells[index].hidden),'draft shows fields for safe entry');w.document.querySelector('.ledger-heading button').click();assert.ok(w.document.querySelector('.table-wrap table').rows[0].cells[index].hidden);}
 w.document.querySelector('.columns-button').click();popup=w.document.querySelector('body>.columns-popup');assert.equal(popup.querySelector('[data-column="'+key+'"]').checked,false);[...popup.querySelectorAll('button')].find(b=>b.textContent==='Reset to default').click();popup.querySelector('.primary').click();assert.equal(w.document.querySelectorAll('td[hidden],th[hidden]').length,0);
 const reload=new JSDOM(dashboardPage({email:'sample@example.test',role,csrf:'test'}),{url:'https://test/#'+kind,runScripts:'outside-only'}),v=reload.window;try{v.scrollTo=()=>{};v.fetch=w.fetch;v.localStorage.setItem('datafoldit-columns-'+kind,JSON.stringify([key,kind==='invoices'?'invoice_number':'tracking_id','garbage']));v.eval(script);await new Promise(r=>setTimeout(r,25));assert.ok(v.document.querySelector('.table-wrap table').rows[0].cells[index].hidden);assert.equal(v.eval('hiddenColumns("'+kind+'").length'),1);}finally{v.close();}
 assert.equal(w.document.querySelector('#message').textContent,'');
 }finally{w.close();}
}
console.log('PASS: all ledgers and roles, locked IDs, hidden cell alignment, per-tab browser persistence, full exports, scrolling checklist, draft safety, reset and invalid preference handling');
