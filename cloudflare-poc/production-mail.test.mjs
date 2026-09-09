import assert from 'node:assert/strict';
import {sendInvitationMail} from './invitation-mail.mjs';
const captured=[];
const MAILER={fetch:async(url,options)=>{captured.push(JSON.parse(options.body));return {ok:true};}};
const token='a'.repeat(64);
await sendInvitationMail({MAILER},'vamsi@datafoldit.com',token);
assert.ok(captured.pop().link.startsWith('https://datafoldit-test-poc.vamsibh07.workers.dev/accept#'));
for(const PUBLIC_ORIGIN of ['https://www.accounts.datafoldit.com','https://datafoldit-production.vamsibh07.workers.dev']){
 await sendInvitationMail({MAILER,DEPLOYMENT_ENV:'production',PUBLIC_ORIGIN},'vamsi@datafoldit.com',token);
 assert.equal(captured.pop().link,PUBLIC_ORIGIN+'/accept#token='+token);
}
for(const PUBLIC_ORIGIN of [undefined,'https://accounts.datafoldit.com','http://www.accounts.datafoldit.com','https://datafoldit-test-poc.vamsibh07.workers.dev','https://example.com']){
 await assert.rejects(sendInvitationMail({MAILER,DEPLOYMENT_ENV:'production',PUBLIC_ORIGIN},'vamsi@datafoldit.com',token));
}
assert.equal(captured.length,0);
console.log('PASS: production mail cannot use test or unapproved origins; test behavior unchanged; no email sent');
