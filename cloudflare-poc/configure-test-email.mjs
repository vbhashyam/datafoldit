// Explicitly restricted to the existing isolated test Worker and admin address.
import {readFile,stat,mkdtemp,writeFile,unlink,rmdir} from 'node:fs/promises';
import {execFileSync} from 'node:child_process';
import {tmpdir} from 'node:os';
import {join} from 'node:path';
import {randomToken,digest} from './auth.mjs';
const resource=JSON.parse(await readFile('./cloud-resources.json','utf8'));
if(resource.database_id!=='f51ca820-3450-4b0c-b93c-a532a5dafb79'||resource.database_name!=='datafoldit-test-poc')throw new Error('Test target mismatch');
const path='../tmp/ui-preview/smtp-invitations.json';
if((await stat(path)).mode&0o077)throw new Error('Private config permissions required');
const config=JSON.parse(await readFile(path,'utf8'));
if(config.sender!=='vamsi@datafoldit.com'||!['smtp.zoho.com','smtppro.zoho.com','smtp.zoho.eu','smtppro.zoho.eu','smtp.zoho.in','smtppro.zoho.in'].includes(config.host)||!config.password)throw new Error('Unexpected SMTP settings');
function cli(args,input){try{return execFileSync(process.execPath,['node_modules/wrangler/bin/wrangler.js',...args],{input,stdio:['pipe','pipe','pipe'],timeout:60000});}catch{throw new Error('Cloudflare configuration command failed; credentials were not printed');}}
cli(['secret','bulk','--name','datafoldit-test-poc'],JSON.stringify({SMTP_CONFIG:JSON.stringify({host:config.host,sender:config.sender,password:config.password})}));
const token=randomToken(),hash=await digest(token),secret=randomToken().slice(0,40),expires=Math.floor(Date.now()/1000)+86400;
const directory=await mkdtemp(join(tmpdir(),'dfit-admin-init-')),sql=join(directory,'bootstrap.sql');
try{
  await writeFile(sql,`INSERT INTO poc_invitations(email,token_hash,role,expires,mfa_hex) SELECT 'vamsi@datafoldit.com','${hash}','admin',${expires},'${secret}' WHERE NOT EXISTS(SELECT 1 FROM poc_users WHERE role='admin') ON CONFLICT(email) DO NOTHING;`,{mode:0o600});
  cli(['d1','execute','datafoldit-test-poc','--remote','--file',sql]);
}finally{await unlink(sql).catch(()=>{});await rmdir(directory);}
console.log('Existing test Zoho settings stored as a Worker secret. Test admin approved; no email sent by this script.');
