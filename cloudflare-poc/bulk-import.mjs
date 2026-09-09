import {validate,fields} from './dashboard.mjs';
const MAX=10*1024*1024;
export function checkFile(name,bytes){
 const ext=String(name).toLowerCase().split('.').at(-1);
 if(!['pdf','docx','png','jpg','jpeg','xlsx','csv'].includes(ext))throw Error('Use PDF, DOCX, PNG, JPG, XLSX or CSV. Convert older DOC/XLS files first.');
 if(typeof name!=='string'||!name.trim()||name.length>200||/[\x00-\x1f]/.test(name)||!bytes.length||bytes.length>MAX)throw Error('Maximum file size is 10 MB.');
 const starts=(a)=>a.every((v,i)=>bytes[i]===v);
 if(ext==='pdf'&&!starts([37,80,68,70,45])||ext==='png'&&!starts([137,80,78,71,13,10,26,10])||['jpg','jpeg'].includes(ext)&&!starts([255,216,255])||['docx','xlsx'].includes(ext)&&!starts([80,75]))throw Error('File contents do not match the filename.');
}
export async function commitImport(db,user,kind,name,bytes,rows,targetId){
 if(!['admin','write'].includes(user.role))throw Error('Write access required');
 if(!fields[kind])throw Error('Invalid transaction type');checkFile(name,bytes);
 if(!Array.isArray(rows)||rows.length>100||(!targetId&&!rows.length)||targetId&&rows.length)throw Error('Review between 1 and 100 transactions.');
 if(targetId){const target=await db.prepare('SELECT id FROM dashboard_records WHERE id=? AND kind=?').bind(String(targetId),kind).first();if(!target)throw Error('Transaction not found');}
 const clean=rows.map((r,i)=>{
  const d=r.data;if(!d||!(d.date||d.month)||kind==='invoices'&&!d.invoice_number||kind!=='payroll'&&(d.amount===''||d.amount==null)||kind==='payroll'&&(d.gross===''||d.gross==null))throw Error('Complete required fields in row '+(i+1));
  if(typeof r.source!=='string'||r.source.length>500)throw Error('Invalid source reference');
  return validate(kind,d);
 });
 if(kind==='invoices'){
  const keys=clean.map(d=>d.invoice_number.trim().toLowerCase());
  if(new Set(keys).size!==keys.length)throw Error('Duplicate invoice numbers in this import. Review repeated or continuation pages.');
  for(const d of clean){const exists=await db.prepare("SELECT id FROM dashboard_records WHERE kind='invoices' AND lower(trim(json_extract(data,'$.invoice_number')))=?").bind(d.invoice_number.toLowerCase()).first();if(exists)throw Error('Duplicate invoice number: '+d.invoice_number+'. Nothing imported.');}
 }
 const sha=Array.from(new Uint8Array(await crypto.subtle.digest('SHA-256',bytes)),b=>b.toString(16).padStart(2,'0')).join('');
 const existing=await db.prepare('SELECT id FROM import_files WHERE kind=? AND sha=?').bind(kind,sha).first();
 if(existing){if(targetId){await db.batch([db.prepare('INSERT OR IGNORE INTO record_attachments(record_id,file_id) VALUES(?,?)').bind(targetId,existing.id),db.prepare('UPDATE dashboard_records SET updated_by=?,updated_at=CURRENT_TIMESTAMP,version=version+1 WHERE id=?').bind(user.email,targetId)]);return {count:0,fileId:existing.id};}
  const {results}=await db.prepare('SELECT data FROM dashboard_records JOIN record_attachments ON record_id=dashboard_records.id WHERE file_id=?').bind(existing.id).all();
  const known=new Set(results.map(r=>JSON.stringify(validate(kind,JSON.parse(r.data)))));if(clean.some(d=>known.has(JSON.stringify(d))))throw Error('Some selected transactions from this file already exist. Uncheck those entries before importing the remaining ones.');
 }
 if((await db.prepare('SELECT COUNT(*) n FROM dashboard_records').first()).n+clean.length>5000)throw Error('Test record limit reached');
 const fileId=existing?.id||crypto.randomUUID(),batch=[],ids=[];
 if(!existing){batch.push(db.prepare('INSERT INTO import_files(id,kind,name,size,sha,uploaded_by) VALUES(?,?,?,?,?,?)').bind(fileId,kind,name,bytes.length,sha,user.email));
 for(let offset=0,n=0;offset<bytes.length;offset+=262144,n++)batch.push(db.prepare('INSERT INTO import_chunks VALUES(?,?,?)').bind(fileId,n,bytes.slice(offset,offset+262144)));}
 if(targetId){batch.push(db.prepare('INSERT INTO record_attachments VALUES(?,?)').bind(targetId,fileId));batch.push(db.prepare('UPDATE dashboard_records SET updated_by=?,updated_at=CURRENT_TIMESTAMP,version=version+1 WHERE id=?').bind(user.email,targetId));}
 clean.forEach((data,i)=>{const id=crypto.randomUUID();ids.push(id);batch.push(db.prepare('INSERT INTO dashboard_records(id,kind,data,updated_by) VALUES(?,?,?,?)').bind(id,kind,JSON.stringify(data),user.email));batch.push(db.prepare('INSERT INTO record_attachments VALUES(?,?)').bind(id,fileId));});
 try{await db.batch(batch);}catch(e){if(String(e.message).includes('unique_invoice_number'))throw Error('Duplicate invoice number. No transactions from this import were saved.');throw Error('Import could not be saved (duplicate file or test storage limit). No partial transactions were saved.');}
 return {count:clean.length,fileId,ids};
}
export async function downloadImport(db,id){
 const file=await db.prepare('SELECT id,name,size FROM import_files WHERE id=? AND EXISTS(SELECT 1 FROM record_attachments WHERE file_id=import_files.id)').bind(id).first();if(!file)return null;
 const {results}=await db.prepare('SELECT position,data FROM import_chunks WHERE file_id=? ORDER BY position').bind(id).all(),bytes=new Uint8Array(file.size);let offset=0;
 results.forEach((part,i)=>{if(part.position!==i)throw Error('Incomplete attachment');const b=new Uint8Array(part.data);bytes.set(b,offset);offset+=b.length;});if(offset!==file.size)throw Error('Incomplete attachment');return {...file,bytes};
}
export async function importRequest(request,db,user){
 if(request.method!=='POST'||!['admin','write'].includes(user.role)||request.headers.get('X-CSRF-Token')!==user.csrf)return Response.json({error:'Write access and valid request required'},{status:403});
 try{
 const reader=request.body?.getReader();if(!reader)throw Error('File required');let size=0;const chunks=[];while(true){const {done,value}=await reader.read();if(done)break;size+=value.length;if(size>12*1024*1024){await reader.cancel();throw Error('Maximum file size is 10 MB');}chunks.push(value);}
 const data=await new Response(new Blob(chunks),{headers:{'Content-Type':request.headers.get('Content-Type')||''}}).formData(),file=data.get('file');if(!file||typeof file.arrayBuffer!=='function')throw Error('File required');
 return Response.json(await commitImport(db,user,data.get('kind'),file.name,new Uint8Array(await file.arrayBuffer()),JSON.parse(data.get('rows')||'[]'),data.get('targetId')||null),{headers:{'Cache-Control':'no-store'}});
 }catch(e){return Response.json({error:e.message},{status:400,headers:{'Cache-Control':'no-store'}});}
}
