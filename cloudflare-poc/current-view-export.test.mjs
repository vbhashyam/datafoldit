import assert from 'node:assert/strict';
import {JSDOM} from 'jsdom';
import {dashboardPage,dashboardJs,fields} from './dashboard.mjs';
const script=process.env.HOSTED_CHECK==='1'?await(await fetch('https://datafoldit-test-poc.vamsibh07.workers.dev/dashboard.js')).text():dashboardJs;
for(const kind of Object.keys(fields)){
 const rows=['Alpha','Beta'].map((name,i)=>({id:name,kind,updated_by:'Sample',tracking_id:'TEST-'+i,data:{date:'2026-09-01',month:'2026-09',description:name,detail:name,customer:name,vendor:name,first_name:name,amount:100*(i+1),gross:100*(i+1),status:'Open',balance_due:100*(i+1),type:'Deposit'}}));
 const dom=new JSDOM(dashboardPage({email:'sample@example.test',role:'read',csrf:'test'}),{url:'https://test/#'+kind,runScripts:'outside-only'}),w=dom.window;let csvBlob,excelRows;
 w.scrollTo=()=>{};w.fetch=async path=>({ok:true,json:async()=>path==='/api/records'?rows:[]});w.Blob=Blob;w.URL.createObjectURL=b=>(csvBlob=b,'blob:test');w.URL.revokeObjectURL=()=>{};w.HTMLAnchorElement.prototype.click=()=>{};
 try{w.eval(script);await new Promise(r=>setTimeout(r,25));assert.ok(w.document.querySelector('#page-filters .ledger-filters'));assert.equal(w.document.querySelectorAll('.summary-card').length,kind==='invoices'?5:3);assert.equal([...w.document.querySelectorAll('.toolbar>button')].some(b=>['Export CSV','Export Excel'].includes(b.textContent)),false);
 const search=w.document.querySelector('.toolbar input[name=search]'),open=()=>{w.document.querySelector('.ledger-export-button').click();return w.document.querySelector('body>.export-popup');};
 for(const query of ['','Alpha']){search.value=query;search.dispatchEvent(new w.Event('input'));const count=query?1:2;assert.ok(w.document.querySelector('.summary-cards').textContent.includes(count+' matching transactions'));let popup=open();assert.ok(popup.textContent.includes('Export '+count+' matching'));[...popup.querySelectorAll('button')].find(b=>b.textContent==='CSV (.csv)').click();const csv=await csvBlob.text();assert.equal(csv.split('\r\n').length,count+1);assert.equal(csv.includes('Beta'),!query);
 w.loadExportModule=async()=>({exportWorkbook:async records=>{excelRows=records;}});popup=open();await [...popup.querySelectorAll('button')].find(b=>b.textContent==='Excel (.xlsx)').onclick();assert.equal(excelRows.length,count);assert.equal(excelRows.some(r=>r.id==='Beta'),!query);
 }
 search.value='not present';search.dispatchEvent(new w.Event('input'));const popup=open();assert.ok([...popup.querySelectorAll('button')].every(b=>b.disabled));
 }finally{w.close();}
}
console.log('PASS: compact top filters, updated summary cards, one export icon, all/filtered CSV and Excel exactly match table rows, empty exports disabled');
