import {readFile} from 'node:fs/promises';
import assert from 'node:assert/strict';
import {getDocument} from 'pdfjs-dist/legacy/build/pdf.mjs';
import {textCandidates} from './import-parser.mjs';
import {fields} from './dashboard.mjs';
// Read-only local verification. Never uploads the user's source document.
const task=getDocument({data:new Uint8Array(await readFile('/Users/vamsikrishnabhashyam/Downloads/invoices.pdf')),isEvalSupported:false}),doc=await task.promise;
try{const rows=[];for(let n=1;n<=doc.numPages;n++){const p=await doc.getPage(n),c=await p.getTextContent(),lines=new Map();for(const item of c.items){if(!('str' in item))continue;const y=Math.round(item.transform[5]/3)*3,list=lines.get(y)||[];list.push({x:item.transform[4],text:item.str});lines.set(y,list);}const text=[...lines].sort((a,b)=>b[0]-a[0]).map(([,items])=>items.sort((a,b)=>a.x-b.x).map(i=>i.text).join('  ')).join('\n');rows.push(...textCandidates(text,'invoices',fields,'Page '+n));}assert.equal(rows.length,6);assert.equal(new Set(rows.map(r=>r.data.invoice_number)).size,6);assert.ok(rows.every(r=>r.data.amount>0&&r.data.date&&r.data.customer),JSON.stringify(rows.map(r=>({keys:Object.keys(r.data),date:r.data.date,amount:r.data.amount,hasCustomer:!!r.data.customer}))));console.log('PASS: original local bulk PDF contains 6 pages and yields 6 distinct invoices with amounts, dates and customers. Source was not uploaded.');}finally{await task.destroy();}
