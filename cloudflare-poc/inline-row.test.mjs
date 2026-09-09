import assert from 'node:assert/strict';
import {runInNewContext} from 'node:vm';
import {inlineInvoiceJs} from './inline-invoice.mjs';
import {fields,dashboardJs} from './dashboard.mjs';
const clientCode=process.env.HOSTED_CHECK==='1'?(await (await fetch('https://datafoldit-test-poc.vamsibh07.workers.dev/dashboard.js')).text()).split('let records=[],tab=')[0]:inlineInvoiceJs;
function element(tag,text){return {tagName:tag.toUpperCase(),textContent:text,children:[],value:'',disabled:false,append(...nodes){this.children.push(...nodes);},setAttribute(){},focus(){},reportValidity(){return !this.required||!!this.value;},click(){return this.onclick?.();}};}
const descendants=n=>n.children.flatMap(c=>[c,...descendants(c)]);
for(const kind of Object.keys(fields)){
 let writes=0,saved=0,cancelled=0,body,fail=false;const message={};
 const context={fields,message,el:element,label:k=>k,input:(key,type,value)=>Object.assign(element(Array.isArray(type)?'select':'input'),{name:key,value}),api:async data=>{writes++;body=data;if(fail)throw Error('Retry test');}};
 const make=runInNewContext(clientCode+'\ntransactionDraftRow',context);
 const row=make(kind,async()=>saved++,()=>cancelled++),controls=Object.fromEntries(descendants(row).filter(c=>c.name).map(c=>[c.name,c])),[save,cancel]=row.children.at(-1).children;
 assert.equal(row.tagName,'TR');assert.equal(row.children.length,Object.keys(fields[kind]).length-(kind==='payroll'?6:0)+3+(kind==='invoices'?0:1)+(['bank','invoices'].includes(kind)?1:0));
 if(controls.amount)controls.amount.value='100';if(controls.invoice_number)controls.invoice_number.value='TEST-1';
 if(kind==='invoices'){controls.amount.oninput();assert.equal(controls.balance_due.value,'100');controls.status.value='Paid';controls.status.onchange();assert.equal(controls.balance_due.value,'0');}
 fail=true;await save.onclick();assert.equal(saved,0);assert.equal(save.disabled,false);assert.equal(message.textContent,'Retry test');
 fail=false;await save.onclick();assert.equal(saved,1);assert.equal(body.kind,kind);await save.onclick();assert.equal(writes,2,'busy guard prevents double submission');
 const second=make(kind,async()=>{},()=>cancelled++);second.children.at(-1).children[1].click();assert.equal(cancelled,1);
 let opened=0;
 context.showImport=async(target,file)=>{assert.equal(target,null);assert.equal(file.name,'sample.pdf');opened++;};
 const attached=make(kind,async()=>saved++,()=>cancelled++);const picker=attached.children.at(-2).children[0];picker.files=[new File(['%PDF-1.7 sample'],'sample.pdf')];await picker.onchange();assert.equal(opened,1,'file selection opens extraction without required manual values');await attached.children.at(-1).children[0].onclick();assert.equal(opened,2,'save cannot bypass extraction and import only one row');

}
new Function(dashboardJs);assert.ok(dashboardJs.includes('t.append(draft)'));assert.ok(!dashboardJs.includes("if(tab!=='invoices'){createForm();return;}"));
console.log('PASS: inline rows for all four transaction tabs; required inputs, invoice balance, save/cancel, retry and double-submit protection');
