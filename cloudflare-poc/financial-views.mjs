// Shared pure calculations are serialized into the browser and tested in Node.
export function amountTone(kind,data,key){
 if(!Number(data[key]))return '';
 if(kind==='bank'&&key==='amount'){if(['Expense','Withdrawal','Transfer Out','Adjustment Out'].includes(data.type))return 'money-out';if(['Deposit','Transfer In','Adjustment In'].includes(data.type))return 'money-in';}
 if(kind==='expenses'&&key==='amount')return 'money-out';
 if(kind==='payroll'&&['gross','tax','employee_pay'].includes(key))return 'money-out';
 if(kind==='invoices'&&data.status!=='VOID'&&['amount','balance_due','commission_amount'].includes(key))return 'money-in';
 return '';
}
export function financialSummary(records,today=new Date().toISOString().slice(0,10)){
 const list=k=>records.filter(r=>r.kind===k).map(r=>r.data),sum=(a,k)=>a.reduce((n,d)=>n+(Number(d[k])||0),0);
 const bank=list('bank'),expenses=list('expenses'),payroll=list('payroll'),allInvoices=list('invoices'),invoices=allInvoices.filter(d=>d.status!=='VOID');
 const negative=d=>['Expense','Withdrawal','Transfer Out','Adjustment Out'].includes(d.type);
 const incoming=sum(bank.filter(d=>!negative(d)&&d.type!=='Opening'),'amount'),outgoing=sum(bank.filter(negative),'amount');
 const categories={};for(const d of expenses)categories[d.category||'Uncategorized']=(categories[d.category||'Uncategorized']||0)+(Number(d.amount)||0);
 const latest=expenses.map(d=>d.date?.slice(0,7)||'').sort().at(-1)||'';
 return {
 bank:{count:bank.length,balance:sum(bank.filter(d=>d.type==='Opening'),'amount')+incoming-outgoing,incoming,outgoing,net:incoming-outgoing},
 expenses:{count:expenses.length,total:sum(expenses,'amount'),latest,latestTotal:sum(expenses.filter(d=>d.date?.startsWith(latest)),'amount'),categories:Object.entries(categories).sort((a,b)=>b[1]-a[1]).slice(0,3)},
 payroll:{count:payroll.length,gross:sum(payroll,'gross'),tax:sum(payroll,'tax'),commission:sum(payroll,'commission'),net:sum(payroll,'employee_pay'),pending:sum(payroll.filter(d=>!d.credit_date),'employee_pay')},
 invoices:{count:invoices.length,voidCount:allInvoices.length-invoices.length,total:sum(invoices,'amount'),paid:sum(invoices.filter(d=>d.status==='Paid'),'amount'),outstanding:sum(invoices.filter(d=>d.status==='Open'),'balance_due'),overdue:sum(invoices.filter(d=>d.status==='Open'&&d.due_date&&d.due_date<today),'balance_due')}
 };
}
export function filterReport(records,kind,from='',to='',query=''){
 return records.filter(r=>{if(r.kind!==kind)return false;const d=r.kind==='payroll'?r.data.month:r.data.date;if(!d)return !from&&!to;const start=r.kind==='payroll'?from.slice(0,7):from,end=r.kind==='payroll'?to.slice(0,7):to;return (!start||d>=start)&&(!end||d<=end)&&JSON.stringify({...r.data,tracking_id:r.tracking_id}).toLowerCase().includes(query.toLowerCase());});
}
export function reportGroups(rows,kind){
 const groups=new Map();for(const r of rows){const d=r.data,key=kind==='expenses'?(d.category||'Uncategorized'):kind==='invoices'?(d.status||'Open'):kind==='payroll'?d.month:(d.type||'Other');const g=groups.get(key)||{name:key,count:0,total:0};g.count++;g.total+=Number(d[kind==='payroll'?'gross':'amount'])||0;groups.set(key,g);}return [...groups.values()].sort((a,b)=>a.name.localeCompare(b.name));
}
export function makeCsv(rows,keys){
 const safe=v=>typeof v==='string'&&/^[\s]*[=+@-]/.test(v)?"'"+v:v;
 const quote=v=>'"'+String(safe(v)??'').replaceAll('"','""')+'"';
 const ordered=keys.includes('tracking_id')?['tracking_id','__type',...keys.filter(k=>k!=='tracking_id')]:['__type',...keys];
 return [[...ordered.map(k=>k==='tracking_id'?'trnsc_id':k==='__type'?'Type':k),'Updated by'],...rows.map(r=>[...ordered.map(k=>k==='__type'?r.kind:k==='tracking_id'?r.tracking_id:r.data[k]),r.updated_by])].map(row=>row.map(quote).join(',')).join('\r\n');
}
// Wrangler preserves nested function names with this helper. Serialized functions
// must carry it into the browser, rather than relying on the Worker's scope.
export const financialViewsJs='const __name=(target,value)=>Object.defineProperty(target,"name",{value,configurable:true});\n'+[amountTone,financialSummary,filterReport,reportGroups,makeCsv].map(f=>f.toString()).join('\n')+String.raw`
function detailedDashboard(){
 const s=financialSummary(records),grid=el('div',undefined,'grid');
 function card(kind,title,value,caption,lines,note){const c=el('article'),head=el('div',undefined,'card-heading'),link=el('a','View all →');link.href='#'+kind;head.append(el('h2',title),link);c.append(head,el('strong',money(value),'amount '+(value?(kind==='expenses'||kind==='payroll'?'money-out':kind==='invoices'?'money-in':value<0?'money-out':''):'')),el('p',caption));const dl=el('dl',undefined,'summary-lines');for(const [name,v] of lines){dl.append(el('dt',name),el('dd',money(v),v?(kind==='expenses'||kind==='payroll'?'money-out':kind==='invoices'?'money-in':name==='Total money out'?'money-out':name==='Net cash movement'&&v<0?'money-out':'money-in'):''));}c.append(dl,el('p',note,'card-note'));grid.append(c);}
 card('bank','Bank summary',s.bank.balance,s.bank.count+' transactions · current balance', [['Total money in',s.bank.incoming],['Total money out',s.bank.outgoing],['Net cash movement',s.bank.net]],'Opening balances are included in the balance, not money in/out. Transfers and adjustments are included.');
 card('expenses','Expenses summary',s.expenses.total,s.expenses.count+' expense records · all time',[[s.expenses.latest?'Latest recorded month ('+s.expenses.latest+')':'Latest recorded month',s.expenses.latestTotal],...s.expenses.categories.map(([k,v])=>['Category: '+k,v])],'Top categories shown. Expenses and bank outflows are separate records; do not add both totals together.');
 card('payroll','Payroll summary',s.payroll.gross,s.payroll.count+' payroll entries · gross payroll',[['Tax',s.payroll.tax],['Commission',s.payroll.commission],['Net employee pay',s.payroll.net],['Net pay without credit date',s.payroll.pending]],'A credit date is a recorded date, not independent confirmation of payment.');
 card('invoices','Invoices summary',s.invoices.total,s.invoices.count+' active invoices · '+s.invoices.voidCount+' void',[['Paid invoices',s.invoices.paid],['Outstanding balance',s.invoices.outstanding],['Overdue balance',s.invoices.overdue]],'Void invoices are excluded. Overdue is a subset of outstanding. Paid invoices exclude partial payments on open invoices.');
 content.append(el('p','Your financial overview · all recorded test data','view-description'),grid);
}
function downloadRows(rows,keys,name){const url=URL.createObjectURL(new Blob([makeCsv(rows,keys)],{type:'text/csv;charset=utf-8'})),a=el('a');a.href=url;a.download=name+'.csv';a.click();setTimeout(()=>URL.revokeObjectURL(url),1000);}
function reportsView(){
 if(document.querySelector('#role').value==='admin'){const backup=el('section',undefined,'import-panel'),link=el('a','Download transaction and attachment backup (.sql)');link.href='/api/operational-backup';backup.append(el('h2','Data backup'),link,el('p','Administrator only. SQLite-compatible operational data export; excludes login accounts, MFA and email credentials. This is not a full Cloudflare account backup.'));content.append(backup);}
 const exports=el('section',undefined,'import-panel excel-reports');exports.append(el('h2','Excel reports'));
 const kind=input('report_tab',['all','bank','expenses','payroll','invoices'],'all'),period=input('period',['all','monthly','daily','custom'],'all'),month=input('month','month',new Date().toISOString().slice(0,7)),day=input('day','date',new Date().toISOString().slice(0,10)),from=input('from','date'),to=input('to','date');
 for(const option of kind.options)option.textContent=({all:'All tabs',bank:'Bank',expenses:'Expenses',payroll:'Payroll',invoices:'Invoices'})[option.value];
 for(const option of period.options)option.textContent=({all:'All time',monthly:'Monthly',daily:'Daily',custom:'Custom range'})[option.value];
 const controls=el('div',undefined,'report-controls');
 for(const [name,control] of [['Tab',kind],['Period',period],['Month',month],['Day',day],['From',from],['To',to]]){const l=el('label',name);l.append(control);controls.append(l);}
 const help=el('p'),payrollNote=el('p'),excel=el('button','Download Excel','primary');
 function updatePeriod(){month.disabled=period.value!=='monthly';day.disabled=period.value!=='daily';from.disabled=to.disabled=period.value!=='custom';help.textContent=period.value==='custom'?'Custom range: choose From and To dates. Both dates are included. Month and Day are not used.':period.value==='all'?'All time: includes every recorded date. Month and Day are not used.':period.value==='monthly'?'Monthly: choose a Month to export. Day is not used.':'Daily: choose a Day to export. Month is not used.';payrollNote.textContent=period.value==='custom'&&(kind.value==='all'||kind.value==='payroll')?'Payroll includes full records whose effective salary range overlaps your selection. If no range is recorded, the payroll month is used. Amounts are not prorated; credit date is not used.':period.value==='daily'&&(kind.value==='all'||kind.value==='payroll')?'Payroll is recorded by month, so daily exports include payroll for the selected day’s entire month.':'';}
 period.onchange=kind.onchange=updatePeriod;updatePeriod();
 excel.onclick=async()=>{if(period.value==='custom'&&(!from.value||!to.value||from.value>to.value)){message.textContent='Choose From and To dates. From must be on or before To.';return;}if(period.value==='monthly'&&!month.value||period.value==='daily'&&!day.value){message.textContent='Choose a valid reporting period.';return;}excel.disabled=true;message.textContent='';try{const {exportWorkbook}=await loadExportModule();await exportWorkbook(records,fields,{tab:kind.value,period:period.value,month:month.disabled?'':month.value,day:day.disabled?'':day.value,from:from.disabled?'':from.value,to:to.disabled?'':to.value});}catch(e){message.textContent='Export failed: '+e.message;}finally{excel.disabled=false;}};
 exports.append(controls,help,payrollNote,excel,el('p','All tabs exports Bank, Expenses, Payroll and Invoices as separate sheets. Choose one tab to export only that tab.'));content.append(exports);
}
`;
