import assert from 'node:assert/strict';
import {JSDOM} from 'jsdom';
import {runInNewContext} from 'node:vm';
import {dashboardPage,dashboardJs,fields} from './dashboard.mjs';
import {ledgerControlsJs} from './ledger-controls.mjs';
import {filterReport,makeCsv} from './financial-views.mjs';
const row={id:'sample',kind:'expenses',tracking_id:'EXP-000042',version:1,updated_by:'Sample',data:{date:'2026-09-09',amount:100,description:'Synthetic'}};
const helpers=runInNewContext(ledgerControlsJs+'\n({ledgerRows,ledgerState,ledgerValue})',{fields});const state=helpers.ledgerState('expenses');state.search='EXP-000042';assert.equal(helpers.ledgerRows([row],'expenses',state).length,1);state.search='';state.columns={tracking_id:'000042'};assert.equal(helpers.ledgerRows([row],'expenses',state).length,1);
assert.equal(filterReport([row],'expenses','','','EXP-000042').length,1);assert.ok(makeCsv([row],['tracking_id','amount']).includes('EXP-000042'));
for(const tab of ['expenses']){
 const dom=new JSDOM(dashboardPage({email:'sample@example.test',role:'write',csrf:'test'}),{url:'https://test/#'+tab,runScripts:'outside-only'}),w=dom.window;w.scrollTo=()=>{};w.fetch=async path=>({ok:true,json:async()=>path==='/api/records'?[row]:[]});
 try{w.eval(dashboardJs);await new Promise(r=>setTimeout(r,20));assert.equal(w.document.querySelector('#message').textContent,'');assert.ok(w.document.querySelector('#content').textContent.includes('trnsc_id'));const cell=[...w.document.querySelectorAll('td')].find(td=>td.textContent==='EXP-000042');assert.ok(cell);assert.equal(cell.cellIndex,0);cell.click();assert.equal(cell.querySelector('input'),null,'tracking ID is read-only');}finally{w.close();}
}
console.log('PASS: read-only tracking column in ledger and report, ID search, ID column filter and CSV reference');
