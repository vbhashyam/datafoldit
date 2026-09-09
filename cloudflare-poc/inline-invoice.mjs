// Literal client code: do not serialize bundled functions with toString().
export const inlineInvoiceJs=String.raw`
function transactionDraftRow(kind,onSaved,onCancel,source=null){
 const row=el('tr',undefined,'invoice-draft'),controls={};let receivedDateCell=null;row.setAttribute('aria-label','New '+kind+' transaction');
 if(kind!=='invoices')row.append(el('td','Assigned on save'));
 for(const [key,type] of Object.entries(fields[kind])){
  if(kind==='payroll'&&['pct','commission','tax_breakdown','job_start','job_end','effective_end'].includes(key))continue;
  if(kind==='payroll'&&key==='effective_start'){const cell=el('td');for(const [name,title] of [['effective_start','From'],['effective_end','To']]){const l=el('label',title),control=input(name,'date');l.append(control);cell.append(l);controls[name]=control;}row.append(cell);continue;}
  const cell=el('td'),control=input(key,type,key==='date'?new Date().toISOString().slice(0,10):key==='month'?new Date().toISOString().slice(0,7):Array.isArray(type)?type[0]:'');
  control.placeholder=label(key);if(['date','month','invoice_number','amount'].includes(key))control.required=true;
  cell.append(control);if(kind==='invoices'&&key==='received_date')receivedDateCell=cell;else row.append(cell);controls[key]=control;
 }
 if(source){row.cloneSourceId=source.id;row.setAttribute('aria-label','Cloned '+kind+' transaction');for(const key of Object.keys(controls))controls[key].value=source.data[key]??controls[key].value;}
 if(kind==='invoices'){if(!source)controls.commission_pct.value='30';if(typeof records!=='undefined'){const max=Math.max(0,...records.filter(r=>r.kind==='invoices').map(r=>Number(String(r.data.invoice_number).match(/^INV-(\d+)$/i)?.[1])||0));controls.invoice_number.value='INV-'+String(max+1).padStart(6,'0');}if(source){controls.status.value='Open';controls.balance_due.value=controls.amount.value;controls.received_date.value='';}}
 if(source&&kind==='payroll'){controls.credit_date.value='';controls.paystub_sent.value='N';}
 if(kind==='bank'||kind==='invoices')row.append(el('td','Calculated on save'));
 if(receivedDateCell)row.append(receivedDateCell);
 row.append(el('td','Not saved'));
 const fileCell=el('td'),fileInput=el('input');fileInput.type='file';fileInput.accept='.pdf,.docx,.png,.jpg,.jpeg,.xlsx';fileInput.setAttribute('aria-label','Transaction attachment');fileCell.append(fileInput);row.append(fileCell);
 const actions=el('td',undefined,'draft-actions'),save=el('button','Save','primary'),cancel=el('button','Cancel');save.type=cancel.type='button';actions.append(save,cancel);row.append(actions);
 let busy=false,balanceEdited=false;
 fileInput.onchange=async()=>{const file=fileInput.files?.[0];if(!file)return;await showImport(null,file);};
 function updateBalance(){if(kind!=='invoices')return;const closed=controls.status.value!=='Open';controls.balance_due.disabled=closed;if(closed)controls.balance_due.value='0';else if(!balanceEdited)controls.balance_due.value=controls.amount.value;}
 if(kind==='invoices'){controls.balance_due.oninput=()=>{balanceEdited=true;};controls.amount.oninput=updateBalance;controls.status.onchange=()=>{balanceEdited=false;updateBalance();};}
 cancel.onclick=()=>{if(!busy)onCancel();};
 row.cancelDraft=()=>cancel.onclick();
 save.onclick=async()=>{
  if(busy)return;
  const file=fileInput.files?.[0];if(file&&file.size>10485760){message.textContent='Maximum attachment size is 10 MB.';return;}
  if(file){await showImport(null,file);return;}
  for(const control of Object.values(controls))if(!control.reportValidity()){control.focus({preventScroll:true});return;}
  if(kind==='invoices'&&Number(controls.balance_due.value)>Number(controls.amount.value)){message.textContent='Balance due cannot exceed the invoice amount.';controls.balance_due.focus({preventScroll:true});return;}
  const data={...(source?Object.fromEntries(Object.keys(fields[kind]).filter(k=>!controls[k]).map(k=>[k,source.data[k]])):{}),...Object.fromEntries(Object.entries(controls).map(([key,control])=>[key,control.value]))};
  busy=true;fileInput.disabled=save.disabled=cancel.disabled=true;for(const control of Object.values(controls))control.disabled=true;
  try{await api({action:'create',kind,data});}
  catch(error){message.textContent=error.message;busy=false;fileInput.disabled=save.disabled=cancel.disabled=false;for(const control of Object.values(controls))control.disabled=false;updateBalance();return;}
  // A successful write must never be retried if the following read fails.
  await onSaved();
 };
 row.onkeydown=e=>{if(e.target.tagName==='BUTTON')return;if(e.key==='Escape'){e.preventDefault();cancel.click();}if(e.key==='Enter'&&e.target.tagName!=='SELECT'){e.preventDefault();save.click();}};
 return row;
}
`;
