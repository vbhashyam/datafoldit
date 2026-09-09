// Offline compatibility check; prints only aggregate issues, never record contents.
import {execFileSync} from 'node:child_process';
import {validate,fields} from './dashboard.mjs';
const path=process.argv[2];
if(!path?.includes('/private-backups/'))throw Error('Private snapshot path required');
const rows=JSON.parse(execFileSync('sqlite3',['-readonly','-json',path,'SELECT kind,data FROM dashboard_records'],{maxBuffer:10*1024*1024}).toString());
const issues={};
for(const row of rows){try{const data=JSON.parse(row.data),result=validate(row.kind,data);for(const [key,type]of Object.entries(fields[row.kind]))if(type==='number'&&Number(data[key]||0)!==result[key])issues[row.kind+': recalculated '+key]=(issues[row.kind+': recalculated '+key]||0)+1;}catch(e){issues[row.kind+': '+e.message]=(issues[row.kind+': '+e.message]||0)+1;}}
console.log(JSON.stringify({records:rows.length,issues}));
if(Object.keys(issues).length)process.exitCode=1;
