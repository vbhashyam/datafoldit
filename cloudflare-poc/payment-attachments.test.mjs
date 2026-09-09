import assert from 'node:assert/strict';
import {JSDOM} from 'jsdom';
import {receivedDateOnPayment,dashboardPage,dashboardJs} from './dashboard.mjs';
const apply=(data,previous)=>receivedDateOnPayment('invoices',data,previous,'2026-09-09');
assert.equal(apply({status:'Paid',received_date:''},{status:'Open'}).received_date,'2026-09-09');
assert.equal(apply({status:'Paid',received_date:'2026-08-01'},{status:'Open'}).received_date,'2026-08-01');
assert.equal(apply({status:'Paid',received_date:''},{status:'Paid'}).received_date,'');
assert.equal(apply({status:'Open',received_date:''},{status:'Paid'}).received_date,'');
assert.equal(apply({status:'Paid',received_date:''}).received_date,'2026-09-09');
for(const tab of ['bank','expenses','payroll','invoices'])for(const role of ['read','write']){
 const dom=new JSDOM(dashboardPage({email:'test@example.test',role,csrf:'test'}),{url:'https://example.test/#'+tab,runScripts:'outside-only'}),w=dom.window;
 w.scrollTo=()=>{};w.fetch=async()=>({ok:true,json:async()=>[]});
 try{w.eval(dashboardJs);await new Promise(r=>setTimeout(r,20));
 const empty=w.transactionAttachments({id:'empty',attachments:[]});assert.equal(empty.querySelector('button').disabled,role==='read');assert.equal(empty.textContent,'');
 const filled=w.transactionAttachments({id:'full',attachments:[{id:'file',name:'sample.pdf'}]});w.document.body.append(filled);const trigger=filled.querySelector('button');assert.equal(trigger.textContent,'1');assert.ok(trigger.classList.contains('has-attachments'));assert.ok(filled.querySelector('.attachment-popover').hidden);trigger.click();const popup=w.document.querySelector('.attachment-popover:not([hidden])');assert.equal(popup.querySelector('a').getAttribute('href'),'/api/import-files/file');assert.equal(!!popup.querySelector('[aria-label="Upload attachment"]'),role==='write');w.closeTransactionActions();assert.ok(popup.hidden);
 }finally{w.close();}
}
console.log('PASS: payment date defaults/preservation and attachment icons/popups across all ledgers and roles');
