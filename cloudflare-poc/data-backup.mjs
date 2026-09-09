const baseTables=['transaction_sequences','dashboard_records','import_files','import_chunks','record_attachments'];
export function sqlLiteral(value){if(value===null)return 'NULL';if(typeof value==='number')return String(value);if(typeof value==='string')return "'"+value.replaceAll("'","''")+"'";const bytes=new Uint8Array(value);return "X'"+Array.from(bytes,b=>b.toString(16).padStart(2,'0')).join('')+"'";}
export async function operationalBackup(db,production=false){
 const {results:optional}=await db.prepare("SELECT name FROM sqlite_master WHERE type='table' AND name IN ('legacy_archive','transaction_id_reassignments') ORDER BY name").all();
 const tables=[...baseTables,...optional.map(r=>r.name)];
 const sizes=await db.prepare('SELECT COALESCE(MAX(length(data)),32768) AS largest FROM import_chunks').first();
 const chunkLimit=Math.max(1,Math.min(64,Math.floor(2097152/Math.max(1,sizes.largest))));
 const counts=await db.prepare(tables.map(name=>"SELECT '"+name+"' AS name,COUNT(*) AS n FROM "+name).join(' UNION ALL ')).all();
 if(4+tables.length+counts.results.reduce((n,r)=>n+(Math.floor(r.n/(r.name==='import_chunks'?chunkLimit:250))+1),0)>48)return Response.json({error:'Backup exceeds the single-download limit. Request a full database export from the administrator; do not rely on Excel alone as a complete backup.'},{status:413,headers:{'Cache-Control':'no-store'}});
 const encoder=new TextEncoder();
 async function* dump(){yield '-- DataFoldIT '+(production?'production':'test')+' operational backup. Contains transactions and linked files; excludes accounts, passwords, MFA and email settings. Restore into an empty SQLite/D1 database.\nPRAGMA foreign_keys=OFF;\nBEGIN TRANSACTION;\n';
 for(const name of tables){const schema=await db.prepare("SELECT sql FROM sqlite_master WHERE type='table' AND name=?").bind(name).first();if(!schema)throw Error('Missing backup table');yield schema.sql+';\n';let cursor=0;const limit=name==='import_chunks'?chunkLimit:250;while(true){const {results}=await db.prepare('SELECT rowid AS backup_rowid,* FROM '+name+' WHERE rowid>? ORDER BY rowid LIMIT '+limit).bind(cursor).all();for(const row of results){cursor=row.backup_rowid;delete row.backup_rowid;yield 'INSERT INTO '+name+'('+Object.keys(row).map(k=>'"'+k+'"').join(',')+') VALUES('+Object.values(row).map(sqlLiteral).join(',')+');\n';}if(results.length<limit)break;}}
 const {results:rules}=await db.prepare("SELECT sql FROM sqlite_master WHERE name IN ('unique_invoice_number','unique_transaction_tracking','assign_transaction_tracking','protect_transaction_tracking') AND sql IS NOT NULL ORDER BY type,name").all();
 for(const rule of rules)yield rule.sql+';\n';
 yield 'COMMIT;\nPRAGMA foreign_keys=ON;\n';}
 const iterator=dump();return new Response(new ReadableStream({async pull(controller){try{const next=await iterator.next();if(next.done)controller.close();else controller.enqueue(encoder.encode(next.value));}catch(e){controller.error(e);}},async cancel(){await iterator.return();}}),{headers:{'Content-Type':'application/sql','Content-Disposition':'attachment; filename="datafoldit-'+(production?'production':'test')+'-operational-backup.sql"','Cache-Control':'no-store','X-Content-Type-Options':'nosniff'}});
}
