// Approved production-only credential transfer; never prints secrets or setup tokens.
import {readFile,stat,mkdtemp,writeFile,unlink,rmdir} from 'node:fs/promises';
import {execFileSync} from 'node:child_process';
import {tmpdir} from 'node:os';
import {join} from 'node:path';
import {randomToken,digest} from './auth.mjs';
const cfg=JSON.parse(await readFile('./wrangler.production.jsonc','utf8'));
if(cfg.name!=='datafoldit-production'||cfg.d1_databases[0].database_id!=='b6a2d9d1-0c4c-4b2f-992e-98e94c0a1196'||cfg.account_id!=='ffd1f71df12ae3e828f9aeede4e087d1')throw new Error('Production target mismatch');
const path='../tmp/ui-preview/smtp-invitations.json';
if((await stat(path)).mode&0o077)throw new Error('Private SMTP configuration permissions required');
const smtp=JSON.parse(await readFile(path,'utf8'));
if(smtp.sender!=='vamsi@datafoldit.com'||!['smtp.zoho.com','smtppro.zoho.com','smtp.zoho.eu','smtppro.zoho.eu','smtp.zoho.in','smtppro.zoho.in'].includes(smtp.host)||!smtp.password)throw new Error('Unexpected SMTP settings');
function cli(args,input){try{return execFileSync(process.execPath,['node_modules/wrangler/bin/wrangler.js',...args,'--config','wrangler.production.jsonc'],{input,stdio:['pipe','pipe','pipe'],timeout:60000});}catch{throw new Error('Production provisioning command failed; credentials were not printed');}}
const check=JSON.parse(cli(['d1','execute','datafoldit-production','--remote','--command',"SELECT (SELECT count(*) FROM poc_users) AS users,(SELECT count(*) FROM poc_invitations) AS invitations",'--json']).toString());
if(check[0].results[0].users||check[0].results[0].invitations)throw new Error('Onboarding already exists; refusing to replace it');
cli(['secret','bulk'],JSON.stringify({SMTP_CONFIG:JSON.stringify({host:smtp.host,sender:smtp.sender,password:smtp.password})}));
const token=randomToken(),hash=await digest(token),secret=randomToken().slice(0,40),expires=Math.floor(Date.now()/1000)+86400;
const directory=await mkdtemp(join(tmpdir(),'dfit-production-admin-')),sql=join(directory,'bootstrap.sql');
try{
 await writeFile(sql,`INSERT INTO poc_invitations(email,token_hash,role,expires,mfa_hex) SELECT 'vamsi@datafoldit.com','${hash}','admin',${expires},'${secret}' WHERE NOT EXISTS(SELECT 1 FROM poc_users) AND NOT EXISTS(SELECT 1 FROM poc_invitations);`,{mode:0o600});
 cli(['d1','execute','datafoldit-production','--remote','--file',sql]);
}finally{await unlink(sql).catch(()=>{});await rmdir(directory);}
console.log('Zoho configuration stored as encrypted production Worker secret; only vamsi@datafoldit.com approved. No email sent by this script.');
