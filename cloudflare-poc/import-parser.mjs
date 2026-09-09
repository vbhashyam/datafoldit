export function normalizeDate(value,month=false){
 if(value instanceof Date&&!isNaN(value))return value.toISOString().slice(0,month?7:10);
 const s=String(value??'').trim();if(!s)return '';
 if(/^\d{4}-\d{2}(-\d{2})?$/.test(s))return s.slice(0,month?7:10);
 const us=s.match(/^(\d{1,2})\/(\d{1,2})\/(\d{4})$/);if(us)return `${us[3]}-${us[1].padStart(2,'0')}-${us[2].padStart(2,'0')}`.slice(0,month?7:10);
 if(/[a-z]{3}/i.test(s)){const d=new Date(s);if(!isNaN(d))return d.toISOString().slice(0,month?7:10);}return s;
}
export function numberValue(v){if(typeof v==='number')return v;const s=String(v??'').trim().replace(/[$,\s]/g,'');return s&&/^-?\d+(\.\d+)?$/.test(s)?Number(s):'';}
const aliases={pay_period_start:'effective_start',pay_period_end:'effective_end',period_start:'effective_start',period_end:'effective_end',effective_from:'effective_start',effective_to:'effective_end',invoice_no:'invoice_number',invoice:'invoice_number',invoice_date:'date',transaction_date:'date',customer_name:'customer',bill_to:'customer',total:'amount',total_amount:'amount',balance:'balance_due',employee_first_name:'first_name',employee_last_name:'last_name',net_pay:'employee_pay',gross_pay:'gross',payroll_month:'month',received_on:'received_date'};
export function tableCandidates(matrix,kind,fields,source){
 const norm=v=>String(v??'').trim().toLowerCase().replace(/[#.]/g,'').replace(/[^a-z0-9]+/g,'_').replace(/^_|_$/g,'');
 let header=-1,keys=[];for(let i=0;i<Math.min(matrix.length,30);i++){const k=matrix[i].map(v=>aliases[norm(v)]||norm(v));if(k.filter(v=>Object.hasOwn(fields[kind],v)).length>=2){header=i;keys=k;break;}}
 if(header<0)return null;
 return matrix.slice(header+1).flatMap((row,i)=>{if(!row.some(v=>v!==''&&v!=null))return [];const data={};keys.forEach((k,j)=>{if(kind==='payroll'&&k==='effective_date_range'){const range=String(row[j]||'').match(/^(\d{4}-\d{2}-\d{2})\s+to\s+(\d{4}-\d{2}-\d{2})$/);if(range){data.effective_start=range[1];data.effective_end=range[2];}}if(Object.hasOwn(fields[kind],k)){const type=fields[kind][k],v=row[j]??'';data[k]=type==='number'?numberValue(v):type==='date'||type==='month'?normalizeDate(v,type==='month'):String(v).trim();}});return [{data,source:source+' row '+(header+i+2),warning:'Review column mapping and values.'}];});
}
export function textCandidates(text,kind,fields,source){
 const lines=text.split(/\r?\n/),matrix=lines.map(l=>l.trim().split(/\t+|\s{2,}/));const table=tableCandidates(matrix,kind,fields,source);if(table?.length)return table;
 if(['bank','expenses'].includes(kind)){const dated=lines.map(line=>line.trim().match(/^(\d{4}-\d{2}-\d{2}|\d{1,2}\/\d{1,2}\/\d{4})\s+(.+?)\s+\$?([\d,]+\.\d{2})\s*$/)).filter(Boolean);if(dated.length)return dated.map((m,i)=>({data:{date:normalizeDate(m[1]),[kind==='bank'?'detail':'description']:m[2],amount:numberValue(m[3]),...(kind==='bank'?{type:''}:{})},source:source+' transaction '+(i+1),warning:'Check description, date and amount. For bank entries, choose the inflow/outflow type.'}));}
 // Split on explicit document identifiers, not invoice line items or totals.
 const starts=[...text.matchAll(/(?:^|\n)[ \t]*(?:Invoice\s*(?:Number|No\.?|#)\s*[:#]?\s*|#\s*)((?=[-A-Z0-9]*\d)[-A-Z0-9]{3,})[^\n]*/gi)];
 const boundaries=kind==='invoices'?starts:[...text.matchAll(/(?:^|\n)[ \t]*(?:Transaction Date|Date|Month)\s*:\s*[^\n]+/gi)];
 const parts=boundaries.length>1?boundaries.map((m,i)=>text.slice(m.index,boundaries[i+1]?.index??text.length)):[text];
 return parts.map((part,index)=>{
  const data={};for(const [key,type] of Object.entries(fields[kind])){
   const names=[key.replaceAll('_',' '),...Object.entries(aliases).filter(([,v])=>v===key).map(([k])=>k.replaceAll('_',' '))];
   for(const name of names){const re=new RegExp('(?:^|\\n)\\s*'+name+'\\s*[:#]?[ \\t]+([^\\n]+)','i'),m=part.match(re);if(m){data[key]=type==='number'?numberValue(m[1]):type==='date'||type==='month'?normalizeDate(m[1],type==='month'):m[1].trim();break;}}
  }
  if(kind==='invoices'){
   const id=part.match(/\bINV[-\w]*\d[-\w]*\b/i)||part.match(/invoice\s*(?:number|no\.?|#)\s*[:#]?\s*([\w-]+)/i);if(id)data.invoice_number=id[1]||id[0];
   const date=part.match(/Invoice Date\s*:?\s*([^\n]+)/i),due=part.match(/Due Date\s*:?\s*([^\n]+)/i),total=part.match(/(?:^|\n)\s*Total\s+\$?([\d,]+\.\d{2})/i),balance=part.match(/Balance Due\s*\$?([\d,]+\.\d{2})/i),customer=part.match(/Bill To\s*\n([^\n]+)/i);
   if(date)data.date=normalizeDate(date[1]);if(due)data.due_date=normalizeDate(due[1]);if(total)data.amount=numberValue(total[1]);if(balance)data.balance_due=numberValue(balance[1]);if(customer)data.customer=customer[1].trim().split(/\s{2,}|Invoice Date/i)[0].trim();
   if(data.balance_due!==undefined&&data.balance_due!=='')data.status=data.balance_due===0?'Paid':'Open';
  }
  return {data,source:source+(parts.length>1?' document '+(index+1):''),warning:'Automatic extraction may be incomplete. Check against the original.'};
 });
}
export function prepareCandidates(candidates,kind,fields){return candidates.map(row=>{const data={};for(const [k,t] of Object.entries(fields[kind]))data[k]=row.data[k]??(Array.isArray(t)?t[0]:'');if(kind==='invoices'&&data.balance_due==='')data.balance_due=data.status==='Open'?data.amount:0;return {...row,data};});}
